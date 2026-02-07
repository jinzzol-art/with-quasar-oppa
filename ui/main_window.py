"""
AI 문서 검토 시스템 v5.0 - 공고문 기반 통합 검증

- 다중 파일 추가 후 한 번에 분석 (동시 최대 4개)
- 모든 분석 완료 시 결과 한 화면에 출력
"""
from __future__ import annotations

import os
from typing import Optional
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QMutex, QMutexLocker
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QCheckBox,
    QProgressBar,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTabWidget,
    QMessageBox,
    QGroupBox,
    QComboBox,
)

from core.exclusion_rules import AnnouncementConfig, AnnouncementConfigManager
from core.integrated_verification import (
    IntegratedVerificationSystem,
    FinalVerdict,
    convert_ai_result_to_exclusion_data,
)
from core.enhanced_validation_engine import EnhancedValidator
from core.result_formatter import format_result_for_ui
from core.announcement_parser import parse_announcement_pdf
from core.data_models import PublicHousingReviewResult


# ★ v4: 순차 처리 (동시 API 호출 방지)
MAX_CONCURRENT_WORKERS = 1
DELAY_BETWEEN_FILES_MS = 200


@dataclass
class AnalysisTask:
    """분석 작업 정보"""
    file_path: str
    file_name: str
    status: str = "대기중"
    progress: int = 0
    ai_result: Optional[PublicHousingReviewResult] = None  # 병합·검증 1회용
    meta: Optional[dict] = None
    error: str = ""


class SingleFileWorker(QThread):
    """단일 파일 분석 워커 - PDF만 분석, 검증은 그룹 병합 후 1회만"""
    
    finished = Signal(str, object, dict)  # file_path, ai_result, meta
    error = Signal(str, str)
    progress = Signal(str, str, int)

    def __init__(
        self,
        file_path: str,
        config: AnnouncementConfig,
        housing_type: str = "일반",
        enable_dual: bool = False,
        parent=None
    ):
        super().__init__(parent)
        self.file_path = file_path
        self.config = config
        self.housing_type = housing_type
        self.enable_dual = enable_dual

    def run(self):
        try:
            self.progress.emit(self.file_path, "분석 중...", 10)
            from core.single_shot_analyzer import SingleShotPDFAnalyzer
            analyzer = SingleShotPDFAnalyzer(provider="gemini")
            ai_result, meta = analyzer.analyze(
                self.file_path,
                self.config.announcement_date,
            )
            self.progress.emit(self.file_path, "완료", 100)
            self.finished.emit(self.file_path, ai_result, meta)
        except Exception as e:
            import traceback
            self.error.emit(self.file_path, f"{e}\n{traceback.format_exc()}")


class MultiFileAnalyzer(QThread):
    """다중 파일 분석 관리자 - 동시 최대 4개"""
    
    task_started = Signal(str)
    task_progress = Signal(str, str, int)
    task_finished = Signal(str, object, dict)  # file_path, ai_result, meta
    task_error = Signal(str, str)
    all_finished = Signal()

    def __init__(
        self,
        file_paths: list[str],
        config: AnnouncementConfig,
        housing_type: str = "일반",
        enable_dual: bool = False,
        parent=None
    ):
        super().__init__(parent)
        self.file_paths = file_paths
        self.config = config
        self.housing_type = housing_type
        self.enable_dual = enable_dual
        self.active_workers: list[SingleFileWorker] = []
        self.pending_files: list[str] = []
        self.mutex = QMutex()
        self._stop_requested = False

    def run(self):
        self.pending_files = list(self.file_paths)
        self._stop_requested = False
        
        while len(self.active_workers) < MAX_CONCURRENT_WORKERS and self.pending_files:
            self._start_next_worker()
        
        while self.active_workers or self.pending_files:
            if self._stop_requested:
                break
            self.msleep(50)
        
        self.all_finished.emit()

    def _start_next_worker(self):
        if not self.pending_files:
            return
        with QMutexLocker(self.mutex):
            file_path = self.pending_files.pop(0)
        
        self.task_started.emit(file_path)
        worker = SingleFileWorker(
            file_path,
            self.config,
            self.housing_type,
            self.enable_dual,
            self
        )
        worker.progress.connect(self._on_worker_progress)
        worker.finished.connect(self._on_worker_finished)
        worker.error.connect(self._on_worker_error)
        self.active_workers.append(worker)
        worker.start()

    def _on_worker_progress(self, file_path: str, status: str, percent: int):
        self.task_progress.emit(file_path, status, percent)

    def _on_worker_finished(self, file_path: str, ai_result, meta: dict):
        self.task_finished.emit(file_path, ai_result, meta)
        self._remove_worker(file_path)
        QTimer.singleShot(DELAY_BETWEEN_FILES_MS, self._start_next_worker)

    def _on_worker_error(self, file_path: str, error: str):
        self.task_error.emit(file_path, error)
        self._remove_worker(file_path)
        QTimer.singleShot(DELAY_BETWEEN_FILES_MS, self._start_next_worker)

    def _remove_worker(self, file_path: str):
        with QMutexLocker(self.mutex):
            self.active_workers = [w for w in self.active_workers if w.file_path != file_path]

    def stop(self):
        self._stop_requested = True


