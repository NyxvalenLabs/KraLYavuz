import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable, Iterable, List, Optional, Union
from urllib.parse import urlsplit

import requests
from playwright.sync_api import Error as PlaywrightError, Page, sync_playwright

from .btk_operator import CDP_URL, normalize_domain, verify_opera_vpn
from .guvenlinet_checker import (
    GUVENLINET_URL,
    GuvenliNetResult,
    _find_existing_opera_page,
    check_guvenlinet_in_session,
)
from .opera_btk_runner import (
    BtkOperatorResult,
    check_btk_in_session,
    is_private_opera_page,
)
from .opera_cdp_bootstrap import ensure_opera_cdp


ProgressCallback = Callable[[int, int, str, str, str], None]
CaptchaCallback = Callable[[str, str, str, bytes, bool], None]
CaptchaWaiter = Callable[[str], Optional[str]]
TargetCallback = Callable[[str, str, str], None]
StopRequested = Callable[[], bool]
DomainInput = Union[str, Iterable[str]]
ServiceRunner = Callable[
    [str, str, Optional[CaptchaCallback], Optional[CaptchaWaiter]], object
]


@dataclass
class BatchDomainResult:
    domain: str
    btk_status: str = "Bekliyor"
    family_status: str = "Bekliyor"
    btk_screenshot_path: Optional[Path] = None
    family_screenshot_path: Optional[Path] = None
    btk_result: Optional[BtkOperatorResult] = None
    family_result: Optional[GuvenliNetResult] = None
    btk_screenshot_ok: bool = False
    family_screenshot_ok: bool = False


@dataclass(frozen=True)
class ServiceTargets:
    btk_target_id: str
    btk_url: str
    family_target_id: str
    family_url: str


def normalize_domains(values: DomainInput) -> List[str]:
    lines = values.splitlines() if isinstance(values, str) else values
    domains: List[str] = []
    seen = set()
    for value in lines:
        if not value.strip():
            continue
        source = value.strip()
        normalize_domain(source)
        duplicate_key = source.rstrip("/").lower()
        if duplicate_key in seen:
            continue
        seen.add(duplicate_key)
        domains.append(source)
    return domains


def _notify(
    callback: Optional[ProgressCallback],
    completed: int,
    total: int,
    domain: str,
    service: str,
    stage: str,
) -> None:
    if callback is not None:
        callback(completed, total, domain, service, stage)


def _is_btk_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.hostname == "internet.btk.gov.tr" and parsed.path.startswith(
        "/sitesorgu"
    )


def _is_family_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.hostname in {"guvenlinet.org.tr", "www.guvenlinet.org.tr"} and (
        parsed.path.startswith("/sorgula")
    )


def _target_id(page: Page) -> str:
    page_session = page.context.new_cdp_session(page)
    try:
        return str(page_session.send("Target.getTargetInfo")["targetInfo"]["targetId"])
    finally:
        page_session.detach()


def _window_id(browser, page: Page) -> int:
    browser_session = browser.new_browser_cdp_session()
    try:
        return int(
            browser_session.send(
                "Browser.getWindowForTarget", {"targetId": _target_id(page)}
            )["windowId"]
        )
    finally:
        browser_session.detach()


def _service_pages(browser) -> tuple[Page, Page]:
    pages = [page for context in browser.contexts for page in context.pages]
    btk_pages = [
        page for page in pages if _is_btk_url(page.url) and is_private_opera_page(browser, page)
    ]
    family_pages = [
        page
        for page in pages
        if _is_family_url(page.url) and is_private_opera_page(browser, page)
    ]
    if not btk_pages or not family_pages:
        raise RuntimeError("Hazır BTK/GüvenliNet sekmesi bulunamadı")
    if len(btk_pages) != 1 or len(family_pages) != 1:
        raise RuntimeError(
            "Hazır BTK/GüvenliNet sekmeleri tekil değil "
            f"(BTK: {len(btk_pages)}, GüvenliNet: {len(family_pages)})."
        )
    btk_page = btk_pages[0]
    family_page = family_pages[0]
    if _target_id(btk_page) == _target_id(family_page):
        raise RuntimeError("BTK ve GüvenliNet aynı Opera sekmesine bağlandı.")
    if _window_id(browser, btk_page) != _window_id(browser, family_page):
        raise RuntimeError("BTK ve GüvenliNet aynı Opera private penceresinde değil.")
    return btk_page, family_page


