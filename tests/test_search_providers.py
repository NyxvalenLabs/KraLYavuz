import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import requests

from kralyavuz.clone_checker.models import (
    CloneCheckStatus,
    CloneResult,
    RedirectResult,
    SearchResult,
)
from kralyavuz.clone_checker.providers import (
    GoogleSearchProvider,
    PlaywrightGoogleSearchFallback,
    SearchProviderError,
)
from kralyavuz.clone_checker.providers.search_provider import (
    resolve_google_result_url,
)
from kralyavuz.clone_checker.reporting import build_clipboard_report
from kralyavuz.clone_checker.service import CloneCheckerService
from kralyavuz.clone_checker.whitelist import WhitelistStore


class _Response:
    def __init__(self, text, url="https://www.google.com/search?q=atlasbet"):
        self.text = text
        self.url = url
        self.headers = {"Content-Type": "text/html; charset=UTF-8"}

    def raise_for_status(self):
        return None


class _Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class _FailingSession:
    def get(self, url, **kwargs):
        raise requests.ConnectionError("test bağlantı hatası")


class _BrowserFallback:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def search(self, keyword, limit):
        self.calls.append((keyword, limit))
        return self.items


class _FailingGoogleProvider:
    search_engine = "Google"

    def search(self, keyword, limit=10):
        raise SearchProviderError("Google geçici olarak kullanılamıyor.")


class _WorkingYandexProvider:
    search_engine = "Yandex"

    def search(self, keyword, limit=10):
        return [
            SearchResult(
                self.search_engine,
                keyword,
                1,
                "https://atlasbet1893.com",
                "Atlasbet giriş",
            )
        ]


class _RedirectProvider:
    def check(self, url):
        return RedirectResult(url, (), (url,), url)


