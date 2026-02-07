"""
SingleShot v4.0 — 쿨다운 제로, Rate Limiter 제로

원칙:
- API 1회 호출. 끝.
- 429 → 5초 대기 후 재시도. 쿨다운 없음.
- 기존 다중호출 분석기로 폴백 절대 안 함.
- GlobalLimiter 일절 사용 안 함.
"""
from __future__ import annotations

import base64, io, json, os, re, time, random
from typing import Optional, List, Dict, Tuple, Any
from datetime import datetime
from dotenv import load_dotenv

try:
    import fitz
except ImportError:
    fitz = None

try:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
except ImportError:
    Image = None

from core.data_models import PublicHousingReviewResult, DocumentStatus
from core.unified_pdf_analyzer import UnifiedPDFAnalyzer, DocType, DocumentInfo


# ── 타입 매핑 ──

_NORM: Dict[str, DocType] = {
    "주택매도신청서": DocType.HOUSING_SALE_APPLICATION,
    "매도신청서": DocType.HOUSING_SALE_APPLICATION,
    "매도신청주택임대현황": DocType.RENTAL_STATUS,
    "임대현황": DocType.RENTAL_STATUS,
    "위임장": DocType.POWER_OF_ATTORNEY,
    "개인정보동의서": DocType.CONSENT_FORM,
    "개인정보수집이용동의서": DocType.CONSENT_FORM,
    "개인정보수집이용및제공동의서": DocType.CONSENT_FORM,
    "청렴서약서": DocType.INTEGRITY_PLEDGE,
    "공사직원확인서": DocType.LH_EMPLOYEE_CONFIRM,
    "공사직원여부확인서": DocType.LH_EMPLOYEE_CONFIRM,
    "인감증명서": DocType.SEAL_CERTIFICATE,
    "건축물대장표제부": DocType.BUILDING_LEDGER_TITLE,
    "표제부": DocType.BUILDING_LEDGER_TITLE,
    "건축물대장총괄표제부": DocType.BUILDING_LEDGER_SUMMARY,
    "총괄표제부": DocType.BUILDING_LEDGER_SUMMARY,
    "건축물대장전유부": DocType.BUILDING_LEDGER_EXCLUSIVE,
    "전유부": DocType.BUILDING_LEDGER_EXCLUSIVE,
    "건축물현황도": DocType.BUILDING_LAYOUT,
    "토지대장": DocType.LAND_LEDGER,
    "토지이용계획확인원": DocType.LAND_USE_PLAN,
    "토지이용계획": DocType.LAND_USE_PLAN,
    "건물등기부등본": DocType.BUILDING_REGISTRY,
    "건물등기사항전부증명서": DocType.BUILDING_REGISTRY,
    "토지등기부등본": DocType.LAND_REGISTRY,
    "토지등기사항전부증명서": DocType.LAND_REGISTRY,
    "등기부등본": DocType.BUILDING_REGISTRY,
    "등기사항전부증명서": DocType.BUILDING_REGISTRY,
    "준공도면": DocType.AS_BUILT_DRAWING,
    "시험성적서": DocType.TEST_CERTIFICATE,
    "납품확인서": DocType.DELIVERY_CONFIRMATION,
    "중개사무소등록증": DocType.REALTOR_REGISTRATION,
    "사업자등록증": DocType.BUSINESS_REGISTRATION,
}

