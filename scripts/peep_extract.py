"""Run extract_recipes_from_file on one book and print a quality summary."""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indexer import extract_recipes_from_file


def main(path, show=6):
    recs = extract_recipes_from_file(path)
    print(f"{os.path.basename(path)}: {len(recs)} recipes")
    n_img = sum(1 for r in recs if r.get('image'))
    print(f"  with image: {n_img}")
    for r in recs[:show]:
        ing = r['ingredients'].split('\n')
        st = r['steps'].split('\n')
        print(f"  - {r['title'][:70]}")
        print(f"      serves: {r.get('serves','')[:60]}")
        print(f"      ingredients[{len(ing)}]: {ing[0][:60]} | {ing[1][:60] if len(ing)>1 else ''}")
        print(f"      steps[{len(st)}]: {st[0][:70]}")
        print(f"      image: {os.path.basename(r.get('image',''))}")
    # quality checks
    bad_titles = [r['title'] for r in recs if len(r['title']) < 4 or
                  any(w in r['title'].upper() for w in ('YIELD', 'MAKE-AHEAD', 'DIRECTIONS', 'INGREDIENTS'))]
    if bad_titles:
        print('  BAD TITLES:', bad_titles[:10])
    return recs


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 6)
