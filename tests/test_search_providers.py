import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import requests
from playwright.sync_api import Error as PlaywrightError

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
    YandexSearchProvider,
)
from kralyavuz.clone_checker.providers.search_provider import (
    clone_edge_profile,
    google_browser_profile_dirs,
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
    def test_default_service_registers_only_yandex(self):
        service = CloneCheckerService()
        self.assertEqual(
            [type(provider) for provider in service.providers],
            [YandexSearchProvider],
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

    def test_playwright_fallback_launches_visible_and_reads_dom_results(self):
        edge_profile = Path("C:/LocalAppData/Microsoft/Edge/User Data")
        with patch(
            "kralyavuz.clone_checker.providers.search_provider.sync_playwright"
        ) as playwright_factory:
            playwright = playwright_factory.return_value.__enter__.return_value
            context = playwright.chromium.launch_persistent_context.return_value
            page = context.new_page.return_value
            page.url = "https://www.google.com/search?q=atlasbet"
            page.title.return_value = "Google"
            page.locator.return_value.inner_text.return_value = "Google results"
            page.locator.return_value.evaluate_all.return_value = [
                {"url": "https://browser.example/result", "title": "Result"}
            ]

            fallback = PlaywrightGoogleSearchFallback((("msedge", edge_profile),))
            with self.assertLogs(
                "kralyavuz.clone_checker.providers.search_provider",
                level="WARNING",
            ) as captured_logs:
                items = fallback.search("atlasbet giriş", 5)

        playwright.chromium.launch_persistent_context.assert_called_once_with(
            user_data_dir=str(edge_profile),
            channel="msedge",
            headless=False,
            locale="tr-TR",
            timeout=10_000,
        )
        self.assertEqual(page.goto.call_count, 2)
        self.assertEqual(page.goto.call_args_list[0].args[0], "https://www.google.com")
        self.assertIn(
            "q=atlasbet+giri%C5%9F", page.goto.call_args_list[1].args[0]
        )
        page.wait_for_timeout.assert_called_once_with(2_000)
        page.locator.assert_any_call("a:has(h3)")
        context.close.assert_called_once_with()
        self.assertEqual(fallback.selected_channel, "msedge")
        self.assertEqual(fallback.selected_profile_dir, edge_profile)
        logs = "\n".join(captured_logs.output)
        self.assertIn("browser channel: msedge", logs)
        self.assertIn(f"user_data_dir: {edge_profile}", logs)
        self.assertIn("profile türü: mevcut Edge kullanıcı profili", logs)
        self.assertIn("ana sayfa page.title(): Google", logs)
        self.assertIn("arama page.url:", logs)
        self.assertEqual(
            items,
            [("https://browser.example/result", "Result")],
        )

    def test_playwright_captcha_uses_clean_google_message(self):
        profile = Path("C:/Profiles/Edge")
        with patch(
            "kralyavuz.clone_checker.providers.search_provider.sync_playwright"
        ) as playwright_factory:
            playwright = playwright_factory.return_value.__enter__.return_value
            context = playwright.chromium.launch_persistent_context.return_value
            page = context.new_page.return_value
            page.url = "https://www.google.com/sorry/index"
            page.title.return_value = "Google"
            fallback = PlaywrightGoogleSearchFallback((("msedge", profile),))

            with self.assertRaisesRegex(
                SearchProviderError,
                r"^Google CAPTCHA doğrulaması istedi\.$",
            ):
                fallback.search("atlasbet giriş", 5)

        context.close.assert_called_once_with()

    def test_locked_system_profile_retries_dedicated_profile_on_same_browser(self):
        profiles = (
            ("msedge", Path("C:/Profiles/Edge/User Data")),
            ("msedge", Path("C:/KraLYavuz/Profiles/Edge")),
            ("chrome", Path("C:/Profiles/Chrome/User Data")),
        )
        with patch(
            "kralyavuz.clone_checker.providers.search_provider.sync_playwright"
        ) as playwright_factory:
            playwright = playwright_factory.return_value.__enter__.return_value
            context = MagicMock()
            page = context.new_page.return_value
            page.url = "https://www.google.com/search?q=atlasbet"
            page.locator.return_value.inner_text.return_value = "Google results"
            page.locator.return_value.evaluate_all.return_value = []
            playwright.chromium.launch_persistent_context.side_effect = [
                PlaywrightError("Profil kilitli"),
                context,
            ]
            fallback = PlaywrightGoogleSearchFallback(profiles)

            with self.assertLogs(
                "kralyavuz.clone_checker.providers.search_provider",
                level="WARNING",
            ) as captured_logs:
                fallback.search("atlasbet giriş", 5)

        self.assertEqual(
            [
                (
                    browser_call.kwargs["channel"],
                    browser_call.kwargs["user_data_dir"],
                )
                for browser_call in playwright.chromium.launch_persistent_context.call_args_list
            ],
            [
                ("msedge", str(profiles[0][1])),
                ("msedge", str(profiles[1][1])),
            ],
        )
        self.assertEqual(fallback.selected_channel, "msedge")
        self.assertEqual(fallback.selected_profile_dir, profiles[1][1])
        logs = "\n".join(captured_logs.output)
        first_attempt = (
            f"profil deneniyor: channel=msedge, user_data_dir={profiles[0][1]}"
        )
        failure = (
            f"profil açılamadı: channel=msedge, user_data_dir={profiles[0][1]}, "
            "exception=Profil kilitli"
        )
        selected_fallback = f"kullanılan user_data_dir: {profiles[1][1]}"
        self.assertIn(first_attempt, logs)
        self.assertIn(failure, logs)
        self.assertIn(selected_fallback, logs)
        self.assertLess(logs.index(failure), logs.index(selected_fallback))

    def test_playwright_fallback_uses_system_priority_without_opera(self):
        profiles = (
            ("msedge", Path("C:/Profiles/Edge")),
            ("chrome", Path("C:/Profiles/Chrome")),
            ("chromium", Path("C:/Profiles/Chromium")),
        )
        with patch(
            "kralyavuz.clone_checker.providers.search_provider.sync_playwright"
        ) as playwright_factory:
            playwright = playwright_factory.return_value.__enter__.return_value
            context = MagicMock()
            page = context.new_page.return_value
            page.url = "https://www.google.com/search?q=atlasbet"
            page.locator.return_value.inner_text.return_value = "Google results"
            page.locator.return_value.evaluate_all.return_value = []
            playwright.chromium.launch_persistent_context.side_effect = [
                PlaywrightError("Edge kurulu değil"),
                context,
            ]

            PlaywrightGoogleSearchFallback(profiles).search("atlasbet giriş", 5)

        self.assertEqual(
            playwright.chromium.launch_persistent_context.call_args_list,
            [
                call(
                    user_data_dir=str(profiles[0][1]),
                    channel="msedge",
                    headless=False,
                    locale="tr-TR",
                    timeout=10_000,
                ),
                call(
                    user_data_dir=str(profiles[1][1]),
                    channel="chrome",
                    headless=False,
                    locale="tr-TR",
                    timeout=10_000,
                ),
            ],
        )
        self.assertTrue(
            all(
                "executable_path" not in browser_call.kwargs
                for browser_call in playwright.chromium.launch_persistent_context.call_args_list
            )
        )
        self.assertNotIn(
            "opera",
            str(
                playwright.chromium.launch_persistent_context.call_args_list
            ).casefold(),
        )

    def test_playwright_fallback_reaches_chromium_when_edge_and_chrome_fail(self):
        profiles = (
            ("msedge", Path("C:/Profiles/Edge")),
            ("chrome", Path("C:/Profiles/Chrome")),
            ("chromium", Path("C:/Profiles/Chromium")),
        )
        with patch(
            "kralyavuz.clone_checker.providers.search_provider.sync_playwright"
        ) as playwright_factory:
            playwright = playwright_factory.return_value.__enter__.return_value
            context = MagicMock()
            page = context.new_page.return_value
            page.url = "https://www.google.com/search?q=atlasbet"
            page.locator.return_value.inner_text.return_value = "Google results"
            page.locator.return_value.evaluate_all.return_value = []
            playwright.chromium.launch_persistent_context.side_effect = [
                PlaywrightError("Edge kurulu değil"),
                PlaywrightError("Chrome kurulu değil"),
                context,
            ]

            PlaywrightGoogleSearchFallback(profiles).search("atlasbet giriş", 5)

        self.assertEqual(
            [
                browser_call.kwargs["channel"]
                for browser_call in playwright.chromium.launch_persistent_context.call_args_list
            ],
            ["msedge", "chrome", "chromium"],
        )

    def test_existing_edge_and_chrome_profiles_are_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_app_data = root / "LocalAppData"
            edge_profile = local_app_data / "Microsoft" / "Edge" / "User Data"
            chrome_profile = local_app_data / "Google" / "Chrome" / "User Data"
            (edge_profile / "Default").mkdir(parents=True)
            (chrome_profile / "Default").mkdir(parents=True)
            (edge_profile / "Local State").write_text("{}", encoding="utf-8")
            (chrome_profile / "Local State").write_text("{}", encoding="utf-8")
            config_dir = root / "config"

            profiles = google_browser_profile_dirs(
                system_name="Windows",
                environ={"LOCALAPPDATA": str(local_app_data)},
                home=root,
                config_dir=config_dir,
            )

        self.assertEqual(
            profiles,
            (
                ("msedge", edge_profile),
                ("msedge", config_dir / "browser_profiles" / "msedge"),
                ("chrome", chrome_profile),
                ("chrome", config_dir / "browser_profiles" / "chrome"),
                (
                    "chromium",
                    config_dir / "browser_profiles" / "chromium",
                ),
            ),
        )
        self.assertEqual(profiles[0], ("msedge", edge_profile))

    def test_edge_user_data_is_safely_cloned_without_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Edge" / "User Data"
            target = root / "KraLYavuz" / "browser_profiles" / "msedge"
            (source / "Default").mkdir(parents=True)
            (source / "Local State").write_text(
                '{"source": true}', encoding="utf-8"
            )
            (source / "Default" / "History").write_text(
                "history", encoding="utf-8"
            )
            for cache_dir in ("Cache", "GPUCache", "Code Cache", "Crashpad"):
                cache_path = source / "Default" / cache_dir
                cache_path.mkdir(parents=True)
                (cache_path / "ignored.bin").write_bytes(b"cache")
            (target / "Default").mkdir(parents=True)
            (target / "Default" / "target-only.txt").write_text(
                "preserve", encoding="utf-8"
            )

            copied_count, errors = clone_edge_profile(source, target)

            self.assertGreaterEqual(copied_count, 2)
            self.assertEqual(errors, ())
            self.assertEqual(
                (target / "Local State").read_text(encoding="utf-8"),
                '{"source": true}',
            )
            self.assertEqual(
                (target / "Default" / "History").read_text(encoding="utf-8"),
                "history",
            )
            self.assertEqual(
                (target / "Default" / "target-only.txt").read_text(
                    encoding="utf-8"
                ),
                "preserve",
            )
            self.assertFalse((target / "Default" / "Cache").exists())
            self.assertFalse((target / "Default" / "GPUCache").exists())
            self.assertFalse((target / "Default" / "Code Cache").exists())
            self.assertFalse((target / "Default" / "Crashpad").exists())

    def test_google_fallback_clones_then_launches_edge_target_profile(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "kralyavuz.clone_checker.providers.search_provider.sync_playwright"
        ) as playwright_factory:
            root = Path(directory)
            source = root / "Microsoft" / "Edge" / "User Data"
            target = root / ".kralyavuz" / "browser_profiles" / "msedge"
            (source / "Default").mkdir(parents=True)
            (source / "Local State").write_text("{}", encoding="utf-8")
            (source / "Default" / "Preferences").write_text(
                '{"profile": "edge"}', encoding="utf-8"
            )
            playwright = playwright_factory.return_value.__enter__.return_value
            context = playwright.chromium.launch_persistent_context.return_value
            page = context.new_page.return_value
            page.url = "https://www.google.com/search?q=atlasbet"
            page.title.return_value = "Google"
            page.locator.return_value.inner_text.return_value = "Google results"
            page.locator.return_value.evaluate_all.return_value = []
            fallback = PlaywrightGoogleSearchFallback(
                (("msedge", target),),
                edge_clone_paths=(source, target),
            )

            with self.assertLogs(
                "kralyavuz.clone_checker.providers.search_provider",
                level="WARNING",
            ) as captured_logs:
                fallback.search("atlasbet giriş", 5)

            self.assertEqual(
                (target / "Default" / "Preferences").read_text(
                    encoding="utf-8"
                ),
                '{"profile": "edge"}',
            )

        playwright.chromium.launch_persistent_context.assert_called_once_with(
            user_data_dir=str(target),
            channel="msedge",
            headless=False,
            locale="tr-TR",
            timeout=10_000,
        )
        logs = "\n".join(captured_logs.output)
        self.assertIn(f"clone kaynak profil: {source}", logs)
        self.assertIn(f"clone hedef profil: {target}", logs)
        self.assertIn("profil kopyalanan zaman:", logs)
        self.assertIn(f"kullanılan user_data_dir: {target}", logs)

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
