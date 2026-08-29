import os
import platform
import subprocess
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

import requests

from .btk_operator import CDP_URL
from .platform_paths import find_opera_gx


def cdp_is_ready(timeout: float = 1.0) -> bool:
    try:
        response = requests.get(f"{CDP_URL}/json", timeout=timeout)
        response.raise_for_status()
    except requests.RequestException:
        return False
    return True


def _launch_opera(executable: Path) -> None:
    if platform.system() == "Darwin":
        subprocess.Popen(
            [
                "open",
                "-gj",
                str(executable.parents[2]),
                "--args",
                "--remote-debugging-port=9222",
                "--private",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return

    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [str(executable), "--remote-debugging-port=9222", "--private"],
        **kwargs,
    )


def private_window_is_ready(timeout: float = 3.0) -> bool:
    if not cdp_is_ready():
        return False

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(
                CDP_URL,
                timeout=int(timeout * 1000),
            )

            browser_session = browser.new_browser_cdp_session()
            try:
                default_context_id = str(
                    browser_session.send("Target.getBrowserContexts").get(
                        "defaultBrowserContextId",
                        "",
                    )
                )
            finally:
                browser_session.detach()

            for context in browser.contexts:
                for page in context.pages:
                    page_session = context.new_cdp_session(page)
                    try:
                        target_info = page_session.send(
                            "Target.getTargetInfo"
                        ).get("targetInfo", {})
                    finally:
                        page_session.detach()

                    context_id = str(
                        target_info.get("browserContextId", "")
                    )

                    if not context_id or context_id == default_context_id:
                        continue

                    try:
                        user_agent = page.evaluate("navigator.userAgent")
                    except Exception:
                        continue

                    if "OPR/" in user_agent:
                        return True

    except Exception:
        return False

    return False

def ensure_opera_cdp(wait_seconds: float = 12.0) -> bool:
    """Opera CDP ve en az bir gerçek Private Window hazır olsun."""

    executable = find_opera_gx()
    if executable is None:
        raise RuntimeError("Opera GX uygulaması bulunamadı.")

    if cdp_is_ready():
        if private_window_is_ready():
            return False

        # CDP zaten var fakat private pencere yok.
        # Mevcut Opera instance'ına --private isteği gönder.
        _launch_opera(executable)
    else:
        # Opera/CDP tamamen kapalıysa normal başlangıç.
        _launch_opera(executable)

    deadline = time.monotonic() + wait_seconds

    while time.monotonic() < deadline:
        if cdp_is_ready() and private_window_is_ready():
            return True

        time.sleep(0.25)

    if cdp_is_ready():
        raise RuntimeError(
            "Opera GX CDP hazır ancak Private Window oluşturulamadı."
        )

    raise RuntimeError(
        "Opera GX CDP ile başlatılamadı. Çalışan Opera sürecine sonradan CDP "
        "eklenemez; Opera'yı kapatıp KraLYavuz'u yeniden açın."
    )
