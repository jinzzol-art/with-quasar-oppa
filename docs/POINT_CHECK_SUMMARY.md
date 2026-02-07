# 전체 점검 요약 (점검 싹)

## 1. 린트
- **core/** 전역: 린트 오류 없음 ✅

## 2. 인감 일치율 기준 통일 (50% 완화)
| 파일 | 상태 |
|------|------|
| enhanced_validation_engine.py | SEAL_MATCH_THRESHOLD=50, 경계선 45~50% ✅ |
| validation_engine.py | 50% 미만 시 보완서류 ✅ |
| advanced_validator.py | SEAL_MATCH_THRESHOLD=50 ✅ |
| data_models.py | 설명 "50% 이상" ✅ |
| result_formatter.py | SEAL_THRESHOLD=50 ✅ |
| main_window.py | "50% 기준" 문구 ✅ |
| verification_rules.py | 규칙·설명 50%로 수정 ✅ |
| dual_analysis_client.py | 프롬프트 기준 50%로 수정 ✅ |

- 코드·문서에 **56%** 기준 남아 있던 부분 모두 **50%**로 통일 완료.

## 3. 토지이용계획확인원 지구·지역 (매입제외 미반영)
- **integrated_verification.py**: 토지이용계획 → 매입제외 데이터 변환 시 지구/지역 필드 채우지 않음 (기본 False 유지) ✅
- **enhanced_validation_engine.py**: 규칙 25에서 "제외 대상 구역 해당" 보완서류 추가 제거 ✅
- **validation_engine.py**: 동일 제거 ✅

## 4. 총괄표제부 vs 표제부 (내진설계는 표제부만)
- **unified_pdf_analyzer.py**: BUILDING_LEDGER_TITLE → _apply_building_ledger_title, BUILDING_LEDGER_SUMMARY → _apply_building_ledger_summary 분리 ✅
- **표제부**: 사용승인일·내진설계·층수·주차·승강기 등 검토 ✅
- **총괄표제부**: exists, required, building_count만 설정, 내진설계 미반영 ✅
- **data_models.py**: 총괄/표제부 주석으로 구분 명시 ✅

## 5. Core 모듈 임포트
- `PublicHousingReviewResult`, `EnhancedValidator`, `UnifiedPDFAnalyzer`, `DocType`, `convert_ai_result_to_exclusion_data` 등 정상 임포트 ✅
- (google.generativeai FutureWarning은 main에서 억제 중)

## 6. UI 연동
- **main_window.py**: analyze_pdf_unified, IntegratedVerificationSystem, EnhancedValidator, convert_ai_result_to_exclusion_data 등 사용 ✅

## 7. 기타 확인
- TODO/FIXME: 실제 미해결 TODO 없음 (예시 문자열 "010-XXXX-XXXX"만 매칭됨)
- 표제부/총괄표제부 Apply 분기: TITLE→표제부, SUMMARY→총괄표제부 각각 적용 확인 ✅

---

**다음 단계**: 위 반영 상태로 테스트 진행하시면 됩니다.
