# 이중검증·전체 프로세스 속도 개선 솔루션 (3분 이내 목표)

## 1. 병목 요약

| 구간 | 소요 추정 | 비고 |
|------|------------|------|
| **파일당 API 호출 수** | 문서 유형 수 + 미확인 페이지 수 | 1유형당 1회, 미확인 1페이지당 1회 |
| **파일 간 대기** | 3초 × (파일 수 - 1) | 429 방지용 |
| **이중검증** | 2배 (1차 + 2차 순차) | 현재 메인 플로우에 미연동, 연동 시 2배 |

예: 3개 파일, 파일당 8유형 + 2미확인 = 10회 호출 → 3×10 = 30회 API 호출. 호출당 ~8초면 240초 + 대기 → **4분 이상**.

---

## 2. 적용 가능 솔루션 (연결 위치)

### A. 즉시 적용 가능 (코드 반영됨)

| 조치 | 파일 | 효과 |
|------|------|------|
| **DPI 300 → 200** | `core/unified_pdf_analyzer.py` (`DPI`) | 이미지 용량·처리 시간 감소 |
| **MAX_PAGES 100 → 50** | `core/unified_pdf_analyzer.py` (`MAX_PAGES`) | 대용량 PDF 시 호출 수·시간 감소 |
| **미확인 페이지 API 최대 2회** | `core/unified_pdf_analyzer.py` (`_analyze_with_gemini`) | 미확인 페이지 많을 때 호출 수 감소 |
| **이중검증 1차/2차 병렬** | `core/dual_analysis_client.py` (`analyze_with_dual_validation`) | 이중검증 시 소요 시간 약 ½ (연동 시) |

### B. 추가로 연결하면 좋은 부분

| 조치 | 연결 위치 | 효과 |
|------|-----------|------|
| **전체 1회 호출 모드** | `core/unified_pdf_analyzer.py` | 페이지 수 적을 때(예: ≤20) 전체 페이지를 **1회** API로 보내고, 한 번에 구조화 결과 반환하도록 별도 경로 추가 → **파일당 1회 호출**로 대폭 단축 |
| **문서 유형별 배치** | `core/unified_pdf_analyzer.py` `_analyze_with_gemini` | 2~3개 유형을 한 프롬프트에 묶어 1회 호출 → 유형 수만큼 나누던 호출을 1/2~1/3로 감소 |
| **이중검증 선택적 사용** | `ui/main_window.py` | 체크 시에만 이중검증 경로 사용, 기본은 단일 분석만 → 평소에는 2배 부담 없음 |
| **동시 워커 2로 완화** | `ui/main_window.py` `MAX_CONCURRENT_WORKERS` | 429 여유 있으면 2로 올리고 `DELAY_BETWEEN_FILES_MS` 5초 등으로 조정 → 다중 파일 시 전체 시간 단축 |

### C. 3분 이내 목표 시 권장 조합

1. **위 A 항목 유지** (DPI 200, MAX_PAGES 50, 미확인 최대 2회, 이중검증 병렬).
2. **파일 수·페이지 수 제한**: 예) 한 번에 3~4개 파일, 파일당 30페이지 이하 권장.
3. **전체 1회 호출 모드** 구현: 페이지 ≤ 20인 PDF는 1회 호출로 통합 분석 (가장 큰 단축).
4. 이중검증은 **필요한 경우에만** 켜고, 켤 때는 병렬 버전 사용.

---

## 3. 상수·설정 위치 요약

| 설정 | 파일 | 변수/위치 |
|------|------|-----------|
| DPI | `core/unified_pdf_analyzer.py` | `DPI = 200` |
| 최대 페이지 | `core/unified_pdf_analyzer.py` | `MAX_PAGES = 50` |
| 미확인 페이지 최대 API 횟수 | `core/unified_pdf_analyzer.py` | `_analyze_with_gemini` 내 미확인 루프 상한 |
| 파일 간 대기(ms) | `ui/main_window.py` | `DELAY_BETWEEN_FILES_MS` |
| 동시 워커 수 | `ui/main_window.py` | `MAX_CONCURRENT_WORKERS` |
| 이중검증 1/2차 병렬 | `core/dual_analysis_client.py` | `analyze_with_dual_validation` |
