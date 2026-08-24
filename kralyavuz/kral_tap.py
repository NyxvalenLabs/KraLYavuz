from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .app_config import load_config, save_config
from .platform_paths import ASSETS_DIR, CONFIG_PATH


TOOLTIP_TEXT = (
    "Günde 4 kere tapmanız önerilir. Bunun sebebi allaha 5 kere tapılması ve "
    "Yavuz Kral'ın henüz allah seviyesine çıkamamış olmasıdır"
)
DEFAULT_VIDEO_PATH = ASSETS_DIR / "kral_tap" / "kral_tap.mp4"
MAX_VIDEO_SIZE = QSize(720, 480)


def load_tap_count(config_path: Path = CONFIG_PATH) -> int:
    value = load_config(config_path).get("kral_tap_count", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def save_tap_count(count: int, config_path: Path = CONFIG_PATH) -> None:
    config = load_config(config_path)
    config["kral_tap_count"] = max(0, count)
    save_config(config, config_path)


class KralTapVideoPopup(QDialog):
    closed = Signal()
    playback_failed = Signal(str)

    def __init__(
        self,
        video_path: Path = DEFAULT_VIDEO_PATH,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.video_path = video_path
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setStyleSheet("background-color: black;")

        self.video_widget = QVideoWidget()
        self.video_widget.setAspectRatioMode(Qt.KeepAspectRatio)
        self.video_widget.setFixedSize(720, 405)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.video_widget)
        self.setLayout(layout)

        self.audio_output = QAudioOutput(self)
        self.audio_output.setMuted(False)
        self.audio_output.setVolume(1.0)

        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.player.metaDataChanged.connect(self._resize_for_video)
        self.player.errorOccurred.connect(self._on_playback_error)
        if video_path.is_file():
            self.player.setSource(QUrl.fromLocalFile(str(video_path.resolve())))

    def show_and_play(self) -> None:
        if not self.video_path.is_file():
            self.playback_failed.emit(f"Video bulunamadı: {self.video_path}")
            self.close()
            return
        self.show()
        self._center_on_screen()
        self.raise_()
        self.activateWindow()
        self.player.play()

    @Slot(object)
    def _on_media_status_changed(self, status: object) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.close()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._on_playback_error(
                QMediaPlayer.Error.FormatError,
                self.player.errorString() or "Video biçimi desteklenmiyor.",
            )

    @Slot()
    def _resize_for_video(self) -> None:
        resolution = self.player.metaData().value(QMediaMetaData.Key.Resolution)
        if not isinstance(resolution, QSize) or not resolution.isValid():
            return
        target_size = resolution.scaled(MAX_VIDEO_SIZE, Qt.KeepAspectRatio)
        self.video_widget.setFixedSize(target_size)
        self.adjustSize()
        self._center_on_screen()

    @Slot(object, str)
    def _on_playback_error(self, error: object, message: str = "") -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        self.playback_failed.emit(message or "Krala Tap videosu oynatılamadı.")
        self.close()

    def _center_on_screen(self) -> None:
        parent_window = self.parentWidget()
        screen = parent_window.screen() if parent_window is not None else None
        screen = screen or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.move(available.center() - self.rect().center())

    def closeEvent(self, event: QCloseEvent) -> None:
        self.player.stop()
        self.closed.emit()
        super().closeEvent(event)


class KralTapWidget(QWidget):
    count_changed = Signal(int)
    persistence_failed = Signal(str)
    playback_failed = Signal(str)
    video_started = Signal()
    video_finished = Signal()

    def __init__(
        self,
        config_path: Path = CONFIG_PATH,
        video_path: Path = DEFAULT_VIDEO_PATH,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.config_path = config_path
        self.video_path = video_path
        self.tap_count = load_tap_count(config_path)
        self.video_popup: Optional[KralTapVideoPopup] = None

        self.tap_button = QPushButton("👑 Krala Tap")
        self.tap_button.clicked.connect(self.tap)

        self.help_button = QToolButton()
        self.help_button.setText("?")
        self.help_button.setToolTip(TOOLTIP_TEXT)
        self.help_button.setAccessibleName("Krala Tap hakkında")
        self.help_button.setAutoRaise(True)
        self.help_button.setFixedSize(24, 24)

        self.count_label = QLabel()
        self._update_count_label()

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        controls.addWidget(self.tap_button)
        controls.addWidget(self.help_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(controls)
        layout.addWidget(self.count_label)
        self.setLayout(layout)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)

    @Slot()
    def tap(self) -> None:
        self.tap_count += 1
        self._update_count_label()
        try:
            save_tap_count(self.tap_count, self.config_path)
        except OSError as exc:
            self.persistence_failed.emit(str(exc))
        self.count_changed.emit(self.tap_count)
        self._show_video()

    def _update_count_label(self) -> None:
        self.count_label.setText(f"Total Tapma: {self.tap_count}")

    def _show_video(self) -> None:
        if self.video_popup is not None:
            self.video_popup.close()

        parent_window = self.window() if self.window() is not self else None
        popup = KralTapVideoPopup(self.video_path, parent_window)
        self.video_popup = popup
        popup.closed.connect(lambda: self._on_popup_closed(popup))
        popup.playback_failed.connect(self.playback_failed)
        popup.show_and_play()
        if popup.isVisible():
            self.video_started.emit()

    def _on_popup_closed(self, popup: KralTapVideoPopup) -> None:
        if self.video_popup is popup:
            self.video_popup = None
            self.video_finished.emit()
