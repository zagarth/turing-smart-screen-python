import threading
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

import library.config as config
import library.screen_state as screen_state
from library.display import display
from library.log import logger

# Per-file (crop box, first visible frame, opaque) found on first play; frames themselves are not cached.
_gif_layouts = {}
# Near-black pixels of opaque GIFs fade out so their background blends into the dashboard.
_KEY_LUT = [0 if value <= 12 else min(255, (value - 12) * 255 // 28) for value in range(256)]


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
    idle_since = None
    first_play = True
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

        required_idle = 0 if first_play else idle_seconds
        if now >= next_allowed and now - idle_since >= required_idle:
            gif_path = gif_paths[playlist_index % len(gif_paths)]
            completed = _play_gif(gif_path, overlay_config, cpu_threshold, gpu_threshold)
            if completed:
                first_play = False
                playlist_index = (playlist_index + 1) % len(gif_paths)
                next_allowed = time.monotonic() + min_interval
                idle_since = time.monotonic()
            else:
                next_allowed = time.monotonic() + poll_seconds
                idle_since = time.monotonic()

        time.sleep(poll_seconds)


def _play_gif(gif_path, overlay_config, cpu_threshold, gpu_threshold):
    if screen_state.idle_weather_active or not _loads_below(cpu_threshold, gpu_threshold):
        return False

    playback_fps = max(0.5, float(overlay_config.get("PLAYBACK_FPS", 8)))
    slot_seconds = max(1, float(overlay_config.get("SLOT_SECONDS", 12)))
    abort_after = max(0, float(overlay_config.get("ABORT_AFTER_SECONDS", 30)))
    target_area = str(overlay_config.get("TARGET_AREA", "AMD_LOGO"))
    target_box = overlay_config.get("TARGET_BOX")
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
        area_position, area_size, area_background, target_image_data, target_image = _target_area(
            target_area, target_box)
        crop_box, first_frame, opaque = _gif_layout(gif_path)
        logger.info("GIF overlay started: %s area=%s size=%s slot=%ss fps=%.1f",
                    gif_path.name, target_area, area_size, slot_seconds, playback_fps)
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
            padding = max(0, int(overlay_config.get("AREA_PADDING", 0)))
            crop_width = crop_box[2] - crop_box[0]
            crop_height = crop_box[3] - crop_box[1]
            scale = min(max(1, area_size[0] - padding * 2) / crop_width,
                        max(1, area_size[1] - padding * 2) / crop_height)
            if not bool(overlay_config.get("ALLOW_UPSCALE", False)):
                scale = min(1.0, scale)
            frame_size = (max(1, round(crop_width * scale)), max(1, round(crop_height * scale)))
            frame_position = (
                area_position[0] + (area_size[0] - frame_size[0]) // 2,
                area_position[1] + (area_size[1] - frame_size[1]) // 2,
            )
            edge_mask = _edge_mask(frame_size, int(overlay_config.get("EDGE_FEATHER", 12))) if opaque else None
            n_frames = getattr(gif, "n_frames", 1)
            display.lcd.SetTemporaryFrameRate(playback_fps)
            started = time.monotonic()
            deadline = started + slot_seconds
            frame_index = first_frame
            gif.seek(frame_index)
            frame_end = started + _frame_duration(gif)
            over_since = None
            while True:
                now = time.monotonic()
                if now >= deadline:
                    break
                if _loads_below(cpu_threshold, gpu_threshold):
                    over_since = None
                elif over_since is None:
                    over_since = now
                elif now - over_since >= abort_after:
                    logger.info("GIF overlay stopped: load above threshold for %.0fs", abort_after)
                    return False
                # Skip frames the USB send rate cannot show so the clip plays at its real speed.
                while now >= frame_end:
                    frame_index = frame_index + 1 if frame_index + 1 < n_frames else first_frame
                    gif.seek(frame_index)
                    frame_end += _frame_duration(gif)
                frame = _prepare_frame(gif, crop_box, frame_size, edge_mask)
                if not display.lcd.SetOverlayImageAndWait(frame, *frame_position, timeout=5.0):
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


def _gif_layout(gif_path):
    layout = _gif_layouts.get(gif_path)
    if layout is not None:
        return layout

    luma_box = alpha_box = None
    first_luma = first_alpha = None
    opaque = True
    with Image.open(gif_path) as gif:
        for index in range(getattr(gif, "n_frames", 1)):
            gif.seek(index)
            frame = gif.convert("RGBA")
            alpha = frame.getchannel("A")
            if alpha.getextrema()[0] < 255:
                opaque = False
            box = frame.convert("L").point(lambda value: 255 if value > 40 else 0).getbbox()
            if box:
                first_luma = index if first_luma is None else first_luma
                luma_box = box if luma_box is None else _union_box(luma_box, box)
            box = alpha.getbbox()
            if box:
                first_alpha = index if first_alpha is None else first_alpha
                alpha_box = box if alpha_box is None else _union_box(alpha_box, box)
        full_box = (0, 0, gif.width, gif.height)

    if opaque:
        layout = (luma_box or full_box, first_luma or 0, True)
    else:
        layout = (alpha_box or full_box, first_alpha or 0, False)
    _gif_layouts[gif_path] = layout
    logger.info("GIF layout: %s crop=%s first_frame=%d opaque=%s", Path(gif_path).name, *layout)
    return layout


def _union_box(a, b):
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def _frame_duration(gif):
    duration = gif.info.get("duration", 100) or 100
    # Browsers treat near-zero GIF delays as 100 ms; match that.
    return (duration if duration >= 20 else 100) / 1000


def _edge_mask(size, feather):
    mask = Image.new("L", size, 255)
    feather = max(0, min(feather, min(size) // 2))
    draw = ImageDraw.Draw(mask)
    for step in range(feather):
        draw.rectangle((step, step, size[0] - 1 - step, size[1] - 1 - step),
                       outline=round(255 * step / feather))
    return mask


def _prepare_frame(gif, crop_box, frame_size, edge_mask):
    frame = gif.convert("RGBA")
    if crop_box != (0, 0, frame.width, frame.height):
        frame = frame.crop(crop_box)
    if frame.size != frame_size:
        frame = frame.resize(frame_size, Image.Resampling.LANCZOS)
    if edge_mask is not None:
        frame.putalpha(ImageChops.multiply(frame.convert("L").point(_KEY_LUT), edge_mask))
    return frame


def _target_area(area_name, target_box=None):
    area_data = config.THEME_DATA.get("static_images", {}).get(area_name)
    if not area_data:
        raise ValueError(f"GIF target area is not defined in theme static_images: {area_name}")

    logo_x = int(area_data.get("X", 0))
    logo_y = int(area_data.get("Y", 0))
    logo_width = int(area_data.get("WIDTH", 0))
    logo_height = int(area_data.get("HEIGHT", 0))
    if logo_width <= 0 or logo_height <= 0:
        raise ValueError(f"GIF target area has invalid dimensions: {area_name}")

    if target_box:
        x, y, width, height = (int(value) for value in target_box)
    else:
        x, y, width, height = logo_x, logo_y, logo_width, logo_height
    if not (x <= logo_x and y <= logo_y and logo_x + logo_width <= x + width and logo_y + logo_height <= y + height):
        raise ValueError("GIF TARGET_BOX must fully contain the target logo")

    background_data = config.THEME_DATA.get("static_images", {}).get("BACKGROUND", {})
    background_path = Path(config.THEME_DATA["PATH"]) / background_data.get("PATH", "background.png")
    background = Image.open(background_path).convert("RGBA")
    if background.size != (display.lcd.get_width(), display.lcd.get_height()):
        background = background.resize(
            (display.lcd.get_width(), display.lcd.get_height()),
            Image.Resampling.LANCZOS,
        )

    image_path = Path(config.THEME_DATA["PATH"]) / area_data.get("PATH", "")
    logo = Image.open(image_path).convert("RGBA")
    if logo.size != (logo_width, logo_height):
        logo = logo.resize((logo_width, logo_height), Image.Resampling.LANCZOS)
    target_image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    target_image.alpha_composite(logo, dest=(logo_x - x, logo_y - y))

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