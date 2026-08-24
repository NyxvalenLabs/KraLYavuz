import tempfile
import unittest
from pathlib import Path

from kralyavuz.clone_checker.models import (
    CloneCheckStatus,
    RedirectResult,
    SearchResult,
)
from kralyavuz.clone_checker.providers.google_api_provider import (
    GoogleApiSearchProvider,
)
from kralyavuz.clone_checker.providers.search_provider import (
    GoogleSearchProvider,
    YandexSearchProvider,
)
from kralyavuz.clone_checker.service import CloneCheckerService
from kralyavuz.clone_checker.whitelist import WhitelistStore


class _SearchProvider:
    def __init__(self, search_engine, domain):
        self.search_engine = search_engine
        self.domain = domain
        self.calls = []

    def search(self, keyword, limit=10):
        self.calls.append((keyword, limit))
        return [
            SearchResult(
                self.search_engine,
                keyword,
                1,
                f"https://{self.domain}",
                "Atlasbet giriş",
            )
        ]


class _RedirectProvider:
    def check(self, url):
        return RedirectResult(url, (), (url,), url)


class DisabledGoogleProviderTests(unittest.TestCase):
    def test_default_service_uses_only_yandex(self):
        service = CloneCheckerService()

        self.assertEqual(len(service.providers), 1)
        self.assertIsInstance(service.providers[0], YandexSearchProvider)

    def test_google_provider_classes_remain_importable(self):
        self.assertEqual(
            GoogleApiSearchProvider.__name__, "GoogleApiSearchProvider"
        )
        self.assertEqual(GoogleSearchProvider.__name__, "GoogleSearchProvider")

    def test_custom_provider_injection_remains_active(self):
        with tempfile.TemporaryDirectory() as directory:
            google = _SearchProvider("Google", "atlasbet1893.com")
            yandex = _SearchProvider("Yandex", "atlasbet777.com")
            service = CloneCheckerService(
                providers=(google, yandex),
                redirect_provider=_RedirectProvider(),
                whitelist=WhitelistStore(Path(directory) / "config.json"),
            )

            result = service.check("Atlasbet", "atlasbet.com")

        expected_calls = [
            (keyword, 10) for keyword in service.keywords_for("Atlasbet")
        ]
        self.assertEqual(google.calls, expected_calls)
        self.assertEqual(yandex.calls, expected_calls)
        self.assertEqual(result.status, CloneCheckStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