_KW: List[Tuple[str, DocType]] = [
    ("매도신청", DocType.HOUSING_SALE_APPLICATION),
    ("임대현황", DocType.RENTAL_STATUS),
    ("위임장", DocType.POWER_OF_ATTORNEY),
    ("개인정보", DocType.CONSENT_FORM),
    ("동의서", DocType.CONSENT_FORM),
    ("청렴", DocType.INTEGRITY_PLEDGE),
    ("직원확인", DocType.LH_EMPLOYEE_CONFIRM),
    ("직원여부", DocType.LH_EMPLOYEE_CONFIRM),
    ("인감증명", DocType.SEAL_CERTIFICATE),
    ("총괄표제부", DocType.BUILDING_LEDGER_SUMMARY),
    ("전유부", DocType.BUILDING_LEDGER_EXCLUSIVE),
    ("표제부", DocType.BUILDING_LEDGER_TITLE),
    ("현황도", DocType.BUILDING_LAYOUT),
    ("토지이용", DocType.LAND_USE_PLAN),
    ("토지대장", DocType.LAND_LEDGER),
    ("토지등기", DocType.LAND_REGISTRY),
    ("건물등기", DocType.BUILDING_REGISTRY),
    ("등기사항", DocType.BUILDING_REGISTRY),
    ("등기부등본", DocType.BUILDING_REGISTRY),
    ("준공도면", DocType.AS_BUILT_DRAWING),
    ("시험성적", DocType.TEST_CERTIFICATE),
    ("납품확인", DocType.DELIVERY_CONFIRMATION),
    ("사업자등록", DocType.BUSINESS_REGISTRATION),
]

_SAFETY: List[Tuple[List[str], DocType]] = [
    (["주택매도신청서", "매도신청서"], DocType.HOUSING_SALE_APPLICATION),
    (["임대현황"], DocType.RENTAL_STATUS),
    (["위임장"], DocType.POWER_OF_ATTORNEY),
    (["개인정보"], DocType.CONSENT_FORM),
    (["청렴서약"], DocType.INTEGRITY_PLEDGE),
    (["직원확인", "공사직원"], DocType.LH_EMPLOYEE_CONFIRM),
    (["인감증명서"], DocType.SEAL_CERTIFICATE),
    (["총괄표제부"], DocType.BUILDING_LEDGER_SUMMARY),
    (["전유부"], DocType.BUILDING_LEDGER_EXCLUSIVE),
    (["표제부", "건축물대장"], DocType.BUILDING_LEDGER_TITLE),
    (["건축물현황도"], DocType.BUILDING_LAYOUT),
    (["토지이용계획"], DocType.LAND_USE_PLAN),
    (["토지대장"], DocType.LAND_LEDGER),
    (["등기부등본", "등기사항전부증명"], DocType.BUILDING_REGISTRY),
    (["준공도면"], DocType.AS_BUILT_DRAWING),
    (["시험성적서"], DocType.TEST_CERTIFICATE),
]


def _n(s: str) -> str:
    return re.sub(r"[\s\-_()（）·:：\u3000]", "", s).strip()


def _mt(raw: str) -> DocType:
    if not raw: return DocType.UNKNOWN
    n = _n(raw)
    if n in _NORM: return _NORM[n]
    for k, d in _KW:
        if k in n: return d
    r = raw.replace(" ", "")
    for k, d in _KW:
        if k in r: return d
    return DocType.UNKNOWN


def _td(text: str) -> Dict[DocType, bool]:
    t = text.replace(" ", "").replace("\n", "")
    return {d: True for kws, d in _SAFETY if any(k in t for k in kws)}


# ── 프롬프트 ──

