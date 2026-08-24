import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

import requests
from playwright.sync_api import Error as PlaywrightError, Page, TimeoutError, sync_playwright

from .btk_operator import verify_opera_vpn
from .captcha_capture import capture_captcha_image
from .opera_btk_runner import is_private_opera_page
from .output_settings import screenshot_path
from .result_capture import capture_result_element


GUVENLINET_URL = "https://www.guvenlinet.org.tr/sorgula"
CDP_URL = "http://127.0.0.1:9222"
BTK_URL = "https://internet.btk.gov.tr/sitesorgu/"

CaptchaCallback = Callable[[bytes, bool], None]
CaptchaWaiter = Callable[[], Optional[str]]
ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str], None]


@dataclass
class GuvenliNetResult:
    summary: str
    text: str
    screenshot_path: Path


class GuvenliNetCancelled(Exception):
    pass


def extract_domain(value: str) -> str:
    candidate = value.strip()
    parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
    domain = parsed.hostname
    if not domain:
        raise ValueError("Geçerli bir URL veya domain girin.")
    return domain.rstrip(".").lower()


def _profile_value(result_text: str, label: str) -> str:
    lines = [line.strip() for line in result_text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if label.lower() not in line.lower():
            continue
        if ":" in line and line.split(":", 1)[1].strip():
            return line.split(":", 1)[1].strip()
        if index + 1 < len(lines):
            return lines[index + 1]
        return line
    return "Bilinmiyor"


def _is_profile_result(text: str) -> bool:
    normalized = " ".join(text.split()).lower()
    return "aile profili" in normalized and "çocuk profili" in normalized


def _find_existing_opera_page(browser) -> Optional[Page]:
    pages = [page for context in browser.contexts for page in context.pages]
    return next(
        (
            page
            for page in reversed(pages)
            if page.url.rstrip("/") in {GUVENLINET_URL.rstrip("/"), BTK_URL.rstrip("/")}
        ),
        None,
    )


def prepare_guvenlinet_page(page: Page, value: str) -> bytes:
    domain = extract_domain(value)
    query = value.strip()
    response = page.goto(GUVENLINET_URL, wait_until="domcontentloaded", timeout=30_000)
    if response is None or not response.ok:
        status = response.status if response is not None else "yanıt yok"
        raise RuntimeError(f"GüvenliNet açılamadı: HTTP {status}")

    domain_input = page.locator("input#domain_name")
    captcha_image = page.locator("#captcha")
    captcha_input = page.locator("#security_code")
    submit = page.locator("#sorgula")
    for locator, name in (
        (domain_input, "domain alanı"),
        (captcha_image, "güvenlik kodu"),
        (captcha_input, "güvenlik kodu giriş alanı"),
        (submit, "sorgu butonu"),
    ):
        try:
            locator.wait_for(state="visible", timeout=15_000)
        except TimeoutError as exc:
            raise RuntimeError(f"GüvenliNet {name} bulunamadı.") from exc
    page.locator("#domain_result").wait_for(state="attached", timeout=15_000)
    domain_input.fill(query)
    captcha_input.fill("")
    page.evaluate("document.title = 'KraLYavuz - GüvenliNet'")
    return capture_captcha_image(captcha_image)


def check_guvenlinet_in_session(
    browser,
    page: Page,
    value: str,
    verify_session: bool = True,
    captcha_ready: Optional[CaptchaCallback] = None,
    wait_for_captcha: Optional[CaptchaWaiter] = None,
    prepared: bool = False,
    initial_captcha_code: Optional[str] = None,
) -> GuvenliNetResult:
    domain = extract_domain(value)
    embedded_captcha = captcha_ready is not None and wait_for_captcha is not None
    if verify_session:
        if not is_private_opera_page(browser, page):
            raise RuntimeError(
                "GüvenliNet sayfası Opera GX Private Window içinde değil; kontrol durduruldu."
            )
        verify_opera_vpn(page)
    if not prepared:
        while True:
            try:
                prepare_guvenlinet_page(page, value)
                break
            except (PlaywrightError, RuntimeError):
                if not embedded_captcha:
                    raise
                captcha_ready(b"", True)
                if wait_for_captcha() is None:
                    raise RuntimeError("GüvenliNet güvenlik kodu girişi iptal edildi.")

    domain_input = page.locator("input#domain_name")
    captcha_image = page.locator("#captcha")
    captcha_input = page.locator("#security_code")
    submit = page.locator("#sorgula")
    result_area = page.locator("#domain_result")
    previous_html = result_area.inner_html()
    retry = False
    pending_code = initial_captcha_code
    while True:
        if pending_code is not None:
            captcha_input.fill(pending_code)
            pending_code = None
            submit.click()
        elif captcha_ready is not None and wait_for_captcha is not None:
            captcha_ready(capture_captcha_image(captcha_image), retry)
            code = wait_for_captcha()
            if code is None:
                raise RuntimeError("GüvenliNet güvenlik kodu girişi iptal edildi.")
            if not code.strip():
                prepare_guvenlinet_page(page, value)
                domain_input = page.locator("input#domain_name")
                captcha_image = page.locator("#captcha")
                captcha_input = page.locator("#security_code")
                submit = page.locator("#sorgula")
                result_area = page.locator("#domain_result")
                previous_html = result_area.inner_html()
                retry = True
                continue
            captcha_input.fill(code)
            submit.click()
        else:
            print(
                "Opera GüvenliNet sekmesinde güvenlik kodunu girip Sorgula düğmesine basın.",
                flush=True,
            )
        page.wait_for_function(
            """
            previous => {
                const area = document.querySelector('#domain_result');
                if (!area || area.innerHTML === previous) return false;
                const text = area.innerText.trim();
                return text.length > 0 && !area.querySelector('img[src*="loader"]');
            }
            """,
            arg=previous_html,
            timeout=0,
        )
        result_text = result_area.inner_text().strip()
        if _is_profile_result(result_text):
            if "guvenlinet.org.tr/sorgula" not in page.url:
                raise RuntimeError("GüvenliNet sonucu yanlış target URL üzerinden alındı.")
            page.evaluate("document.title = 'KraLYavuz - GüvenliNet'")
            break

        retry = True
        print(f"GüvenliNet sonucu oluşmadı: {result_text or 'güvenlik kodu kabul edilmedi'}", flush=True)
        page.goto(GUVENLINET_URL, wait_until="domcontentloaded", timeout=30_000)
        prepare_guvenlinet_page(page, value)
        domain_input = page.locator("input#domain_name")
        captcha_image = page.locator("#captcha")
        captcha_input = page.locator("#security_code")
        submit = page.locator("#sorgula")
        result_area = page.locator("#domain_result")
        previous_html = result_area.inner_html()

    family = _profile_value(result_text, "Aile Profili")
    child = _profile_value(result_text, "Çocuk Profili")
    summary = f"Aile Profili: {family} | Çocuk Profili: {child}"
    output_path = screenshot_path(value, "AileProfili")
    capture_result_element(
        page,
        "#domain_result",
        output_path,
        crop_before_text="Sizce bu alan adının profili nasıl olmalı?",
    )
    return GuvenliNetResult(summary, result_text, output_path)


def run_opera_guvenlinet_check(value: str) -> GuvenliNetResult:
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
        if page is None:
            raise RuntimeError("Mevcut Opera oturumunda BTK/GüvenliNet sayfası bulunamadı.")
        return check_guvenlinet_in_session(browser, page, value)


def check_guvenlinet_page(
    page: Page,
    value: str,
    captcha_ready: CaptchaCallback,
    wait_for_captcha: CaptchaWaiter,
    progress: ProgressCallback,
    log: LogCallback,
) -> GuvenliNetResult:
    domain = extract_domain(value)
    query = value.strip()
    log(f"GüvenliNet sorgusu hazırlanıyor: {domain}")
    progress(25, "GüvenliNet: Kontrol ediliyor")
    page.goto(GUVENLINET_URL, wait_until="domcontentloaded", timeout=30_000)

    domain_input = page.locator("#domain_name")
    captcha_image = page.locator("#captcha")
    domain_input.wait_for(state="visible", timeout=15_000)
    captcha_image.wait_for(state="visible", timeout=15_000)
    domain_input.fill(query)

    retry = False
    while True:
        progress(45, "GüvenliNet: Güvenlik kodu bekleniyor")
        captcha_ready(capture_captcha_image(captcha_image), retry)
        code = wait_for_captcha()
        if code is None:
            raise GuvenliNetCancelled

        progress(65, "GüvenliNet: Sorgu gönderiliyor")
        page.locator("#security_code").fill(code)
        page.locator("#sorgula").click()

        result_area = page.locator("#domain_result")
        result_area.wait_for(state="attached", timeout=15_000)
        page.wait_for_function(
            "document.querySelector('#domain_result').innerText.trim().length > 0",
            timeout=30_000,
        )
        result_text = result_area.inner_text().strip()
        if "Güvenlik kodunu yanlış girdiniz." in result_text:
            retry = True
            log(result_text)
            captcha_image.wait_for(state="visible", timeout=15_000)
            domain_input.fill(query)
            continue
        break

    family = _profile_value(result_text, "Aile Profili")
    child = _profile_value(result_text, "Çocuk Profili")
    summary = f"Aile Profili: {family} | Çocuk Profili: {child}"

    output_path = screenshot_path(domain, "AileProfili")

    progress(85, "GüvenliNet: Sonuç kaydediliyor")
    capture_result_element(
        page,
        "#domain_result",
        output_path,
        crop_before_text="Sizce bu alan adının profili nasıl olmalı?",
    )

    log(f"GüvenliNet sonucu: {result_text or 'Sonuç metni bulunamadı.'}")
    log(summary)
    log(f"Screenshot oluşturuldu: {output_path}")
    progress(100, "GüvenliNet: Tamamlandı")
    return GuvenliNetResult(summary, result_text, output_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Mevcut Opera VPN oturumunda GüvenliNet sorgusu")
    parser.add_argument("domain")
    args = parser.parse_args()
    try:
        result = run_opera_guvenlinet_check(args.domain)
    except (PlaywrightError, requests.RequestException, RuntimeError, ValueError) as exc:
        print(f"Hata: {exc}")
        return 1
    print(f"GüvenliNet sonucu: {result.summary}")
    print(f"Screenshot: {result.screenshot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
