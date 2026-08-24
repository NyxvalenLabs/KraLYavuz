from dataclasses import replace
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QPoint, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from .models import CloneCheckResult, SearchResult
from .reporting import build_clipboard_report, report_result_count
from .scoring import CLONE_CANDIDATE_THRESHOLD
from .service import RISK_STATUSES, CloneCheckerService


class CloneCheckWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self, service: CloneCheckerService, brand_name: str, main_domain: str
    ) -> None:
        super().__init__()
        self.service = service
        self.brand_name = brand_name
        self.main_domain = main_domain

    def run(self) -> None:
        try:
            result = self.service.check(self.brand_name, self.main_domain)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(result)


class CloneCheckerPanel(QGroupBox):
    log_added = Signal(str)

    def __init__(self, service: Optional[CloneCheckerService] = None) -> None:
        super().__init__("Klon Kontrol")
        self.service = service or CloneCheckerService()
        self.worker: Optional[CloneCheckWorker] = None
        self.current_result: Optional[CloneCheckResult] = None

        self.brand_input = QLineEdit()
        self.brand_input.setPlaceholderText("Marka adı")
        self.main_domain_input = QLineEdit()
        self.main_domain_input.setPlaceholderText("Ana Domain")
        self.check_button = QPushButton("Kontrol Et")
        self.check_button.clicked.connect(self.run_check)
        self.copy_button = QPushButton("Kopyala")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_risk_report)
        self.brand_input.returnPressed.connect(self.run_check)
        self.main_domain_input.returnPressed.connect(self.run_check)

        self.result_label = QLabel("Henüz kontrol yapılmadı.")
        self.result_label.setWordWrap(True)
        self.result_table = QTreeWidget()
        self.result_table.setHeaderLabels(
            [
                "Arama",
                "Anahtar Kelime",
                "Sıra",
                "Başlık",
                "URL",
                "Redirect",
                "Durum",
            ]
        )
        self.result_table.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.result_table.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.result_table.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.result_table.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.result_table.header().setSectionResizeMode(4, QHeaderView.Stretch)
        self.result_table.header().setSectionResizeMode(5, QHeaderView.Stretch)
        self.result_table.header().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.result_table.setRootIsDecorated(False)
        self.result_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.result_table.customContextMenuRequested.connect(self._show_context_menu)
        self.result_table.setMinimumHeight(120)
        self.result_table.setMaximumHeight(180)
        self.result_table.hide()

        controls = QHBoxLayout()
        controls.addWidget(self.brand_input)
        controls.addWidget(QLabel("Ana Domain"))
        controls.addWidget(self.main_domain_input)
        controls.addWidget(self.check_button)

        result_header = QHBoxLayout()
        result_header.addWidget(self.result_label, 1)
        result_header.addWidget(self.copy_button)

        self.whitelist_table = QTreeWidget()
        self.whitelist_table.setHeaderLabels(["Domain", "Eklenme tarihi"])
        self.whitelist_table.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.whitelist_table.header().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self.whitelist_table.setRootIsDecorated(False)
        self.whitelist_table.setMaximumHeight(96)
        self.whitelist_delete_button = QPushButton("Sil")
        self.whitelist_delete_button.clicked.connect(self._remove_whitelist_entry)
        whitelist_header = QHBoxLayout()
        whitelist_header.addWidget(QLabel("Whitelist Yönetimi"))
        whitelist_header.addStretch(1)
        whitelist_header.addWidget(self.whitelist_delete_button)

        layout = QVBoxLayout()
        layout.addLayout(controls)
        layout.addLayout(result_header)
        layout.addWidget(self.result_table)
        layout.addLayout(whitelist_header)
        layout.addWidget(self.whitelist_table)
        self.setLayout(layout)
        self._reload_whitelist()

    @Slot()
    def run_check(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        brand_name = self.brand_input.text().strip()
        main_domain = self.main_domain_input.text().strip()
        if not brand_name:
            result = self.service.check(brand_name)
            self.result_label.setText(result.message)
            return
        self.result_table.clear()
        self.result_table.hide()
        self.current_result = None
        self.copy_button.setEnabled(False)
        self.result_label.setText("Arama sonuçları alınıyor...")
        self.brand_input.setEnabled(False)
        self.main_domain_input.setEnabled(False)
        self.check_button.setEnabled(False)
        self.worker = CloneCheckWorker(self.service, brand_name, main_domain)
        self.worker.completed.connect(self._show_results)
        self.worker.failed.connect(self._show_error)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def run_placeholder_check(self) -> None:
        self.run_check()

    @Slot(object)
    def _show_results(self, payload: object) -> None:
        result = payload
        if not isinstance(result, CloneCheckResult):
            self._show_error("Clone Checker sonucu doğrulanamadı.")
            return
        risky_results = tuple(
            item
            for item in result.results
            if item.clone_result and item.clone_result.status in RISK_STATUSES
        )
        result = replace(result, results=risky_results)
        self.current_result = result
        self.result_label.setText(result.message)
        self.result_table.clear()
        for item in result.results:
            if item.redirect is None:
                redirect_text = "Kontrol edilemedi"
                redirect_detail = redirect_text
            elif len(item.redirect.redirect_chain) == 1:
                redirect_text = "Yok"
                redirect_detail = "Yönlendirme yok"
            else:
                redirect_text = item.redirect.final_url
                codes = ", ".join(str(code) for code in item.redirect.status_codes)
                redirect_detail = (
                    f"HTTP: {codes}\n" + " -> ".join(item.redirect.redirect_chain)
                )
            status_text = self._status_text(item)
            status_detail = self._status_detail(item)
            row = QTreeWidgetItem(
                [
                    item.search_engine,
                    item.keyword,
                    str(item.rank),
                    item.title,
                    item.url,
                    redirect_text,
                    status_text,
                ]
            )
            row.setToolTip(5, redirect_detail)
            row.setToolTip(6, status_detail)
            row.setData(0, Qt.UserRole, item)
            self.result_table.addTopLevelItem(row)
        self.result_table.setVisible(bool(result.results))
        self.copy_button.setEnabled(bool(build_clipboard_report(result.results)))
        if result.brand_name:
            self.log_added.emit(
                f"Klon Kontrol: {result.brand_name} için {len(result.results)} sonuç alındı."
            )

    @Slot()
    def copy_risk_report(self) -> None:
        if not self.current_result or not self.current_result.results:
            return
        report = build_clipboard_report(self.current_result.results)
        if not report:
            self.result_label.setText("Kopyalanabilir riskli sonuç bulunamadı.")
            return
        QApplication.clipboard().setText(report)
        count = report_result_count(report)
        self.result_label.setText(
            f"{count} benzersiz riskli sonuç panoya kopyalandı."
        )

    @Slot(QPoint)
    def _show_context_menu(self, position: QPoint) -> None:
        row = self.result_table.itemAt(position)
        if row is None:
            return
        menu = QMenu(self.result_table)
        safe_action = menu.addAction("Güvenli olarak işaretle")
        selected = menu.exec(self.result_table.viewport().mapToGlobal(position))
        if selected == safe_action:
            self._mark_result_safe(row)

    def _mark_result_safe(self, row: QTreeWidgetItem) -> None:
        result = row.data(0, Qt.UserRole)
        if not isinstance(result, SearchResult):
            return
        try:
            domain = self.service.whitelist.add_domain(
                self.service.whitelist.domain_for_result(result)
            )
        except (OSError, ValueError) as exc:
            self._show_error(f"Whitelist kaydedilemedi: {exc}")
            return

        if self.current_result:
            remaining = tuple(
                item
                for item in self.current_result.results
                if not self.service.whitelist.is_whitelisted_result(item)
            )
            self.current_result = replace(self.current_result, results=remaining)
            self._show_results(self.current_result)
        self._reload_whitelist()
        self.result_label.setText(f"{domain} güvenli olarak işaretlendi.")
        self.log_added.emit(f"Klon Kontrol whitelist: {domain} eklendi.")

    @Slot()
    def _remove_whitelist_entry(self) -> None:
        item = self.whitelist_table.currentItem()
        if item is None:
            return
        domain = item.text(0)
        try:
            removed = self.service.whitelist.remove_domain(domain)
        except OSError as exc:
            self._show_error(f"Whitelist güncellenemedi: {exc}")
            return
        if removed:
            self._reload_whitelist()
            self.result_label.setText(f"{domain} whitelist'ten silindi.")
            self.log_added.emit(f"Klon Kontrol whitelist: {domain} silindi.")

    def _reload_whitelist(self) -> None:
        self.whitelist_table.clear()
        for entry in self.service.whitelist.entries():
            self.whitelist_table.addTopLevelItem(
                QTreeWidgetItem(
                    [entry.domain, self._format_added_at(entry.added_at)]
                )
            )

    @staticmethod
    def _format_added_at(value: str) -> str:
        try:
            return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return value or "-"

    @staticmethod
    def _status_text(result: SearchResult) -> str:
        if result.clone_result:
            icons = {
                "Sorunsuz": "✅",
                "Klon": "🚨",
                "Klon adayı": "🚨",
                "Hariç tutuldu": "⚪",
            }
            icon = icons.get(result.clone_result.status, "")
            return f"{icon} {result.clone_result.status}".strip()
        candidate = result.candidate
        if candidate and candidate.score >= CLONE_CANDIDATE_THRESHOLD:
            return "🚨 Klon adayı"
        return "Normal sonuç"

    @staticmethod
    def _status_detail(result: SearchResult) -> str:
        details = []
        if result.clone_result:
            details.append(result.clone_result.status_reason)
        if result.excluded:
            details.append("Skorlama uygulanmadı")
        elif result.candidate:
            details.append(f"Skor: {result.candidate.score}")
            details.extend(result.candidate.reasons or ("Kural eşleşmesi yok",))
        else:
            details.append("Skor hesaplanamadı")
        return "\n".join(details)

    @Slot(str)
    def _show_error(self, error: str) -> None:
        self.result_label.setText(f"Hata: {error}")
        self.log_added.emit(f"Klon Kontrol hatası: {error}")

    @Slot()
    def _worker_finished(self) -> None:
        self.brand_input.setEnabled(True)
        self.main_domain_input.setEnabled(True)
        self.check_button.setEnabled(True)
        if self.worker is not None:
            self.worker.deleteLater()
        self.worker = None
