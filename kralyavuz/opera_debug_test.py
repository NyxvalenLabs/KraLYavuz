import argparse
import json
import subprocess
import tempfile
import time
from datetime import datetime
from typing import Dict, Optional

import requests
from playwright.sync_api import Error as PlaywrightError, sync_playwright

from .platform_paths import find_opera_gx


BTK_URL = "https://internet.btk.gov.tr/sitesorgu/"
IP_URL = "https://api.ipify.org?format=json"


def direct_ip() -> str:
    try:
        response = requests.get(IP_URL, timeout=15)
        response.raise_for_status()
        return str(response.json().get("ip", ""))
    except (requests.RequestException, ValueError):
        return ""


def wait_for_debugger(port: int, timeout: float = 15) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=1)
            if response.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.25)
    return False


def page_ip(page) -> str:
    response = page.goto(IP_URL, wait_until="domcontentloaded", timeout=15_000)
    if not response or not response.ok:
        return ""
    try:
        return str(json.loads(page.locator("body").inner_text()).get("ip", ""))
    except (ValueError, json.JSONDecodeError):
        return ""


def wait_for_vpn(page, normal_ip: str, seconds: int) -> str:
    deadline = time.monotonic() + seconds
    current_ip = page_ip(page)
    while seconds and time.monotonic() < deadline and (not current_ip or current_ip == normal_ip):
        time.sleep(2)
        current_ip = page_ip(page)
    return current_ip


def run_test(port: int, vpn_wait_seconds: int) -> Dict[str, object]:
    executable = find_opera_gx()
    report: Dict[str, object] = {
        "tested_at": datetime.now().isoformat(),
        "opera_path": str(executable or ""),
        "remote_debugging_port": port,
        "debug_endpoint_ready": False,
        "playwright_connected": False,
        "normal_exit_ip": direct_ip(),
        "opera_exit_ip": "",
        "vpn_preserved": False,
        "btk_opened": False,
        "captcha_visible": False,
        "http_status": None,
        "load_seconds": None,
        "error": "",
    }
    if not executable:
        report["error"] = "Opera executable bulunamadı."
        return report

    with tempfile.TemporaryDirectory(prefix="kralyavuz-opera-debug-") as profile:
        process = subprocess.Popen(
            [
                str(executable),
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            report["debug_endpoint_ready"] = wait_for_debugger(port)
            if not report["debug_endpoint_ready"]:
                report["error"] = "Remote debugging endpoint açılmadı."
                return report

            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                report["playwright_connected"] = True
                try:
                    context = browser.contexts[0]
                    page = context.new_page()
                    opera_ip = wait_for_vpn(page, str(report["normal_exit_ip"]), vpn_wait_seconds)
                    report["opera_exit_ip"] = opera_ip
                    report["vpn_preserved"] = bool(
                        opera_ip and report["normal_exit_ip"] and opera_ip != report["normal_exit_ip"]
                    )

                    started = time.monotonic()
                    try:
                        response = page.goto(BTK_URL, wait_until="domcontentloaded", timeout=30_000)
                        report["load_seconds"] = time.monotonic() - started
                        if response:
                            report["http_status"] = response.status
                        report["captcha_visible"] = page.locator("#security_code_image").is_visible()
                        report["btk_opened"] = report["captcha_visible"]
                    except PlaywrightError as exc:
                        report["load_seconds"] = time.monotonic() - started
                        report["error"] = str(exc).splitlines()[0]
                finally:
                    browser.close()
        except (PlaywrightError, OSError) as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Opera GX remote debugging ve BTK bağlantı testi")
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument(
        "--wait-for-vpn",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Geçici Opera penceresinde VPN'in kullanıcı tarafından açılmasını bekler.",
    )
    args = parser.parse_args()
    print(json.dumps(run_test(args.port, args.wait_for_vpn), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
