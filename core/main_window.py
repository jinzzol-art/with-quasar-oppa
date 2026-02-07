"""
AI 문서 검토 시스템 - 메인 윈도우 v3

개선사항:
1. 단일 분석 모드 (속도 향상)
2. 자가학습 시스템 연동
3. 사용자 교정 기능
4. 후처리 자동 적용
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QThread, QTimer, Signal
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
)

from core.pdf_processor import extract_content_from_pdf
from core.improved_gemini_client import ImprovedGeminiClient
from core.enhanced_validation_engine import EnhancedValidator
from core.result_formatter import format_result_for_ui


class PdfAnalyzeWorker(QThread):
    """PDF 분석 워커"""
    
    finished = Signal(str)
    error = Signal(str)
    progress = Signal(str)

    def __init__(
        self, 
        pdf_path: str, 
        enable_dual: bool = False,
        parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.enable_dual = enable_dual
        self.announcement_date = "2025-07-05"

    def run(self) -> None:
        try:
            # 1. PDF 추출
            self.progress.emit("PDF 스캔 중...")
            content = extract_content_from_pdf(self.pdf_path)
            
            # 2. Gemini 분석 (개선된 버전)
            mode_text = "이중검증" if self.enable_dual else "단일"
            self.progress.emit(f"AI 분석 중 ({mode_text} 모드)...")
            
            client = ImprovedGeminiClient()
            result, meta = client.analyze(
                content,
                self.announcement_date,
                enable_dual_validation=self.enable_dual,
                enable_post_processing=True
            )
            
            # 3. 34개 규칙 검증
            self.progress.emit("규칙 검증 중...")
            validator = EnhancedValidator(self.announcement_date)
            validated = validator.validate(result, None)
            
            # 4. 결과 포맷
            formatted = format_result_for_ui(validated)
            
            # 5. 자동 교정 내역 추가
            if meta.get("corrections_report"):
                formatted += meta["corrections_report"]
            
            # 6. 수동확인 리포트
            manual_report = validator.get_manual_check_report()
            if "없음" not in manual_report:
                formatted += "\n\n" + manual_report
            
            self.finished.emit(formatted)
            
        except Exception as e:
            self.error.emit(str(e) + "\n\n[디버그]\n" + repr(e))


class MainWindow(QMainWindow):
    """메인 윈도우"""
    
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.current_pdf_path: Optional[str] = None
        self.worker: Optional[PdfAnalyzeWorker] = None
        self._stall_timer: Optional[QTimer] = None
        self._stall_counter: int = 0
        self._setup_ui()

    def _setup_ui(self) -> None:
        central = QWidget(self)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(8)
        
        left_panel = self._create_left_panel()
        right_panel = self._create_right_panel()
        
        root_layout.addWidget(left_panel, 2)
        root_layout.addWidget(right_panel, 3)
        self.setCentralWidget(central)

    def _create_left_panel(self) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        
        # 파일 불러오기 버튼
        self.load_button = QPushButton("파일 불러오기", container)
        self.load_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.load_button.clicked.connect(self._on_click_load_file)
        
        # 이중검증 체크박스
        self.dual_check = QCheckBox("이중검증 (느리지만 정확)", container)
        self.dual_check.setChecked(False)  # 기본: 빠른 단일 분석
        
        # 상태 표시
        self.image_label = QLabel(
            "PDF를 선택하면 분석이 시작됩니다.\n\n"
            "• 단일 모드: 빠른 분석\n"
            "• 이중검증: 정확도 향상", 
            container
        )
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setFrameStyle(QFrame.Box | QFrame.Plain)
        
        scroll_area = QScrollArea(container)
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self.image_label)
        
        # 버튼 레이아웃
        button_layout = QHBoxLayout()
        button_layout.addWidget(self.load_button)
        button_layout.addWidget(self.dual_check)
        button_layout.addStretch()
        
        layout.addLayout(button_layout, 0)
        layout.addWidget(scroll_area, 1)
        return container

    def _create_right_panel(self) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        
        title = QLabel("AI 검토 결과 (자가학습 시스템 v3)", container)
        title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        
        self.result_edit = QTextEdit(container)
        self.result_edit.setReadOnly(True)
        self.result_edit.setPlaceholderText(
            "PDF를 불러오면 분석이 시작됩니다.\n\n"
            "개선된 기능:\n"
            "• 환각(Hallucination) 방지\n"
            "• 패턴 기반 자동 교정\n"
            "• 내진설계/지하층 정확도 향상\n"
            "• 인감 일치율 45% 기준"
        )
        
        layout.addWidget(title)
        layout.addWidget(self.result_edit, 1)
        return container

    def _on_click_load_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "PDF 파일 선택", "", "PDF 파일 (*.pdf)"
        )
        if not file_path:
            return
        self.current_pdf_path = file_path
        self._start_analyze(file_path)

    def _start_analyze(self, pdf_path: str) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()
        
        enable_dual = self.dual_check.isChecked()
        mode_text = "이중검증" if enable_dual else "단일"
        
        self.load_button.setEnabled(False)
        self.dual_check.setEnabled(False)
        self.image_label.setText(f"분석 중... ({mode_text} 모드)")
        self.result_edit.setPlainText(f"분석 중입니다... ({mode_text} 모드)\n잠시만 기다려주세요.")
        
        self.worker = PdfAnalyzeWorker(pdf_path, enable_dual, self)
        self.worker.finished.connect(self._on_analyze_finished)
        self.worker.error.connect(self._on_analyze_error)
        self.worker.progress.connect(self._on_progress)
        self.worker.start()
        
        if self._stall_timer is None:
            self._stall_timer = QTimer(self)
            self._stall_timer.setInterval(1000)
            self._stall_timer.timeout.connect(self._on_stall_tick)
        self._stall_counter = 0
        self._stall_timer.start()

    def _on_progress(self, msg: str) -> None:
        self.image_label.setText("분석 중...\n" + msg)
        self.result_edit.setPlainText("분석 중...\n" + msg)
        self._stall_counter = 0

    def _on_analyze_finished(self, result: str) -> None:
        if self._stall_timer:
            self._stall_timer.stop()
        self.result_edit.setText(result)
        self.load_button.setEnabled(True)
        self.dual_check.setEnabled(True)
        self.image_label.setText("분석 완료!")

    def _on_analyze_error(self, error_msg: str) -> None:
        if self._stall_timer:
            self._stall_timer.stop()
        self.result_edit.setText("오류 발생:\n" + error_msg)
        self.load_button.setEnabled(True)
        self.dual_check.setEnabled(True)
        self.image_label.setText("오류 발생")

    def _on_stall_tick(self) -> None:
        if not self.worker or not self.worker.isRunning():
            if self._stall_timer:
                self._stall_timer.stop()
            return
        self._stall_counter += 1
        if self._stall_counter >= 15:
            self.result_edit.setPlainText(
                "분석 중... (15초 이상 소요)\n"
                "작업은 계속 진행 중입니다."
            )
