import os
import platform
import subprocess
import time
from pathlib import Path

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


def ensure_opera_cdp(wait_seconds: float = 12.0) -> bool:
    """Start Opera with CDP when needed, without creating pages or profiles."""
    if cdp_is_ready():
        return False

    executable = find_opera_gx()
    if executable is None:
        raise RuntimeError("Opera GX uygulaması bulunamadı.")

    _launch_opera(executable)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if cdp_is_ready():
            return True
        time.sleep(0.25)

    raise RuntimeError(
        "Opera GX CDP ile başlatılamadı. Çalışan Opera sürecine sonradan CDP "
        "eklenemez; Opera'yı kapatıp KraLYavuz'u yeniden açın."
    )
