import argparse
import json
import shutil
import socket
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from playwright.sync_api import BrowserType, Error as PlaywrightError, Page, sync_playwright

from .output_settings import get_output_dir
from .platform_paths import find_opera_gx, opera_gx_profile_dir


BTK_URL = "https://internet.btk.gov.tr/sitesorgu/"
IP_CHECK_URL = "https://api.ipify.org?format=json"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)


@dataclass
class ConnectionResult:
    method: str
    dns_addresses: List[str]
    remote_ip: str = ""
    http_status: Optional[int] = None
    first_byte_seconds: Optional[float] = None
    total_seconds: Optional[float] = None
    error_type: str = ""
    error: str = ""
    exit_ip: str = ""
    vpn_state: str = "unknown"


def resolve_dns() -> List[str]:
    records = socket.getaddrinfo("internet.btk.gov.tr", 443, type=socket.SOCK_STREAM)
    return sorted({record[4][0] for record in records})


def requests_test(dns_addresses: List[str]) -> ConnectionResult:
    result = ConnectionResult("requests", dns_addresses)
    started = time.monotonic()
    try:
        with requests.get(
            BTK_URL,
            headers={"User-Agent": BROWSER_USER_AGENT, "Accept": "text/html,*/*"},
            timeout=30,
            stream=True,
        ) as response:
            result.first_byte_seconds = time.monotonic() - started
            result.http_status = response.status_code
            result.remote_ip = _requests_remote_ip(response)
            for _chunk in response.iter_content(chunk_size=64 * 1024):
                pass
    except requests.RequestException as exc:
        result.error_type = type(exc).__name__
        result.error = str(exc)
    result.total_seconds = time.monotonic() - started
    return result


def _requests_remote_ip(response: requests.Response) -> str:
    try:
        return response.raw._connection.sock.getpeername()[0]
    except (AttributeError, OSError, TypeError):
        return ""


def _page_test(
    page: Page,
    method: str,
    dns_addresses: List[str],
    vpn_state: str = "unknown",
) -> ConnectionResult:
    result = ConnectionResult(method, dns_addresses, vpn_state=vpn_state)
    try:
        ip_response = page.goto(IP_CHECK_URL, wait_until="domcontentloaded", timeout=15_000)
        if ip_response and ip_response.ok:
            payload = json.loads(page.locator("body").inner_text())
            result.exit_ip = str(payload.get("ip", ""))
    except (PlaywrightError, ValueError, json.JSONDecodeError):
        pass

    started = time.monotonic()
    first_response: Dict[str, object] = {}

    def capture_response(response) -> None:
        if response.request.resource_type == "document" and response.url.startswith(BTK_URL):
            first_response.setdefault("time", time.monotonic())
            first_response.setdefault("status", response.status)
            try:
                first_response.setdefault("ip", response.server_addr().get("ipAddress", ""))
            except PlaywrightError:
                pass

    page.on("response", capture_response)
    try:
        response = page.goto(BTK_URL, wait_until="load", timeout=30_000)
        if response:
            result.http_status = response.status
            try:
                result.remote_ip = response.server_addr().get("ipAddress", "")
            except PlaywrightError:
                pass
    except PlaywrightError as exc:
        result.error_type = type(exc).__name__
        result.error = str(exc).splitlines()[0]
    result.total_seconds = time.monotonic() - started
    if first_response:
        result.first_byte_seconds = float(first_response["time"]) - started
        result.http_status = int(first_response["status"])
        result.remote_ip = str(first_response.get("ip", result.remote_ip))
    return result


def chromium_test(
    chromium: BrowserType,
    dns_addresses: List[str],
    headless: bool,
) -> ConnectionResult:
    browser = chromium.launch(headless=headless)
    try:
        page = browser.new_page(user_agent=BROWSER_USER_AGENT)
        mode = "headless" if headless else "visible"
        return _page_test(page, f"chromium_{mode}", dns_addresses, "off")
    finally:
        browser.close()


