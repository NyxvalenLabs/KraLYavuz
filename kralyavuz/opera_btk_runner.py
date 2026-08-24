import argparse
from typing import Callable, Optional

import requests
from playwright.sync_api import Error as PlaywrightError, Page, TimeoutError, sync_playwright

from .btk_operator import (
    BTK_URL,
    CDP_URL,
    BtkOperatorResult,
    find_btk_page,
    normalize_domain,
    result_text,
    save_result_screenshot,
    set_input_value,
    submit_query,
    verify_opera_vpn,
    wait_for_captcha_entry,
)
from .captcha_capture import capture_captcha_image


CaptchaCallback = Callable[[bytes, bool], None]
CaptchaWaiter = Callable[[], Optional[str]]


def _query_value(value: str) -> str:
    query = value.strip()
    normalize_domain(query)
    return query


def _fill_btk_captcha(page: Page, code: str) -> None:
    selector = "#security_code"
    captcha_input = page.locator(selector)
    exists = captcha_input.count() == 1
    before = captcha_input.input_value() if exists else ""
    print(
        f"BTK URL: {page.url} | CAPTCHA selector: {selector} | "
        f"mevcut: {exists} | önceki değer: {before!r}",
        flush=True,
    )
    if not exists:
        raise RuntimeError("BTK CAPTCHA input elementi bulunamadı.")

    set_input_value(page, selector, "")
    set_input_value(page, selector, code)
    current = page.locator(selector)
    value = current.input_value()

    print(f"BTK CAPTCHA submit öncesi değer: {value!r}", flush=True)
    if value != code:
        raise RuntimeError("BTK CAPTCHA kodu input alanına aktarılamadı.")


def prepare_btk_page(page: Page, domain: str) -> bytes:
    query = _query_value(domain)
    page.goto(BTK_URL, wait_until="domcontentloaded", timeout=30_000)

    domain_input = page.locator("#deger")
    captcha_image = page.locator("#security_code_image")
    captcha_input = page.locator("#security_code")
    submit = page.locator("#submit1")
    for locator, name in (
        (domain_input, "domain alanı"),
        (captcha_image, "CAPTCHA"),
        (captcha_input, "CAPTCHA giriş alanı"),
        (submit, "sorgu butonu"),
    ):
        try:
            locator.wait_for(state="attached", timeout=15_000)
        except TimeoutError as exc:
            raise RuntimeError(f"BTK {name} bulunamadı.") from exc

    set_input_value(page, "#deger", query)
    set_input_value(page, "#security_code", "")
    page.evaluate("document.title = 'KraLYavuz - BTK'")
    return capture_captcha_image(captcha_image, allow_element_screenshot=False)


def is_private_opera_page(browser, page: Page) -> bool:
    browser_session = browser.new_browser_cdp_session()
    try:
        default_context_id = browser_session.send("Target.getBrowserContexts").get(
            "defaultBrowserContextId", ""
        )
    finally:
        browser_session.detach()

    page_session = page.context.new_cdp_session(page)
    try:
        target_info = page_session.send("Target.getTargetInfo").get("targetInfo", {})
    finally:
        page_session.detach()

    context_id = str(target_info.get("browserContextId", ""))
    user_agent = page.evaluate("navigator.userAgent")
    return bool(context_id and context_id != default_context_id and "OPR/" in user_agent)


def check_btk_in_session(
    browser,
    page: Page,
    domain: str,
    verify_session: bool = True,
    captcha_ready: Optional[CaptchaCallback] = None,
    wait_for_captcha: Optional[CaptchaWaiter] = None,
    prepared: bool = False,
    initial_captcha_code: Optional[str] = None,
) -> BtkOperatorResult:
    normalized = normalize_domain(domain)
    query = _query_value(domain)
    embedded_captcha = captcha_ready is not None and wait_for_captcha is not None
    if verify_session:
        if not is_private_opera_page(browser, page):
            raise RuntimeError("BTK sayfası Opera GX Private Window içinde değil; kontrol durduruldu.")
        opera_ip = verify_opera_vpn(page)
        print(f"Mevcut Opera GX Private VPN oturumuna bağlanıldı: {opera_ip}", flush=True)

    if not prepared:
        while True:
            try:
                prepare_btk_page(page, query)
                break
            except (PlaywrightError, RuntimeError):
                if not embedded_captcha:
                    raise
                captcha_ready(b"", True)
                if wait_for_captcha() is None:
                    raise RuntimeError("BTK CAPTCHA girişi iptal edildi.")

    domain_input = page.locator("#deger")
    captcha_image = page.locator("#security_code_image")
    captcha_input = page.locator("#security_code")
    submit = page.locator("#submit1")
    retry = False
    pending_code = initial_captcha_code
    while True:
        if pending_code is not None:
            _fill_btk_captcha(page, pending_code)
            pending_code = None
            result_ready = False
        elif embedded_captcha:
            captcha_ready(
                capture_captcha_image(
                    captcha_image, allow_element_screenshot=False
                ),
                retry,
            )
            code = wait_for_captcha()
            if code is None:
                raise RuntimeError("BTK CAPTCHA girişi iptal edildi.")
            if not code.strip():
                prepare_btk_page(page, domain)
                domain_input = page.locator("#deger")
                captcha_image = page.locator("#security_code_image")
                captcha_input = page.locator("#security_code")
                submit = page.locator("#submit1")
                retry = True
                continue
            _fill_btk_captcha(page, code)
            result_ready = False
        else:
            result_ready = wait_for_captcha_entry(page)
        if not result_ready:
            try:
                submit_query(page)
                page.wait_for_load_state("load", timeout=30_000)
            except PlaywrightError:
                pass

        if "internet.btk.gov.tr/sitesorgu" not in page.url:
            raise RuntimeError("BTK submit sonrası target URL değişti.")
        text = result_text(page)
        if text:
            page.evaluate("document.title = 'KraLYavuz - BTK'")
            break
        retry = True
        print("Sonuç oluşmadı; yeni CAPTCHA girişini bekliyor.", flush=True)
        prepare_btk_page(page, query)
        domain_input = page.locator("#deger")
        captcha_image = page.locator("#security_code_image")
        captcha_input = page.locator("#security_code")
        submit = page.locator("#submit1")
        domain_input.wait_for(state="attached", timeout=15_000)
        captcha_input.wait_for(state="attached", timeout=15_000)
        set_input_value(page, "#deger", query)
        set_input_value(page, "#security_code", "")

    screenshot = save_result_screenshot(page, domain)
    return BtkOperatorResult(normalized, text, screenshot)


def run_private_check(domain: str) -> BtkOperatorResult:
    try:
        requests.get(f"{CDP_URL}/json", timeout=5).raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            "Opera GX VPN açık gizli pencere ile hazır değil. "
            "Opera'yı açıp VPN'i aktif ettikten sonra tekrar deneyin."
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(CDP_URL, timeout=10_000)
        page = find_btk_page(browser)
        if page is None:
            raise RuntimeError("Mevcut Opera oturumunda açık BTK sayfası bulunamadı.")
        return check_btk_in_session(browser, page, domain)


def main() -> int:
    parser = argparse.ArgumentParser(description="Opera GX gizli pencere BTK runner testi")
    parser.add_argument("domain")
    args = parser.parse_args()
    try:
        result = run_private_check(args.domain)
    except (PlaywrightError, requests.RequestException, RuntimeError, ValueError) as exc:
        print(f"Hata: {exc}")
        return 1
    print(f"BTK sonucu: {result.result_text}")
    print(f"Screenshot: {result.screenshot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
