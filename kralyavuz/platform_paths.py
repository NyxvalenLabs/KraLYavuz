import os
import platform
import sys
from pathlib import Path, PurePath, PureWindowsPath
from typing import Mapping, Optional, Tuple


APP_NAME = "KraLYavuz"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
IS_FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
ASSETS_DIR = RESOURCE_ROOT / "assets"
CONFIG_DIR = Path.home() / ".kralyavuz"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_OUTPUT_DIR = (
    Path.home() / APP_NAME / "results" if IS_FROZEN else PROJECT_ROOT / "results"
)


def opera_gx_candidates(
    system_name: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Tuple[PurePath, ...]:
    system_name = system_name or platform.system()
    environ = environ or os.environ
    home = home or Path.home()

    if system_name == "Darwin":
        return (
            Path("/Applications") / "Opera GX.app" / "Contents" / "MacOS" / "Opera",
            home / "Applications" / "Opera GX.app" / "Contents" / "MacOS" / "Opera",
        )

    if system_name == "Windows":
        candidates = []
        for variable, suffix in (
            ("LOCALAPPDATA", ("Programs", "Opera GX", "opera.exe")),
            ("ProgramFiles", ("Opera GX", "opera.exe")),
            ("ProgramFiles(x86)", ("Opera GX", "opera.exe")),
        ):
            base = environ.get(variable)
            if base:
                candidates.append(PureWindowsPath(base).joinpath(*suffix))
        return tuple(candidates)

    return ()


def find_opera_gx() -> Optional[Path]:
    for candidate in opera_gx_candidates():
        path = Path(candidate)
        if path.is_file():
            return path
    return None


def opera_gx_profile_dir(
    system_name: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Optional[PurePath]:
    system_name = system_name or platform.system()
    environ = environ or os.environ
    home = home or Path.home()

    if system_name == "Darwin":
        return home / "Library" / "Application Support" / "com.operasoftware.OperaGX"
    if system_name == "Windows" and environ.get("APPDATA"):
        return PureWindowsPath(environ["APPDATA"]) / "Opera Software" / "Opera GX Stable"
    return None


def is_opera_gx_command(command_line: str) -> bool:
    normalized = command_line.replace("\\", "/").lower()
    return "opera gx.app/" in normalized or "/opera gx/opera.exe" in normalized
