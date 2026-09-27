import datetime
import random
import textwrap
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import psutil
import requests
from PIL import Image, ImageDraw, ImageFont

import library.config as config
import library.screen_state as screen_state
from library.display import display
from library.log import logger


WEATHER_UNITS = {
    "metric": ("C", "m/s"),
    "imperial": ("F", "mph"),
    "standard": ("K", "m/s"),
}

BOXES = {
    "temperature": (38, 56, 232, 250),
    "day": (30, 370, 420, 240),
    "week": (30, 700, 420, 300),
    "news": (30, 1040, 420, 520),
    "wind": (30, 1695, 420, 145),
}


def start_idle_weather_monitor():
    idle_config = config.CONFIG_DATA.get("idle_weather", {})
    if not idle_config.get("ENABLED", False):
        return None

    worker = threading.Thread(target=_idle_weather_loop, name="Idle_Weather", daemon=True)
    worker.start()
    return worker


def _idle_weather_loop():
    idle_config = config.CONFIG_DATA.get("idle_weather", {})
    threshold = float(idle_config.get("CPU_THRESHOLD_PERCENT", 15))
    interval_seconds = max(60, int(idle_config.get("INTERVAL_SECONDS", 3600)))
    display_seconds = int(idle_config.get("DISPLAY_SECONDS", 300))
    poll_seconds = max(1, int(idle_config.get("POLL_SECONDS", 1)))
    refresh_seconds = max(60, int(idle_config.get("REFRESH_SECONDS", 600)))

    last_drawn = 0
    weather_started = None
    next_allowed_weather = time.monotonic() + interval_seconds

    logger.info("Idle weather screen enabled: %ds every %ds when CPU < %.1f%%",
                display_seconds, interval_seconds, threshold)

    while True:
        try:
            cpu_percent = psutil.cpu_percent(interval=poll_seconds)
            now = time.monotonic()

            if screen_state.idle_weather_active and weather_started is not None and now - weather_started >= display_seconds:
                _restore_dashboard_with_values()
                weather_started = None
                logger.info("Idle weather screen ended after %ds", display_seconds)
                continue

            if (now >= next_allowed_weather and cpu_percent < threshold and
                    not screen_state.idle_weather_active and not screen_state.gif_overlay_active):
                _draw_weather_screen()
                last_drawn = now
                weather_started = now
                next_allowed_weather = now + interval_seconds
        except Exception as exc:
            logger.error("Idle weather screen error: %s", exc)
            screen_state.idle_weather_active = False
            time.sleep(5)


def _draw_weather_screen():
    weather = _fetch_weather()
    headline = _fetch_news_headline()
    background_path = _select_background(weather)

    image = _display_safe_image(Image.open(background_path).convert("RGB").resize((480, 1920), Image.Resampling.LANCZOS))

    _draw_box_text(image, BOXES["temperature"], weather["temperature"], font_size=72, color=(255, 255, 255))
    _draw_box_text(image, BOXES["day"], weather["day"], font_size=40, color=(255, 255, 255))
    _draw_box_text(image, BOXES["week"], weather["week"], font_size=31, color=(255, 255, 255))
    _draw_box_text(image, BOXES["news"], headline, font_size=32, color=(255, 255, 255))
    _draw_box_text(image, BOXES["wind"], weather["wind"], font_size=28, color=(255, 255, 255))
    image = _display_safe_image(image)

    screen_state.idle_weather_active = True
    display.lcd.DisplayPILImage(image, 0, 0, 480, 1920)
    logger.info("Idle weather screen displayed with %s", Path(background_path).name)


def _restore_dashboard_with_values():
    display.display_static_images()
    display.display_static_text()
    screen_state.idle_weather_active = False

    try:
        import library.stats as stats

        stats.CPU.percentage()
        stats.CPU.frequency()
        stats.CPU.temperature()
        if stats.Gpu.is_available():
            stats.Gpu.stats()
        stats.Memory.stats()
        stats.Disk.stats()
        stats.Net.stats()
    except Exception as exc:
        logger.error("Idle weather dashboard value restore failed: %s", exc)


