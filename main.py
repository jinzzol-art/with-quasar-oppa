import sys
import warnings
warnings.filterwarnings("ignore", category=FutureWarning, message=".*google.generativeai.*")


from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main() -> int:
    """
    AI 문서 검토 시스템 v1.0 진입점.
    - Qt 6 기본 HiDPI 대응 사용 (별도 설정 불필요)
    - 기본 창 크기 1200x800
    """
    # google.generativeai 패키지 deprecated 경고 억제 (google.genai 전환 전까지)
    warnings.filterwarnings(
        "ignore",
        category=FutureWarning,
        message=".*google.generativeai.*",
    )

    app = QApplication(sys.argv)

    window = MainWindow()
    window.resize(1200, 800)
    window.setWindowTitle("AI 문서 검토 시스템 v1.0")
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

