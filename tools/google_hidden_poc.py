"""Experimental hidden-headful Google search proof of concept.

This script is intentionally independent from KraLYavuz production providers.
"""

from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from playwright.sync_api import BrowserContext, Error as PlaywrightError, Page, sync_playwright


QUERY = "atlasbet giriş"
WINDOW_TITLE = "KRALYAVUZ_GOOGLE_POC"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = Path.home() / ".kralyavuz" / "browser_profiles" / "google_hidden_poc"
DEBUG_HTML = PROJECT_ROOT / "results" / "google_hidden_poc.html"
DEBUG_PNG = PROJECT_ROOT / "results" / "google_hidden_poc.png"


def _browser_candidates() -> list[tuple[str, Optional[Path]]]:
    home = Path.home()
    candidates: list[tuple[str, Optional[Path]]] = []

    if platform.system() == "Darwin":
        candidates.extend(
            [
                (
                    "Microsoft Edge",
                    Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                ),
                (
                    "Microsoft Edge",
                    home
                    / "Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                ),
                (
                    "Google Chrome",
                    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                ),
                (
                    "Google Chrome",
                    home
                    / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                ),
            ]
        )
    elif platform.system() == "Windows":
        local_app_data = Path.home() / "AppData" / "Local"
        program_files = [
            Path("C:/Program Files"),
            Path("C:/Program Files (x86)"),
        ]
        candidates.append(
            (
                "Microsoft Edge",
                local_app_data / "Microsoft/Edge/Application/msedge.exe",
            )
        )
        candidates.extend(
            ("Microsoft Edge", root / "Microsoft/Edge/Application/msedge.exe")
            for root in program_files
        )
        candidates.append(
            (
                "Google Chrome",
                local_app_data / "Google/Chrome/Application/chrome.exe",
            )
        )
        candidates.extend(
            ("Google Chrome", root / "Google/Chrome/Application/chrome.exe")
            for root in program_files
        )

    for command, label in (
        ("msedge", "Microsoft Edge"),
        ("microsoft-edge", "Microsoft Edge"),
        ("google-chrome", "Google Chrome"),
        ("chrome", "Google Chrome"),
    ):
        executable = shutil.which(command)
        if executable:
            candidates.append((label, Path(executable)))

    unique: list[tuple[str, Optional[Path]]] = []
    seen: set[str] = set()
    for label, executable in candidates:
        if executable is None or not executable.is_file():
            continue
        key = str(executable.resolve())
        if key not in seen:
            seen.add(key)
            unique.append((label, executable))

    unique.append(("Playwright Chromium", None))
    return unique


def _launch_context(playwright: Any) -> tuple[BrowserContext, str]:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    for label, executable in _browser_candidates():
        options: dict[str, Any] = {
            "user_data_dir": str(PROFILE_DIR),
            "headless": False,
            "viewport": {"width": 1280, "height": 900},
            "args": [
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1280,900",
            ],
        }
        if executable is not None:
            options["executable_path"] = str(executable)

        try:
            return playwright.chromium.launch_persistent_context(**options), label
        except PlaywrightError as exc:
            failures.append(f"{label}: {exc}")

    raise RuntimeError("Browser baslatilamadi:\n" + "\n".join(failures))


def _conceal_poc_browser() -> tuple[bool, str]:
    if platform.system() == "Darwin":
        return (
            False,
            "macOS'ta yalniz bu Chromium instance'ini guvenilir bicimde "
            "gizleyen sistem API'si yok; gorunur headful fallback kullaniliyor",
        )
    return False, "bu platform icin pencere gizleme backend'i uygulanmadi"


def _captcha_detected(page: Page) -> bool:
    current_url = page.url.lower()
    if "/sorry/" in current_url:
        return True

    body_text = ""
    try:
        body_text = page.locator("body").inner_text(timeout=3_000).lower()
    except PlaywrightError:
        pass

    messages = (
        "our systems have detected unusual traffic",
        "unusual traffic from your computer network",
        "sistemlerimiz bilgisayar aginizdan gelen olagan disi trafik algiladi",
    )
    if any(message in body_text for message in messages):
        return True

    challenge_selectors = (
        'form[action*="/sorry/"]',
        "#captcha-form",
        'iframe[src*="recaptcha"]',
        '[id*="recaptcha"]',
        ".g-recaptcha",
    )
    return any(page.locator(selector).count() > 0 for selector in challenge_selectors)


def _organic_results(page: Page) -> list[dict[str, Any]]:
    raw_results = page.evaluate(
        """
        () => {
          const results = [];
          const adContainers = '#tads, #taw, [data-text-ad], [aria-label="Ads"]';

          for (const heading of document.querySelectorAll('#search h3')) {
            const anchor = heading.closest('a[href]') || heading.parentElement?.closest('a[href]');
            if (!anchor || anchor.closest(adContainers)) continue;

            results.push({
              title: (heading.textContent || '').trim(),
              href: anchor.href || anchor.getAttribute('href') || ''
            });
          }
          return results;
        }
        """
    )

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_results:
        title = str(item.get("title", "")).strip()
        url = _normalize_google_result_url(str(item.get("href", "")))
        if not title or not url or url in seen or _is_google_internal_url(url):
            continue
        seen.add(url)
        results.append({"rank": len(results) + 1, "title": title, "url": url})
        if len(results) == 10:
            break
    return results


def _normalize_google_result_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlsplit(url)
    if parsed.netloc.lower().endswith("google.com") and parsed.path == "/url":
        query = parse_qs(parsed.query)
        target = query.get("q", query.get("url", [""]))[0]
        url = unquote(target)

    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return url


def _is_google_internal_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host in {"accounts.google.com", "support.google.com"}:
        return True
    return host == "google.com" or host.endswith(".google.com")


def _save_debug(page: Page) -> None:
    DEBUG_HTML.parent.mkdir(parents=True, exist_ok=True)
    try:
        DEBUG_HTML.write_text(page.content(), encoding="utf-8")
        print(f"Debug HTML: {DEBUG_HTML}")
    except (OSError, PlaywrightError) as exc:
        print(f"Debug HTML kaydi basarisiz: {exc}", file=sys.stderr)

    try:
        page.evaluate(
            "window.stop(); "
            "document.querySelectorAll('iframe').forEach((node) => node.remove())"
        )
        page.screenshot(
            path=str(DEBUG_PNG),
            full_page=False,
            animations="disabled",
            timeout=10_000,
        )
        print(f"Debug PNG: {DEBUG_PNG}")
    except (OSError, PlaywrightError) as exc:
        print(f"Debug PNG kaydi basarisiz: {exc}", file=sys.stderr)


def run() -> int:
    context: Optional[BrowserContext] = None
    page: Optional[Page] = None
    browser_label = "Baslatilamadi"
    window_hidden = False

    with sync_playwright() as playwright:
        try:
            context, browser_label = _launch_context(playwright)
            page = context.pages[0] if context.pages else context.new_page()
            page.set_content(
                f"<html><head><title>{WINDOW_TITLE}</title></head>"
                "<body>KraLYavuz Google POC hazirlaniyor...</body></html>",
                wait_until="domcontentloaded",
            )
            page.wait_for_timeout(500)

            window_hidden, hide_detail = _conceal_poc_browser()
            print(f"Browser: {browser_label}")
            print(f"Profile: {PROFILE_DIR}")
            print(f"Window hidden: {'YES' if window_hidden else 'NO'}")
            print(f"Window mode detail: {hide_detail}")
            print(f"Query: {QUERY}")

            search_url = "https://www.google.com/search?" + urlencode({"q": QUERY})
            page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(4_000)

            captcha = _captcha_detected(page)
            results = [] if captcha else _organic_results(page)

            print(f"Final URL: {page.url}")
            print(f"Page title: {page.title()}")
            print(f"CAPTCHA: {'YES' if captcha else 'NO'}")
            print(f"Organic result count: {len(results)}")
            for result in results:
                print(f"{result['rank']} | {result['title']} | {result['url']}")

            if captcha or not results:
                _save_debug(page)
            return 0 if captcha or results else 1
        except (PlaywrightError, RuntimeError) as exc:
            print(f"POC error: {exc}", file=sys.stderr)
            if context is not None and page is not None:
                _save_debug(page)
            return 1
        finally:
            if context is not None:
                try:
                    context.close()
                except PlaywrightError as exc:
                    print(f"POC browser kapatilamadi: {exc}", file=sys.stderr)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    raise SystemExit(run())
