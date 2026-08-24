import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from .platform_paths import DEFAULT_OUTPUT_DIR


_output_dir = DEFAULT_OUTPUT_DIR


def get_output_dir() -> Path:
    return _output_dir


def set_output_dir(path: Path) -> Path:
    global _output_dir
    _output_dir = Path(path).expanduser().resolve()
    return _output_dir


def output_stem(value: str) -> str:
    candidate = value.strip()
    parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
    domain = (parsed.hostname or "").rstrip(".").lower()
    path = parsed.path.strip("/")
    raw = f"{domain}_{path}" if path else domain
    safe_domain = re.sub(r"[^A-Za-z0-9.-]+", "_", raw).strip("._")
    if not safe_domain:
        raise ValueError("Geçerli bir screenshot dosya adı oluşturulamadı.")
    return safe_domain


def screenshot_path(value: str, report_name: str) -> Path:
    safe_domain = output_stem(value)
    output = get_output_dir() / f"{safe_domain}_{report_name}.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def clean_output_dir() -> Path:
    output = get_output_dir().resolve()
    if output == Path.home().resolve() or output == Path(output.anchor):
        raise ValueError("Ana dizin screenshot klasörü olarak temizlenemez.")
    output.mkdir(parents=True, exist_ok=True)
    for child in output.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    return output
