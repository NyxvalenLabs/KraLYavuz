import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event, Lock
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .app_config import ensure_config, load_config, save_config, save_domain_config
from .batch_checker import (
    BatchDomainResult,
    ServiceTargets,
    discover_service_targets,
    normalize_domains,
    run_batch,
)
from .clone_checker.ui import CloneCheckerPanel
from .clone_checker.whitelist import normalize_domain_list
from .kral_tap import KralTapWidget
from .output_settings import clean_output_dir, get_output_dir, set_output_dir
from .platform_paths import application_icon_path


APP_NAME = "KraLYavuz"
shutdown_requested = Event()
UrlTask = Tuple[str, str]


def set_application_icon(app: QApplication) -> Path:
    icon_path = application_icon_path()
    app.setWindowIcon(QIcon(str(icon_path)))
    return icon_path


def parse_url_tasks(text: str) -> List[UrlTask]:
    return [(domain, domain) for domain in normalize_domains(text)]


@dataclass
class CaptchaPanelState:
    target_id: str = ""
    image: bytes = b""
    input_value: str = ""
    ready: bool = False
    status: str = "Hazır değil"


class BatchWorker(QThread):
    item_status = Signal(str, str, str)
    progress_changed = Signal(int, str)
    log_added = Signal(str)
    captcha_ready = Signal(bytes, str, str, str, bool)
    captcha_reset = Signal(str)
    target_bound = Signal(str, str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, domains: List[str], stop_event: Optional[Event] = None) -> None:
        super().__init__()
        self.domains = domains
        self._cancelled = stop_event or Event()
        self._captcha_lock = Lock()
        self._captcha_events = {"BTK": Event(), "Aile Profili": Event()}
        self._captcha_codes: Dict[str, Optional[str]] = {
            "BTK": None,
            "Aile Profili": None,
        }
        self._service_targets: Dict[str, str] = {}
        self._captcha_domain: Optional[str] = None
        self._active_domain: Optional[str] = None

    def _progress(
        self,
        completed: int,
        total: int,
        domain: str,
        service: str,
        stage: str,
    ) -> None:
        if self._active_domain != domain:
            self._active_domain = domain
            with self._captcha_lock:
                self._captcha_domain = None
                for captcha_service in self._captcha_codes:
                    self._captcha_codes[captcha_service] = None
                for event in self._captcha_events.values():
                    event.clear()
            self.captcha_reset.emit(domain)
            self.log_added.emit(f"Kontrol edilen URL: {domain}")
        if service == "BTK" and stage == "Kontrol ediliyor":
            self.log_added.emit(f"BTK gönderilen: {domain}")
            self.log_added.emit(f"[{domain}] BTK sayfası açılıyor; CAPTCHA bekleniyor.")
        elif service == "Aile Profili" and stage == "Kontrol ediliyor":
            self.log_added.emit(f"GüvenliNet gönderilen: {domain}")
            self.log_added.emit(f"[{domain}] GüvenliNet Aile Profili kontrol ediliyor.")
        elif service == "BTK":
            message = "BTK sonucu alındı." if stage == "Tamamlandı" else stage
            self.log_added.emit(f"[{domain}] {message}")
        else:
            message = "Aile Profili sonucu alındı." if stage == "Tamamlandı" else stage
            self.log_added.emit(f"[{domain}] {message}")
        self.item_status.emit(domain, service, stage)
        percent = int(completed * 100 / total) if total else 0
        self.progress_changed.emit(percent, f"{completed}/{total} adım tamamlandı")

    def _show_captcha(
        self,
        domain: str,
        service: str,
        target_id: str,
        image: bytes,
        retry: bool,
    ) -> None:
        with self._captcha_lock:
            if self._captcha_domain != domain:
                self._captcha_domain = domain
                for event in self._captcha_events.values():
                    event.clear()
            existing_target = self._service_targets.get(service)
            if existing_target and existing_target != target_id:
                raise RuntimeError(f"{service} CAPTCHA targetId değişti.")
            other_service = "Aile Profili" if service == "BTK" else "BTK"
            if self._service_targets.get(other_service) == target_id:
                raise RuntimeError("BTK ve GüvenliNet aynı CAPTCHA targetId kullanıyor.")
            self._service_targets[service] = target_id
            if not self._captcha_events[service].is_set():
                self._captcha_codes[service] = None
                self._captcha_events[service].clear()
        self.captcha_ready.emit(image, domain, service, target_id, retry)

    def _target_ready(self, service: str, target_id: str, url: str) -> None:
        panel_service = "Aile Profili" if service == "GüvenliNet" else service
        with self._captcha_lock:
            other_service = "Aile Profili" if panel_service == "BTK" else "BTK"
            if self._service_targets.get(other_service) == target_id:
                raise RuntimeError("BTK ve GüvenliNet aynı targetId kullanıyor.")
            self._service_targets[panel_service] = target_id
        self.target_bound.emit(panel_service, target_id)
        self.log_added.emit(f"{service} target id: {target_id}")
        self.log_added.emit(f"URL: {url}")

    def _wait_for_captcha(self, service: str) -> Optional[str]:
        self._captcha_events[service].wait()
        if self._cancelled.is_set():
            return None
        with self._captcha_lock:
            code = self._captcha_codes[service]
            self._captcha_codes[service] = None
            self._captcha_events[service].clear()
            return code

    def submit_captcha(self, service: str, target_id: str, code: str) -> None:
        with self._captcha_lock:
            if self._service_targets.get(service) != target_id:
                raise RuntimeError(f"{service} CAPTCHA yanlış targetId için gönderilemez.")
            self._captcha_codes[service] = code
        self._captcha_events[service].set()

    def cancel(self) -> None:
        self._cancelled.set()
        for event in self._captcha_events.values():
            event.set()

    def run(self) -> None:
        self.log_added.emit("Opera bağlantısı bekleniyor.")
        try:
            results = run_batch(
                self.domains,
                self._progress,
                captcha_ready=self._show_captcha,
                wait_for_captcha=self._wait_for_captcha,
                target_ready=self._target_ready,
                stop_requested=self._cancelled.is_set,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished_ok.emit(results)


class OperaDiscoveryWorker(QThread):
    discovered = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            targets = discover_service_targets()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.discovered.emit(targets)


class MainWindow(QMainWindow):
    def __init__(self, auto_start: bool = False, auto_exit: bool = False) -> None:
        super().__init__()
        self.worker: Optional[BatchWorker] = None
        self.discovery_worker: Optional[OperaDiscoveryWorker] = None
        self.service_targets: Optional[ServiceTargets] = None
        self.pending_start = False
        self.auto_exit = auto_exit
        self.close_when_finished = False
        self.status_items: Dict[str, QTreeWidgetItem] = {}
        self.config = load_config()
        self._migrate_output_setting()
        saved_output_dir = self.config.get("screenshot_output_dir", "")
        if saved_output_dir:
            set_output_dir(Path(str(saved_output_dir)))
        self.latest_result_path = get_output_dir()

        self.setWindowTitle(APP_NAME)
        self.resize(900, 700)

        self.url_input = QPlainTextEdit()
        saved_domains = self.config.get("domains", [])
        if not isinstance(saved_domains, list):
            saved_domains = []
        self.url_input.setPlainText("\n".join(str(value) for value in saved_domains))
        self.url_input.setPlaceholderText("Her satıra bir URL veya domain girin")
        self.url_input.setFixedHeight(110)
        self.url_input.textChanged.connect(self.save_domain_list)

        self.clone_checker_panel = CloneCheckerPanel()
        self.clone_checker_panel.log_added.connect(self.add_log)

        self.kral_tap_widget = KralTapWidget()
        self.kral_tap_widget.persistence_failed.connect(
            lambda error: self.add_log(f"Krala Tap sayacı kaydedilemedi: {error}")
        )
        self.kral_tap_widget.playback_failed.connect(
            lambda error: self.add_log(f"Krala Tap videosu oynatılamadı: {error}")
        )

        self.domain_list_label = QLabel("URL veya domain listesi")
        self.domain_header_row = QHBoxLayout()
        self.domain_header_row.addWidget(self.domain_list_label)
        self.domain_header_row.addStretch(1)
        self.domain_header_row.addWidget(self.kral_tap_widget)

        self.output_dir_input = QLineEdit(str(get_output_dir()))
        self.output_dir_input.setReadOnly(True)
        self.select_output_dir_button = QPushButton("Klasör Seç")
        self.select_output_dir_button.clicked.connect(self.select_output_dir)

        output_dir_row = QHBoxLayout()
        output_dir_row.addWidget(self.output_dir_input)
        output_dir_row.addWidget(self.select_output_dir_button)

        self.start_button = QPushButton("Kontrolü Başlat")
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_check)
        self.open_results_button = QPushButton("Sonuçları Aç")
        self.open_results_button.setEnabled(False)
        self.open_results_button.clicked.connect(self.open_results)

        button_row = QHBoxLayout()
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.open_results_button)
        button_row.addStretch(1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.status_label = QLabel("Hazır")

        self.captcha_widgets: Dict[str, Tuple[QLabel, QLineEdit, QPushButton]] = {}
        self.captcha_status_labels: Dict[str, QLabel] = {}
        self.captcha_states: Dict[str, CaptchaPanelState] = {
            "BTK": CaptchaPanelState(),
            "Aile Profili": CaptchaPanelState(),
        }
        self.btk_captcha_state = self.captcha_states["BTK"]
        self.guvenlinet_captcha_state = self.captcha_states["Aile Profili"]
        self.captcha_target_ids: Dict[str, str] = {}
        self.captcha_domain: Optional[str] = None
        self.btk_captcha_group = self._create_captcha_group("BTK")
        self.family_captcha_group = self._create_captcha_group("Aile Profili")
        self.captcha_image, self.captcha_code_input, self.captcha_continue_button = (
            self.captcha_widgets["BTK"]
        )

        self.status_table = QTreeWidget()
        self.status_table.setHeaderLabels(
            ["Alan Adı", "BTK", "Aile Profili", "Screenshot"]
        )
        self.status_table.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.status_table.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.status_table.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.status_table.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.status_table.setRootIsDecorated(False)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)

        layout = QVBoxLayout()
        layout.addLayout(self.domain_header_row)
        layout.addWidget(self.url_input)
        layout.addWidget(self.clone_checker_panel)
        layout.addWidget(QLabel("Screenshot kayıt klasörü"))
        layout.addLayout(output_dir_row)
        layout.addLayout(button_row)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_label)
        captcha_row = QHBoxLayout()
        captcha_row.addWidget(self.btk_captcha_group)
        captcha_row.addWidget(self.family_captcha_group)
        layout.addLayout(captcha_row)
        layout.addWidget(self.status_table)
        layout.addWidget(QLabel("Log"))
        layout.addWidget(self.log_output)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.save_domain_list()
        QTimer.singleShot(0, self.check_opera_session)
        if auto_start:
            QTimer.singleShot(300, self.start_check)

    def _migrate_output_setting(self) -> None:
        if "screenshot_output_dir" in self.config:
            return
        legacy = QSettings(APP_NAME, APP_NAME).value("screenshot_output_dir", "", type=str)
        if legacy:
            self.config["screenshot_output_dir"] = legacy
            save_config(self.config)

    @Slot()
    def save_domain_list(self) -> None:
        domains = [
            line.strip()
            for line in self.url_input.toPlainText().splitlines()
            if line.strip()
        ]
        try:
            self.config = save_domain_config(
                domains,
                normalize_domain_list(domains),
            )
        except OSError as exc:
            self.status_label.setText(f"Link listesi kaydedilemedi: {exc}")
        else:
            self.clone_checker_panel.reload_whitelist()

    def _create_captcha_group(self, service: str) -> QGroupBox:
        image = QLabel("CAPTCHA bekleniyor")
        image.setAlignment(Qt.AlignCenter)
        image.setMinimumSize(160, 64)
        image.setStyleSheet("border: 1px solid palette(mid); background: white;")
        code_input = QLineEdit()
        code_input.setPlaceholderText("Güvenlik kodu")
        code_input.setEnabled(False)
        continue_button = QPushButton("Devam Et")
        continue_button.setEnabled(False)
        continue_button.clicked.connect(lambda checked=False, name=service: self.submit_captcha(name))
        code_input.returnPressed.connect(lambda name=service: self.submit_captcha(name))
        code_input.textChanged.connect(
            lambda value, name=service: self._remember_captcha_value(name, value)
        )
        status = QLabel("Hazır değil")

        controls = QVBoxLayout()
        controls.addWidget(image)
        controls.addWidget(status)
        controls.addWidget(code_input)
        controls.addWidget(continue_button)
        group = QGroupBox(service)
        group.setLayout(controls)
        self.captcha_widgets[service] = (image, code_input, continue_button)
        self.captcha_status_labels[service] = status
        return group

    def _remember_captcha_value(self, service: str, value: str) -> None:
        self.captcha_states[service].input_value = value

    @Slot()
    def check_opera_session(self) -> None:
        if self.discovery_worker and self.discovery_worker.isRunning():
            return
        self.service_targets = None
        self.start_button.setEnabled(False)
        self.status_label.setText("Hazır BTK/GüvenliNet sekmeleri aranıyor...")
        self.discovery_worker = OperaDiscoveryWorker(self)
        self.discovery_worker.discovered.connect(self.on_opera_targets_discovered)
        self.discovery_worker.failed.connect(self.on_opera_discovery_failed)
        self.discovery_worker.finished.connect(self.on_discovery_finished)
        self.discovery_worker.start()

    @Slot(object)
    def on_opera_targets_discovered(self, payload: object) -> None:
        targets = payload
        if not isinstance(targets, ServiceTargets):
            self.on_opera_discovery_failed("Opera target bilgisi doğrulanamadı.")
            return
        self.service_targets = targets
        self.on_target_bound("BTK", targets.btk_target_id)
        self.on_target_bound("Aile Profili", targets.family_target_id)
        self.add_log(f"BTK target id: {targets.btk_target_id}")
        self.add_log(f"URL: {targets.btk_url}")
        self.add_log(f"GüvenliNet target id: {targets.family_target_id}")
        self.add_log(f"URL: {targets.family_url}")
        self.status_label.setText("Opera bağlantısı hazır")
        self.start_button.setEnabled(True)
        if self.pending_start:
            self.pending_start = False
            QTimer.singleShot(0, self._begin_batch)

    @Slot(str)
    def on_opera_discovery_failed(self, error: str) -> None:
        self.service_targets = None
        self.start_button.setEnabled(True)
        self.status_label.setText(f"Hata: {error}")
        self.add_log(f"Hata: {error}")
        if self.pending_start:
            self.pending_start = False
            QMessageBox.warning(self, APP_NAME, error)

    @Slot()
    def on_discovery_finished(self) -> None:
        retry = self.pending_start and self.service_targets is None
        if self.discovery_worker is not None:
            self.discovery_worker.deleteLater()
        self.discovery_worker = None
        if retry:
            QTimer.singleShot(0, self.check_opera_session)

    @Slot()
    def select_output_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Screenshot kayıt klasörü seç", str(get_output_dir())
        )
        if not selected:
            return
        output_dir = set_output_dir(Path(selected))
        self.config["screenshot_output_dir"] = str(output_dir)
        save_config(self.config)
        self.output_dir_input.setText(str(output_dir))
        self.latest_result_path = output_dir

    @Slot()
    def start_check(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        self.pending_start = True
        self.check_opera_session()

    @Slot()
    def _begin_batch(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        if self.service_targets is None:
            self.pending_start = True
            self.check_opera_session()
            return
        try:
            domains = normalize_domains(self.url_input.toPlainText())
        except ValueError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        if not domains:
            QMessageBox.warning(self, APP_NAME, "Lütfen en az bir URL veya domain girin.")
            return

        shutdown_requested.clear()

        self.status_table.clear()
        self.status_items.clear()
        self.log_output.clear()
        try:
            clean_output_dir()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, APP_NAME, f"Screenshot klasörü temizlenemedi: {exc}")
            return
        self.add_log("Screenshot klasörü temizlendi.")
        for domain in domains:
            item = QTreeWidgetItem([domain, "Bekliyor", "Bekliyor", "✗ BTK  ✗ AileProfili"])
            self.status_table.addTopLevelItem(item)
            self.status_items[domain] = item

        self.progress_bar.setValue(0)
        self.status_label.setText(f"0/{len(domains) * 2} adım tamamlandı")
        self.start_button.setEnabled(False)
        self.open_results_button.setEnabled(False)
        self.select_output_dir_button.setEnabled(False)

        self.worker = BatchWorker(domains, shutdown_requested)
        self.worker.item_status.connect(self.on_item_status)
        self.worker.progress_changed.connect(self.on_progress)
        self.worker.log_added.connect(self.add_log)
        self.worker.captcha_ready.connect(self.on_captcha_ready)
        self.worker.captcha_reset.connect(self.on_captcha_reset)
        self.worker.target_bound.connect(self.on_target_bound)
        self.worker.finished_ok.connect(self.on_success)
        self.worker.failed.connect(self.on_failure)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    @Slot(bytes, str, str, str, bool)
    def on_captcha_ready(
        self,
        image: bytes,
        domain: str,
        service: str,
        target_id: str,
        retry: bool,
    ) -> None:
        if self.captcha_domain != domain:
            self.on_captcha_reset(domain)
        existing_target = self.captcha_target_ids.get(service)
        if existing_target and existing_target != target_id:
            self.on_failure(f"{service} UI targetId değişti; CAPTCHA reddedildi.")
            return
        other_service = "Aile Profili" if service == "BTK" else "BTK"
        if self.captcha_target_ids.get(other_service) == target_id:
            self.on_failure("BTK ve GüvenliNet aynı UI targetId kullanıyor.")
            return
        self.captcha_target_ids[service] = target_id
        state = self.captcha_states[service]
        state.target_id = target_id
        state.image = image
        state.ready = bool(image)
        state.status = "Kod bekleniyor" if image else "CAPTCHA bekleniyor"
        image_label, code_input, continue_button = self.captcha_widgets[service]
        if not image:
            image_label.clear()
            image_label.setText("CAPTCHA bekleniyor")
            code_input.setEnabled(False)
            continue_button.setEnabled(True)
            self.captcha_status_labels[service].setText(state.status)
            self.add_log(f"{service}: CAPTCHA henüz hazır değil; yenileme bekleniyor")
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(image):
            self.on_failure("CAPTCHA görseli KraLYavuz içinde gösterilemedi.")
            return
        image_label.setPixmap(
            pixmap.scaled(
                image_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )
        code_input.clear()
        code_input.setEnabled(True)
        continue_button.setEnabled(True)
        self.captcha_status_labels[service].setText(state.status)
        message = (
            f"{service}: yeni güvenlik kodunu girin"
            if retry
            else f"{service}: güvenlik kodunu girin"
        )
        self.status_label.setText(message)
        self.add_log(message)

    @Slot(str)
    def on_captcha_reset(self, domain: str) -> None:
        self._reset_captcha_panel(preserve_targets=True)
        self.captcha_domain = domain

    @Slot(str, str)
    def on_target_bound(self, service: str, target_id: str) -> None:
        other_service = "Aile Profili" if service == "BTK" else "BTK"
        if self.captcha_target_ids.get(other_service) == target_id:
            self.on_failure("BTK ve GüvenliNet aynı UI targetId kullanıyor.")
            return
        state = self.captcha_states[service]
        state.target_id = target_id
        state.status = "CAPTCHA bekleniyor"
        self.captcha_target_ids[service] = target_id
        self.captcha_status_labels[service].setText(state.status)
        self.captcha_widgets[service][2].setEnabled(True)

    @Slot()
    def submit_captcha(self, service: str = "BTK") -> None:
        _, code_input, continue_button = self.captcha_widgets[service]
        code = code_input.text().strip()
        if not self.worker or not self.worker.isRunning():
            return
        target_id = self.captcha_target_ids.get(service)
        if not target_id:
            QMessageBox.warning(self, APP_NAME, f"{service} targetId bulunamadı.")
            return
        state = self.captcha_states[service]
        state.input_value = code
        state.ready = False
        state.status = "Gönderiliyor" if code else "CAPTCHA bekleniyor"
        code_input.setEnabled(False)
        continue_button.setEnabled(False)
        self.captcha_status_labels[service].setText(state.status)
        if code:
            self.add_log(f"{service} güvenlik kodu kendi Opera sayfasına aktarılıyor.")
        else:
            self.add_log(f"{service} CAPTCHA yenileniyor.")
        try:
            self.worker.submit_captcha(service, target_id, code)
        except RuntimeError as exc:
            self.on_failure(str(exc))

    @Slot(str, str, str)
    def on_item_status(self, domain: str, service: str, status: str) -> None:
        item = self.status_items.get(domain)
        if not item:
            return
        item.setText(1 if service == "BTK" else 2, status)
        if service not in self.captcha_states:
            return
        state = self.captcha_states[service]
        if status == "Tamamlandı":
            state.status = "Tamamlandı"
            state.ready = False
            self.captcha_widgets[service][1].setEnabled(False)
            self.captcha_widgets[service][2].setEnabled(False)
        elif status.startswith("Hata"):
            state.status = "Hazır değil"
            state.ready = False
            self.captcha_widgets[service][1].setEnabled(False)
            self.captcha_widgets[service][2].setEnabled(False)
        self.captcha_status_labels[service].setText(state.status)

    @Slot(int, str)
    def on_progress(self, value: int, stage: str) -> None:
        self.progress_bar.setValue(value)
        self.status_label.setText(stage)

    @Slot(str)
    def add_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_output.appendPlainText(f"[{timestamp}] {message}")

    @Slot(object)
    def on_success(self, payload: object) -> None:
        results: List[BatchDomainResult] = list(payload)
        paths: List[Path] = []
        for result in results:
            item = self.status_items.get(result.domain)
            if item:
                item.setText(1, result.btk_status)
                item.setText(2, result.family_status)
                btk_mark = "✓" if result.btk_screenshot_ok else "✗"
                family_mark = "✓" if result.family_screenshot_ok else "✗"
                item.setText(3, f"{btk_mark} BTK  {family_mark} AileProfili")
            for path in (result.btk_screenshot_path, result.family_screenshot_path):
                if path:
                    paths.append(path)
                    self.add_log(f"Kaydedildi: {path}")
        self.start_button.setEnabled(True)
        self.select_output_dir_button.setEnabled(True)
        self.open_results_button.setEnabled(bool(paths))
        self.progress_bar.setValue(100)
        self._reset_captcha_panel()
        self.status_label.setText("Kontrol tamamlandı")
        if paths:
            self.latest_result_path = paths[0].parent
        if self.auto_exit:
            print(f"GUI_TEST_OK: {len(results)} domain", flush=True)
            QTimer.singleShot(500, QApplication.instance().quit)

    @Slot(str)
    def on_failure(self, error: str) -> None:
        self.start_button.setEnabled(True)
        self.select_output_dir_button.setEnabled(True)
        self.status_label.setText(f"Hata: {error.splitlines()[0]}")
        self.add_log(f"Hata: {error}")
        self._reset_captcha_panel()
        if self.auto_exit:
            print(f"GUI_TEST_ERROR: {error}", file=sys.stderr, flush=True)
            QTimer.singleShot(500, lambda: QApplication.instance().exit(1))

    @Slot()
    def on_worker_finished(self) -> None:
        self.worker = None
        if self.close_when_finished:
            QTimer.singleShot(0, self.close)

    def _reset_captcha_panel(self, preserve_targets: bool = False) -> None:
        self.captcha_domain = None
        if not preserve_targets:
            self.captcha_target_ids.clear()
        for service, (image, code_input, continue_button) in self.captcha_widgets.items():
            target_id = self.captcha_target_ids.get(service, "") if preserve_targets else ""
            state = self.captcha_states[service]
            state.target_id = target_id
            state.image = b""
            state.input_value = ""
            state.ready = False
            state.status = "CAPTCHA bekleniyor" if target_id else "Hazır değil"
            image.clear()
            image.setText("CAPTCHA bekleniyor")
            code_input.clear()
            code_input.setEnabled(False)
            continue_button.setEnabled(bool(target_id))
            self.captcha_status_labels[service].setText(state.status)

    @Slot()
    def open_results(self) -> None:
        QDesktopServices.openUrl(self.latest_result_path.resolve().as_uri())

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker and self.worker.isRunning():
            shutdown_requested.set()
            self.close_when_finished = True
            self.worker.cancel()
            self.status_label.setText("Kontroller durduruluyor...")
            event.ignore()
            return
        event.accept()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--auto-start", action="store_true")
    parser.add_argument("--auto-exit", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_config()
    get_output_dir().mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    set_application_icon(app)
    window = MainWindow(auto_start=args.auto_start, auto_exit=args.auto_exit)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
