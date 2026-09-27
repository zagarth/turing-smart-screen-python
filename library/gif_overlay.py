import threading
import time
from pathlib import Path

from PIL import Image

import library.config as config
import library.screen_state as screen_state
from library.display import display
from library.log import logger


def start_gif_overlay_rotation():
    overlay_config = config.CONFIG_DATA.get("gif_overlay", {})
    if not overlay_config.get("ENABLED", False) or not overlay_config.get("LOOP_ENABLED", True):
        return None

    worker = threading.Thread(target=_gif_overlay_loop, name="GIF_Overlay_Rotation", daemon=True)
    worker.start()
    return worker


def _gif_overlay_loop():
    overlay_config = config.CONFIG_DATA.get("gif_overlay", {})
    gif_paths = [Path(path) for path in overlay_config.get("FILES", [])]
    gif_paths = [path for path in gif_paths if path.exists()]
    if not gif_paths:
        logger.error("GIF overlay rotation has no valid files")
        return

    if not hasattr(display.lcd, "SetOverlayImage"):
        logger.error("The active display backend does not support transparent GIF overlays")
        return

    cpu_threshold = float(overlay_config.get("CPU_THRESHOLD_PERCENT", 15))
    gpu_threshold = float(overlay_config.get("GPU_THRESHOLD_PERCENT", 15))
    idle_seconds = max(1, int(overlay_config.get("IDLE_SECONDS", 30)))
    min_interval = max(1, int(overlay_config.get("MIN_INTERVAL_SECONDS", 300)))
    poll_seconds = max(1, int(overlay_config.get("POLL_SECONDS", 2)))
    next_allowed = time.monotonic()
    idle_since = time.monotonic() - idle_seconds
    playlist_index = 0

    logger.info("GIF rotation enabled: %d sequential files, CPU < %.1f%%, GPU < %.1f%% for %ds",
                len(gif_paths), cpu_threshold, gpu_threshold, idle_seconds)

    while True:
        now = time.monotonic()
        if (screen_state.idle_weather_active or screen_state.gif_overlay_active or
                not _loads_below(cpu_threshold, gpu_threshold)):
            idle_since = None
            time.sleep(poll_seconds)
            continue

        if idle_since is None:
            idle_since = now

        if now >= next_allowed and now - idle_since >= idle_seconds:
            gif_path = gif_paths[playlist_index % len(gif_paths)]
            completed = _play_gif(gif_path, overlay_config, cpu_threshold, gpu_threshold)
            if completed:
                playlist_index = (playlist_index + 1) % len(gif_paths)
                next_allowed = time.monotonic() + min_interval
                idle_since = time.monotonic()
            else:
                next_allowed = time.monotonic() + poll_seconds
                idle_since = time.monotonic()

        time.sleep(poll_seconds)


