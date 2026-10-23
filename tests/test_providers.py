import unittest
from unittest.mock import patch

from moviq.core.providers import ids
from moviq.core.providers.anilist import AniListProvider
from moviq.core.providers.offline import OfflineProvider
from moviq.core.providers.router import ProviderRouter
from moviq.core.providers.settings import ProviderSettings
from moviq.core.tmdb import TMDBAuthError, TMDBError


_CACHE_PATCH = patch("moviq.core.providers.base.get_cache", return_value=None)


def setUpModule():
    _CACHE_PATCH.start()   # never read or write the real response cache


def tearDownModule():
    _CACHE_PATCH.stop()


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.headers = data, status, {}

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def request(self, method, url, params=None, headers=None, json=None,
                timeout=None):
        self.calls.append((method, url, params, json))
        return self.handler(method, url, params or {}, headers or {}, json)


def anilist_handler(method, url, params, headers, body):
    if "Page(" in body["query"]:
        return Resp({"data": {"Page": {"media": [{
            "id": 113415,
            "title": {"romaji": "Jujutsu Kaisen", "english": "JUJUTSU KAISEN",
                      "native": "\u546a\u8853\u5ec3\u6226"},
            "startDate": {"year": 2020, "month": 10, "day": 3},
            "format": "TV", "episodes": 24, "popularity": 400000,
            "genres": ["Action"], "coverImage": {"large": "https://c/x.jpg"},
            "description": "A boy...",
        }]}}})
    return Resp({"data": {"Media": {
        "id": 113415,
        "title": {"romaji": "Jujutsu Kaisen", "english": "JUJUTSU KAISEN",
                  "native": "x"},
        "startDate": {"year": 2020, "month": 10, "day": 3}, "episodes": 24,
        "streamingEpisodes": [{"title": "Episode 2 - Fearsome Womb"}],
        "genres": [], "popularity": 1,
    }}})


class IdTests(unittest.TestCase):
    def test_round_trip_and_tmdb_unchanged(self):
        self.assertEqual(ids.encode("tmdb", 603), 603)
        self.assertEqual(ids.decode(603), ("tmdb", 603))
        self.assertEqual(
            ids.external_tag(ids.encode("omdb", 111161)), ("imdb", "tt0111161"),
        )
        self.assertIsNone(ids.external_tag(ids.encode("tvmaze", 169)))
        self.assertEqual(ids.decode(ids.encode("anilist", 7)), ("anilist", 7))
        self.assertEqual(ids.decode(ids.encode("offline", 7)), ("offline", 7))

    def test_typed_ids(self):
        self.assertEqual(ids.parse_typed_id("imdb:tt0111161"), ids.encode("omdb", 111161))
        self.assertIsNone(ids.parse_typed_id("tvdb: 81189"))
        self.assertEqual(ids.parse_typed_id("tmdb:603"), 603)
        self.assertIsNone(ids.parse_typed_id("603"))


class AniListTests(unittest.TestCase):
    def test_titles_and_episodes(self):
        provider = AniListProvider(session=FakeSession(anilist_handler))
        result = provider.search_tv("jujutsu kaisen", 2020)[0]
        self.assertEqual(result["name"], "JUJUTSU KAISEN")
        self.assertEqual(result["original_name"], "Jujutsu Kaisen")
        self.assertEqual(result["first_air_date"], "2020-10-03")
        details = provider.tv(113415)
        self.assertEqual(details["seasons"][0]["episode_count"], 24)
        episodes = provider.season(113415, 1)["episodes"]
        self.assertEqual(len(episodes), 24)
        self.assertEqual(episodes[1]["name"], "Fearsome Womb")
        self.assertEqual(episodes[0]["name"], "Episode 1")
        self.assertIsNone(provider.season(113415, 2))
        self.assertEqual(provider.absolute_episode(113415, 5)["episode_number"], 5)
        self.assertIsNone(provider.absolute_episode(113415, 99))


class Stub:
    """A provider double for router tests."""

    def __init__(self, name, results=None, error=None, kinds=("movie", "tv"),
                 needs_key=False, key="x"):
        self.provider_id, self.kinds = name, kinds
        self.needs_key, self.key = needs_key, key
        self._results, self._error = results or [], error
        self.calls = 0

    def available(self):
        return bool(self.key) if self.needs_key else True

    def _go(self):
        self.calls += 1
        if self._error:
            raise self._error
        return list(self._results)

    search = search_tv = search_tv_strict = lambda self, q, y=None: self._go()


def router_with(order, stubs, enabled=None):
    settings = ProviderSettings(
        order=tuple(order) + tuple(
            n for n in ("tmdb", "tvmaze", "anilist", "omdb", "offline")
            if n not in order),
        enabled=tuple(enabled if enabled is not None else order),
    )
    router = ProviderRouter(settings, tmdb_key="k")
    router._built.update(stubs)
    return router


GOOD = [{"id": 1, "title": "Inception", "release_date": "2010-01-01"}]
OTHER = [{"id": 2, "title": "Something Else Entirely", "release_date": "1999-01-01"}]