class MainWindow(QMainWindow):
    """메인 윈도우 - 다중 파일 · 동시 4개 분석"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.tasks: dict[str, AnalysisTask] = {}
        self.file_widgets: dict[str, QListWidgetItem] = {}
        self.analyzer: Optional[MultiFileAnalyzer] = None
        self.current_config: Optional[AnnouncementConfig] = None
        self.config_manager = AnnouncementConfigManager()
        self._error_tab_index: Optional[int] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        central = QWidget(self)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(8)
        
        left_panel = self._create_left_panel()
        right_panel = self._create_right_panel()
        
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([400, 800])
        root_layout.addWidget(splitter)
        self.setCentralWidget(central)

    def _create_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # 공고문
        config_group = QGroupBox("공고문")
        config_layout = QVBoxLayout(config_group)
        self.config_label = QLabel("적용 공고: (선택 없음)")
        self.config_label.setStyleSheet("color: #333; font-weight: bold;")
        config_layout.addWidget(self.config_label)
        
        btn_layout = QHBoxLayout()
        upload_btn = QPushButton("공고문 PDF 업로드")
        upload_btn.clicked.connect(self._on_upload_announcement)
        self.saved_configs_combo = QComboBox()
        self.saved_configs_combo.addItem("-- 저장된 공고문 --")
        self.saved_configs_combo.currentIndexChanged.connect(self._on_select_saved_config)
        btn_layout.addWidget(upload_btn)
        btn_layout.addWidget(self.saved_configs_combo, 1)
        config_layout.addLayout(btn_layout)
        layout.addWidget(config_group)
        
        # 주택 유형
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("주택 유형:"))
        self.housing_type_combo = QComboBox()
        self.housing_type_combo.addItems(["일반", "다세대", "기타"])
        type_layout.addWidget(self.housing_type_combo, 1)
        layout.addLayout(type_layout)
        
        self.dual_check = QCheckBox("이중검증 (느리지만 정확)")
        layout.addWidget(self.dual_check)
        
        # 파일 목록
        file_group = QGroupBox("분석 대상 파일")
        file_layout = QVBoxLayout(file_group)
        self.file_list = QListWidget()
        self.file_list.setMinimumHeight(120)
        file_layout.addWidget(self.file_list)
        
        file_btn_layout = QHBoxLayout()
        self.add_files_btn = QPushButton("파일 추가")
        self.add_files_btn.clicked.connect(self._on_add_files)
        self.clear_btn = QPushButton("전체 삭제")
        self.clear_btn.clicked.connect(self._on_clear_files)
        file_btn_layout.addWidget(self.add_files_btn)
        file_btn_layout.addWidget(self.clear_btn)
        file_layout.addLayout(file_btn_layout)
        layout.addWidget(file_group)
        
        # 분석 제어
        self.analyze_btn = QPushButton("분석 시작")
        self.analyze_btn.clicked.connect(self._on_start_analysis)
        self.stop_btn = QPushButton("중지")
        self.stop_btn.clicked.connect(self._on_stop_analysis)
        self.stop_btn.setEnabled(False)
        
        ctrl_layout = QHBoxLayout()
        ctrl_layout.addWidget(self.analyze_btn)
        ctrl_layout.addWidget(self.stop_btn)
        layout.addLayout(ctrl_layout)
        
        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        layout.addWidget(self.overall_progress)
        
        self.status_label = QLabel("파일을 추가하세요")
        self.stats_label = QLabel("")
        layout.addWidget(self.status_label)
        layout.addWidget(self.stats_label)
        layout.addStretch()
        return panel

    def _create_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        
        title = QLabel("📋 검토 결과 (한 화면 정리)")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title)
        
        self.result_tabs = QTabWidget()
        self.result_tabs.setTabsClosable(True)
        self.result_tabs.tabCloseRequested.connect(self._on_close_result_tab)
        layout.addWidget(self.result_tabs, 1)
        
        self._error_tab_index = None
        default_widget = QTextEdit()
        default_widget.setReadOnly(True)
        default_widget.setPlaceholderText(
            "공고문 기반 AI 문서 검토 시스템\n\n"
            "1. 공고문 업로드(또는 저장된 공고문 선택)\n"
            "2. '파일 추가'로 PDF 여러 개 선택\n"
            "3. '분석 시작' 클릭 → 최대 4개 동시 분석\n\n"
            "모든 분석이 끝나면 '검토 결과' 탭에 한 번에 표시됩니다."
        )
        self.result_tabs.addTab(default_widget, "안내")
        self.result_text_edit = QTextEdit()
        self.result_text_edit.setReadOnly(True)
        self.result_tabs.addTab(self.result_text_edit, "검토 결과")
        return panel

    def _refresh_saved_configs(self):
        self.saved_configs_combo.clear()
        self.saved_configs_combo.addItem("-- 저장된 공고문 --")
        for config_id in self.config_manager.list_configs():
            self.saved_configs_combo.addItem(config_id)

    def _on_upload_announcement(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "공고문 PDF 선택", "", "PDF 파일 (*.pdf)"
        )
        if not file_path:
            return
        file_name = Path(file_path).stem
        region = "미지정"
        if "경기남부" in file_name:
            region = "경기남부"
        elif "경기북부" in file_name:
            region = "경기북부"
        elif "서울" in file_name:
            region = "서울"
        try:
            config = parse_announcement_pdf(file_path, region)
            self.current_config = config
            self.config_label.setText(f"현재: {config.title}")
            self._refresh_saved_configs()
            QMessageBox.information(self, "완료", f"공고문 설정 완료!\n{config.title}")
        except Exception as e:
            QMessageBox.warning(self, "오류", f"공고문 파싱 오류:\n{e}")

    def _on_select_saved_config(self, index: int):
        if index <= 0:
            return
        config_id = self.saved_configs_combo.itemText(index)
        config = self.config_manager.load_config(config_id)
        if config:
            self.current_config = config
            self.config_label.setText(f"현재: {config.title}")

    def _on_add_files(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "PDF 파일 선택 (다중 선택 가능)", "", "PDF 파일 (*.pdf)"
        )
        if not file_paths:
            return
        for file_path in file_paths:
            if file_path in self.tasks:
                continue
            self.tasks[file_path] = AnalysisTask(
                file_path=file_path,
                file_name=Path(file_path).name,
            )
            item = QListWidgetItem(Path(file_path).name)
            item.setData(Qt.UserRole, file_path)
            self.file_list.addItem(item)
            self.file_widgets[file_path] = item
        self._update_status()

    def _on_clear_files(self):
        self.tasks.clear()
        self.file_widgets.clear()
        self.file_list.clear()
        self._update_status()
        self.stats_label.setText("")

    def _update_status(self):
        count = len(self.tasks)
        if count == 0:
            self.status_label.setText("파일을 추가하세요")
        else:
            self.status_label.setText(f"{count}개 파일 준비됨")

    def _on_start_analysis(self):
        if not self.current_config:
            QMessageBox.warning(self, "알림", "먼저 공고문을 업로드하거나 저장된 공고문을 선택하세요.")
            return
        pending_files = [p for p in self.tasks.keys() if self.tasks[p].status == "대기중"]
        if not pending_files:
            QMessageBox.information(self, "알림", "분석할 파일이 없습니다.")
            return
        
        self.analyze_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.add_files_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.overall_progress.setValue(0)
        self.overall_progress.setMaximum(len(pending_files))
        housing_type = self.housing_type_combo.currentText()
        
        self.analyzer = MultiFileAnalyzer(
            pending_files,
            self.current_config,
            housing_type,
            self.dual_check.isChecked()
        )
        self.analyzer.task_started.connect(self._on_task_started)
        self.analyzer.task_progress.connect(self._on_task_progress)
        self.analyzer.task_finished.connect(self._on_task_finished)
        self.analyzer.task_error.connect(self._on_task_error)
        self.analyzer.all_finished.connect(self._on_all_finished)
        self.analyzer.start()
        
        self.result_text_edit.clear()
        self.result_text_edit.setPlainText(
            f"분석 중... (0/{len(pending_files)})\n\n"
            "모든 분석이 끝나면 여기에 결과가 한 번에 표시됩니다."
        )
        while self.result_tabs.count() > 2:
            self.result_tabs.removeTab(2)
        self._error_tab_index = None
        self.result_tabs.setCurrentIndex(0)
        self.status_label.setText(f"분석 중... (0/{len(pending_files)})")

    def _on_stop_analysis(self):
        if self.analyzer:
            self.analyzer.stop()
            self.status_label.setText("중지됨")
        self.analyze_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.add_files_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)

    def _on_task_started(self, file_path: str):
        if file_path in self.tasks:
            self.tasks[file_path].status = "분석중"
        if file_path in self.file_widgets:
            self.file_widgets[file_path].setText(Path(file_path).name + " [분석중]")

    def _on_task_progress(self, file_path: str, status: str, percent: int):
        if file_path in self.tasks:
            self.tasks[file_path].progress = percent
        if file_path in self.file_widgets:
            self.file_widgets[file_path].setText(Path(file_path).name + f" [분석중 {percent}%]")

    def _on_task_finished(self, file_path: str, ai_result, meta: dict):
        if file_path in self.tasks:
            self.tasks[file_path].status = "완료"
            self.tasks[file_path].ai_result = ai_result
            self.tasks[file_path].meta = meta
        if file_path in self.file_widgets:
            self.file_widgets[file_path].setText(Path(file_path).name + " [완료]")
        completed = sum(1 for t in self.tasks.values() if t.status == "완료")
        total = len(self.tasks)
        self.result_text_edit.setPlainText(
            f"분석 중... ({completed}/{total})\n\n"
            "모든 분석이 끝나면 같은 건물(지번)끼리 묶어 검증하고, 보완서류 목록을 한 번만 표시합니다."
        )
        self.overall_progress.setValue(completed)
        self.status_label.setText(f"분석 중... ({completed}/{total})")

    def _on_task_error(self, file_path: str, error: str):
        if file_path in self.tasks:
            self.tasks[file_path].status = "오류"
            self.tasks[file_path].error = error
        if file_path in self.file_widgets:
            self.file_widgets[file_path].setText(Path(file_path).name + " [❌ 오류]")
        
        completed = sum(1 for t in self.tasks.values() if t.status in ("완료", "오류"))
        total = len(self.tasks)
        self.result_text_edit.setPlainText(
            f"분석 중... ({completed}/{total})\n\n"
            "모든 분석이 끝나면 여기에 결과가 한 번에 표시됩니다."
        )
        file_name = Path(file_path).name
        section = "\n\n" + "=" * 70 + "\n" + f"❌ 파일: {file_name} (오류)\n" + "=" * 70 + "\n\n오류 발생:\n\n" + error
        if self._error_tab_index is None or self._error_tab_index >= self.result_tabs.count():
            err_edit = QTextEdit()
            err_edit.setReadOnly(True)
            self._error_tab_index = self.result_tabs.addTab(err_edit, "❌ 오류")
            err_edit.setPlainText("【 오류 발생 파일 】\n\n" + section.lstrip())
        else:
            w = self.result_tabs.widget(self._error_tab_index)
            if isinstance(w, QTextEdit):
                w.append(section)
        self.overall_progress.setValue(completed)
        self._update_stats()

    def _on_all_finished(self):
        completed = sum(1 for t in self.tasks.values() if t.status == "완료")
        total = len(self.tasks)
        self.status_label.setText(f"완료! ({completed}/{total})")
        self.analyze_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.add_files_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)
        
        # 완료된 작업만 그룹별로 묶기: 같은 건물(지번) = 파일명 공통 접두사로 한 그룹
        completed_tasks = [(p, t) for p, t in self.tasks.items() if t.status == "완료" and t.ai_result]
        groups: dict[str, list[tuple[str, AnalysisTask]]] = {}
        if not completed_tasks:
            pass
        elif len(completed_tasks) == 1:
            groups["결과"] = completed_tasks
        else:
            stems = [Path(p).stem for p, _ in completed_tasks]
            common = os.path.commonprefix(stems).strip().rstrip(" _-")
            group_key = common if common else "결과"
            groups[group_key] = completed_tasks
        
        review_date = datetime.now().strftime("%Y-%m-%d")
        housing_type = self.housing_type_combo.currentText()
        verification_system = IntegratedVerificationSystem(self.current_config) if self.current_config else None
        verdict_counts = {"EXCLUDED": 0, "CONDITIONAL": 0, "SUPPLEMENTARY": 0, "APPROVED": 0}
        
        lines = ["【 검토 결과 】", ""]
        if self.current_config:
            lines.append(f"적용 공고: {self.current_config.title}")
            lines.append(f"검토일자: {review_date}")
            lines.append("")
        lines.append("-" * 70)
        
        # RULES_LIST 가져오기
        from core.verification_rules import RULES_LIST
        
        for group_key, group_tasks in sorted(groups.items(), key=lambda x: x[0]):
            file_paths = [p for p, _ in group_tasks]
            ai_results = [t.ai_result for _, t in group_tasks if t.ai_result]
            if not ai_results or not self.current_config:
                continue
            merged = PublicHousingReviewResult.merge_results(ai_results, review_date, self.current_config.announcement_date)
            exclusion_data = convert_ai_result_to_exclusion_data(merged, housing_type)
            integrated = verification_system.verify(exclusion_data, merged)
            verdict_counts[integrated.final_verdict.name] = verdict_counts.get(integrated.final_verdict.name, 0) + 1
            
            icon = {"EXCLUDED": "❌", "CONDITIONAL": "⚠️", "SUPPLEMENTARY": "📝", "APPROVED": "✅"}.get(integrated.final_verdict.name, "📝")
            verdict_text = {"EXCLUDED": "매입제외", "CONDITIONAL": "조건부 검토", "SUPPLEMENTARY": "보완필요", "APPROVED": "심사가능"}.get(integrated.final_verdict.name, "보완필요")
            
            # ★★★ 모든 내용을 34개 검증 항목 하나에 통합 ★★★
            lines.append(f"[ 34개 검증 항목별 결과 ] {icon} {verdict_text}")
            lines.append(f"파일: {', '.join(Path(p).name for p in sorted(file_paths)[:5])}{' ...' if len(file_paths) > 5 else ''}")
            lines.append("")
            
            # 보완서류 목록을 규칙 번호별로 정리
            by_rule = {}
            if integrated.stage2_result and integrated.stage2_result.supplementary_documents:
                for doc in integrated.stage2_result.supplementary_documents:
                    rule_num = doc.rule_number
                    if rule_num not in by_rule:
                        by_rule[rule_num] = []
                    by_rule[rule_num].append((doc.document_name, doc.reason))
            
            passed_count = 0
            failed_count = 0
            
            # 서류별 추출 정보 준비
            hsa = merged.housing_sale_application
            owner = hsa.owner_info
            seal = hsa.seal_verification
            owner_id = merged.owner_identity
            bl_title = merged.building_ledger_title
            bl_excl = merged.building_ledger_exclusive
            ll = merged.land_ledger
            lup = merged.land_use_plan
            br = merged.building_registry
            lr = merged.land_registry
            rs = merged.rental_status
            poa = merged.power_of_attorney
            corp = merged.corporate_documents
            consent = merged.consent_form
            pledge = merged.integrity_pledge
            lh_confirm = merged.lh_employee_confirmation
            realtor = merged.realtor_documents
            as_built = merged.as_built_drawing
            test_cert = merged.test_certificate_delivery
            bl_summary = merged.building_ledger_summary
            bl_layout = merged.building_layout_plan
            trust_docs = merged.trust_documents
            agent = hsa.agent_info  # 대리인 정보 (규칙 5, 9~11용)
            is_proxy = agent.exists and bool(agent.name and str(agent.name).strip())  # 대리접수 여부
            is_corp = corp.is_corporation  # 법인 여부 (규칙 15, 17용)
            is_realtor = realtor.is_realtor_agent if hasattr(realtor, 'is_realtor_agent') else False  # 중개사 여부 (규칙 18용)
            
            for rule_num, rule_name, rule_desc in RULES_LIST:
                if rule_num in by_rule:
                    items = by_rule[rule_num]
                    reasons = "; ".join(r for (_, r) in items[:2])
                    if len(items) > 2:
                        reasons += f" 외 {len(items) - 2}건"
                    lines.append(f"{rule_num:2d}. ❌ {rule_desc}")
                    lines.append(f"    → {reasons}")
                    failed_count += 1
                else:
                    # 조건부 규칙: 해당 안 되면 ➖ 표시
                    if rule_num == 5 and not is_proxy:
                        lines.append(f"{rule_num:2d}. ➖ {rule_desc} (대리접수 아님)")
                    elif rule_num in (9, 10, 11) and not is_proxy:
                        lines.append(f"{rule_num:2d}. ➖ {rule_desc} (대리접수 아님)")
                    elif rule_num == 15 and not is_corp:
                        lines.append(f"{rule_num:2d}. ➖ {rule_desc} (법인 아님)")
                    elif rule_num == 17 and not is_corp:
                        lines.append(f"{rule_num:2d}. ➖ {rule_desc} (법인 아님)")
                    elif rule_num == 18 and not is_realtor:
                        lines.append(f"{rule_num:2d}. ➖ {rule_desc} (중개사 아님)")
                    else:
                        lines.append(f"{rule_num:2d}. ✅ {rule_desc}")
                    passed_count += 1
                
                # 각 규칙에 해당하는 서류 추출 정보 표시
                if rule_num == 1:
                    lines.append(f'    "주택매도신청서": 존재={hsa.exists}')
                elif rule_num == 2:
                    lines.append(f'    "주택매도신청서": 작성일={hsa.written_date or "[미추출]"}')
                elif rule_num == 3:
                    lines.append(f'    "주택매도신청서": 성명={owner.name or "[미추출]"}, 생년월일={owner.birth_date or "[미추출]"}, 주소={owner.address or "[미추출]"}, 연락처={owner.phone or "[미추출]"}, 이메일={owner.email or "[미추출]"}')
                elif rule_num == 4:
                    lines.append(f'    "주택매도신청서": 인감날인={seal.seal_exists}, "인감증명서": 존재={owner_id.seal_certificate.exists}, 일치율={seal.match_rate or "[미검증]"}%')
                elif rule_num == 5:
                    if is_proxy:
                        lines.append(f'    "주택매도신청서": 대리인={agent.name or "[없음]"}, "대리인신분증사본": 존재={agent.exists}, 일치={agent.id_card_match}')
                    else:
                        lines.append(f'    대리접수 아님 → 해당없음')
                elif rule_num == 6:
                    lines.append(f'    "주택매도신청서": 대지면적={hsa.land_area or "[미추출]"}㎡, "토지대장": {ll.land_area or "[미추출]"}㎡, "토지이용계획확인원": {lup.land_area or "[미추출]"}㎡')
                elif rule_num == 7:
                    lines.append(f'    "주택매도신청서": 사용승인일={hsa.approval_date or "[미추출]"}, "건축물대장 표제부": {bl_title.approval_date or "[미추출]"}')
                elif rule_num == 8:
                    lines.append(f'    "임대현황": 호수={len(rs.units)}개, "건축물대장 전유부": 호수={len(bl_excl.units)}개')
                elif rule_num == 9:
                    if is_proxy:
                        lines.append(f'    "위임장": 존재={poa.exists}')
                    else:
                        lines.append(f'    대리인 란 비어있음 → 대리접수 아님')
                elif rule_num == 10:
                    if is_proxy:
                        lines.append(f'    "위임장": 소재지={poa.location or "[미추출]"}, 대지면적={poa.land_area or "[미추출]"}㎡')
                    else:
                        lines.append(f'    대리접수 아님 → 해당없음')
                elif rule_num == 11:
                    if is_proxy:
                        lines.append(f'    "위임장": 작성일={poa.written_date or "[미추출]"}, 위임자인감={poa.delegator.seal_valid}, 수임자인감={poa.delegatee.seal_valid}')
                    else:
                        lines.append(f'    대리접수 아님 → 해당없음')
                elif rule_num == 12:
                    lines.append(f'    "인감증명서": 존재={owner_id.seal_certificate.exists}, 발급일={owner_id.seal_certificate_issue_date or "[미추출]"}')
                elif rule_num == 13:
                    lines.append(f'    "소유자 신분증": 제출={owner_id.all_ids_submitted}, 수량={len(owner_id.identity_documents)}')
                elif rule_num == 14:
                    lines.append(f'    "소유자 신분증": 소유자수={owner_id.owner_count}, 신분증수={len(owner_id.identity_documents)}, 전원제출={owner_id.all_ids_submitted}')
                elif rule_num == 15:
                    if is_corp:
                        lines.append(f'    법인여부={corp.is_corporation}, "사업자등록증"={corp.business_registration.exists}, "법인인감증명서"={corp.corporate_seal_certificate.exists}, "법인등기부"={corp.corporate_registry.exists}')
                    else:
                        lines.append(f'    법인 아님 → 해당없음')
                elif rule_num == 16:
                    lines.append(f'    "개인정보동의서": 존재={consent.exists}, 소유자작성={consent.owner_signed}, 인감유효={consent.owner_seal_valid}')
                elif rule_num == 17:
                    if is_corp:
                        lines.append(f'    "연간계약건수동의서": 존재={corp.contract_limit_consent.exists}, 전원서명={corp.all_executives_signed}')
                    else:
                        lines.append(f'    법인 아님 → 해당없음')
                elif rule_num == 18:
                    if is_realtor:
                        lines.append(f'    "중개사무소등록증"={realtor.office_registration.exists}, "사업자등록증"={realtor.business_registration.exists}')
                    else:
                        lines.append(f'    중개사 아님 → 해당없음')
                elif rule_num == 19:
                    lines.append(f'    "청렴서약서": 존재={pledge.exists}, 소유자작성={pledge.owner_submitted}')
                elif rule_num == 20:
                    lines.append(f'    "공사직원여부확인서": 존재={lh_confirm.exists}')
                elif rule_num == 21:
                    lines.append(f'    "건축물대장 표제부": 존재={bl_title.exists}, 사용승인일={bl_title.approval_date or "[미추출]"}, 내진={bl_title.seismic_design}, 승강기={bl_title.has_elevator}')
                elif rule_num == 22:
                    min_area = bl_excl.min_exclusive_area or "[미추출]"
                    max_area = bl_excl.max_exclusive_area or "[미추출]"
                    lines.append(f'    "건축물대장 전유부": 최소={min_area}㎡, 최대={max_area}㎡')
                elif rule_num == 23:
                    lines.append(f'    "건축물현황도": 존재={bl_layout.exists}, 배치도={bl_layout.has_site_plan}, 층별평면도={bl_layout.has_all_floor_plans}, 지자체발급={bl_layout.is_government_issued}')
                elif rule_num == 24:
                    lines.append(f'    "토지대장": 존재={ll.exists}, 면적={ll.land_area or "[미추출]"}㎡, 지목={ll.land_category or "[미추출]"}')
                elif rule_num == 25:
                    lines.append(f'    "토지이용계획확인원": 존재={lup.exists}, 면적={lup.land_area or "[미추출]"}㎡, 재정비촉진={lup.is_redevelopment_zone}, 정비구역={lup.is_maintenance_zone}')
                elif rule_num == 26:
                    lines.append(f'    "토지등기부등본": 존재={lr.exists}, 면적={lr.land_area or "[미추출]"}㎡, 필지={lr.submitted_parcels}/{lr.total_parcels}')
                elif rule_num == 27:
                    lines.append(f'    "건물등기부등본": 존재={br.exists}, 호수={br.submitted_units}/{br.total_units}, 근저당={br.has_mortgage}, 압류={br.has_seizure}, 신탁={br.has_trust}')
                elif rule_num == 28:
                    lines.append(f'    "신탁원부계약서"={trust_docs.trust_contract.exists}, "권한확인서"={trust_docs.sale_authority_confirmation.exists}')
                elif rule_num == 29:
                    lines.append(f'    "준공도면": 존재={as_built.exists}, 외벽마감={as_built.exterior_finish_material or "[미추출]"}, 외벽단열={as_built.exterior_insulation_material or "[미추출]"}')
                elif rule_num == 30:
                    lines.append(f'    "시험성적서": 열방출={test_cert.has_heat_release_test}, 가스유해성={test_cert.has_gas_toxicity_test}, "납품확인서"={test_cert.has_delivery_confirmation}')
                elif rule_num == 31:
                    lines.append(f'    "건축물대장 표제부": 근생여부={bl_title.has_worker_living_facility}')
                elif rule_num == 32:
                    min_units = ", ".join(bl_excl.min_area_unit_numbers[:3]) if bl_excl.min_area_unit_numbers else "[미추출]"
                    max_units = ", ".join(bl_excl.max_area_unit_numbers[:3]) if bl_excl.max_area_unit_numbers else "[미추출]"
                    lines.append(f'    "건축물대장 전유부": 최소면적={bl_excl.min_exclusive_area or "[미추출]"}㎡(호:{min_units}), 최대면적={bl_excl.max_exclusive_area or "[미추출]"}㎡(호:{max_units})')
                elif rule_num == 33:
                    lines.append(f'    "건물등기부등본": 민간임대용={br.is_private_rental_stated}')
                elif rule_num == 34:
                    lines.append(f'    "토지대장": 지목={ll.land_category or "[미추출]"}, 용도제한={", ".join(ll.use_restrictions[:3]) if ll.use_restrictions else "[없음]"}')
            
            lines.append("")
            lines.append(f"═ 통과: {passed_count}개 | 보완: {failed_count}개 ═")
            
            # 매입제외/조건부 사유도 여기에 포함
            if not integrated.stage1_passed and integrated.stage1_result:
                if integrated.stage1_result.excluded_rules:
                    lines.append("")
                    lines.append("🚫 매입제외:")
                    for rule in integrated.stage1_result.excluded_rules[:5]:
                        lines.append(f"  • {rule.rule_description}")
                if integrated.stage1_result.conditional_rules:
                    lines.append("⚠️ 조건부:")
                    for rule in integrated.stage1_result.conditional_rules[:5]:
                        lines.append(f"  • {rule.rule_description}")
            lines.append("")
        
        for file_path, task in sorted(self.tasks.items(), key=lambda x: Path(x[0]).name):
            if task.status == "오류":
                lines.append(f"❌ {Path(file_path).name} (오류)")
                lines.append("-" * 40)
                lines.append(task.error or "오류 발생")
                lines.append("")
        
        self.result_text_edit.setPlainText("\n".join(lines))
        self.result_tabs.setCurrentIndex(1)
        
        ex, co, su, ap = verdict_counts["EXCLUDED"], verdict_counts["CONDITIONAL"], verdict_counts["SUPPLEMENTARY"], verdict_counts["APPROVED"]
        if ex + co + su + ap > 0:
            self.stats_label.setText(f"📊 그룹 결과: ❌제외 {ex} | ⚠️조건부 {co} | 📝보완 {su} | ✅가능 {ap}")

    def _on_close_result_tab(self, index: int):
        if index <= 1 or index >= self.result_tabs.count():
            return
        self.result_tabs.removeTab(index)
        if self._error_tab_index is not None and self._error_tab_index >= index:
            self._error_tab_index = self._error_tab_index - 1 if self._error_tab_index > index else None

    def _update_stats(self):
        # 통계는 그룹별 검증 후 _on_all_finished에서 한 번만 갱신
        if not self.tasks:
            self.stats_label.setText("")
