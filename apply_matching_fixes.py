"""Apply the Moviq matching fixes by text replacement (no line numbers, keeps CRLF/LF).
Run from the repo root:  python apply_matching_fixes.py
Safe to run twice. Prints APPLIED / ALREADY / NOT FOUND for each edit."""
import io, sys
from pathlib import Path

EDITS = [
 ("moviq/core/naming.py", "accent folding + accent-insensitive similarity",
  'def similarity(a, b):\n    return SequenceMatcher(None, norm(a), norm(b)).ratio()',
  'def fold_diacritics(value):\n    """Drop accents from Latin letters only (Amelie == Amélie); keep kana/hangul marks."""\n    out = []\n    for char in unicodedata.normalize("NFC", value or ""):\n        parts = unicodedata.normalize("NFD", char)\n        if (\n            len(parts) > 1 and ord(parts[0]) < 0x250\n            and all(unicodedata.combining(mark) for mark in parts[1:])\n        ):\n            out.append(parts[0])\n        else:\n            out.append(char)\n    return "".join(out)\n\n\ndef similarity(a, b):\n    return SequenceMatcher(\n        None, fold_diacritics(norm(a)), fold_diacritics(norm(b)),\n    ).ratio()'),
 ("moviq/core/naming.py", "year typo repair (20O0 -> 2000)",
  'def parse_name(stem):\n    """\'The.Dark.Knight.2008.1080p.BluRay\' -> (\'The Dark Knight\', 2008)"""\n    stem = FILE_NUMBER_RE.sub("", stem)',
  '_OCR_YEAR_RE = re.compile(r"(?<![A-Za-z0-9])([12][09OoIl][0-9OoIl]{2})(?![A-Za-z0-9])")\n\n\ndef _fix_ocr_year(text):\n    """Repair years typed with letters: 20O0 -> 2000 (only when it becomes 19xx/20xx)."""\n    table = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1"})\n\n    def repair(match):\n        token = match.group(1)\n        if token.isdigit():\n            return token\n        fixed = token.translate(table)\n        return fixed if re.fullmatch(r"(?:19|20)\\d{2}", fixed) else token\n\n    return _OCR_YEAR_RE.sub(repair, text)\n\n\ndef parse_name(stem):\n    """\'The.Dark.Knight.2008.1080p.BluRay\' -> (\'The Dark Knight\', 2008)"""\n    stem = _fix_ocr_year(FILE_NUMBER_RE.sub("", stem))'),
 ("moviq/core/matching.py", "import fold_diacritics",
  'from .naming import similarity, tv_episode_name_for',
  'from .naming import fold_diacritics, similarity, tv_episode_name_for'),
 ("moviq/core/matching.py", "accent-insensitive match title",
  '    value = unicodedata.normalize("NFKC", value).casefold()\n    value = value.replace("&", " and ")',
  '    value = fold_diacritics(unicodedata.normalize("NFKC", value).casefold())\n    value = value.replace("&", " and ")'),
 ("moviq/core/show_resolver.py", "import fold_diacritics",
  'from .tmdb import TMDB, TMDBError',
  'from .naming import fold_diacritics\nfrom .tmdb import TMDB, TMDBError'),
 ("moviq/core/show_resolver.py", "accent-insensitive TV similarity",
  '        None, normalize_show_title(left), normalize_show_title(right),\n    ).ratio()',
  '        None,\n        fold_diacritics(normalize_show_title(left)),\n        fold_diacritics(normalize_show_title(right)),\n    ).ratio()'),
 ("moviq/core/file_ops.py", "sample rule: only 'sample'/'trailer' as the last word",
  '    if not _SAMPLE_WORD_RE.search(p.name.lower()):\n        return False',
  '    words = [w for w in re.split(r"[\\s._\\-\\[\\]()]+", p.stem.lower()) if w]\n    if not words or words[-1] not in ("sample", "trailer"):\n        return False'),
]
def main():
    failed = 0
    for path, label, old, new in EDITS:
        p = Path(path)
        if not p.exists():
            print("NOT FOUND (file)  %-26s %s" % (path, label)); failed += 1; continue
        raw = p.read_bytes().decode("utf-8")
        crlf = "\r\n" in raw
        text = raw.replace("\r\n", "\n")
        if new in text:
            print("ALREADY           %-26s %s" % (path, label)); continue
        if text.count(old) != 1:
            print("NOT FOUND (code)  %-26s %s" % (path, label)); failed += 1; continue
        text = text.replace(old, new, 1)
        if crlf:
            text = text.replace("\n", "\r\n")
        p.write_bytes(text.encode("utf-8"))
        print("APPLIED           %-26s %s" % (path, label))
    if failed:
        print("\n%d edit(s) not applied. Send me those files (or `git log --oneline -3`) and I'll adapt them." % failed)
        sys.exit(1)
    print("\nDone. Next: python matching_check.py")

if __name__ == "__main__":
    main()