def discover_service_targets() -> ServiceTargets:
    """Discover the two user-prepared Opera tabs without creating or navigating pages."""
    ensure_opera_cdp()
    try:
        requests.get(f"{CDP_URL}/json", timeout=2).raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            "Opera GX CDP bağlantısı bulunamadı. Hazır BTK/GüvenliNet sekmesi bulunamadı"
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(CDP_URL, timeout=5_000)
        btk_page, family_page = _service_pages(browser)
        return ServiceTargets(
            btk_target_id=_target_id(btk_page),
            btk_url=btk_page.url,
            family_target_id=_target_id(family_page),
            family_url=family_page.url,
        )


def _page_for_target(browser, target_id: str, service: str) -> Page:
    matches_url = _is_btk_url if service == "BTK" else _is_family_url
    for context in browser.contexts:
        for page in context.pages:
            if _target_id(page) == target_id:
                if not matches_url(page.url):
                    raise RuntimeError(
                        f"Target URL eşleşmedi: {target_id} -> {page.url}"
                    )
                return page
    raise RuntimeError("Opera servis sayfası CDP oturumunda bulunamadı.")


def _run_btk_target(
    target_id: str,
    domain: str,
    captcha_ready: Optional[CaptchaCallback],
    wait_for_captcha: Optional[CaptchaWaiter],
) -> BtkOperatorResult:
    print(f"BTK gönderilen: {domain}", flush=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(CDP_URL, timeout=10_000)
        page = _page_for_target(browser, target_id, "BTK")
        if not _is_btk_url(page.url):
            raise RuntimeError("BTK worker yanlış Opera sekmesine bağlandı.")
        result = check_btk_in_session(
            browser,
            page,
            domain,
            verify_session=False,
            captcha_ready=(
                (
                    lambda image, retry: captcha_ready(
                        domain, "BTK", target_id, image, retry
                    )
                )
                if captcha_ready
                else None
            ),
            wait_for_captcha=(
                (lambda: wait_for_captcha("BTK")) if wait_for_captcha else None
            ),
        )
        if _target_id(page) != target_id or not _is_btk_url(page.url):
            raise RuntimeError("BTK sonucu sabit BTK target üzerinden alınmadı.")
        return result


def _run_family_target(
    target_id: str,
    domain: str,
    captcha_ready: Optional[CaptchaCallback],
    wait_for_captcha: Optional[CaptchaWaiter],
) -> GuvenliNetResult:
    print(f"GüvenliNet gönderilen: {domain}", flush=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(CDP_URL, timeout=10_000)
        page = _page_for_target(browser, target_id, "Aile Profili")
        if not _is_family_url(page.url):
            raise RuntimeError("GüvenliNet worker yanlış Opera sekmesine bağlandı.")
        result = check_guvenlinet_in_session(
            browser,
            page,
            domain,
            verify_session=False,
            captcha_ready=(
                (
                    lambda image, retry: captcha_ready(
                        domain, "Aile Profili", target_id, image, retry
                    )
                )
                if captcha_ready
                else None
            ),
            wait_for_captcha=(
                (lambda: wait_for_captcha("Aile Profili")) if wait_for_captcha else None
            ),
        )
        if _target_id(page) != target_id or not _is_family_url(page.url):
            raise RuntimeError("GüvenliNet sonucu sabit GüvenliNet target üzerinden alınmadı.")
        return result


def _run_independent_service_queues(
    domains: List[str],
    btk_target_id: str,
    family_target_id: str,
    progress: Optional[ProgressCallback] = None,
    captcha_ready: Optional[CaptchaCallback] = None,
    wait_for_captcha: Optional[CaptchaWaiter] = None,
    stop_requested: Optional[StopRequested] = None,
    btk_runner: ServiceRunner = _run_btk_target,
    family_runner: ServiceRunner = _run_family_target,
) -> List[BatchDomainResult]:
    results = [BatchDomainResult(domain=domain) for domain in domains]
    total_steps = len(domains) * 2
    completed_steps = 0
    progress_lock = Lock()

    def completed_count() -> int:
        with progress_lock:
            return completed_steps

    def finish_step(domain: str, service: str, status: str) -> None:
        nonlocal completed_steps
        with progress_lock:
            completed_steps += 1
            current = completed_steps
        _notify(progress, current, total_steps, domain, service, status)

    def run_service_queue(
        service: str, target_id: str, runner: ServiceRunner
    ) -> None:
        for item in results:
            if stop_requested is not None and stop_requested():
                break
            domain = item.domain
            _notify(
                progress,
                completed_count(),
                total_steps,
                domain,
                service,
                "Kontrol ediliyor",
            )
            if stop_requested is not None and stop_requested():
                break
            try:
                result = runner(
                    target_id,
                    domain,
                    captcha_ready,
                    wait_for_captcha,
                )
            except (PlaywrightError, RuntimeError, ValueError) as exc:
                status = f"Hata: {exc}"
            else:
                status = "Tamamlandı"
                if service == "BTK":
                    item.btk_result = result
                    item.btk_screenshot_path = result.screenshot_path
                    item.btk_screenshot_ok = result.screenshot_path.is_file()
                    if not item.btk_screenshot_ok:
                        status = "Eksik screenshot"
                else:
                    item.family_result = result
                    item.family_screenshot_path = result.screenshot_path
                    item.family_screenshot_ok = result.screenshot_path.is_file()
                    if not item.family_screenshot_ok:
                        status = "Eksik screenshot"

            if service == "BTK":
                item.btk_status = status
            else:
                item.family_status = status
            finish_step(domain, service, status)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(
                run_service_queue, "BTK", btk_target_id, btk_runner
            ),
            executor.submit(
                run_service_queue,
                "Aile Profili",
                family_target_id,
                family_runner,
            ),
        )
        for future in as_completed(futures):
            future.result()
    return results


def run_batch(
    values: DomainInput,
    progress: Optional[ProgressCallback] = None,
    captcha_ready: Optional[CaptchaCallback] = None,
    wait_for_captcha: Optional[CaptchaWaiter] = None,
    target_ready: Optional[TargetCallback] = None,
    stop_requested: Optional[StopRequested] = None,
) -> List[BatchDomainResult]:
    domains = normalize_domains(values)
    if not domains:
        raise ValueError("En az bir geçerli domain girin.")
    if stop_requested is not None and stop_requested():
        return []

    try:
        requests.get(f"{CDP_URL}/json", timeout=5).raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            "Opera GX VPN açık gizli pencere ile hazır değil. "
            "Opera'yı açıp VPN'i aktif ettikten sonra tekrar deneyin."
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(CDP_URL, timeout=10_000)
        page = _find_existing_opera_page(browser)
        if page is None or not is_private_opera_page(browser, page):
            raise RuntimeError("Mevcut Opera GX Private BTK/GüvenliNet sayfası bulunamadı.")
        verify_opera_vpn(page)
        btk_page, family_page = _service_pages(browser)
        btk_target_id = _target_id(btk_page)
        family_target_id = _target_id(family_page)
        if target_ready is not None:
            target_ready("BTK", btk_target_id, btk_page.url)
            target_ready("GüvenliNet", family_target_id, family_page.url)
        return _run_independent_service_queues(
            domains,
            btk_target_id,
            family_target_id,
            progress,
            captcha_ready,
            wait_for_captcha,
            stop_requested,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Mevcut Opera VPN oturumunda toplu kontrol")
    parser.add_argument("domains", nargs="+")
    args = parser.parse_args()

    def print_progress(
        completed: int,
        total: int,
        domain: str,
        service: str,
        stage: str,
    ) -> None:
        print(f"[{completed}/{total}] {domain} {service}: {stage}", flush=True)

    try:
        results = run_batch(args.domains, print_progress)
    except (PlaywrightError, requests.RequestException, RuntimeError, ValueError) as exc:
        print(f"Hata: {exc}")
        return 1

    for item in results:
        print(f"{item.domain} | BTK: {item.btk_status} | Aile Profili: {item.family_status}")
        if item.btk_screenshot_path:
            print(f"  BTK screenshot: {item.btk_screenshot_path}")
        if item.family_screenshot_path:
            print(f"  Aile Profili screenshot: {item.family_screenshot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
