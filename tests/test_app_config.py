import json
import tempfile
import unittest
from pathlib import Path

from kralyavuz.app_config import has_google_api_config, load_config, save_config


class GoogleApiConfigTests(unittest.TestCase):
    def test_empty_google_api_config_is_inactive(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(json.dumps({}), encoding="utf-8")

            config = load_config(config_path)

            self.assertEqual(config["google_api_key"], "")
            self.assertEqual(config["google_cx_id"], "")
            self.assertFalse(has_google_api_config(config_path))

    def test_google_api_config_is_active_when_both_fields_are_filled(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            save_config(
                {
                    "domains": [],
                    "google_api_key": "test-api-key",
                    "google_cx_id": "test-cx-id",
                },
                config_path,
            )

            self.assertTrue(has_google_api_config(config_path))


if __name__ == "__main__":
    unittest.main()
