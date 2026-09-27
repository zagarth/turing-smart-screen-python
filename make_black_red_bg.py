from PIL import Image, ImageDraw, ImageFilter
from pathlib import Path

w, h = 480, 1920
img = Image.new('RGB', (w, h), (8, 8, 10))
d = ImageDraw.Draw(img)

# Vertical dark-to-red atmospheric gradient
for y in range(h):
    t = y / (h - 1)
    r = int(10 + 55 * t)
    g = int(6 + 6 * t)
    b = int(8 + 10 * (1 - t))
    d.line([(0, y), (w, y)], fill=(r, g, b))

# Top red glow
for i in range(280):
    alpha_r = max(0, 200 - i)
    d.line([(0, i), (w, i)], fill=(min(255, 35 + alpha_r), 6, 10))

# Bottom crimson glow
for i in range(360):
    y = h - 1 - i
    alpha_r = max(0, 220 - i)
    d.line([(0, y), (w, y)], fill=(min(255, 45 + alpha_r), 4, 8))

# Subtle red tech grid
for x in range(0, w, 24):
    d.line([(x, 0), (x, h)], fill=(40, 10, 14), width=1)
for y in range(0, h, 48):
    d.line([(0, y), (w, y)], fill=(35, 9, 13), width=1)

# Strong accent bars behind stats zones
d.rectangle([145, 1248, 352, 1298], fill=(20, 20, 22), outline=(180, 20, 36), width=2)
d.rectangle([145, 1318, 352, 1368], fill=(20, 20, 22), outline=(180, 20, 36), width=2)
d.rectangle([250, 1568, 430, 1808], fill=(16, 16, 18), outline=(150, 24, 42), width=2)

# Vignette to deepen blacks at edges
v = Image.new('L', (w, h), 0)
vd = ImageDraw.Draw(v)
vd.ellipse([-220, -150, w + 220, h + 150], fill=210)
v = v.filter(ImageFilter.GaussianBlur(80))
black = Image.new('RGB', (w, h), (0, 0, 0))
img = Image.composite(img, black, v)

out = Path(r'F:/Scratch/tester/turing-smart-screen-python/res/themes/Cyberpunk 2077 Vertical/background.png')
img.save(out)
print(f'Wrote {out}')