def _play_gif(gif_path, overlay_config, cpu_threshold, gpu_threshold):
    if screen_state.idle_weather_active or not _loads_below(cpu_threshold, gpu_threshold):
        return

    playback_fps = max(0.5, float(overlay_config.get("PLAYBACK_FPS", 8)))
    slot_seconds = max(1, float(overlay_config.get("SLOT_SECONDS", 12)))
    target_area = str(overlay_config.get("TARGET_AREA", "AMD_LOGO"))
    target_opacity = max(0, min(255, int(overlay_config.get("TARGET_OPACITY", 0))))
    transition_seconds = max(0, float(overlay_config.get("TRANSITION_SECONDS", 1.0)))
    transition_steps = max(1, int(overlay_config.get("TRANSITION_STEPS", 8)))
    target_image_data = None
    target_image = None
    target_hidden = False
    area_position = None
    area_background = None
    screen_state.gif_overlay_active = True
    try:
        area_position, area_size, area_background, target_image_data, target_image = _target_area(target_area)
        logger.info("GIF overlay started: %s area=%s slot=%ss fps=%.1f",
                    gif_path.name, target_area, slot_seconds, playback_fps)
        if target_opacity == 0:
            transition_fps = transition_steps / transition_seconds if transition_seconds > 0 else playback_fps
            display.lcd.SetTemporaryFrameRate(transition_fps)
            _fade_target(
                area_background,
                target_image,
                area_position,
                start_opacity=255,
                end_opacity=0,
                duration=transition_seconds,
                steps=transition_steps,
            )
            display.lcd.DisplayPILImage(area_background, *area_position, *area_size)
            target_hidden = True
        with Image.open(gif_path) as gif:
            available_width = max(1, area_size[0] - max(0, int(overlay_config.get("AREA_PADDING", 8))) * 2)
            available_height = max(1, area_size[1] - max(0, int(overlay_config.get("AREA_PADDING", 8))) * 2)
            scale = min(available_width / gif.width, available_height / gif.height)
            if not bool(overlay_config.get("ALLOW_UPSCALE", False)):
                scale = min(1.0, scale)
            frame_size = (max(1, round(gif.width * scale)), max(1, round(gif.height * scale)))
            frame_position = ((area_size[0] - frame_size[0]) // 2, (area_size[1] - frame_size[1]) // 2)
            first_visible_frame = 0
            for frame_index in range(getattr(gif, "n_frames", 1)):
                gif.seek(frame_index)
                if gif.convert("RGB").getbbox() is not None:
                    first_visible_frame = frame_index
                    break
            display.lcd.SetTemporaryFrameRate(playback_fps)
            deadline = time.monotonic() + slot_seconds
            while time.monotonic() < deadline:
                for frame_index in range(first_visible_frame, getattr(gif, "n_frames", 1)):
                    if time.monotonic() >= deadline:
                        break
                    gif.seek(frame_index)
                    frame = gif.convert("RGBA")
                    if frame.size != frame_size:
                        frame = frame.resize(frame_size, Image.Resampling.LANCZOS)
                    overlay_position = (area_position[0] + frame_position[0], area_position[1] + frame_position[1])
                    if not display.lcd.SetOverlayImageAndWait(frame, *overlay_position, timeout=5.0):
                        logger.warning("GIF overlay frame %d timed out waiting for USB send", frame_index)
            return True
    except Exception:
        logger.exception("GIF overlay failed")
        return False
    finally:
        if target_hidden and target_image_data and target_image is not None and area_position is not None:
            display.lcd.SetOverlayImageAndWait(area_background, *area_position, timeout=5.0)
            transition_fps = transition_steps / transition_seconds if transition_seconds > 0 else playback_fps
            display.lcd.SetTemporaryFrameRate(transition_fps)
            _fade_target(
                area_background,
                target_image,
                area_position,
                start_opacity=0,
                end_opacity=255,
                duration=transition_seconds,
                steps=transition_steps,
            )
            _restore_target_image(target_image_data)
            display.lcd.ClearOverlayImageAndWait(timeout=5.0)
        else:
            display.lcd.ClearOverlayImageAndWait(timeout=5.0)
        display.lcd.ResetFrameRate()
        screen_state.gif_overlay_active = False
        logger.info("GIF overlay ended")


def _loads_below(cpu_threshold, gpu_threshold):
    cpu_percent = screen_state.cpu_percent
    gpu_percent = screen_state.gpu_percent
    return (
        cpu_percent is not None and
        gpu_percent is not None and
        cpu_percent < cpu_threshold and
        gpu_percent < gpu_threshold
    )


def _target_area(area_name):
    area_data = config.THEME_DATA.get("static_images", {}).get(area_name)
    if not area_data:
        raise ValueError(f"GIF target area is not defined in theme static_images: {area_name}")

    x = int(area_data.get("X", 0))
    y = int(area_data.get("Y", 0))
    width = int(area_data.get("WIDTH", 0))
    height = int(area_data.get("HEIGHT", 0))
    if width <= 0 or height <= 0:
        raise ValueError(f"GIF target area has invalid dimensions: {area_name}")

    background_data = config.THEME_DATA.get("static_images", {}).get("BACKGROUND", {})
    background_path = Path(config.THEME_DATA["PATH"]) / background_data.get("PATH", "background.png")
    background = Image.open(background_path).convert("RGBA")
    if background.size != (display.lcd.get_width(), display.lcd.get_height()):
        background = background.resize(
            (display.lcd.get_width(), display.lcd.get_height()),
            Image.Resampling.LANCZOS,
        )

    image_path = Path(config.THEME_DATA["PATH"]) / area_data.get("PATH", "")
    target_image = Image.open(image_path).convert("RGBA")
    if target_image.size != (width, height):
        target_image = target_image.resize((width, height), Image.Resampling.LANCZOS)

    return (
        (x, y),
        (width, height),
        background.crop((x, y, x + width, y + height)),
        area_data,
        target_image,
    )


def _fade_target(background, target, position, start_opacity, end_opacity, duration, steps):
    for step in range(steps + 1):
        ratio = step / steps
        opacity = round(start_opacity + (end_opacity - start_opacity) * ratio)
        frame = background.copy()
        faded_target = target.copy()
        alpha = faded_target.getchannel("A").point(lambda value: value * opacity // 255)
        faded_target.putalpha(alpha)
        frame.alpha_composite(faded_target)
        display.lcd.SetOverlayImageAndWait(frame, *position, timeout=5.0)


def _restore_target_image(area_data):
    image_path = Path(config.THEME_DATA["PATH"]) / area_data.get("PATH", "")
    display.lcd.DisplayBitmap(
        str(image_path),
        x=int(area_data.get("X", 0)),
        y=int(area_data.get("Y", 0)),
        width=int(area_data.get("WIDTH", 0)),
        height=int(area_data.get("HEIGHT", 0)),
    )