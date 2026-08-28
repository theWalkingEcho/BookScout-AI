"""Simple cleaner to remove CSS and extract relationship triples from noisy text.

Usage:
  python indexer/cleaner.py input.txt > triples.jsonl
  cat input.txt | python indexer/cleaner.py

The script performs heuristic cleaning (removes CSS blocks and selectors)
and extracts records of the form: id, subject, relation, object.
"""
import re
import sys
import json
from typing import List, Dict, Optional


CSS_BLOCK_RE = re.compile(r":root\s*\{[\s\S]*?\}\s*", re.IGNORECASE)
CSS_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")


def remove_css(text: str) -> str:
    # Remove large :root { ... } blocks
    text = CSS_BLOCK_RE.sub("", text)
    # Remove C-style comments
    text = CSS_COMMENT_RE.sub("", text)
    # Remove lines that look like selector rules (start with dot or contain .bsdg-)
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            lines.append(ln)
            continue
        if s.startswith(".") or s.startswith("@"):
            # skip typical CSS selector lines
            continue
        if "bsdg-" in s:
            continue
        lines.append(ln)
    return "\n".join(lines)


def extract_triples(text: str) -> List[Dict[str, Optional[str]]]:
    lines = [l.rstrip() for l in text.splitlines()]
    n = len(lines)
    i = 0
    triples = []
    current_id = None
    while i < n:
        line = lines[i].strip()
        # detect numeric id line
        if line.isdigit():
            current_id = line
            i += 1
            continue

        # skip empty lines
        if not line:
            i += 1
            continue

        # possible subject: a non-empty line not uppercase relation
        subject = line

        # lookahead to see if a '-' separator follows soon
        j = i + 1
        while j < n and not lines[j].strip():
            j += 1

        # If immediate next non-empty is just '-' skip it
        if j < n and lines[j].strip() == '-':
            j += 1
            while j < n and not lines[j].strip():
                j += 1

        # Now expect relation line (often UPPERCASE or words like WRITTEN_BY, IN_CATEGORY, SELLS)
        relation = None
        obj = None
        if j < n:
            rel_line = lines[j].strip()
            # Sometimes relation and arrow are on different lines
            if '->' in rel_line:
                parts = rel_line.split('->', 1)
                relation = parts[0].strip()
                obj_candidate = parts[1].strip()
                if obj_candidate:
                    obj = obj_candidate
                    j += 1
                else:
                    # object on following non-empty line
                    j += 1
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n:
                        obj = lines[j].strip()
                        j += 1
            else:
                # relation may be on rel_line and arrow '->' next
                relation = rel_line
                k = j + 1
                while k < n and not lines[k].strip():
                    k += 1
                if k < n and lines[k].strip().startswith('->'):
                    # arrow may be standalone or with object
                    arrow_line = lines[k].strip()
                    if arrow_line == '->':
                        # object likely on next non-empty
                        k += 1
                        while k < n and not lines[k].strip():
                            k += 1
                        if k < n:
                            obj = lines[k].strip()
                            j = k + 1
                    else:
                        # arrow line contains object after ->
                        parts = arrow_line.split('->', 1)
                        obj = parts[1].strip()
                        j = k + 1
                else:
                    # fallback: next non-empty after relation is object if it doesn't look like a relation
                    k = j + 1
                    while k < n and not lines[k].strip():
                        k += 1
                    if k < n:
                        candidate = lines[k].strip()
                        # if candidate looks like an uppercase RELATION, don't treat as object
                        if not (candidate.upper() == candidate and len(candidate.split()) <= 3):
                            obj = candidate
                            j = k + 1
                        else:
                            # no object found
                            j = k

        # If relation or object found, add triple
        if relation or obj:
            triples.append({
                'id': current_id,
                'subject': subject if subject else None,
                'relation': relation if relation else None,
                'object': obj if obj else None,
            })
            # advance i to j
            i = j
        else:
            # nothing recognizable; advance by one line
            i += 1

    return triples


_CSS_CHARS_RE = re.compile(r"[{};@]|:root|\.bsdg-|\bpx\b|\brem\b", re.IGNORECASE)


def _is_plausible_author(text: str) -> bool:
    """Return True only if *text* looks like a real human author name.

    Rejects:
    - Empty or overly long strings (> 120 characters)
    - Text containing CSS-specific characters or keywords
    - All-uppercase strings longer than 3 words (headings / labels)
    """
    if not text or len(text) > 120:
        return False
    if _CSS_CHARS_RE.search(text):
        return False
    words = text.split()
    if len(words) > 3 and text == text.upper():
        return False
    return True


def clean_listing(listing: Dict[str, any]) -> Dict[str, any]:
    """Clean textual fields in a scraper listing dict in-place and return it.

    Fields cleaned: title, author_name, category_name, url, cover_image

    Additionally, if ``author_name`` fails the plausibility check after
    CSS removal it is replaced with ``"Unknown"`` so CSS class names never
    reach the database.
    """
    for key in ("title", "author_name", "category_name", "url", "cover_image"):
        if key in listing and isinstance(listing[key], str):
            listing[key] = remove_css(listing[key]).strip()

    # Guard: reject CSS-like strings that leaked through as author names
    if not _is_plausible_author(listing.get("author_name", "")):
        listing["author_name"] = "Unknown"

    return listing


def extract_triples_from_text(text: str) -> List[Dict[str, Optional[str]]]:
    """Convenience wrapper: removes CSS then extracts triples."""
    cleaned = remove_css(text)
    return extract_triples(cleaned)


def main(argv=sys.argv[1:]):
    if argv:
        path = argv[0]
        with open(path, 'r', encoding='utf8') as f:
            text = f.read()
    else:
        text = sys.stdin.read()

    cleaned = remove_css(text)
    triples = extract_triples(cleaned)

    # output as JSON lines
    for t in triples:
        print(json.dumps(t, ensure_ascii=False))


if __name__ == '__main__':
    main()
