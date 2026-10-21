"""Offline matching check for Moviq. Run from the repo root:
    python matching_check.py
Uses the real parser/matching code with a tiny fake TMDB (no network, no key).
Edit CASES to add your own rows. 'expect' is what a correct app should do."""
import csv, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.getcwd())
from moviq.core.models import Item
from moviq.core.match_pipeline import match_movie_items
from moviq.core.matching import normalize_search_title
from moviq.core import file_ops
try:
    from moviq.core.naming import fold_diacritics
except ImportError:  # before the accent patch
    import unicodedata
    fold_diacritics = lambda x: "".join(c for c in unicodedata.normalize("NFD", x) if not unicodedata.combining(c))

DB = [  # fake movie database: (id, title, release_date, popularity)
    (1, "Aladdin", "1992-11-25", 60), (2, "Aladdin", "2019-05-22", 80),
    (3, "Dr. No", "1962-10-05", 40), (4, "From Russia with Love", "1963-10-10", 40),
    (5, "1917", "2019-12-25", 70), (6, "2020", "2012-01-01", 5),
    (7, "Test Movie Title", "", 1), (8, "Matchbox the Movie", "2026-01-01", 1),
    (9, "The SpongeBob Movie: Sponge Out of Water", "2015-02-05", 50),
    (10, "One Piece: The Movie", "2000-03-04", 20), (11, "Final Draw 2", "", 1),
    (12, "The Title Goes at the End", "2024-01-01", 2),
    (13, "The Weight of Steel Title", "2020-01-01", 2), (14, "Title ()", "2020-01-01", 1),
    (15, "Inception", "2010-07-16", 90),
    (16, "Am\u00e9lie", "2001-04-25", 70), (17, "2000 Movie", "2000-01-01", 1),
]

class FakeTMDB:
    def search(self, query, year=None):
        fold = lambda x: fold_diacritics(x.lower()).replace(":", " ")  # TMDB search ignores accents
        toks = {t for t in fold(query).split() if len(t) > 1}
        out = []
        for i, t, d, p in DB:
            if toks & set(fold(t).split()):
                out.append({"id": i, "title": t, "release_date": d, "popularity": p})
        return out

def run_movie(name):
    root = Path("/lib")
    item = Item(root / name, root, "movie")
    res = match_movie_items(FakeTMDB(), [item], 0.9, normalize_search_title(item.query))[0]
    status, choice = res[0], res[1]
    picked = "%s (%s)" % (choice["title"], choice["year"]) if choice else "-"
    return item.query, item.year, status, picked, (res[3] if len(res) > 3 else "")

