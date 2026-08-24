import argparse
import ipaddress
import json
from queue import Empty, Queue
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from typing import Optional
from urllib.parse import urlsplit

import requests
from playwright.sync_api import Error as PlaywrightError, Page, TimeoutError, sync_playwright
from .output_settings import screenshot_path
from .result_capture import capture_btk_result


CDP_URL = "http://127.0.0.1:9222"
BTK_URL = "https://internet.btk.gov.tr/sitesorgu/"
IP_CHECK_URLS = (
    "https://api.ipify.org?format=json",
    "https://ipwho.is/",
    "https://ifconfig.me/ip",
)


@dataclass
class BtkOperatorResult:
    domain: str
    result_text: str
    screenshot_path: Path


def normalize_domain(value: str) -> str:
    candidate = value.strip()
    parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
    if not parsed.hostname:
        raise ValueError("Geçerli bir domain girin.")
    return parsed.hostname.rstrip(".").lower()


def _extract_ip(payload: str) -> str:
    try:
        value = json.loads(payload)
        candidate = str(value.get("ip", "")) if isinstance(value, dict) else ""
    except json.JSONDecodeError:
        candidate = payload.strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return ""


def _direct_ip_with_deadline(url: str, seconds: float = 5) -> str:
    result: Queue[str] = Queue(maxsize=1)

    def request_ip() -> None:
        try:
            response = requests.get(url, timeout=seconds)
            response.raise_for_status()
            result.put_nowait(_extract_ip(response.text))
        except (requests.RequestException, ValueError):
            result.put_nowait("")

    worker = Thread(target=request_ip, daemon=True)
    worker.start()
    try:
        return result.get(timeout=seconds)
    except Empty:
        return ""


def verify_opera_vpn(page: Page) -> str:
    matched_direct_ip = False
    for url in IP_CHECK_URLS:
        try:
            direct_ip = _direct_ip_with_deadline(url)
            if not direct_ip:
                continue
            opera_text = page.evaluate(
                """
                async ({url, timeoutMs}) => {
                    const controller = new AbortController();
                    const timer = setTimeout(() => controller.abort(), timeoutMs);
                    try {
                        const response = await fetch(url, {
                            cache: 'no-store',
                            signal: controller.signal,
                        });
                        if (!response.ok) throw new Error(`HTTP ${response.status}`);
                        return await response.text();
                    } finally {
                        clearTimeout(timer);
                    }
                }
                """,
                {"url": url, "timeoutMs": 5_000},
            )
            opera_ip = _extract_ip(opera_text)
        except (PlaywrightError, requests.RequestException):
            continue
        if not direct_ip or not opera_ip:
            continue
        if opera_ip != direct_ip:
            return opera_ip
        matched_direct_ip = True

    if matched_direct_ip:
        raise RuntimeError("Opera GX VPN aktif görünmüyor; kontrol durduruldu.")
    raise RuntimeError("Opera VPN çıkış IP'si otomatik doğrulanamadı.")


def find_btk_page(browser) -> Optional[Page]:
    pages = [page for context in browser.contexts for page in context.pages]
    return next(
        (page for page in reversed(pages) if page.url.rstrip("/") == BTK_URL.rstrip("/")),
        None,
    )


def wait_for_captcha_entry(page: Page, timeout_seconds: int = 300) -> bool:
    print("CAPTCHA'yı mevcut Opera BTK sekmesinde girin; sorgu otomatik gönderilecek.", flush=True)
    try:
        page.wait_for_function(
            """
            () => {
                const code = document.querySelector('#security_code');
                const result = document.querySelector('#sonuc')?.innerText || '';
                return code?.value.trim().length === 6 ||
                    (result.includes('Site Bilgileri') && result.includes('İlgili Kararlar'));
            }
            """,
            timeout=timeout_seconds * 1000,
        )
    except TimeoutError as exc:
        raise RuntimeError(
            f"BTK CAPTCHA {timeout_seconds} saniye içinde girilmedi."
        ) from exc
    return bool(result_text(page))


def submit_query(page: Page) -> None:
    try:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
            page.evaluate(
                """
                () => {
                    const submit = document.querySelector('#submit1');
                    if (!submit) throw new Error('BTK submit elementi bulunamadı');
                    submit.click();
                }
                """
            )
    except TimeoutError:
        if page.url.rstrip("/") != BTK_URL.rstrip("/"):
            raise


def result_text(page: Page) -> str:
    decision = page.locator("#sonuc span.yazi2_2")
    if decision.count():
        text = (decision.first.text_content() or "").strip()
        if text:
            return text

    area = page.locator("#sonuc")
    text = (
        area.evaluate(
            """
            element => {
                const clone = element.cloneNode(true);
                clone.querySelectorAll('script, style, template, noscript').forEach(
                    node => node.remove()
                );
                return clone.textContent.trim();
            }
            """
        )
        if area.count()
        else ""
    )
    if "Site Bilgileri" in text and "İlgili Kararlar" in text:
        return text
    if text and "Genel Açıklamalar" not in text:
        return text
    return ""


def save_result_screenshot(page: Page, domain: str) -> Path:
    output = screenshot_path(domain, "BTK")
    return capture_btk_result(page, output)


def set_input_value(page: Page, selector: str, value: str) -> None:
    page.evaluate(
        """
        ({selector, value}) => {
            const element = document.querySelector(selector);
            if (!element) throw new Error(`BTK input bulunamadı: ${selector}`);
            element.value = value;
            element.dispatchEvent(new Event('input', {bubbles: true}));
            element.dispatchEvent(new Event('change', {bubbles: true}));
        }
        """,
        {"selector": selector, "value": value},
    )


def check_domain(domain: str) -> BtkOperatorResult:
    normalized = normalize_domain(domain)
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
        verify_opera_vpn(page)
        if result_text(page):
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
                locator.wait_for(state="attached", timeout=10_000)
            except TimeoutError as exc:
                raise RuntimeError(f"BTK {name} bulunamadı.") from exc

        set_input_value(page, "#deger", normalized)
        set_input_value(page, "#security_code", "")
        print(f"BTK hazır; domain forma yazıldı: {normalized}", flush=True)

        result_ready = wait_for_captcha_entry(page)
        if not result_ready:
            submit_query(page)
            page.wait_for_load_state("load", timeout=30_000)
        page.locator("#sonuc").wait_for(state="attached", timeout=30_000)
        text = result_text(page)
        if not text:
            raise RuntimeError("BTK sonucu oluşmadı; CAPTCHA kabul edilmemiş olabilir.")

        screenshot = save_result_screenshot(page, normalized)
        return BtkOperatorResult(normalized, text, screenshot)


def main() -> int:
    parser = argparse.ArgumentParser(description="Mevcut Opera CDP oturumunda BTK sorgusu")
    parser.add_argument("domain")
    args = parser.parse_args()
    try:
        result = check_domain(args.domain)
    except (PlaywrightError, RuntimeError, ValueError) as exc:
        print(f"Hata: {exc}")
        return 1
    print(f"BTK sonucu: {result.result_text}")
    print(f"Screenshot: {result.screenshot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
