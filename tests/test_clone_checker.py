import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from kralyavuz.app_config import save_domain_config
from kralyavuz.clone_checker.models import (
    CloneCheckResult,
    CloneCheckStatus,
    CloneResult,
    RedirectResult,
    SearchResult,
)
from kralyavuz.clone_checker.service import RISK_STATUSES, CloneCheckerService
from kralyavuz.clone_checker.ui import CloneCheckerPanel
from kralyavuz.clone_checker.whitelist import WhitelistStore, normalize_domain_list
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
            saved = json.loads(path.read_text())
            self.assertEqual(saved["domains"], ["keep.example"])
            self.assertEqual(saved["manual_whitelist"][0]["domain"], "example.com")

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
            self.assertEqual(whitelist_row.text(1), "Manuel")
            self.assertTrue(whitelist_row.text(2))

            panel.whitelist_table.setCurrentItem(whitelist_row)
            panel._remove_whitelist_entry()
            self.assertEqual(whitelist.entries(), ())
            self.assertEqual(panel.whitelist_table.topLevelItemCount(), 0)

    def test_legacy_whitelist_migrates_without_data_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "domains": ["keep.example"],
                        "custom": True,
                        "manual_whitelist": [
                            {
                                "domain": "manual.example",
                                "added_at": "2026-01-01T00:00:00+00:00",
                            }
                        ],
                        "clone_whitelist": [
                            {
                                "domain": "legacy.example",
                                "added_at": "2025-01-01T00:00:00+00:00",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            entries = WhitelistStore(path).entries()
            self.assertEqual(
                [entry.domain for entry in entries],
                ["legacy.example", "manual.example"],
            )

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("clone_whitelist", saved)
            self.assertEqual(
                [entry["domain"] for entry in saved["manual_whitelist"]],
                ["legacy.example", "manual.example"],
            )
            self.assertEqual(saved["domains"], ["keep.example"])
            self.assertTrue(saved["custom"])

    def test_synced_domains_follow_main_list_and_preserve_manual_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "domains": [],
                        "manual_whitelist": [
                            {
                                "domain": "manual.example",
                                "added_at": "2026-01-01T00:00:00+00:00",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            store = WhitelistStore(path)

            domains = ["https://www.atlasbet.com/path", "EXAMPLE.com"]
            save_domain_config(domains, normalize_domain_list(domains), path)
            self.assertTrue(store.is_whitelisted_url("sub.atlasbet.com"))
            self.assertEqual(
                json.loads(path.read_text())["synced_domains"],
                ["atlasbet.com", "example.com"],
            )

            remaining_domains = ["example.com"]
            save_domain_config(
                remaining_domains,
                normalize_domain_list(remaining_domains),
                path,
            )
            self.assertFalse(store.is_whitelisted_url("atlasbet.com"))
            self.assertTrue(store.is_whitelisted_url("example.com"))
            self.assertTrue(store.is_whitelisted_url("manual.example"))
            self.assertEqual(
                [entry.domain for entry in store.entries()],
                ["example.com", "manual.example"],
            )

    def test_same_domain_is_merged_with_both_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "domains": ["shared.example"],
                        "synced_domains": ["shared.example"],
                        "manual_whitelist": [
                            {
                                "domain": "shared.example",
                                "added_at": "2026-01-01T00:00:00+00:00",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            entries = WhitelistStore(path).entries()
            self.assertEqual(len(entries), 1)
            self.assertTrue(entries[0].is_synced)
            self.assertTrue(entries[0].is_manual)
            self.assertEqual(entries[0].source, "Ana Liste + Manuel")

    def test_whitelist_ui_shows_sources_and_protects_synced_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "synced_domains": ["synced.example", "shared.example"],
                        "manual_whitelist": [
                            {
                                "domain": "manual.example",
                                "added_at": "2026-01-01T00:00:00+00:00",
                            },
                            {
                                "domain": "shared.example",
                                "added_at": "2026-01-02T00:00:00+00:00",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            whitelist = WhitelistStore(path)
            panel = CloneCheckerPanel(CloneCheckerService(whitelist=whitelist))
            rows = {}
            for index in range(panel.whitelist_table.topLevelItemCount()):
                row = panel.whitelist_table.topLevelItem(index)
                rows[row.text(0)] = row

            self.assertEqual(rows["synced.example"].text(1), "Ana Domain Listesi")
            self.assertEqual(rows["manual.example"].text(1), "Manuel")
            self.assertEqual(rows["shared.example"].text(1), "Ana Liste + Manuel")

            panel.whitelist_table.setCurrentItem(rows["synced.example"])
            self.assertFalse(panel.whitelist_delete_button.isEnabled())
            panel._remove_whitelist_entry()
            self.assertTrue(whitelist.is_whitelisted_url("synced.example"))

            panel.whitelist_table.setCurrentItem(rows["shared.example"])
            self.assertTrue(panel.whitelist_delete_button.isEnabled())
            panel._remove_whitelist_entry()
            shared = next(
                entry for entry in whitelist.entries()
                if entry.domain == "shared.example"
            )
            self.assertTrue(shared.is_synced)
            self.assertFalse(shared.is_manual)
            self.assertEqual(shared.source, "Ana Domain Listesi")

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
