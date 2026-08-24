from typing import Callable, Optional

import requests
from playwright.sync_api import sync_playwright

from .btk_operator import CDP_URL
from .btk_checker import BtkResult, check_btk
from .guvenlinet_checker import (
    GUVENLINET_URL,
    GuvenliNetResult,
    _find_existing_opera_page,
    check_guvenlinet_page,
)
from .opera_btk_runner import is_private_opera_page


ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str], None]
CaptchaCallback = Callable[[bytes, bool], None]
CaptchaWaiter = Callable[[], Optional[str]]
OPERA_NOT_READY = (
    "Opera GX VPN açık gizli pencere ile hazır değil. "
    "Opera'yı açıp VPN'i aktif ettikten sonra tekrar deneyin."
)


def _require_cdp() -> None:
    try:
        requests.get(f"{CDP_URL}/json", timeout=5).raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(OPERA_NOT_READY) from exc


def run_btk_check(
    value: str,
    captcha_ready: CaptchaCallback,
    wait_for_captcha: CaptchaWaiter,
    progress: ProgressCallback,
    log: LogCallback,
) -> BtkResult:
    progress(15, "Opera bağlantısı bekleniyor")
    log("BTK kontrolü mevcut Opera GX VPN oturumunda başlatılıyor.")
    result = check_btk(value)
    progress(100, "Sonuç alındı")
    log(f"BTK screenshot oluşturuldu: {result.screenshot_path}")
    return result


def run_guvenlinet_check(
    value: str,
    captcha_ready: CaptchaCallback,
    wait_for_captcha: CaptchaWaiter,
    progress: ProgressCallback,
    log: LogCallback,
) -> GuvenliNetResult:
    _require_cdp()
    with sync_playwright() as playwright:
        progress(15, "Opera GX oturumuna bağlanılıyor")
        browser = playwright.chromium.connect_over_cdp(CDP_URL, timeout=10_000)
        page = _find_existing_opera_page(browser)
        if page is None or not is_private_opera_page(browser, page):
            raise RuntimeError(OPERA_NOT_READY)
        verify_opera_vpn(page)
        if page.url.rstrip("/") != GUVENLINET_URL.rstrip("/"):
            page.goto(GUVENLINET_URL, wait_until="domcontentloaded", timeout=30_000)
        return check_guvenlinet_page(
            page,
            value,
            captcha_ready,
            wait_for_captcha,
            progress,
            log,
        )