MOVIES = [  # (file, expected status set, expected pick or None)
    ("at@title.mkv", {"nomatch", "review"}, None),
    ("Aladdin (2019).mkv", {"auto"}, "Aladdin (2019)"),
    ("007 (1963).mkv", {"nomatch", "review"}, None),
    ("1917.mkv", {"auto", "review"}, "1917 (2019)"),
    ("2020.mkv", {"review", "nomatch"}, None),
    ("2020 Movie Title.mkv", {"nomatch"}, None),
    ("final_final2.mkv", {"nomatch"}, None),
    ("Movie.mkv", {"nomatch"}, None), ("Movie__2020__1080p.mkv", {"nomatch"}, None),
    ("movie (1).mkv", {"nomatch"}, None), ("MOVIE TITLE.mkv", {"nomatch"}, None),
    ("Movie.Title.1080p.mkv", {"nomatch"}, None),
    ("Movie.Title.2020.1080p.mkv", {"nomatch"}, None),
    ("Movie.Title.2020.mkv", {"nomatch"}, None),
    ("Movie.Title.WEB-DL.mkv", {"nomatch"}, None), ("Movie.Title.x264.mkv", {"nomatch"}, None),
    ("Movie.Title.202.mkv", {"nomatch"}, None), ("Movie.Title.20O0.mkv", {"nomatch"}, None),
    ("The.Title.(2020).mkv", {"nomatch"}, None),
    ("Title-(2020)-[1080p]-{x265}.mkv", {"nomatch"}, None),
    ("Title[1080p](2020){WEB-DL}.mkv", {"nomatch"}, None),
    ("Sample.Movie.2022.UNRATED.EXTENDED.mkv", {"nomatch"}, None),
    ("Amelie.2001.1080p.mkv", {"auto"}, "Am\u00e9lie (2001)"),
    ("Inception.2O1O.mkv", {"auto"}, "Inception (2010)"),
]
TV = [  # (path, expected title, season, episode)
    ("Futurama - 3ABF01 - Amazon Women in the Mood.mkv", "Futurama", None, None),
    ("Law & Order SVU SOI EOI Payback.mkv", "Law & Order SVU", 1, 1),
    ("\u0645\u0633\u0644\u0633\u0644.S01E03.mkv", "\u0645\u0633\u0644\u0633\u0644", 1, 3),
    ("\u0926\u093f\u0932\u094d\u0932\u0940 S01E04.mkv", "\u0926\u093f\u0932\u094d\u0932\u0940", 1, 4),
    ("\u30a2\u30cb\u30e1.\u7b2c01\u8a71.mkv", "\u30a2\u30cb\u30e1", None, 1),
    ("\u30c9\u30e9\u30de S01E01.mkv", "\u30c9\u30e9\u30de", 1, 1),
    ("\uc2dc\ub9ac\uc988.S01E02.mkv", "\uc2dc\ub9ac\uc988", 1, 2),
    ("Specials (1991)/Example.Show.S00E01.mkv", None, 0, 1),
    ("[SubsPlease] Jujutsu Kaisen - 02 (1080p) [AIB301 B2].mkv", "Jujutsu Kaisen", None, 2),
    ("The.Simpsons.S01 E16.mkv", "The Simpsons", 1, 16),
]

def main():
    bad = 0
    print("== MOVIES ==")
    for name, ok_status, ok_pick in MOVIES:
        q, y, st, pick, why = run_movie(name)
        ok = st in ok_status and (ok_pick is None or pick == ok_pick)
        bad += not ok
        print("%-4s %-42s q=%-18r y=%-5s -> %-8s %-22s %s" % ("OK" if ok else "FAIL", name, q, y, st, pick, why))
    print("\n== TV (parse level) ==")
    for rel, title, season, ep in TV:
        root = Path("/lib")
        it = Item(root / rel, root, "tv")
        ok = (title is None or it.query == title) \
            and (season is None or it.season_number == season) and (ep is None or it.episode_number == ep or it.episode_number is None and it.episode_title)
        bad += not ok
        print("%-4s %-52s q=%-18r S=%s E=%s ep_title=%r code=%r tv=%s" % (
            "OK" if ok else "FAIL", rel, it.query, it.season_number, it.episode_number,
            it.episode_title, it.production_code, it.is_tv_episode))
    print("\n== FOLDER IMPORT (25 tiny 'Sample.Movie...' files) ==")
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d) / "06_Resolution_Codec_Release_Tags"; folder.mkdir()
        for i in range(25):
            (folder / ("Sample.Movie.2022.V%02d.mkv" % i)).write_bytes(b"x" * 1024)
        n = len(file_ops.scan_dir(folder))
        ok = n == 25; bad += not ok
        print("%-4s scan_dir found %d of 25" % ("OK" if ok else "FAIL", n))
        (folder / "movie-sample.mkv").write_bytes(b"x" * 1024)
        (folder / "sample.mkv").write_bytes(b"x" * 1024)
        n2 = len(file_ops.scan_dir(folder))
        ok = n2 == 25; bad += not ok
        print("%-4s real samples (movie-sample.mkv, sample.mkv) still skipped: %d files" % ("OK" if ok else "FAIL", n2))
    print("\n%d problem(s)" % bad)
if __name__ == "__main__":
    main()
