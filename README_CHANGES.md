# AI 문서 검증 시스템 - v2.2 수정사항

## 🔧 핵심 수정 (2025-02-04)

### 1. 사용승인일 "불일치" 오탐 해결

**문제:** 사용승인일이 일치하는데도 계속 "불일치"로 판정

**원인 분석:**
1. `unified_pdf_analyzer.py`에서 `approval_date_match = True` 설정
2. 그런데 `enhanced_validation_engine.py`에서 **다시 비교**를 수행
3. 두 곳의 파싱 로직이 미묘하게 달라서 불일치 발생

**해결책:**
- `enhanced_validation_engine.py`에서 **이미 `approval_date_match = True`이면 재비교 안함**
- `unified_pdf_analyzer.py`에서 더 관대한 비교 로직 적용 (연월 일치 시 통과)

```python
# enhanced_validation_engine.py 수정 전
if app_ymd != title_ymd:
    self._add_supplementary(...)  # 무조건 보완서류 추가

# 수정 후
already_matched = getattr(result.housing_sale_application, "approval_date_match", None)
if already_matched is True:
    pass  # 이미 일치로 판정됨 - 재검사 생략
elif already_matched is False:
    self._add_supplementary(...)  # 명시적 불일치만 보완서류
```

---

### 2. 준공도면 "자재명 미추출" 오탐 해결

**문제:** 필로티 구조가 아닌 건물인데 필로티 자재를 필수로 요구

**해결책:**
- **필로티 구조일 때만** 필로티 자재 검사
- 외벽 자재만 필수, 필로티 자재는 조건부 필수
- `has_piloti` 필드 추가하여 구조 판별

```python
# 수정 전
if not piloti_finish_material:
    missing.append("필로티마감재료")  # 무조건 체크

# 수정 후
if has_piloti:  # 필로티 구조일 때만
    if not piloti_finish_material:
        missing.append("필로티마감재료")
```

---

### 3. Decompression Bomb 오류 해결 (v2.1)

**문제:** 시공사진 같은 대용량 이미지에서 PIL 보안 제한 오류

**해결책:**
- `Image.MAX_IMAGE_PIXELS = None` 설정
- 적응형 DPI (페이지 크기에 따라 72~300 DPI)
- 오류 시 72 DPI로 재시도

---

## 수정된 파일 목록

| 파일 | 수정 내용 |
|------|----------|
| `enhanced_validation_engine.py` | 규칙 7 (사용승인일) 재비교 방지, 규칙 29 (준공도면) 필로티 조건부 |
| `validation_engine.py` | 규칙 7 동일 수정 |
| `unified_pdf_analyzer.py` | 사용승인일 비교 로직 강화 |
| `data_models.py` | `BuildingLedgerTitle.has_piloti` 필드 추가 |
| `precision_pdf_analyzer.py` | 적응형 DPI, PIL 제한 해제 |
| `ultra_unified_pdf_analyzer.py` | PIL 제한 해제 |

---

## 테스트 체크리스트

- [ ] 사용승인일 일치 케이스 → "일치"로 판정되는지 확인
- [ ] 필로티 없는 건물 → 필로티 자재 검사 생략되는지 확인
- [ ] 대용량 시공사진 → 오류 없이 처리되는지 확인

---

## 버전 히스토리

- **v2.2** (2025-02-04): 사용승인일 재비교 방지, 준공도면 필로티 조건부 검사
- **v2.1** (2025-02-04): Decompression Bomb 오류 해결
- **v2.0** (2025-02-04): False Positive 문제 해결
