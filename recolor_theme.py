from pathlib import Path
from PIL import Image
import colorsys

img_path = Path(r'F:/Scratch/tester/turing-smart-screen-python/res/themes/Cyberpunk 2077 Vertical/background.png')
img = Image.open(img_path).convert('RGB')
pix = img.load()
w, h = img.size

for y in range(h):
    for x in range(w):
        r, g, b = pix[x, y]
        h1, s1, v1 = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        hue_deg = h1 * 360.0

        # Shift pink/purple/blue accents to red.
        if 190 <= hue_deg <= 330 and s1 > 0.12:
            h1 = 0.0
            s1 = min(1.0, s1 * 1.10)
            v1 = min(1.0, v1 * 0.95)
            rr, gg, bb = colorsys.hsv_to_rgb(h1, s1, v1)
            r, g, b = int(rr * 255), int(gg * 255), int(bb * 255)

        # Global dark/red grade for a black-red look.
        g = int(g * 0.42)
        b = int(b * 0.28)
        r = min(255, int(r * 1.05))

        pix[x, y] = (r, g, b)

img.save(img_path)
print(f'Recolored: {img_path}')
