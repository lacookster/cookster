"""Dump EPUB document structure: names, headings, class histograms.

Usage: python scripts/peep_structure.py "books/<file>.epub" [--full N] [--classes]
"""
import sys
import os
import collections

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ebooklib import epub
import ebooklib
from bs4 import BeautifulSoup


def main(path, full_index=None, show_classes=False):
    book = epub.read_epub(path)
    items = list(book.get_items())
    docs = [(i, it) for i, it in enumerate(items) if it.get_type() == ebooklib.ITEM_DOCUMENT]
    print(f"=== {os.path.basename(path)} : {len(docs)} docs ===")
    for n, (i, item) in enumerate(docs):
        html = item.get_content().decode('utf-8', errors='ignore')
        soup = BeautifulSoup(html, 'lxml')
        text_len = len(soup.get_text(strip=True))
        headings = []
        for h in soup.find_all(['h1', 'h2', 'h3', 'h4'])[:6]:
            cls = '.'.join(h.get('class', []))
            headings.append(f"{h.name}.{cls}:{h.get_text(' ', strip=True)[:60]}")
        print(f"[{n}] {item.get_name()} len={text_len}")
        for h in headings:
            print(f"     {h}")
        if full_index is not None and n == full_index:
            print("----- FULL DOC -----")
            body = soup.body or soup
            for elem in body.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'p', 'div', 'li', 'img']):
                cls = '.'.join(elem.get('class', []))
                if elem.name == 'img':
                    print(f"  <img class={cls} src={elem.get('src')}>")
                    continue
                if elem.name == 'div' and elem.find(['p', 'div', 'h1', 'h2', 'h3']):
                    continue
                txt = elem.get_text(' ', strip=True)[:100]
                if txt:
                    print(f"  {elem.name}.{cls}: {txt}")
        if show_classes:
            ctr = collections.Counter()
            for elem in soup.find_all(True):
                for c in elem.get('class', []):
                    ctr[f"{elem.name}.{c}"] += 1
            print("  classes:", ctr.most_common(15))


if __name__ == '__main__':
    path = sys.argv[1]
    full = None
    classes = '--classes' in sys.argv
    if '--full' in sys.argv:
        idx = sys.argv.index('--full')
        full = int(sys.argv[idx + 1])
    main(path, full, classes)