def _prompt(ann: str, n: int) -> str:
    return f"""공공임대 매입심사 PDF({n}페이지) 통합 분석. 공고일:{ann}

각 페이지 문서유형 판별 후 정보 추출.

문서유형별 추출항목:
1. 주택매도신청서: owner_name(필수!손글씨해독), owner_birth, owner_address, owner_phone, owner_email, is_corporation, property_address, land_area, approval_date, has_seal, seal_name, written_date, agent_name
2. 매도신청주택임대현황: units배열
3. 위임장: delegator_name, delegate_name, has_seal, written_date
4. 개인정보동의서: owner_signed, owner_seal_valid, owner_written_date
5. 청렴서약서: has_seal, written_date
6. 공사직원확인서: is_employee, has_seal, written_date
7. 인감증명서: name, issue_date
8. 건축물대장표제부: location, main_use, approval_date, above_ground_floors, basement_floors, seismic_design, elevator_count
9. 건축물대장총괄표제부: location, total_units, main_use
10. 건축물대장전유부: units배열(unit,exclusive_area), total_units
11. 건축물현황도: exists
12. 토지대장: location, land_area, land_category
13. 토지이용계획확인원: location, use_zone, land_area
14. 건물등기부등본: location, owner, has_seizure, has_provisional_seizure, has_auction, has_mortgage, mortgage_amount, has_trust, is_private_rental_stated, issue_date
15. 토지등기부등본: location, owner, has_seizure, has_mortgage, issue_date
16. 준공도면: exterior_finish_material, exterior_insulation_material, piloti_finish_material, piloti_insulation_material
17. 시험성적서: has_heat_release_test, has_gas_toxicity_test, material_name
18. 납품확인서: material_name
19. 기타: document_type명시

출력(JSON만): {{"documents":[{{"document_type":"주택매도신청서","pages":[1],"data":{{"exists":true,"owner_name":"홍길동",...}}}}]}}

규칙: document_type 정확히 사용, owner_name null금지, 법인이면 is_corporation:true, 날짜 YYYY-MM-DD, data에 exists:true필수. JSON만."""


# ── Gemini 직접 호출 (Rate Limiter 없음) ──

def _call_gemini(prompt: str, jpeg_list: List[bytes], model_name: str = "gemini-2.0-flash") -> str:
    """Gemini API 직접 호출. GlobalLimiter 일절 안 씀."""
    load_dotenv()
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY 필요")
    
    import google.generativeai as genai
    genai.configure(api_key=key, transport="rest")
    model = genai.GenerativeModel(model_name)
    
    content = [prompt]
    for jpg in jpeg_list:
        content.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(jpg).decode(),
            }
        })
    
    config = genai.types.GenerationConfig(
        response_mime_type="application/json",
        temperature=0.1,
    )
    resp = model.generate_content(content, generation_config=config)
    return getattr(resp, "text", str(resp))


# ── 메인 분석기 ──

