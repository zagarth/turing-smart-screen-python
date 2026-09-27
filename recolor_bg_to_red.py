from pathlib import Path
from PIL import Image
import colorsys

bg_path = Path(r"F:\Scratch\tester\turing-smart-screen-python\res\themes\Cyberpunk 2077 Vertical\background.png")
backup_path = bg_path.with_name("background.original.png")
report_path = bg_path.with_name("recolor-report.txt")

img = Image.open(bg_path).convert("RGB")
if not backup_path.exists():
    img.save(backup_path)

pixels = img.load()
width, height = img.size

yellow_orange_changed = 0
magenta_purple_changed = 0
all_changed = 0

for y in range(height):
    for x in range(width):
        r, g, b = pixels[x, y]
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        hue = h * 360.0

        changed = False

        # Convert yellow/orange accents to red.
        if 20 <= hue <= 85 and s >= 0.18 and v >= 0.12:
            h = 0.0
            s = min(1.0, max(0.55, s * 1.15))
            v = min(1.0, max(0.22, v * 0.92))
            yellow_orange_changed += 1
            changed = True

        # Convert magenta/purple to red as a cleanup pass.
        elif 260 <= hue <= 345 and s >= 0.18 and v >= 0.10:
            h = 0.0
            s = min(1.0, max(0.50, s * 1.05))
            v = min(1.0, max(0.20, v * 0.90))
            magenta_purple_changed += 1
            changed = True

        if changed:
            rr, gg, bb = colorsys.hsv_to_rgb(h, s, v)
            # Final black-red grade.
            nr = min(255, int(rr * 255 * 1.04))
            ng = int(gg * 255 * 0.32)
            nb = int(bb * 255 * 0.25)
            pixels[x, y] = (nr, ng, nb)
            all_changed += 1

img.save(bg_path)

report = (
    f"image={bg_path}\n"
    f"size={width}x{height}\n"
    f"yellow_orange_changed={yellow_orange_changed}\n"
    f"magenta_purple_changed={magenta_purple_changed}\n"
    f"all_changed={all_changed}\n"
)
report_path.write_text(report, encoding="utf-8")
print(report)
