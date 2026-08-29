import logging
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Optional, Tuple
from urllib.parse import urlsplit

import requests

from .version import APP_VERSION


GITHUB_REPOSITORY = "MeteLabs/KraLYavuz"
LATEST_RELEASE_URL = (
    "https://api.github.com/repos/MeteLabs/KraLYavuz/releases/latest"
)
WINDOWS_ASSET_NAME = "KraLYavuz_Windows.zip"
UPDATER_EXE_NAME = "KraLYavuzUpdater.exe"
UPDATE_TEMP_DIR_NAME = "KraLYavuzUpdate"
API_TIMEOUT = 10
DOWNLOAD_TIMEOUT: Tuple[int, int] = (10, 60)
USER_AGENT = f"KraLYavuz/{APP_VERSION} update-checker"
EXPECTED_ARCHIVE_ROOT = PurePosixPath("KraLYavuz")
_VERSION_PATTERN = re.compile(
    r"^[vV]?(\d+)\.(\d+)\.(\d+)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)

logger = logging.getLogger(__name__)


class UpdateError(RuntimeError):
    pass


class ArchiveValidationError(UpdateError):
    pass


@dataclass(frozen=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int
    prerelease: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    name: str
    version: SemanticVersion
    asset_url: str
    asset_size: Optional[int]


@dataclass(frozen=True)
class ArchiveLayout:
    root: PurePosixPath
    executable: PurePosixPath


def parse_version(value: str) -> SemanticVersion:
    match = _VERSION_PATTERN.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Geçersiz sürüm: {value}")
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
    return SemanticVersion(
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        prerelease,
    )


def _compare_prerelease(left: Tuple[str, ...], right: Tuple[str, ...]) -> int:
    if not left and not right:
        return 0
    if not left:
        return 1
    if not right:
        return -1
    for left_part, right_part in zip(left, right):
        if left_part == right_part:
            continue
        left_number = int(left_part) if left_part.isdigit() else None
        right_number = int(right_part) if right_part.isdigit() else None
        if left_number is not None and right_number is not None:
            return 1 if left_number > right_number else -1
        if left_number is not None:
            return -1
        if right_number is not None:
            return 1
        return 1 if left_part > right_part else -1
    if len(left) == len(right):
        return 0
    return 1 if len(left) > len(right) else -1


def is_newer_version(remote: str, local: str) -> bool:
    remote_version = parse_version(remote)
    local_version = parse_version(local)
    remote_core = (
        remote_version.major,
        remote_version.minor,
        remote_version.patch,
    )
    local_core = (
        local_version.major,
        local_version.minor,
        local_version.patch,
    )
    if remote_core != local_core:
        return remote_core > local_core
    return (
        _compare_prerelease(remote_version.prerelease, local_version.prerelease)
        > 0
    )


def _official_asset_url(url: str) -> bool:
    parsed = urlsplit(url)
    expected_prefix = f"/{GITHUB_REPOSITORY}/releases/download/"
    return (
        parsed.scheme == "https"
        and parsed.hostname == "github.com"
        and parsed.path.startswith(expected_prefix)
        and parsed.path.endswith(f"/{WINDOWS_ASSET_NAME}")
    )


def _official_download_response_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and host in {
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
    }


def release_from_payload(payload: object) -> Optional[ReleaseInfo]:
    if not isinstance(payload, dict):
        raise UpdateError("GitHub release yanıtı nesne değil.")
    tag_name = payload.get("tag_name")
    name = payload.get("name")
    assets = payload.get("assets")
    if not isinstance(tag_name, str) or not tag_name.strip():
        raise UpdateError("GitHub release tag bilgisi eksik.")
    if not isinstance(assets, list):
        raise UpdateError("GitHub release asset listesi geçersiz.")

    version = parse_version(tag_name)
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("name") != WINDOWS_ASSET_NAME:
            continue
        asset_url = asset.get("browser_download_url")
        if not isinstance(asset_url, str) or not _official_asset_url(asset_url):
            raise UpdateError("Windows update asset adresi güvenilir değil.")
        size = asset.get("size")
        asset_size = size if isinstance(size, int) and size >= 0 else None
        return ReleaseInfo(
            tag_name=tag_name,
            name=name if isinstance(name, str) else "",
            version=version,
            asset_url=asset_url,
            asset_size=asset_size,
        )
    return None


def check_for_update(
    local_version: str = APP_VERSION,
    session: Optional[requests.Session] = None,
) -> Optional[ReleaseInfo]:
    owns_client = session is None
    client = session or requests.Session()
    response = None
    try:
        response = client.get(
            LATEST_RELEASE_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": USER_AGENT,
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=API_TIMEOUT,
        )
        response.raise_for_status()
        release = release_from_payload(response.json())
    except (requests.RequestException, ValueError, UpdateError) as exc:
        raise UpdateError(f"Güncelleme kontrolü başarısız: {exc}") from exc
    finally:
        if response is not None:
            response.close()
        if owns_client:
            client.close()

    if release is None or not is_newer_version(release.tag_name, local_version):
        return None
    return release


def _safe_member_path(info: zipfile.ZipInfo) -> PurePosixPath:
    raw_name = info.filename.replace("\\", "/")
    path = PurePosixPath(raw_name)
    if (
        not raw_name
        or raw_name.startswith("/")
        or path.is_absolute()
        or ".." in path.parts
        or (path.parts and ":" in path.parts[0])
    ):
        raise ArchiveValidationError(f"Güvensiz ZIP yolu: {info.filename}")
    mode = (info.external_attr >> 16) & 0o170000
    if mode == stat.S_IFLNK:
        raise ArchiveValidationError(f"ZIP sembolik link içeriyor: {info.filename}")
    if info.flag_bits & 0x1:
        raise ArchiveValidationError(f"Şifreli ZIP üyesi desteklenmiyor: {info.filename}")
    return path


def validated_members(
    archive: zipfile.ZipFile,
) -> Iterable[tuple[zipfile.ZipInfo, PurePosixPath]]:
    for info in archive.infolist():
        yield info, _safe_member_path(info)


def validate_update_archive(path: Path) -> ArchiveLayout:
    try:
        with zipfile.ZipFile(path) as archive:
            members = list(validated_members(archive))
            broken_member = archive.testzip()
            if broken_member:
                raise ArchiveValidationError(
                    f"ZIP içindeki dosya bozuk: {broken_member}"
                )
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ArchiveValidationError(f"Geçersiz update ZIP'i: {exc}") from exc

    paths = {member_path for _, member_path in members}
    normalized_paths = [str(path).casefold() for path in paths]
    if len(normalized_paths) != len(members) or len(normalized_paths) != len(set(normalized_paths)):
        raise ArchiveValidationError("ZIP yinelenen veya çakışan dosya yolları içeriyor.")
    if any(
        not path.parts or path.parts[0] != EXPECTED_ARCHIVE_ROOT.name
        for path in paths
    ):
        raise ArchiveValidationError("ZIP beklenen KraLYavuz kökü dışında içerik taşıyor.")
    executable = EXPECTED_ARCHIVE_ROOT / "KraLYavuz.exe"
    updater_executable = EXPECTED_ARCHIVE_ROOT / UPDATER_EXE_NAME
    internal_prefix = EXPECTED_ARCHIVE_ROOT / "_internal"
    if executable not in paths:
        raise ArchiveValidationError("ZIP içinde KraLYavuz/KraLYavuz.exe yok.")
    if updater_executable not in paths:
        raise ArchiveValidationError(
            "ZIP içinde KraLYavuz/KraLYavuzUpdater.exe yok."
        )
    if not any(
        path == internal_prefix or internal_prefix in path.parents for path in paths
    ):
        raise ArchiveValidationError("ZIP içinde KraLYavuz/_internal yok.")
    protected_roots = {
        EXPECTED_ARCHIVE_ROOT / ".kralyavuz",
        EXPECTED_ARCHIVE_ROOT / "config.json",
        EXPECTED_ARCHIVE_ROOT / "results",
    }
    if any(
        any(root == path or root in path.parents for root in protected_roots)
        for path in paths
    ):
        raise ArchiveValidationError("Update ZIP'i kullanıcı ayarı içeremez.")
    return ArchiveLayout(root=EXPECTED_ARCHIVE_ROOT, executable=executable)


def update_temp_dir() -> Path:
    return Path(tempfile.gettempdir()) / UPDATE_TEMP_DIR_NAME


def download_update(
    release: ReleaseInfo,
    progress: Optional[Callable[[int], None]] = None,
    session: Optional[requests.Session] = None,
) -> Path:
    if not _official_asset_url(release.asset_url):
        raise UpdateError("Update yalnız resmi GitHub Release adresinden indirilebilir.")

    destination_dir = update_temp_dir()
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / WINDOWS_ASSET_NAME
    partial = destination.with_suffix(".zip.part")
    owns_client = session is None
    client = session or requests.Session()
    response = None
    try:
        response = client.get(
            release.asset_url,
            headers={"Accept": "application/octet-stream", "User-Agent": USER_AGENT},
            timeout=DOWNLOAD_TIMEOUT,
            stream=True,
            allow_redirects=True,
        )
        response.raise_for_status()
        response_url = getattr(response, "url", release.asset_url)
        if not _official_download_response_url(response_url):
            raise UpdateError("GitHub asset yönlendirmesi güvenilir değil.")
        content_length = response.headers.get("Content-Length")
        expected_size = int(content_length) if content_length else release.asset_size
        written = 0
        with partial.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                output.write(chunk)
                written += len(chunk)
                if progress and expected_size:
                    progress(min(100, int(written * 100 / expected_size)))
        if expected_size is not None and written != expected_size:
            raise UpdateError(
                f"İndirme boyutu uyuşmuyor: beklenen {expected_size}, alınan {written}."
            )
        validate_update_archive(partial)
        partial.replace(destination)
        if progress:
            progress(100)
        return destination
    except (OSError, ValueError, requests.RequestException, UpdateError) as exc:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError(f"Update indirilemedi: {exc}") from exc
    finally:
        if response is not None:
            response.close()
        if owns_client:
            client.close()


def windows_frozen_update_supported() -> bool:
    return platform.system() == "Windows" and bool(getattr(sys, "frozen", False))


def launch_updater(zip_path: Path) -> subprocess.Popen:
    if not windows_frozen_update_supported():
        raise UpdateError("Otomatik kurulum yalnız paketli Windows sürümünde çalışır.")
    validate_update_archive(zip_path)

    install_dir = Path(sys.executable).resolve().parent
    installed_updater = install_dir / UPDATER_EXE_NAME
    if not installed_updater.is_file():
        raise UpdateError(f"Updater bulunamadı: {installed_updater}")

    temporary_updater = update_temp_dir() / UPDATER_EXE_NAME
    shutil.copy2(installed_updater, temporary_updater)
    command = [
        str(temporary_updater),
        "--pid",
        str(os.getpid()),
        "--zip",
        str(zip_path.resolve()),
        "--install-dir",
        str(install_dir),
        "--exe",
        Path(sys.executable).name,
    ]
    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(command, shell=False, creationflags=creation_flags)
