import logging
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional, Protocol, Tuple
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit

import requests
from playwright.sync_api import Error as PlaywrightError, sync_playwright

from ..models import SearchResult
from ...platform_paths import DEFAULT_OUTPUT_DIR, find_opera_gx


logger = logging.getLogger(__name__)
GOOGLE_DEBUG_RESPONSE_PATH = DEFAULT_OUTPUT_DIR / "debug_google_response.html"


class SearchProviderError(RuntimeError):
    pass


class GoogleBrowserFallback(Protocol):
    def search(self, keyword: str, limit: int) -> List[Tuple[str, str]]:
        ...


def _record_google_debug_response(
    response: requests.Response,
    output_path: Path = GOOGLE_DEBUG_RESPONSE_PATH,
) -> None:
    content_type = response.headers.get("Content-Type", "")
    logger.warning(
        "Google debug status_code: %s",
        getattr(response, "status_code", "bilinmiyor"),
    )
    logger.warning("Google debug response.url: %s", response.url)
    logger.warning("Google debug Content-Type: %s", content_type)
    logger.warning("Google debug response.text[:500]: %r", response.text[:500])
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(response.text, encoding="utf-8")
    except OSError as exc:
        logger.warning("Google debug HTML kaydedilemedi: %s", exc)


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


def _decode_google_url(value: str) -> str:
    decoded = value.strip()
    for _ in range(3):
        updated = unquote(decoded)
        if updated == decoded:
            break
        decoded = updated
    if decoded.startswith("//"):
        decoded = f"https:{decoded}"
    return decoded


def _is_google_hostname(hostname: str) -> bool:
    normalized = hostname.casefold().strip(".")
    return normalized.startswith("google.") or ".google." in normalized


def resolve_google_result_url(value: str) -> str:
    candidate = _decode_google_url(value)
    if candidate.startswith("/"):
        candidate = urljoin("https://www.google.com", candidate)

    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if not _is_google_hostname(parsed.hostname or ""):
        return candidate
    if parsed.path.rstrip("/") != "/url":
        return ""

    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        if key.casefold() not in {"q", "url"}:
            continue
        target = _decode_google_url(value)
        target_parts = urlsplit(target)
        if target_parts.scheme in {"http", "https"} and target_parts.netloc:
            return target
    return ""


