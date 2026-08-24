import json
import tempfile
import unittest
from pathlib import Path

import requests

from kralyavuz.clone_checker.models import SearchResult
from kralyavuz.clone_checker.providers import (
    GoogleApiSearchProvider,
    SearchProviderError,
)


class _Response:
    def __init__(self, payload, error=None):
        self.payload = payload
        self.error = error

    def raise_for_status(self):
        if self.error is not None:
            raise self.error

    def json(self):
        return self.payload


class _Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class GoogleApiSearchProviderTests(unittest.TestCase):
    def _write_config(self, directory, api_key="api-key", cx_id="cx-id"):
        config_path = Path(directory) / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "google_api_key": api_key,
                    "google_cx_id": cx_id,
                }
            ),
            encoding="utf-8",
        )
        return config_path

    def test_parses_search_results(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = self._write_config(directory)
            session = _Session(
                _Response(
                    {
                        "items": [
                            {
                                "link": "https://first.example/result",
                                "title": "First Result",
                            },
                            {
                                "link": "https://second.example/result",
                                "title": "Second Result",
                            },
                        ]
                    }
                )
            )

            results = GoogleApiSearchProvider(session, config_path).search(
                "atlasbet giriş", 5
            )

            self.assertEqual(
                results,
                [
                    SearchResult(
                        "Google",
                        "atlasbet giriş",
                        1,
                        "https://first.example/result",
                        "First Result",
                    ),
                    SearchResult(
                        "Google",
                        "atlasbet giriş",
                        2,
                        "https://second.example/result",
                        "Second Result",
                    ),
                ],
            )
            self.assertEqual(
                session.calls,
                [
                    (
                        "https://www.googleapis.com/customsearch/v1",
                        {
                            "params": {
                                "key": "api-key",
                                "cx": "cx-id",
                                "q": "atlasbet giriş",
                                "num": 5,
                            },
                            "timeout": 15,
                        },
                    )
                ],
            )

    def test_empty_items_returns_empty_results(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = self._write_config(directory)
            session = _Session(_Response({}))

            results = GoogleApiSearchProvider(session, config_path).search(
                "atlasbet giriş", 5
            )

            self.assertEqual(results, [])

    def test_http_error_raises_search_provider_error(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = self._write_config(directory)
            session = _Session(
                _Response({}, requests.HTTPError("403 Client Error"))
            )

            with self.assertRaisesRegex(
                SearchProviderError,
                "Google Custom Search API isteği başarısız",
            ):
                GoogleApiSearchProvider(session, config_path).search(
                    "atlasbet giriş", 5
                )

    def test_missing_api_config_raises_search_provider_error(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = self._write_config(directory, api_key="", cx_id="")
            session = _Session(_Response({"items": []}))

            with self.assertRaisesRegex(
                SearchProviderError,
                "API anahtarı veya CX kimliği eksik",
            ):
                GoogleApiSearchProvider(session, config_path).search(
                    "atlasbet giriş", 5
                )
            self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
