import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import requests

from kralyavuz.main import UpdateCheckWorker
from kralyavuz.platform_paths import CONFIG_PATH, PROJECT_ROOT
from kralyavuz.updater import (
    ArchiveValidationError,
    UpdateError,
    check_for_update,
    is_newer_version,
    release_from_payload,
    validate_update_archive,
)
from kralyavuz.updater_runner import apply_update, safe_extract_archive


ASSET_URL = (
    "https://github.com/MeteLabs/KraLYavuz/releases/download/"
    "v1.0.1/KraLYavuz_Windows.zip"
)


def release_payload(
    tag: str = "v1.0.1",
    include_asset: bool = True,
) -> dict:
    assets = []
    if include_asset:
        assets.append(
            {
                "name": "KraLYavuz_Windows.zip",
                "browser_download_url": ASSET_URL,
                "size": 123,
            }
        )
    return {"tag_name": tag, "name": f"Release {tag}", "assets": assets}


def create_archive(
    path: Path,
    include_executable: bool = True,
    include_internal: bool = True,
    extra_entries: tuple[tuple[str, bytes], ...] = (),
) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if include_executable:
            archive.writestr("KraLYavuz/KraLYavuz.exe", b"new executable")
        archive.writestr("KraLYavuz/KraLYavuzUpdater.exe", b"new updater")
        if include_internal:
            archive.writestr("KraLYavuz/_internal/runtime.dat", b"runtime")
        for name, content in extra_entries:
            archive.writestr(name, content)


class TimeoutSession:
    def get(self, *args, **kwargs):
        raise requests.Timeout("mock timeout")


class JsonResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload

    def close(self) -> None:
        return None


class JsonSession:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def get(self, *args, **kwargs) -> JsonResponse:
        return JsonResponse(self.payload)


class VersionAndReleaseTests(unittest.TestCase):
    def test_newer_release_is_available(self):
        self.assertTrue(is_newer_version("v1.0.1", "1.0.0"))

        release = check_for_update(
            local_version="1.0.0",
            session=JsonSession(release_payload("v1.0.1")),
        )
        self.assertIsNotNone(release)
        self.assertEqual(release.tag_name, "v1.0.1")

    def test_equal_release_is_not_available(self):
        self.assertFalse(is_newer_version("v1.0.1", "1.0.1"))
        self.assertIsNone(
            check_for_update(
                local_version="1.0.1",
                session=JsonSession(release_payload("v1.0.1")),
            )
        )

    def test_numeric_semantic_version_order_is_used(self):
        self.assertFalse(is_newer_version("v1.9.9", "1.10.0"))

    def test_expected_windows_asset_is_selected(self):
        release = release_from_payload(release_payload())

        self.assertIsNotNone(release)
        self.assertEqual(release.asset_url, ASSET_URL)

    def test_missing_windows_asset_disables_update(self):
        self.assertIsNone(release_from_payload(release_payload(include_asset=False)))

    def test_network_error_is_reported_without_escaping_worker(self):
        errors = []
        worker = UpdateCheckWorker()
        worker.check_failed.connect(errors.append)
        with patch(
            "kralyavuz.main.check_for_update",
            side_effect=UpdateError("mock timeout"),
        ):
            worker.run()

        self.assertEqual(errors, ["mock timeout"])

    def test_timeout_is_normalized_to_update_error(self):
        with self.assertRaises(UpdateError):
            check_for_update(session=TimeoutSession())


class ArchiveValidationTests(unittest.TestCase):
    def test_invalid_zip_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "invalid.zip"
            archive_path.write_bytes(b"not a zip")

            with self.assertRaises(ArchiveValidationError):
                validate_update_archive(archive_path)

    def test_missing_executable_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "missing-exe.zip"
            create_archive(archive_path, include_executable=False)

            with self.assertRaises(ArchiveValidationError):
                validate_update_archive(archive_path)

    def test_missing_internal_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "missing-internal.zip"
            create_archive(archive_path, include_internal=False)

            with self.assertRaises(ArchiveValidationError):
                validate_update_archive(archive_path)

    def test_path_traversal_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "traversal.zip"
            create_archive(
                archive_path,
                extra_entries=(("../outside.txt", b"blocked"),),
            )

            with self.assertRaises(ArchiveValidationError):
                safe_extract_archive(archive_path, root / "extract")
            self.assertFalse((root / "outside.txt").exists())


class UpdateFileOperationTests(unittest.TestCase):
    def _create_install(self, root: Path) -> Path:
        install_dir = root / "KraLYavuz"
        (install_dir / "_internal").mkdir(parents=True)
        (install_dir / "KraLYavuz.exe").write_bytes(b"old executable")
        (install_dir / "_internal" / "runtime.dat").write_bytes(b"old runtime")
        (install_dir / "results").mkdir()
        (install_dir / "results" / "user.png").write_bytes(b"user data")
        return install_dir

    def test_successful_update_preserves_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install_dir = self._create_install(root)
            archive_path = root / "update.zip"
            create_archive(archive_path)

            executable = apply_update(
                archive_path,
                install_dir,
                restart=False,
            )

            self.assertEqual(executable.read_bytes(), b"new executable")
            self.assertEqual(
                (install_dir / "results" / "user.png").read_bytes(),
                b"user data",
            )
            self.assertFalse((root / "KraLYavuz_update_backup").exists())

    def test_invalid_zip_does_not_touch_current_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install_dir = self._create_install(root)
            archive_path = root / "invalid.zip"
            archive_path.write_bytes(b"not a zip")

            with self.assertRaises(UpdateError):
                apply_update(archive_path, install_dir, restart=False)

            self.assertEqual(
                (install_dir / "KraLYavuz.exe").read_bytes(),
                b"old executable",
            )
            self.assertEqual(
                (install_dir / "results" / "user.png").read_bytes(),
                b"user data",
            )

    def test_activation_failure_rolls_back_old_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install_dir = self._create_install(root)
            archive_path = root / "update.zip"
            create_archive(archive_path)

            with patch(
                "kralyavuz.updater_runner._activate_staged_install",
                side_effect=OSError("mock copy failure"),
            ):
                with self.assertRaises(UpdateError):
                    apply_update(
                        archive_path,
                        install_dir,
                        restart=False,
                    )

            self.assertEqual(
                (install_dir / "KraLYavuz.exe").read_bytes(),
                b"old executable",
            )
            self.assertEqual(
                (install_dir / "results" / "user.png").read_bytes(),
                b"user data",
            )
            self.assertFalse((root / "KraLYavuz_update_backup").exists())

    def test_config_path_is_outside_install_tree(self):
        with self.assertRaises(ValueError):
            CONFIG_PATH.relative_to(PROJECT_ROOT)


if __name__ == "__main__":
    unittest.main()
