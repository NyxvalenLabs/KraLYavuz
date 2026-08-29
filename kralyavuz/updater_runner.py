import argparse
import ctypes
import logging
import os
import platform
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional

from .updater import (
    EXPECTED_ARCHIVE_ROOT,
    ArchiveValidationError,
    UpdateError,
    update_temp_dir,
    validate_update_archive,
    validated_members,
)


logger = logging.getLogger(__name__)


def configure_logging() -> Path:
    log_dir = update_temp_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "updater.log"
    logging.basicConfig(
        filename=str(log_path),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return log_path


def wait_for_process_exit(pid: int, timeout_seconds: int = 180) -> None:
    if pid <= 0:
        raise UpdateError("Geçersiz ana uygulama PID değeri.")
    if platform.system() == "Windows":
        synchronize = 0x00100000
        wait_object_0 = 0x00000000
        wait_timeout = 0x00000102
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return
        try:
            result = kernel32.WaitForSingleObject(handle, timeout_seconds * 1000)
        finally:
            kernel32.CloseHandle(handle)
        if result == wait_timeout:
            raise UpdateError("Ana uygulamanın kapanması zaman aşımına uğradı.")
        if result != wait_object_0:
            raise UpdateError(f"Ana uygulama beklenemedi: Win32 kodu {result}.")
        return

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        time.sleep(0.2)
    raise UpdateError("Ana uygulamanın kapanması zaman aşımına uğradı.")


def safe_extract_archive(zip_path: Path, destination: Path) -> Path:
    layout = validate_update_archive(zip_path)
    destination.mkdir(parents=True, exist_ok=False)
    destination_root = destination.resolve()
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for info, member_path in validated_members(archive):
                target = destination.joinpath(*member_path.parts)
                resolved_target = target.resolve()
                if destination_root not in resolved_target.parents and resolved_target != destination_root:
                    raise ArchiveValidationError(f"ZIP yolu staging dışına çıkıyor: {info.filename}")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination.joinpath(*layout.root.parts)


def _activate_staged_install(staged_install: Path, install_dir: Path) -> None:
    shutil.move(str(staged_install), str(install_dir))


def _restore_results(backup_dir: Path, install_dir: Path) -> None:
    previous_results = backup_dir / "results"
    if not previous_results.exists():
        return
    new_results = install_dir / "results"
    if new_results.exists():
        if new_results.is_dir():
            shutil.rmtree(new_results)
        else:
            new_results.unlink()
    shutil.move(str(previous_results), str(new_results))


def _rollback_install(backup_dir: Path, install_dir: Path) -> None:
    if not backup_dir.exists():
        return
    current_results = install_dir / "results"
    backup_results = backup_dir / "results"
    if current_results.exists() and not backup_results.exists():
        shutil.move(str(current_results), str(backup_results))
    if install_dir.exists():
        shutil.rmtree(install_dir)
    backup_dir.rename(install_dir)


def apply_update(
    zip_path: Path,
    install_dir: Path,
    executable_name: str = "KraLYavuz.exe",
    restart: bool = True,
) -> Path:
    if Path(executable_name).name != executable_name or not executable_name.lower().endswith(".exe"):
        raise UpdateError("Geçersiz uygulama executable adı.")
    install_dir = install_dir.resolve()
    if not install_dir.is_dir():
        raise UpdateError(f"Kurulum klasörü bulunamadı: {install_dir}")
    if not (install_dir / executable_name).is_file():
        raise UpdateError(f"Mevcut uygulama bulunamadı: {install_dir / executable_name}")

    backup_dir = install_dir.with_name(f"{install_dir.name}_update_backup")
    if backup_dir.exists():
        raise UpdateError(f"Önceki update backup klasörü hâlâ mevcut: {backup_dir}")

    staging_parent = Path(tempfile.mkdtemp(prefix="KraLYavuzUpdateExtract-"))
    staging_dir = staging_parent / "payload"
    moved_current = False
    try:
        staged_install = safe_extract_archive(zip_path, staging_dir)
        expected_executable = staged_install / executable_name
        if not expected_executable.is_file() or not (staged_install / "_internal").is_dir():
            raise ArchiveValidationError("Staging içindeki Windows build yapısı geçersiz.")

        install_dir.rename(backup_dir)
        moved_current = True
        _activate_staged_install(staged_install, install_dir)
        _restore_results(backup_dir, install_dir)
        if not (install_dir / executable_name).is_file() or not (install_dir / "_internal").is_dir():
            raise UpdateError("Yeni kurulum doğrulanamadı.")
    except Exception as exc:
        if moved_current:
            try:
                _rollback_install(backup_dir, install_dir)
            except Exception as rollback_error:
                raise UpdateError(
                    f"Update başarısız ve rollback tamamlanamadı: {rollback_error}"
                ) from exc
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError(f"Update uygulanamadı: {exc}") from exc
    else:
        try:
            shutil.rmtree(backup_dir)
        except OSError:
            logger.warning("Update backup klasörü temizlenemedi: %s", backup_dir)
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)

    executable = install_dir / executable_name
    if restart:
        subprocess.Popen([str(executable)], cwd=str(install_dir), shell=False)
    return executable


def run_update(
    pid: int,
    zip_path: Path,
    install_dir: Path,
    executable_name: str,
) -> Path:
    if platform.system() != "Windows":
        raise UpdateError("Updater runner yalnız Windows üzerinde çalışır.")
    wait_for_process_exit(pid)
    return apply_update(zip_path, install_dir, executable_name, restart=True)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KraLYavuz Windows Updater")
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument("--install-dir", required=True, type=Path)
    parser.add_argument("--exe", required=True)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    configure_logging()
    os.chdir(update_temp_dir())
    args = parse_args(argv)
    try:
        run_update(args.pid, args.zip, args.install_dir, args.exe)
    except Exception:
        logger.exception("KraLYavuz update başarısız.")
        try:
            old_executable = args.install_dir / args.exe
            if old_executable.is_file():
                subprocess.Popen(
                    [str(old_executable)],
                    cwd=str(args.install_dir),
                    shell=False,
                )
        except OSError:
            logger.exception("Eski KraLYavuz yeniden başlatılamadı.")
        return 1
    logger.info("KraLYavuz update başarıyla tamamlandı.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
