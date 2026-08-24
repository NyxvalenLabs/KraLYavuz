import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QGroupBox

from kralyavuz.kral_tap import (
    TOOLTIP_TEXT,
    KralTapVideoPopup,
    KralTapWidget,
)
from kralyavuz.main import MainWindow


VIDEO_PATH = (
    Path(__file__).resolve().parent.parent
    / "assets"
    / "kral_tap"
    / "kral_tap.mp4"
)


class KralTapWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_compact_button_count_and_tooltip_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            widget = KralTapWidget(config_path, VIDEO_PATH)
            widget.show()
            self.app.processEvents()

            self.assertTrue(widget.tap_button.isVisible())
            self.assertTrue(widget.help_button.isVisible())
            self.assertEqual(widget.tap_button.text(), "👑 Krala Tap")
            self.assertEqual(widget.help_button.toolTip(), TOOLTIP_TEXT)
            self.assertEqual(widget.count_label.text(), "Total Tapma: 0")
            self.assertFalse(hasattr(widget, "animation_label"))
            self.assertEqual(widget.findChildren(QGroupBox), [])
            self.assertLess(widget.sizeHint().height(), 100)
            widget.close()

    def test_main_window_places_widget_in_top_right_domain_header(self):
        with (
            patch("kralyavuz.main.load_config", return_value={"domains": []}),
            patch(
                "kralyavuz.main.save_domain_config",
                return_value={"domains": [], "synced_domains": []},
            ),
            patch("kralyavuz.main.QTimer.singleShot"),
            patch("kralyavuz.clone_checker.ui.CloneCheckerPanel.reload_whitelist"),
        ):
            window = MainWindow()

        window.show()
        self.app.processEvents()
        central_layout = window.centralWidget().layout()

        self.assertIs(central_layout.itemAt(0).layout(), window.domain_header_row)
        self.assertIs(central_layout.itemAt(1).widget(), window.url_input)
        self.assertEqual(window.domain_header_row.indexOf(window.domain_list_label), 0)
        self.assertEqual(window.domain_header_row.indexOf(window.kral_tap_widget), 2)
        self.assertGreater(
            window.kral_tap_widget.geometry().left(),
            window.domain_list_label.geometry().right(),
        )
        self.assertLess(
            window.kral_tap_widget.geometry().bottom(),
            window.url_input.geometry().top(),
        )
        window.close()

    def test_tap_count_is_saved_before_video_and_preserves_other_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "domains": ["keep.example"],
                        "manual_whitelist": [{"domain": "safe.example"}],
                        "kral_tap_count": 7,
                    }
                ),
                encoding="utf-8",
            )
            widget = KralTapWidget(config_path, VIDEO_PATH)
            widget.show()

            widget.tap_button.click()
            self.app.processEvents()

            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["kral_tap_count"], 8)
            self.assertEqual(saved["domains"], ["keep.example"])
            self.assertEqual(
                saved["manual_whitelist"],
                [{"domain": "safe.example"}],
            )
            self.assertIsNotNone(widget.video_popup)
            self.assertTrue(widget.video_popup.isVisible())

            widget.video_popup.close()
            self.app.processEvents()
            reopened = KralTapWidget(config_path, VIDEO_PATH)
            self.assertEqual(reopened.tap_count, 8)
            self.assertEqual(reopened.count_label.text(), "Total Tapma: 8")
            widget.close()
            reopened.close()

    def test_video_popup_is_frameless_audible_and_closes_at_end(self):
        popup = KralTapVideoPopup(VIDEO_PATH)
        closed = []
        popup.closed.connect(lambda: closed.append(True))

        self.assertTrue(popup.windowFlags() & Qt.FramelessWindowHint)
        self.assertFalse(popup.isFullScreen())
        self.assertEqual(popup.video_widget.aspectRatioMode(), Qt.KeepAspectRatio)
        self.assertFalse(popup.audio_output.isMuted())
        self.assertAlmostEqual(popup.audio_output.volume(), 1.0)
        self.assertEqual(
            Path(popup.player.source().toLocalFile()).resolve(),
            VIDEO_PATH.resolve(),
        )
        self.assertLessEqual(popup.video_widget.width(), 720)
        self.assertLessEqual(popup.video_widget.height(), 480)

        popup.show()
        self.app.processEvents()
        popup._on_media_status_changed(QMediaPlayer.MediaStatus.EndOfMedia)
        self.app.processEvents()
        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