def _draw_box_text(image, box, text, font_size, color):
    x, y, width, height = box
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(Path(config.FONTS_DIR) / "roboto-mono/RobotoMono-Bold.ttf"), font_size)
    line_height = int(font_size * 1.15)
    max_chars = max(8, int(width / (font_size * 0.58)))
    lines = []

    for paragraph in str(text).splitlines():
        wrapped = textwrap.wrap(paragraph, width=max_chars) or [""]
        lines.extend(wrapped)

    max_lines = max(1, height // line_height)
    lines = lines[:max_lines]
    if len(lines) == max_lines and len(" ".join(lines)) < len(str(text).replace("\n", " ")):
        lines[-1] = lines[-1].rstrip(".") + "..."

    total_height = len(lines) * line_height
    cursor_y = y + max(0, (height - total_height) // 2)

    for line in lines:
        left, top, right, bottom = draw.textbbox((0, 0), line, font=font)
        cursor_x = x + max(0, (width - (right - left)) // 2)
        draw.text((cursor_x, cursor_y), line, font=font, fill=color, stroke_width=2, stroke_fill=(0, 0, 0))
        cursor_y += line_height


def _display_safe_image(image):
    return image.quantize(colors=64, method=Image.Quantize.MEDIANCUT).convert("RGB")


def _fetch_weather():
    units = config.CONFIG_DATA["config"].get("WEATHER_UNITS", "metric")
    temp_unit, speed_unit = WEATHER_UNITS.get(units, WEATHER_UNITS["metric"])

    fallback = {
        "temperature": "Weather\nUnavailable",
        "day": "Today\nUnable to fetch weather.",
        "week": "Week\nNo forecast available.",
        "wind": "Wind\nNo live data",
        "condition": "default",
        "is_night": False,
    }

    return _fetch_open_meteo(fallback, temp_unit, speed_unit)


def _fetch_news_headline():
    idle_config = config.CONFIG_DATA.get("idle_weather", {})
    url = idle_config.get("NEWS_RSS_URL", "https://feeds.bbci.co.uk/news/world/rss.xml")

    try:
        response = requests.get(url, timeout=8)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        item = root.find("./channel/item/title")
        if item is not None and item.text:
            return "News\n" + item.text.strip()
    except Exception as exc:
        logger.error("Idle news fetch failed: %s", exc)

    return "News\nNo headline available"


def _fetch_open_meteo(fallback, temp_unit, speed_unit):
    lat = config.CONFIG_DATA["config"].get("WEATHER_LATITUDE", "")
    lon = config.CONFIG_DATA["config"].get("WEATHER_LONGITUDE", "")
    units = config.CONFIG_DATA["config"].get("WEATHER_UNITS", "metric")
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,weather_code,wind_speed_10m,wind_direction_10m,pressure_msl",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "timezone": "auto",
        "forecast_days": 5,
    }

    if units == "imperial":
        params["temperature_unit"] = "fahrenheit"
        params["wind_speed_unit"] = "mph"
    else:
        params["wind_speed_unit"] = "ms"

    try:
        response = requests.get("https://api.open-meteo.com/v1/forecast", timeout=8, params=params)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.error("Open-Meteo fallback fetch failed: %s", exc)
        fallback["temperature"] = "Weather\nUnavailable"
        fallback["day"] = "Today\nUnable to fetch weather."
        return fallback

    current = data.get("current", {})
    daily = data.get("daily", {})
    weather_code = int(current.get("weather_code", 0))
    description, condition = _wmo_weather(weather_code)
    dates = daily.get("time", [])
    highs = daily.get("temperature_2m_max", [])
    lows = daily.get("temperature_2m_min", [])
    codes = daily.get("weather_code", [])
    week_lines = []

    for index, date_text in enumerate(dates[:5]):
        try:
            day_name = datetime.datetime.fromisoformat(date_text).strftime("%a")
        except ValueError:
            day_name = date_text[:3]
        day_desc, _ = _wmo_weather(int(codes[index] if index < len(codes) else weather_code))
        high = highs[index] if index < len(highs) else current.get("temperature_2m", 0)
        low = lows[index] if index < len(lows) else current.get("temperature_2m", 0)
        week_lines.append(f"{day_name} {high:.0f}/{low:.0f} {day_desc}")

    high_today = highs[0] if highs else current.get("temperature_2m", 0)
    low_today = lows[0] if lows else current.get("temperature_2m", 0)

    return {
        "temperature": f"{current.get('temperature_2m', 0):.0f}{temp_unit}",
        "day": f"Today\n{description}\nHigh {high_today:.0f}{temp_unit}  Low {low_today:.0f}{temp_unit}",
        "week": "Week\n" + "\n".join(week_lines),
        "wind": f"Wind {current.get('wind_speed_10m', 0):.1f} {speed_unit} {_degrees_to_direction(float(current.get('wind_direction_10m', 0)))}\nPressure {current.get('pressure_msl', 0):.0f} hPa",
        "condition": condition,
        "is_night": False,
    }


def _wmo_weather(code):
    if code in (0, 1):
        return "Clear", "sun"
    if code in (2, 3, 45, 48):
        return "Cloudy", "cloud"
    if 51 <= code <= 67 or 80 <= code <= 82 or 95 <= code <= 99:
        return "Rain", "rain"
    if 71 <= code <= 77 or 85 <= code <= 86:
        return "Snow", "snow"
    return "Weather", "default"


def _select_background(weather):
    background_dir = Path(config.CONFIG_DATA.get("idle_weather", {}).get("BACKGROUND_DIR", r"F:\Scratch\tester\truzxbackground"))
    condition = weather.get("condition", "default")
    candidates = []

    if weather.get("is_night"):
        candidates.append("truzxnight.png")
    candidates.extend({
        "rain": ["tuxrainy.png"],
        "snow": ["truzxoversnow.png"],
        "cloud": ["truzxovercast.png"],
        "sun": ["turzxsunny.png"],
        "default": ["instagoku.png"],
    }.get(condition, ["instagoku.png"]))

    existing = [background_dir / candidate for candidate in candidates if (background_dir / candidate).exists()]
    if existing:
        return random.choice(existing)

    return config.MAIN_DIRECTORY / "res/themes/Gradient/background.png"


def _condition_name(condition_id):
    if 200 <= condition_id < 600:
        return "rain"
    if 600 <= condition_id < 700:
        return "snow"
    if 700 <= condition_id < 800:
        return "cloud"
    if condition_id == 800:
        return "sun"
    if 801 <= condition_id < 900:
        return "cloud"
    return "default"


def _degrees_to_direction(degrees):
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return directions[int((degrees + 22.5) // 45) % 8]