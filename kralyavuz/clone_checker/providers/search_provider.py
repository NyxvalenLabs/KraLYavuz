from html.parser import HTMLParser
from typing import List, Optional, Tuple
from urllib.parse import urlsplit

import requests

from ..models import SearchResult


class SearchProviderError(RuntimeError):
    pass


class _YandexResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: List[Tuple[str, str]] = []
        self._active_item = False
        self._url = ""
        self._title_parts: List[str] = []
        self._title_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = dict(attrs)
        classes = set(attributes.get("class", "").split())
        if tag == "li" and "serp-item" in classes:
            self._finish_item()
            self._active_item = True
            return
        if not self._active_item:
            return
        if self._title_depth:
            self._title_depth += 1
        if tag == "a" and "OrganicTitle-Link" in classes:
            self._url = attributes.get("href", "").strip()
            self._title_parts = []
            self._title_depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self._title_depth:
            self._title_depth -= 1
        if tag == "li" and self._active_item:
            self._finish_item()

    def handle_data(self, data: str) -> None:
        if self._active_item and self._title_depth and data.strip():
            self._title_parts.append(data.strip())

    def close(self) -> None:
        super().close()
        self._finish_item()

    def _finish_item(self) -> None:
        title = " ".join(self._title_parts).strip()
        parsed = urlsplit(self._url)
        if title and parsed.scheme in {"http", "https"} and parsed.netloc:
            self.items.append((self._url, title))
        self._active_item = False
        self._url = ""
        self._title_parts = []
        self._title_depth = 0


class YandexSearchProvider:
    search_engine = "Yandex"
    search_url = "https://yandex.com.tr/search/"
    user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/134.0.0.0 Safari/537.36"
    )

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session or requests.Session()

    def search(self, keyword: str, limit: int = 10) -> List[SearchResult]:
        try:
            response = self.session.get(
                self.search_url,
                params={"text": keyword, "lr": "11508"},
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
                },
                timeout=15,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SearchProviderError(f"Yandex araması başarısız: {exc}") from exc

        content_type = response.headers.get("Content-Type", "").lower()
        if "html" not in content_type:
            raise SearchProviderError("Yandex geçerli bir HTML yanıtı döndürmedi.")
        if "captcha" in response.text.lower() or "showcaptcha" in response.url.lower():
            raise SearchProviderError("Yandex CAPTCHA doğrulaması istedi.")

        parser = _YandexResultParser()
        parser.feed(response.text)
        parser.close()
        if not parser.items:
            raise SearchProviderError("Yandex organik arama sonucu döndürmedi.")

        return [
            SearchResult(self.search_engine, keyword, rank, url, title)
            for rank, (url, title) in enumerate(parser.items[:limit], start=1)
        ]