class SingleShotPDFAnalyzer:
    """
    SingleShot v4.0 — 쿨다운 제로
    
    - GlobalLimiter 사용 안 함
    - vision_client 사용 안 함
    - 429 → 5초 대기 후 재시도 (최대 4회)
    - 기존 분석기 폴백 없음
    """
    
    IMG_PX = 768
    MAX_PAGES = 30
    
    def __init__(self, provider: str = "gemini", model_name: Optional[str] = None):
        self.provider = (provider or "gemini").strip().lower()
        self.model_name = model_name or "gemini-2.0-flash"
    
    def analyze(
        self, pdf_path: str, announcement_date: str = "2025-07-05",
    ) -> Tuple[PublicHousingReviewResult, Dict]:
        
        t0 = time.time()
        print(f"\n[v4] 분석 시작 — {pdf_path}")
        
        # 1. 페이지 → JPEG
        t1 = time.time()
        jpegs, texts = self._pages_to_jpeg(pdf_path)
        all_text = "\n".join(texts)
        print(f"  페이지 {len(jpegs)}장 ({time.time()-t1:.1f}s)")
        
        # 2. 텍스트 안전장치
        text_det = _td(all_text)
        corp = any(k in all_text.replace(" ", "") for k in 
                    ["주식회사", "(주)", "㈜", "건설", "개발", "법인", "산업"])
        
        # 3. API 1회 호출 (재시도: 5초 고정 대기, 쿨다운 없음)
        t2 = time.time()
        prompt = _prompt(announcement_date, len(jpegs))
        raw = self._call_with_retry(prompt, jpegs)
        print(f"  API 응답 ({time.time()-t2:.1f}s)")
        
        # 4. 파싱 + 매핑
        parsed = self._parse(raw)
        docs_raw = parsed.get("documents", []) if isinstance(parsed, dict) else []
        
        if not docs_raw:
            # 1회 더 시도
            raw = self._call_with_retry(prompt, jpegs)
            parsed = self._parse(raw)
            docs_raw = parsed.get("documents", []) if isinstance(parsed, dict) else []
        
        documents = self._map(docs_raw)
        
        # 5. 텍스트 안전장치 보정
        existing = {d.doc_type for d in documents}
        for dt in text_det:
            if dt not in existing:
                documents.append(DocumentInfo(
                    doc_type=dt, pages=[], merged_data={"exists": True}, confidence=0.7))
        
        # 6. 결과 생성 (_build_result만 빌려씀, API 호출 없음)
        ua = UnifiedPDFAnalyzer.__new__(UnifiedPDFAnalyzer)
        ua.provider = self.provider
        ua.model_name = self.model_name
        ua._detected_corp_from_text = corp
        result = ua._build_result(documents, announcement_date, all_text)
        
        # 7. 사후 보정
        self._fix(result, text_det, documents, corp)
        
        elapsed = time.time() - t0
        
        app = result.housing_sale_application
        print(f"  매도신청서={'✓' if app.exists else '✗'} 소유자={app.owner_info.name or '?'}")
        print(f"  ★ 완료 {elapsed:.1f}초 (API 1회)\n")
        
        return result, {
            "total_pages": len(jpegs),
            "documents_found": [{"type": d.doc_type.value, "pages": d.pages} for d in documents],
            "api_calls": 1, "analysis_time": elapsed, "analyzer": "SingleShot v4",
        }
    
    # ── PDF → JPEG bytes (PIL Image 안 거침) ──
    
    def _pages_to_jpeg(self, pdf_path: str) -> Tuple[List[bytes], List[str]]:
        doc = fitz.open(pdf_path)
        total = min(len(doc), self.MAX_PAGES)
        jpegs, texts = [], []
        
        for i in range(total):
            page = doc.load_page(i)
            texts.append(page.get_text("text") or "")
            
            long = max(page.rect.width, page.rect.height)
            dpi = max(72, min(int(self.IMG_PX * 72 / long), 130))
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72), alpha=False)
            
            # JPEG 변환 (PyMuPDF 직접 → PIL 폴백)
            try:
                jpeg_bytes = pix.tobytes("jpeg")
            except Exception:
                # PyMuPDF JPEG 미지원 시 PIL 사용
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                w, h = img.size
                if max(w, h) > self.IMG_PX:
                    s = self.IMG_PX / max(w, h)
                    img = img.resize((int(w*s), int(h*s)), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=55)
                jpeg_bytes = buf.getvalue()
            
            jpegs.append(jpeg_bytes)
        
        doc.close()
        return jpegs, texts
    
    # ── API 호출 (단순 재시도, 쿨다운 없음) ──
    
    def _call_with_retry(self, prompt: str, jpegs: List[bytes]) -> str:
        """429 → 대기 후 재시도. 쿨다운 시스템 없음."""
        for attempt in range(5):
            try:
                return _call_gemini(prompt, jpegs, self.model_name)
            except Exception as e:
                msg = str(e).lower()
                is_rate = "429" in msg or "rate" in msg or "resource" in msg or "overload" in msg
                if is_rate and attempt < 4:
                    wait = 8 + attempt * 5  # 8초, 13초, 18초, 23초
                    print(f"  [429] {wait}초 대기 ({attempt+1}/5)")
                    time.sleep(wait)
                elif is_rate:
                    raise RuntimeError(f"API 요청 한도 초과 — 1분 후 다시 시도하세요")
                else:
                    raise
        return "{}"
    
    # ── JSON 파싱 ──
    
    def _parse(self, text: str) -> Any:
        if not text: return {}
        text = re.sub(r'```json\s*|```\s*', '', text).strip()
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            try: return json.loads(m.group())
            except: pass
        m = re.search(r'\[[\s\S]*\]', text)
        if m:
            try: return {"documents": json.loads(m.group())}
            except: pass
        return {}
    
    # ── 매핑 ──
    
    def _map(self, raw: list) -> List[DocumentInfo]:
        result = []
        for d in raw:
            if not isinstance(d, dict): continue
            rt = d.get("document_type", "")
            dt = _mt(rt)
            if dt == DocType.UNKNOWN:
                print(f"  [?] \"{rt}\"")
                continue
            pages = d.get("pages", [])
            if isinstance(pages, int): pages = [pages]
            data = d.get("data", {})
            if not isinstance(data, dict): data = {}
            data["exists"] = True
            if dt == DocType.BUILDING_REGISTRY and "토지" in rt:
                dt = DocType.LAND_REGISTRY
            result.append(DocumentInfo(doc_type=dt, pages=pages, merged_data=data, confidence=0.85))
        return result
    
    # ── 사후 보정 ──
    
    def _fix(self, result, text_det, documents, corp):
        if not result.housing_sale_application.exists:
            if DocType.HOUSING_SALE_APPLICATION in text_det:
                result.housing_sale_application.exists = True
                result.housing_sale_application.status = DocumentStatus.VALID
            for doc in documents:
                if doc.doc_type == DocType.HOUSING_SALE_APPLICATION and not result.housing_sale_application.exists:
                    result.housing_sale_application.exists = True
                    result.housing_sale_application.status = DocumentStatus.VALID
                    if isinstance(doc.merged_data, dict):
                        self._fix_housing(result, doc.merged_data)
                    break
        
        for dt, obj in [
            (DocType.CONSENT_FORM, result.consent_form),
            (DocType.INTEGRITY_PLEDGE, result.integrity_pledge),
            (DocType.LH_EMPLOYEE_CONFIRM, result.lh_employee_confirmation),
            (DocType.SEAL_CERTIFICATE, result.owner_identity.seal_certificate),
            (DocType.BUILDING_LEDGER_TITLE, result.building_ledger_title),
            (DocType.LAND_LEDGER, result.land_ledger),
            (DocType.BUILDING_REGISTRY, result.building_registry),
        ]:
            if not obj.exists and (dt in text_det or any(d.doc_type == dt for d in documents)):
                obj.exists = True
                obj.status = DocumentStatus.VALID
        
        if corp and not result.corporate_documents.is_corporation:
            result.corporate_documents.is_corporation = True
        
        o = result.housing_sale_application.owner_info
        o.is_complete = sum(bool(getattr(o, f, None) and str(getattr(o, f)).strip())
                           for f in ("name", "birth_date", "address", "phone", "email")) >= 3
    
    def _fix_housing(self, result, data):
        o = result.housing_sale_application.owner_info
        for keys, attr in [
            (("owner_name", "name", "성명"), "name"),
            (("owner_birth", "birth_date", "생년월일"), "birth_date"),
            (("owner_address", "address", "주소"), "address"),
            (("owner_phone", "phone", "연락처"), "phone"),
            (("owner_email", "email", "이메일"), "email"),
        ]:
            for k in keys:
                v = data.get(k)
                if v and str(v).strip() and str(v).lower() not in ("null", "none"):
                    setattr(o, attr, str(v).strip())
                    break
        if data.get("is_corporation") is True:
            result.corporate_documents.is_corporation = True
        if data.get("has_seal") is True:
            result.housing_sale_application.seal_verification.seal_exists = True
        for k, attr in [("written_date", "written_date"), ("approval_date", "approval_date")]:
            v = data.get(k)
            if v and str(v).lower() not in ("null", "none"):
                setattr(result.housing_sale_application, attr, str(v).strip())
        la = data.get("land_area")
        if la is not None:
            try: result.housing_sale_application.land_area = float(la)
            except: pass


def analyze_pdf_single_shot(
    pdf_path: str, announcement_date: str = "2025-07-05",
    provider: str = "gemini", model_name: Optional[str] = None,
) -> Tuple[PublicHousingReviewResult, Dict]:
    return SingleShotPDFAnalyzer(provider, model_name).analyze(pdf_path, announcement_date)
