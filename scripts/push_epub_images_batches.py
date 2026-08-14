"""Add static/epub_images to git and push in batches to avoid GitHub's 2 GB pack limit.

Run after compressing the images. It walks the book subdirectories under
static/epub_images, groups them into ~250 MB batches, commits each batch, and
pushes it to origin/master.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / 'static' / 'epub_images'
BATCH_MB = 250


def run(cmd, **kwargs):
    print(f'$ {" ".join(cmd)}')
    result = subprocess.run(cmd, text=True, capture_output=True, **kwargs)
    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip(), file=sys.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")
    return result


def get_dirs():
    dirs = []
    for p in ROOT.iterdir():
        if p.is_dir():
            size = sum(f.stat().st_size for f in p.rglob('*') if f.is_file())
            dirs.append((p, size))
    dirs.sort(key=lambda x: x[0].name)
    return dirs


def dir_size_mb(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob('*') if f.is_file()) / (1024 * 1024)


def main():
    dirs = get_dirs()
    if not dirs:
        print('No directories found under static/epub_images')
        return

    # Start from a clean index state for this folder (in case previous partial adds exist)
    run(['git', 'reset', 'HEAD', 'static/epub_images'])

    total = len(dirs)
    batch = []
    batch_size_mb = 0.0
    batch_num = 1

    for idx, (d, size) in enumerate(dirs, 1):
        size_mb = size / (1024 * 1024)

        if batch and batch_size_mb + size_mb > BATCH_MB:
            # Commit and push current batch
            _commit_and_push(batch, batch_num)
            batch = []
            batch_size_mb = 0.0
            batch_num += 1

        batch.append(d)
        batch_size_mb += size_mb

    if batch:
        _commit_and_push(batch, batch_num)

    print(f'All {total} directories pushed in {batch_num} batch(es).')


def _commit_and_push(batch_dirs, batch_num):
    # Stage only the selected directories
    for d in batch_dirs:
        run(['git', 'add', '-f', '--', str(d)])

    size_mb = sum(dir_size_mb(d) for d in batch_dirs)
    names = ', '.join(d.name[:40] for d in batch_dirs[:3])
    if len(batch_dirs) > 3:
        names += f' (+{len(batch_dirs) - 3} more)'

    msg = f'Add compressed EPUB images batch {batch_num} ({len(batch_dirs)} books, {size_mb:.1f} MB)'
    run(['git', 'commit', '-m', msg])

    print(f'Pushing batch {batch_num} ({len(batch_dirs)} books, {size_mb:.1f} MB)...')
    run(['git', 'push', 'origin', 'master'])
    print(f'Batch {batch_num} pushed.\n')


if __name__ == '__main__':
    main()
