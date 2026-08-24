import json
import time
from datetime import datetime
from typing import Dict, List

from playwright.sync_api import Error as PlaywrightError, Page, sync_playwright


CDP_URL = "http://127.0.0.1:9222"
BTK_URL = "https://internet.btk.gov.tr/sitesorgu/"
IP_URL = "https://api.ipify.org?format=json"


def page_summary(page: Page) -> Dict[str, str]:
    return {"url": page.url, "title": page.title()}


def opera_exit_ip(page: Page) -> str:
    payload = page.evaluate(
        """
        async (url) => {
            const response = await fetch(url, {cache: "no-store"});
            if (!response.ok) throw new Error(`IP endpoint HTTP ${response.status}`);
            return await response.json();
        }
        """,
        IP_URL,
    )
    return str(payload.get("ip", "")) if isinstance(payload, dict) else ""


def run_test() -> Dict[str, object]:
    report: Dict[str, object] = {
        "tested_at": datetime.now().isoformat(),
        "cdp_url": CDP_URL,
        "cdp_connected": False,
        "context_count": 0,
        "pages": [],
        "btk_page_found": False,
        "found_page": {},
        "user_agent": "",
        "exit_ip": "",
        "new_tab": {
            "opened": False,
            "url": "",
            "title": "",
            "http_status": None,
            "load_seconds": None,
            "form_visible": False,
            "captcha_visible": False,
            "error": "",
        },
        "error": "",
    }

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(CDP_URL, timeout=10_000)
            report["cdp_connected"] = True
            contexts = browser.contexts
            report["context_count"] = len(contexts)
            pages: List[Page] = [page for context in contexts for page in context.pages]
            report["pages"] = [page_summary(page) for page in pages]

            btk_page = next(
                (page for page in pages if page.url.rstrip("/") == BTK_URL.rstrip("/")),
                None,
            )
            if btk_page is None:
                report["error"] = "Mevcut Opera oturumunda açık BTK sayfası bulunamadı."
                return report

            report["btk_page_found"] = True
            report["found_page"] = page_summary(btk_page)
            report["user_agent"] = btk_page.evaluate("navigator.userAgent")
            report["exit_ip"] = opera_exit_ip(btk_page)

            context = btk_page.context
            test_page = context.new_page()
            new_tab = report["new_tab"]
            new_tab["opened"] = True
            started = time.monotonic()
            try:
                response = test_page.goto(BTK_URL, wait_until="domcontentloaded", timeout=30_000)
                new_tab["load_seconds"] = time.monotonic() - started
                new_tab["url"] = test_page.url
                new_tab["title"] = test_page.title()
                if response:
                    new_tab["http_status"] = response.status
                form = test_page.locator("#deger")
                captcha = test_page.locator("#security_code_image")
                form.wait_for(state="visible", timeout=10_000)
                captcha.wait_for(state="visible", timeout=10_000)
                new_tab["form_visible"] = form.is_visible()
                new_tab["captcha_visible"] = captcha.is_visible()
            except PlaywrightError as exc:
                new_tab["load_seconds"] = time.monotonic() - started
                new_tab["url"] = test_page.url
                new_tab["error"] = str(exc).splitlines()[0]
            finally:
                test_page.close()
        except PlaywrightError as exc:
            report["error"] = str(exc).splitlines()[0]

    return report


def main() -> int:
    print(json.dumps(run_test(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
