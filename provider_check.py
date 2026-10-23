"""Live smoke test for every Moviq provider. Run from the repo root:

    python provider_check.py                      # keyless providers only
    set TMDB_KEY=... & set OMDB_KEY=... & set TVDB_KEY=... & set TVDB_PIN=...
    python provider_check.py                      # (PowerShell: $env:OMDB_KEY="...")

Prints PASS/FAIL per check so you can see what works with your keys.
"""
import os
import sys

sys.path.insert(0, os.getcwd())
from moviq.core.providers.router import ProviderRouter          # noqa: E402
from moviq.core.providers.settings import ProviderSettings      # noqa: E402
from moviq.core.providers import ids                            # noqa: E402

KEYS = {
    "omdb": os.environ.get("OMDB_KEY", ""),
    "tvdb": os.environ.get("TVDB_KEY", ""),
    "tvdb_pin": os.environ.get("TVDB_PIN", ""),
}
TMDB_KEY = os.environ.get("TMDB_KEY", "")
ORDER = ("tmdb", "tvmaze", "anilist", "omdb", "tvdb")
NEEDS = {"tmdb": TMDB_KEY, "omdb": KEYS["omdb"], "tvdb": KEYS["tvdb"]}
CASES = {
    "movie": ("Inception", 2010, "search"),
    "tv": ("Breaking Bad", 2008, "search_tv"),
    "anime": ("Jujutsu Kaisen", 2020, "search_tv"),
}


def run(name):
    settings = ProviderSettings.from_config({
        "providers": {"order": [name], "enabled": {name: True, **{
            n: False for n in ORDER if n != name}}},
        "provider_keys": KEYS,
    })
    router = ProviderRouter(settings, TMDB_KEY)
    provider = router._provider(name)
    if name in NEEDS and not NEEDS[name]:
        print(f"{name:8} SKIP  (no key set)")
        return
    for label, (query, year, method) in CASES.items():
        if label == "movie" and "movie" not in provider.kinds:
            continue
        if label != "movie" and "tv" not in provider.kinds:
            continue
        if label == "anime" and name not in ("anilist", "tvdb", "tmdb"):
            continue
        try:
            results = getattr(provider, method)(query, year)
            ok = bool(results)
            top = results[0] if results else {}
            title = top.get("title") or top.get("name")
            line = f"{name:8} {label:6} {'PASS' if ok else 'FAIL'}  {title!r} {top.get('id')}"
            print(line)
            if ok and label in ("tv", "anime"):
                details = provider.tv(top["id"]) or {}
                seasons = details.get("seasons") or []
                season = provider.season(top["id"], 1) or {}
                eps = season.get("episodes") or []
                first = eps[1]["name"] if len(eps) > 1 else None
                print(f"         details: seasons={len(seasons)} S01 episodes={len(eps)} ep2={first!r}")
        except Exception as error:   # show the provider's own message
            print(f"{name:8} {label:6} FAIL  {type(error).__name__}: {error}")


if __name__ == "__main__":
    print("Moviq provider check (live network)\n")
    for provider_name in ORDER:
        run(provider_name)
    print("\nIds are namespaced, e.g. tvdb 81189 ->", ids.encode("tvdb", 81189))