class RouterTests(unittest.TestCase):
    def test_first_acceptable_provider_wins_and_ids_are_tagged(self):
        a, b = Stub("tmdb", GOOD), Stub("omdb", GOOD, needs_key=True)
        router = router_with(["tmdb", "omdb"], {"tmdb": a, "omdb": b})
        result = router.search("Inception", 2010)
        self.assertEqual(result[0]["id"], 1)
        self.assertEqual(result[0]["provider"], "tmdb")
        self.assertEqual(b.calls, 0)

    def test_falls_through_when_results_do_not_match_the_title(self):
        a, b = Stub("tmdb", OTHER), Stub("omdb", GOOD, needs_key=True)
        router = router_with(["tmdb", "omdb"], {"tmdb": a, "omdb": b})
        result = router.search("Inception", 2010)
        self.assertEqual(result[0]["provider"], "omdb")
        self.assertEqual(result[0]["id"], ids.encode("omdb", 1))

    def test_weak_results_are_kept_when_nothing_better_exists(self):
        a, b = Stub("tmdb", OTHER), Stub("omdb", [], needs_key=True)
        router = router_with(["tmdb", "omdb"], {"tmdb": a, "omdb": b})
        self.assertEqual(router.search("Inception")[0]["provider"], "tmdb")

    def test_auth_failure_skips_provider_for_the_session(self):
        a = Stub("tmdb", error=TMDBAuthError("bad key"))
        b = Stub("tvmaze", GOOD, kinds=("tv",))
        router = router_with(["tmdb", "tvmaze"], {"tmdb": a, "tvmaze": b})
        self.assertEqual(router.search_tv("Inception")[0]["provider"], "tvmaze")
        router.search_tv("Inception")
        self.assertEqual(a.calls, 1)
        self.assertIn("bad key", router.notices[0])

    def test_error_is_raised_only_when_nobody_found_anything(self):
        a = Stub("tmdb", error=TMDBError("down"))
        b = Stub("tvmaze", [], kinds=("tv",))
        router = router_with(["tmdb", "tvmaze"], {"tmdb": a, "tvmaze": b})
        with self.assertRaises(TMDBError):
            router.search_tv("Inception")

    def test_missing_keys_are_skipped_and_offline_engages_as_last_resort(self):
        tmdb = Stub("tmdb", GOOD, needs_key=True, key="")
        router = router_with(["tmdb"], {"tmdb": tmdb}, enabled=["tmdb"])
        result = router.search("the matrix", 1999)
        self.assertEqual(result[0]["provider"], "offline")
        self.assertEqual(result[0]["title"], "The Matrix")
        self.assertEqual(tmdb.calls, 0)

    def test_offline_is_not_used_when_a_general_provider_is_available(self):
        tmdb = Stub("tmdb", [], needs_key=True)
        router = router_with(["tmdb"], {"tmdb": tmdb}, enabled=["tmdb"])
        self.assertEqual(router.search("Nothing Here"), [])

    def test_details_are_routed_by_id(self):
        class Details(Stub):
            def tv(self, native):
                return {"id": native, "name": "Show"}
        tvmaze = Details("tvmaze", kinds=("tv",))
        router = router_with(["tvmaze"], {"tvmaze": tvmaze})
        details = router.tv(ids.encode("tvmaze", 169))
        self.assertEqual(details["id"], ids.encode("tvmaze", 169))

    def test_no_providers_gives_a_clear_error(self):
        tmdb = Stub("tmdb", needs_key=True, key="")
        router = router_with(["tmdb"], {"tmdb": tmdb}, enabled=["tmdb"])
        router.settings = ProviderSettings(order=router.settings.order, enabled=("tmdb",))
        router._failed["offline"] = TMDBError("x")
        with self.assertRaises(TMDBAuthError):
            router.search("anything")


class OfflineTests(unittest.TestCase):
    def test_stable_ids_and_episodes(self):
        provider = OfflineProvider()
        first = provider.search_tv("the office", 2005)[0]
        again = provider.search_tv("the office", 2005)[0]
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(first["name"], "The Office")
        self.assertEqual(provider.season(first["id"], 2)["episodes"][4]["name"], "Episode 5")


class SettingsTests(unittest.TestCase):
    def test_round_trip_and_cleaning(self):
        settings = ProviderSettings.from_config({
            "providers": {"order": ["tvdb", "bogus", "tmdb"],
                          "enabled": {"tvdb": False, "tmdb": True}},
            "provider_keys": {"omdb": " abc ", "tvdb": "k", "tvdb_pin": ""},
        })
        self.assertEqual(settings.order[0], "tmdb")
        self.assertEqual(len(settings.order), 5)
        self.assertNotIn("tvdb", settings.order)
        self.assertNotIn("tvdb", settings.enabled)
        self.assertEqual(settings.key("omdb"), "abc")
        saved = settings.to_config()
        self.assertNotIn("tvdb", saved["providers"]["order"])
        self.assertNotIn("tvdb", saved["providers"]["enabled"])
        self.assertNotIn("tvdb", saved["provider_keys"])
        self.assertNotIn("tvdb_pin", saved["provider_keys"])
        again = ProviderSettings.from_config(settings.to_config())
        self.assertEqual(again, settings)

    def test_defaults(self):
        settings = ProviderSettings.from_config({})
        self.assertEqual(settings.order[0], "tmdb")
        self.assertNotIn("offline", settings.enabled)


if __name__ == "__main__":
    unittest.main()
