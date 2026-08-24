import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from kralyavuz.main import set_application_icon
from kralyavuz.platform_paths import application_icon_path


class ApplicationIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_platform_icon_files_exist(self):
        windows_icon = application_icon_path("Windows")
        macos_icon = application_icon_path("Darwin")

        self.assertEqual(windows_icon.name, "kralyavuz_icon.ico")
        self.assertEqual(macos_icon.name, "kralyavuz_icon.icns")
        self.assertTrue(windows_icon.is_file())
        self.assertTrue(macos_icon.is_file())

    def test_qapplication_window_icon_is_set(self):
        previous_icon = self.app.windowIcon()
        try:
            icon_path = set_application_icon(self.app)

            self.assertEqual(icon_path, application_icon_path())
            self.assertTrue(icon_path.is_file())
            self.assertFalse(self.app.windowIcon().isNull())
        finally:
            self.app.setWindowIcon(previous_icon)


if __name__ == "__main__":
    unittest.main()
