from pathlib import Path
from typing import List, Optional

import requests

from ...app_config import load_config
from ...platform_paths import CONFIG_PATH
from ..models import SearchResult
from .search_provider import SearchProviderError


class GoogleApiSearchProvider:
    search_engine = "Google"
    search_url = "https://www.googleapis.com/customsearch/v1"

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        config_path: Path = CONFIG_PATH,
    ) -> None:
        self.session = session or requests.Session()
        self.config_path = config_path

    def search(self, keyword: str, limit: int = 10) -> List[SearchResult]:
        config = load_config(self.config_path)
        api_key = config["google_api_key"].strip()
        cx_id = config["google_cx_id"].strip()
        if not api_key or not cx_id:
            raise SearchProviderError(
                "Google Custom Search API anahtarı veya CX kimliği eksik."
            )
        if limit <= 0:
            return []

        try:
            response = self.session.get(
                self.search_url,
                params={
                    "key": api_key,
                    "cx": cx_id,
                    "q": keyword,
                    "num": min(limit, 10),
                },
                timeout=15,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SearchProviderError(
                f"Google Custom Search API isteği başarısız: {exc}"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise SearchProviderError(
                "Google Custom Search API geçerli bir JSON yanıtı döndürmedi."
            ) from exc

        if not isinstance(payload, dict):
            raise SearchProviderError(
                "Google Custom Search API geçerli bir yanıt döndürmedi."
            )
        if payload.get("error"):
            error = payload["error"]
            message = error.get("message", "Bilinmeyen API hatası") if isinstance(
                error, dict
            ) else str(error)
            raise SearchProviderError(
                f"Google Custom Search API hata döndürdü: {message}"
            )

        items = payload.get("items", [])
        if not isinstance(items, list):
            return []

        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("link")
            title = item.get("title")
            if not isinstance(url, str) or not url.strip():
                continue
            if not isinstance(title, str) or not title.strip():
                continue
            results.append(
                SearchResult(
                    self.search_engine,
                    keyword,
                    len(results) + 1,
                    url.strip(),
                    title.strip(),
                )
            )
            if len(results) >= limit:
                break
        return results