class _GoogleResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: List[Tuple[str, str]] = []
        self._href = ""
        self._anchor_depth = 0
        self._title_depth = 0
        self._title_parts: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = dict(attrs)
        if not self._anchor_depth:
            if tag == "a" and attributes.get("href", "").strip():
                self._href = attributes["href"].strip()
                self._anchor_depth = 1
            return

        self._anchor_depth += 1
        if self._title_depth:
            self._title_depth += 1
        elif tag == "h3":
            self._title_depth = 1
            self._title_parts = []

    def handle_endtag(self, tag: str) -> None:
        if not self._anchor_depth:
            return
        if self._title_depth:
            self._title_depth -= 1
        if tag == "a":
            self._finish_item()
            return
        self._anchor_depth = max(1, self._anchor_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._title_depth and data.strip():
            self._title_parts.append(data.strip())

    def close(self) -> None:
        super().close()
        self._finish_item()

    def _finish_item(self) -> None:
        title = " ".join(self._title_parts).strip()
        url = resolve_google_result_url(self._href)
        if title and url:
            self.items.append((url, title))
        self._href = ""
        self._anchor_depth = 0
        self._title_depth = 0
        self._title_parts = []


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


class PlaywrightGoogleSearchFallback:
    search_url = "https://www.google.com/search"

    def search(self, keyword: str, limit: int) -> List[Tuple[str, str]]:
        query = urlencode(
            {
                "q": keyword,
                "num": min(limit, 100),
                "hl": "tr",
                "gl": "tr",
                "filter": "0",
            }
        )
        query_url = f"{self.search_url}?{query}"
        try:
            with sync_playwright() as playwright:
                launch_options = {"headless": True}
                browser_executable = find_opera_gx()
                if browser_executable is not None:
                    launch_options["executable_path"] = str(browser_executable)
                browser = playwright.chromium.launch(**launch_options)
                try:
                    context = browser.new_context(
                        user_agent=YandexSearchProvider.user_agent,
                        locale="tr-TR",
                    )
                    try:
                        page = context.new_page()
                        page.goto(
                            query_url,
                            wait_until="domcontentloaded",
                            timeout=20_000,
                        )
                        if "/sorry/" in page.url.casefold():
                            raise SearchProviderError(
                                "Google headless browser CAPTCHA doğrulaması istedi."
                            )
                        try:
                            page.wait_for_selector("a:has(h3)", timeout=5_000)
                        except PlaywrightError:
                            pass
                        values = page.locator("a:has(h3)").evaluate_all(
                            """
                            anchors => anchors.map(anchor => ({
                                url: anchor.getAttribute('href') || anchor.href || '',
                                title: (anchor.querySelector('h3')?.innerText || '').trim(),
                            }))
                            """
                        )
                    finally:
                        context.close()
                finally:
                    browser.close()
        except PlaywrightError as exc:
            raise SearchProviderError(
                f"Google headless browser fallback başarısız: {exc}"
            ) from exc

        if not isinstance(values, list):
            return []
        return [
            (str(item.get("url", "")), str(item.get("title", "")))
            for item in values
            if isinstance(item, dict)
        ]


class GoogleSearchProvider:
    search_engine = "Google"
    search_url = "https://www.google.com/search"
    user_agent = YandexSearchProvider.user_agent

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        browser_fallback: Optional[GoogleBrowserFallback] = None,
    ) -> None:
        self.session = session or requests.Session()
        self.browser_fallback = browser_fallback or PlaywrightGoogleSearchFallback()

    def search(self, keyword: str, limit: int = 10) -> List[SearchResult]:
        if limit <= 0:
            return []
        request_error = ""
        try:
            request_items = self._search_with_requests(keyword, limit)
        except SearchProviderError as exc:
            request_error = str(exc)
        else:
            if request_items:
                return self._build_results(keyword, request_items, limit)
            request_error = "Google organik arama sonucu döndürmedi."

        try:
            browser_items = self.browser_fallback.search(keyword, limit)
        except SearchProviderError as exc:
            raise SearchProviderError(f"{request_error} {exc}") from exc

        results = self._build_results(keyword, browser_items, limit)
        if results:
            return results
        raise SearchProviderError(
            f"{request_error} Headless browser organik arama sonucu döndürmedi."
        )

    def _search_with_requests(
        self, keyword: str, limit: int
    ) -> List[Tuple[str, str]]:
        try:
            response = self.session.get(
                self.search_url,
                params={
                    "q": keyword,
                    "num": min(limit, 100),
                    "hl": "tr",
                    "gl": "tr",
                    "filter": "0",
                },
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
                },
                timeout=15,
            )
            _record_google_debug_response(response)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SearchProviderError(f"Google araması başarısız: {exc}") from exc

        content_type = response.headers.get("Content-Type", "").lower()
        if "html" not in content_type:
            raise SearchProviderError("Google geçerli bir HTML yanıtı döndürmedi.")
        response_text = response.text.lower()
        response_url = response.url.lower()
        if (
            "captcha" in response_text
            or "unusual traffic" in response_text
            or "/sorry/" in response_url
        ):
            raise SearchProviderError("Google CAPTCHA doğrulaması istedi.")

        parser = _GoogleResultParser()
        parser.feed(response.text)
        parser.close()
        return parser.items

    def _build_results(
        self,
        keyword: str,
        items: List[Tuple[str, str]],
        limit: int,
    ) -> List[SearchResult]:
        normalized = []
        seen_urls = set()
        for raw_url, raw_title in items:
            url = resolve_google_result_url(raw_url)
            title = raw_title.strip()
            if not url or not title or url in seen_urls:
                continue
            seen_urls.add(url)
            normalized.append((url, title))
            if len(normalized) >= limit:
                break
        return [
            SearchResult(self.search_engine, keyword, rank, url, title)
            for rank, (url, title) in enumerate(normalized, start=1)
        ]
