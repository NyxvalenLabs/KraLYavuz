import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from kralyavuz.clone_checker.models import (
    CloneCheckResult,
    CloneCheckStatus,
    CloneResult,
    RedirectResult,
    SearchResult,
)
from kralyavuz.clone_checker.service import RISK_STATUSES, CloneCheckerService
from kralyavuz.clone_checker.ui import CloneCheckerPanel
from kralyavuz.clone_checker.whitelist import WhitelistStore
from kralyavuz.clone_checker.reporting import build_clipboard_report, report_result_count


class _SearchProvider:
    search_engine = "Yandex"

    def search(self, keyword, limit=10):
        return [
            SearchResult("Yandex", keyword, 1, "https://atlasbet.com", "Atlasbet"),
            SearchResult(
                "Yandex", keyword, 2, "https://facebook.com/atlasbet", "Atlasbet"
            ),
            SearchResult(
                "Yandex", keyword, 3, "https://safe-clone.com", "Atlasbet"
            ),
            SearchResult(
                "Yandex", keyword, 4, "https://atlasbet1893.com", "Atlasbet"
            ),
        ]


class _RedirectProvider:
    def check(self, url):
        return RedirectResult(url, (), (url,), url)


class CloneCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_whitelist_is_persistent_and_preserves_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps({"domains": ["keep.example"], "custom": True}),
                encoding="utf-8",
            )
            store = WhitelistStore(path)
            store.add_domain("https://www.example.com/path")

            entry = store.entries()[0]
            self.assertEqual(entry.domain, "example.com")
            self.assertTrue(entry.added_at)
            self.assertTrue(store.is_whitelisted_url("sub.example.com"))
            self.assertFalse(store.is_whitelisted_url("evil-example.com"))
            self.assertEqual(json.loads(path.read_text())["domains"], ["keep.example"])

            self.assertTrue(store.remove_domain("example.com"))
            self.assertEqual(store.entries(), ())

    def test_service_returns_only_non_whitelisted_risks(self):
        with tempfile.TemporaryDirectory() as directory:
            whitelist = WhitelistStore(Path(directory) / "config.json")
            whitelist.add_domain("safe-clone.com")
            service = CloneCheckerService(
                (_SearchProvider(),), _RedirectProvider(), whitelist
            )
            result = service.check("Atlasbet", "atlasbet.com")

            self.assertTrue(result.results)
            self.assertTrue(
                all(
                    item.clone_result.status in RISK_STATUSES
                    for item in result.results
                )
            )
            self.assertTrue(
                all("safe-clone.com" not in item.url for item in result.results)
            )
            self.assertTrue(
                all("facebook.com" not in item.url for item in result.results)
            )

            self.assertTrue(whitelist.remove_domain("safe-clone.com"))
            repeated = service.check("Atlasbet", "atlasbet.com")
            self.assertTrue(
                any("safe-clone.com" in item.url for item in repeated.results)
            )

    def test_context_whitelist_and_clipboard_report(self):
        with tempfile.TemporaryDirectory() as directory:
            whitelist = WhitelistStore(Path(directory) / "config.json")
            panel = CloneCheckerPanel(CloneCheckerService(whitelist=whitelist))
            risk = SearchResult(
                "Yandex",
                "Atlasbet giriş",
                2,
                "https://atlasbet1893.com",
                "Atlasbet",
                redirect=RedirectResult(
                    "https://atlasbet1893.com",
                    (),
                    ("https://atlasbet1893.com",),
                    "https://atlasbet1893.com",
                ),
                clone_result=CloneResult(
                    "atlasbet.com",
                    "atlasbet1893.com",
                    "Klon adayı",
                    "Farklı domain",
                ),
            )
            payload = CloneCheckResult(
                "Atlasbet", CloneCheckStatus.COMPLETED, "1 sonuç", (risk,)
            )
            panel._show_results(payload)

            panel.copy_risk_report()
            report = QApplication.clipboard().text()
            self.assertIn("Yandex - Aratılan kelime: Atlasbet giriş", report)
            self.assertIn("2. sıra\n   https://atlasbet1893.com", report)
            self.assertNotIn("](", report)
            self.assertNotIn("Normal sonuç", report)

            panel._mark_result_safe(panel.result_table.topLevelItem(0))
            self.assertEqual(panel.result_table.topLevelItemCount(), 0)
            self.assertFalse(panel.copy_button.isEnabled())
            self.assertEqual(whitelist.entries()[0].domain, "atlasbet1893.com")
            self.assertEqual(panel.whitelist_table.topLevelItemCount(), 1)
            whitelist_row = panel.whitelist_table.topLevelItem(0)
            self.assertEqual(whitelist_row.text(0), "atlasbet1893.com")
            self.assertTrue(whitelist_row.text(1))

            panel.whitelist_table.setCurrentItem(whitelist_row)
            panel._remove_whitelist_entry()
            self.assertEqual(whitelist.entries(), ())
            self.assertEqual(panel.whitelist_table.topLevelItemCount(), 0)

    def test_report_resolves_tracking_and_deduplicates_domains(self):
        def risk(rank, url, keyword="Atlasbet giriş"):
            return SearchResult(
                "Yandex",
                keyword,
                rank,
                url,
                "Atlasbet",
                clone_result=CloneResult(
                    "atlasbet.com", "", "Klon adayı", "Farklı domain"
                ),
            )

        results = (
            risk(2, "https://atlasbet1893.com/"),
            risk(3, "https://atlasbet1893.com/tr/"),
            risk(
                4,
                "https://yandex.com.tr/an/count/?url="
                "https%3A%2F%2Fgov.atlasbetspots.icu%2F",
            ),
            risk(5, "https://yandex.com.tr/an/count/opaque-token"),
            risk(1, "https://m-atlasbet892.com/", "Atlasbet güncel giriş"),
        )
        report = build_clipboard_report(results)

        self.assertEqual(report.count("atlasbet1893.com"), 1)
        self.assertIn("2. sıra\n   https://atlasbet1893.com/", report)
        self.assertIn("4. sıra\n   https://gov.atlasbetspots.icu/", report)
        self.assertIn("1. sıra\n   https://m-atlasbet892.com/", report)
        self.assertNotIn("yandex.com.tr/an/count", report)
        self.assertNotIn("](", report)
        self.assertEqual(report_result_count(report), 3)


if __name__ == "__main__":
    unittest.main()
