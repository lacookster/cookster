"""Re-extract the latest uploaded books, compress images, prune unused ones, and reload the DB."""
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indexer import (
    _images_dir_for_epub,
    _recipe_slug,
    index_preprocessed_dir,
    preprocess_dir,
)

# Latest uploaded books that need re-extraction.
NEW_BOOKS = [
    "Estela (Restaurant)_ Mattos, Ignacio.epub",
    "Frankie Gaw - First Generation _ Recipes from My Taiwanese-American Home.epub",
    "Into the vietnamese kitchen.epub",
    "Nguyen, Luke - Street Food Asia.epub",
    "The Good Bite’s High Protein Meal Prep.epub",
    "Thomas Keller - The French Laundry, Per Se.epub",
]

BOOKS_DIR = Path('books')
RECIPES_DIR = Path('data/recipes')
DB_PATH = Path('cookster.db')
IMAGE_BASE = Path('static/epub_images')


def _find_epub_path(basename: str) -> Path:
    for d in (BOOKS_DIR / 'added', BOOKS_DIR):
        p = d / basename
        if p.exists():
            return p
    return None


def _slug_for_path(epub_path: Path) -> str:
    return _recipe_slug({'file_path': str(epub_path)})


def _compress_dir(path: Path):
    """Run the existing image compressor on a single directory."""
    from scripts.compress_epub_images import compress_image
    files = [p for p in path.rglob('*') if p.is_file() and p.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif')]
    changed = 0
    for f in files:
        before = f.stat().st_size
        if compress_image(f):
            changed += 1
    total = sum(p.stat().st_size for p in files)
    print(f'  Compressed {changed}/{len(files)} images in {path}; total size {total/(1024*1024):.1f} MB')


def _prune_unused_images(book_dir: Path, json_path: Path):
    """Delete image files in book_dir that are not referenced by the JSON recipes."""
    if not json_path.exists():
        return
    with open(json_path, 'r', encoding='utf-8') as f:
        try:
            recipes = json.load(f)
        except json.JSONDecodeError:
            return
    referenced = set()
    for r in recipes:
        img = r.get('image', '')
        if not img:
            continue
        # image URL/path -> basename
        referenced.add(os.path.basename(img))

    removed = 0
    for f in book_dir.rglob('*'):
        if f.is_file() and f.name not in referenced:
            try:
                f.unlink()
                removed += 1
            except Exception as e:
                print(f'    could not remove {f}: {e}')
    print(f'  Pruned {removed} unused image files from {book_dir}')


def main():
    books_to_process = []
    for basename in NEW_BOOKS:
        epub_path = _find_epub_path(basename)
        if not epub_path:
            print(f'Not found, skipping: {basename}')
            continue
        slug = _slug_for_path(epub_path)
        image_dir = IMAGE_BASE / _images_dir_for_epub(str(epub_path))
        json_path = RECIPES_DIR / f'{slug}.json'
        books_to_process.append((epub_path, slug, image_dir, json_path))

    if not books_to_process:
        print('No books to process.')
        return

    # 1. Remove old JSON and image directories so we get a clean re-extraction.
    print('\n=== Removing old JSON and image dirs ===')
    for epub_path, slug, image_dir, json_path in books_to_process:
        if json_path.exists():
            print(f'  removing {json_path}')
            json_path.unlink()
        if image_dir.exists():
            print(f'  removing {image_dir}')
            shutil.rmtree(image_dir)

    # 2. Re-extract books (writes new JSON and image dirs).
    print('\n=== Re-extracting books ===')
    preprocess_dir(str(BOOKS_DIR), str(RECIPES_DIR), force=False)

    # 3. For each book, compress images and prune unused ones.
    print('\n=== Compressing and pruning images ===')
    for epub_path, slug, image_dir, json_path in books_to_process:
        if not image_dir.exists():
            print(f'  no image dir for {slug}')
            continue
        _compress_dir(image_dir)
        _prune_unused_images(image_dir, json_path)

    # 4. Reload affected sources into the DB.
    print('\n=== Reloading DB ===')
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        for _, _, _, json_path in books_to_process:
            if not json_path.exists():
                continue
            with open(json_path, 'r', encoding='utf-8') as f:
                try:
                    recs = json.load(f)
                except json.JSONDecodeError:
                    continue
            if not recs:
                continue
            source = recs[0]['source']
            print(f'  clearing old DB rows for {source}')
            try:
                c.execute('DELETE FROM recipes_fts WHERE rowid IN (SELECT id FROM recipes WHERE source = ?)', (source,))
            except sqlite3.OperationalError:
                pass
            c.execute('DELETE FROM recipes WHERE source = ?', (source,))
            c.execute('DELETE FROM book_index_log WHERE source = ?', (source,))
        conn.commit()
    finally:
        conn.close()

    index_preprocessed_dir(str(RECIPES_DIR), str(DB_PATH), force=False)

    # 5. Print summary.
    print('\n=== Summary ===')
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        for _, slug, image_dir, json_path in books_to_process:
            if not json_path.exists():
                continue
            with open(json_path, 'r', encoding='utf-8') as f:
                recs = json.load(f)
            if not recs:
                print(f'{slug}: 0 recipes')
                continue
            source = recs[0]['source']
            total = c.execute('SELECT COUNT(*) FROM recipes WHERE source = ?', (source,)).fetchone()[0]
            with_images = c.execute('SELECT COUNT(*) FROM recipes WHERE source = ? AND image != ""', (source,)).fetchone()[0]
            unique = c.execute('SELECT COUNT(DISTINCT image) FROM recipes WHERE source = ? AND image != ""', (source,)).fetchone()[0]
            img_count = len(list(image_dir.rglob('*')))
            print(f'{slug}: {total} recipes, {with_images} with images, {unique} unique images, {img_count} image files')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