class GoogleSearchProviderTests(unittest.TestCase):
    def test_default_service_registers_yandex_and_google(self):
        service = CloneCheckerService()
        self.assertEqual(
            [provider.search_engine for provider in service.providers],
            ["Yandex", "Google"],
        )

    def test_google_provider_parses_direct_and_tracking_results(self):
        html = """
        <html><body>
          <a href="/url?q=https%3A%2F%2Ftracked.example%2Fwelcome%3Fx%3D1&amp;sa=U">
            <h3>Tracked <span>Result</span></h3>
          </a>
          <a href="https://direct.example/path">
            <h3>Direct Result</h3>
          </a>
          <a href="/search?q=internal"><h3>Internal Google Link</h3></a>
        </body></html>
        """
        session = _Session(_Response(html))
        browser_fallback = _BrowserFallback([])

        results = GoogleSearchProvider(session, browser_fallback).search(
            "atlasbet giriş", limit=5
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(
            results[0],
            SearchResult(
                "Google",
                "atlasbet giriş",
                1,
                "https://tracked.example/welcome?x=1",
                "Tracked Result",
            ),
        )
        self.assertEqual(results[1].rank, 2)
        self.assertEqual(results[1].url, "https://direct.example/path")
        self.assertEqual(results[1].title, "Direct Result")
        self.assertEqual(session.calls[0][1]["params"]["q"], "atlasbet giriş")
        self.assertEqual(browser_fallback.calls, [])

    def test_google_empty_requests_uses_browser_fallback_and_filters_internal_links(self):
        session = _Session(_Response("<html><body>JS shell</body></html>"))
        browser_fallback = _BrowserFallback(
            [
                ("/search?q=internal", "Search"),
                ("https://www.google.com/preferences?hl=tr", "Preferences"),
                ("https://accounts.google.com/ServiceLogin", "Accounts"),
                (
                    "/url?q=https%3A%2F%2Ftracked.example%2Fentry&amp;sa=U",
                    "Tracked Result",
                ),
                ("https://direct.example/path", "Direct Result"),
            ]
        )

        results = GoogleSearchProvider(session, browser_fallback).search(
            "atlasbet giriş", limit=5
        )

        self.assertEqual(browser_fallback.calls, [("atlasbet giriş", 5)])
        self.assertEqual(
            [(item.rank, item.url, item.title) for item in results],
            [
                (1, "https://tracked.example/entry", "Tracked Result"),
                (2, "https://direct.example/path", "Direct Result"),
            ],
        )

    def test_google_request_failure_uses_browser_fallback(self):
        browser_fallback = _BrowserFallback(
            [("https://browser.example/result", "Browser Result")]
        )

        results = GoogleSearchProvider(_FailingSession(), browser_fallback).search(
            "atlasbet giriş", limit=5
        )

        self.assertEqual(browser_fallback.calls, [("atlasbet giriş", 5)])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].search_engine, "Google")
        self.assertEqual(results[0].keyword, "atlasbet giriş")
        self.assertEqual(results[0].rank, 1)
        self.assertEqual(results[0].url, "https://browser.example/result")
        self.assertEqual(results[0].title, "Browser Result")

    def test_playwright_fallback_launches_headless_and_reads_dom_results(self):
        with patch(
            "kralyavuz.clone_checker.providers.search_provider.sync_playwright"
        ) as playwright_factory, patch(
            "kralyavuz.clone_checker.providers.search_provider.find_opera_gx",
            return_value=Path("C:/Opera GX/opera.exe"),
        ):
            playwright = playwright_factory.return_value.__enter__.return_value
            browser = playwright.chromium.launch.return_value
            context = browser.new_context.return_value
            page = context.new_page.return_value
            page.locator.return_value.evaluate_all.return_value = [
                {"url": "https://browser.example/result", "title": "Result"}
            ]

            items = PlaywrightGoogleSearchFallback().search("atlasbet giriş", 5)

        playwright.chromium.launch.assert_called_once_with(
            headless=True,
            executable_path=str(Path("C:/Opera GX/opera.exe")),
        )
        page.goto.assert_called_once()
        self.assertIn("q=atlasbet+giri%C5%9F", page.goto.call_args.args[0])
        page.locator.assert_called_once_with("a:has(h3)")
        context.close.assert_called_once_with()
        browser.close.assert_called_once_with()
        self.assertEqual(
            items,
            [("https://browser.example/result", "Result")],
        )

    def test_google_tracking_url_supports_q_and_url_parameters(self):
        self.assertEqual(
            resolve_google_result_url(
                "/url?q=https%3A%2F%2Fexample.com%2Ftarget&amp;sa=U"
            ),
            "https://example.com/target",
        )
        self.assertEqual(
            resolve_google_result_url(
                "https://www.google.com/url?url=https%3A%2F%2Fother.example%2F"
            ),
            "https://other.example/",
        )
        self.assertEqual(resolve_google_result_url("/search?q=atlasbet"), "")

    def test_google_failure_does_not_discard_yandex_results(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(json.dumps({"domains": []}), encoding="utf-8")
            service = CloneCheckerService(
                providers=(_FailingGoogleProvider(), _WorkingYandexProvider()),
                redirect_provider=_RedirectProvider(),
                whitelist=WhitelistStore(config_path),
            )

            result = service.check("Atlasbet", "atlasbet.com")

        self.assertEqual(result.status, CloneCheckStatus.COMPLETED)
        self.assertEqual(len(result.results), len(service.query_suffixes))
        self.assertTrue(
            all(item.search_engine == "Yandex" for item in result.results)
        )

    def test_google_results_use_existing_report_format(self):
        result = SearchResult(
            "Google",
            "atlasbet giriş",
            1,
            "https://atlasbet1893.com/",
            "Atlasbet",
            clone_result=CloneResult(
                "atlasbet.com",
                "atlasbet1893.com",
                "Klon adayı",
                "Farklı domain",
            ),
        )

        report = build_clipboard_report((result,))

        self.assertEqual(
            report,
            "Google - Aratılan kelime: atlasbet giriş\n\n"
            "1. sıra\n"
            "   https://atlasbet1893.com/",
        )


if __name__ == "__main__":
    unittest.main()
