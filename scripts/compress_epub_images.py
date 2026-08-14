"""Compress EPUB recipe images in-place to reduce repo size.

JPEG: quality 75, optimized, progressive.
PNG:  optimized; quantize to a 256-colour palette if the image has <=256
      unique colours or is small, otherwise just Pillow optimize.
GIF:  reduce palette to 128 colours.

Keeps original filenames so database image URLs remain valid.
"""
import os
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent / 'static' / 'epub_images'


def _is_small_color_set(img: Image.Image) -> bool:
    """Return True if the image uses 256 or fewer unique colours."""
    try:
        # Resize to a small thumbnail for a cheap uniqueness check
        thumb = img.convert('RGB').resize((150, 150))
        return len(set(thumb.getdata())) <= 256
    except Exception:
        return False


def compress_image(path: Path) -> bool:
    """Compress a single image in-place. Returns True if changed."""
    ext = path.suffix.lower()
    try:
        with Image.open(path) as img:
            img.load()  # force load so we can save over the same file

            if ext in ('.jpg', '.jpeg'):
                rgb = img.convert('RGB')
                rgb.save(path, format='JPEG', quality=75, optimize=True, progressive=True)
                return True

            if ext == '.png':
                # If it has alpha or few colours, keep PNG; otherwise we could convert,
                # but we keep format to preserve DB URLs.
                if img.mode in ('RGBA', 'LA') or _is_small_color_set(img):
                    try:
                        img.save(path, format='PNG', optimize=True)
                    except Exception:
                        img.convert('RGB').save(path, format='PNG', optimize=True)
                else:
                    # Try quantizing to 256 colours; fall back to normal optimize.
                    try:
                        quantized = img.convert('RGB').quantize(colors=256, method=Image.Quantize.MEDIANCUT)
                        quantized.save(path, format='PNG', optimize=True)
                    except Exception:
                        img.convert('RGB').save(path, format='PNG', optimize=True)
                return True

            if ext == '.gif':
                if getattr(img, 'is_animated', False):
                    img.save(path, format='GIF', optimize=True)
                else:
                    pal = img.convert('P', palette=Image.ADAPTIVE, colors=128)
                    pal.save(path, format='GIF', optimize=True)
                return True

    except Exception as e:
        print(f'  skip {path}: {e}')
    return False


def main():
    if not ROOT.exists():
        print(f'Image root not found: {ROOT}')
        return

    files = [p for p in ROOT.rglob('*') if p.is_file() and p.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif')]
    print(f'Found {len(files)} images in {ROOT}')

    total_before = sum(p.stat().st_size for p in files)
    changed = 0

    for i, path in enumerate(files, 1):
        before = path.stat().st_size
        if compress_image(path):
            after = path.stat().st_size
            changed += 1
            if i % 500 == 0 or i == len(files):
                print(f'  processed {i}/{len(files)} ({changed} changed)')

    total_after = sum(p.stat().st_size for p in files)
    before_mb = total_before / (1024 * 1024)
    after_mb = total_after / (1024 * 1024)
    saved_mb = before_mb - after_mb
    print(f'Done. {changed}/{len(files)} images changed.')
    print(f'Size: {before_mb:.1f} MB -> {after_mb:.1f} MB (saved {saved_mb:.1f} MB, {(saved_mb/before_mb)*100:.1f}%)')


if __name__ == '__main__':
    main()