def find_opera() -> Tuple[Optional[Path], Optional[Path]]:
    executable = find_opera_gx()
    profile_candidate = opera_gx_profile_dir()
    profile = Path(profile_candidate) if profile_candidate and Path(profile_candidate).is_dir() else None
    return executable, profile


def _copy_opera_profile(source: Path, destination: Path) -> None:
    ignored = shutil.ignore_patterns(
        "Singleton*",
        "Cache",
        "Code Cache",
        "GPUCache",
        "DawnGraphiteCache",
        "DawnWebGPUCache",
        "GrShaderCache",
        "GraphiteDawnCache",
        "Safe Browsing",
        "Service Worker",
        "Sessions",
    )
    shutil.copytree(source, destination, dirs_exist_ok=True, ignore=ignored)


def opera_test(
    chromium: BrowserType,
    executable: Path,
    dns_addresses: List[str],
    profile_source: Optional[Path],
    copy_profile: bool,
) -> ConnectionResult:
    with tempfile.TemporaryDirectory(prefix="kralyavuz-opera-") as temp_dir:
        user_data_dir = Path(temp_dir) / "profile"
        label = "opera_profile_copy" if copy_profile else "opera_fresh_profile"
        expected_vpn = "profile-copy" if copy_profile else "off"
        if copy_profile and profile_source:
            _copy_opera_profile(profile_source, user_data_dir)
        else:
            user_data_dir.mkdir()
        try:
            context = chromium.launch_persistent_context(
                str(user_data_dir),
                executable_path=str(executable),
                headless=False,
                args=["--no-first-run", "--disable-session-crashed-bubble"],
            )
        except PlaywrightError as exc:
            return ConnectionResult(
                label,
                dns_addresses,
                error_type=type(exc).__name__,
                error=str(exc).splitlines()[0],
                vpn_state=expected_vpn,
            )
        try:
            page = context.new_page()
            return _page_test(page, label, dns_addresses, expected_vpn)
        finally:
            context.close()


def classify_opera_vpn(results: List[ConnectionResult]) -> None:
    fresh = next((item for item in results if item.method == "opera_fresh_profile"), None)
    copied = next((item for item in results if item.method == "opera_profile_copy"), None)
    if fresh:
        fresh.vpn_state = "off"
    if not copied:
        return
    if copied.exit_ip and fresh and fresh.exit_ip and copied.exit_ip != fresh.exit_ip:
        copied.vpn_state = "on (exit IP differs)"
    else:
        copied.vpn_state = "not active in copied profile"


def main() -> int:
    parser = argparse.ArgumentParser(description="KraLYavuz BTK bağlantı teşhisi")
    parser.add_argument("--skip-visible", action="store_true")
    args = parser.parse_args()

    try:
        dns_addresses = resolve_dns()
    except OSError as exc:
        dns_addresses = []
        print(f"DNS error: {type(exc).__name__}: {exc}")

    results = [requests_test(dns_addresses)]
    opera_executable, opera_profile = find_opera()
    with sync_playwright() as playwright:
        results.append(chromium_test(playwright.chromium, dns_addresses, headless=True))
        if not args.skip_visible:
            results.append(chromium_test(playwright.chromium, dns_addresses, headless=False))
        if opera_executable and not args.skip_visible:
            results.append(
                opera_test(playwright.chromium, opera_executable, dns_addresses, opera_profile, False)
            )
            if opera_profile:
                results.append(
                    opera_test(playwright.chromium, opera_executable, dns_addresses, opera_profile, True)
                )

    classify_opera_vpn(results)
    payload = {
        "tested_at": datetime.now().isoformat(),
        "url": BTK_URL,
        "opera_executable": str(opera_executable or ""),
        "opera_profile_source": str(opera_profile or ""),
        "results": [asdict(result) for result in results],
    }
    output = get_output_dir() / f"btk_connection_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nSaved: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
