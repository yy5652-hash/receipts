"""Contact sheet: python tools/sheet.py <frames_dir> <out.jpg> [cols] [width] [first:last:step]"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw
src, out = Path(sys.argv[1]), sys.argv[2]
cols = int(sys.argv[3]) if len(sys.argv) > 3 else 4
w = int(sys.argv[4]) if len(sys.argv) > 4 else 640
files = sorted(src.glob('*.jpg'))
if len(sys.argv) > 5:
    a, b, s = (int(x) if x else None for x in sys.argv[5].split(':'))
    files = files[a:b:s]
h = w * 9 // 16
rows = (len(files) + cols - 1) // cols
sheet = Image.new('RGB', (cols * w, rows * h), 'black')
d = ImageDraw.Draw(sheet)
for i, f in enumerate(files):
    im = Image.open(f).resize((w, h), Image.LANCZOS)
    x, y = (i % cols) * w, (i // cols) * h
    sheet.paste(im, (x, y))
    d.text((x + 6, y + 4), f.stem, fill='yellow')
sheet.save(out, quality=88)
print(out, len(files), 'frames')
