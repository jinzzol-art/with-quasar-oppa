"""
최종 통합 PDF 분석기 v8.2 - 스캔 PDF 완벽 지원

핵심 수정:
- 스캔 PDF 이미지 분석 100% 보장
- AI 응답 파싱 강화
- 디버그 로깅 추가
- 대용량 이미지(시공사진 등) 안전 처리
"""
from __future__ import annotations

import json
import time
import random
import re
import io
from typing import Optional, List, Dict, Tuple, Any
from dataclasses import dataclass
from enum import Enum

from dotenv import load_dotenv

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

try:
    from PIL import Image, ImageEnhance
    # PIL 이미지 크기 제한 해제 (대용량 시공사진 등 처리)
    Image.MAX_IMAGE_PIXELS = None
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    from google.api_core import exceptions as google_exceptions
except Exception:
    google_exceptions = None

from core.data_models import (
    PublicHousingReviewResult,
    DocumentStatus,
    ApplicantType,
    AgentType,
)
from core.vision_client import create_vision_client

# DocType 매핑
from core.unified_pdf_analyzer import (
    DocType,
    DOC_DETECTION_RULES,
    PageAnalysis,
    DocumentInfo,
)


class UltraUnifiedPDFAnalyzer:
    """
    울트라 통합 PDF 분석 시스템 v8.2
    - 스캔 PDF 완벽 지원
    - 이미지 기반 AI 분석
    - 대용량 이미지 안전 처리
    """
    
    # 설정 (고해상도로 개선 - 스캔 PDF 손글씨 인식 향상)
    DEFAULT_DPI = 200  # 150 → 200 (손글씨 인식 향상)
    MAX_PAGES = 50
    MAX_IMAGE_PX = 1500  # 1200 → 1500 (더 큰 이미지로 AI 인식률 향상)
    MAX_SAFE_PIXELS = 100_000_000  # 1억 픽셀 이상은 저해상도로 처리
    
    # API 딜레이
    MIN_RPM_DELAY = 0.5
    MAX_RPM_DELAY = 1.5
    RPM_DELAY_JITTER = 0.3
    
    # 배치 크기
    UNKNOWN_BATCH_SIZE = 6
    
    # 재시도
    MAX_RETRIES_429 = 5
    RETRY_BASE_DELAY = 8
    RETRY_MAX_DELAY = 120
    RETRY_JITTER = 0.2
    
    def __init__(
        self,
        provider: str = "claude",
        model_name: Optional[str] = None,
    ):
        load_dotenv()
        self.provider = (provider or "claude").strip().lower()
        self._vision_client = create_vision_client(self.provider, model_name)
        self.model_name = getattr(self._vision_client, "model_name", model_name or "claude-opus-4-5")
        
        # 성능 모니터링
        self._api_call_count = 0
        self._total_wait_time = 0.0
        self._page_count = 0
        self._current_rpm_delay = self.MIN_RPM_DELAY
    
    def analyze(
        self,
        pdf_path: str,
        announcement_date: str = "2025-07-05"
    ) -> Tuple[PublicHousingReviewResult, Dict]:
        """PDF 분석 메인"""
        start_time = time.time()
        self._api_call_count = 0
        self._total_wait_time = 0.0
        
        print(f"\n{'='*70}")
        print(f"[UltraAnalyzer v8.1] 분석 시작")
        print(f"AI: {self.provider} ({self.model_name})")
        print(f"파일: {pdf_path}")
        print(f"{'='*70}\n")
        
        # 1단계: PDF 이미지 추출 (직접 처리, ProcessPool 미사용)
        print(">>> [1단계] PDF 페이지 추출...")
        pages = self._extract_pages_direct(pdf_path)
        self._page_count = len(pages)
        print(f"    총 {len(pages)}페이지 추출 완료\n")
        
        # 적응형 딜레이
        self._current_rpm_delay = self._calculate_adaptive_delay(len(pages))
        
        # 2단계: 텍스트 기반 1차 감지
        print(">>> [2단계] 문서 유형 1차 감지...")
        page_analyses = []
        unknown_count = 0
        for page_num, (image, text) in enumerate(pages, 1):
            doc_type, confidence = self._detect_document_type(text)
            
            # 스캔 PDF: 텍스트 부족하면 UNKNOWN
            text_len = len((text or "").replace(" ", "").replace("\n", ""))
            if text_len < 30:
                doc_type, confidence = DocType.UNKNOWN, 0.0
                unknown_count += 1
            
            page_analyses.append(PageAnalysis(
                page_num=page_num,
                detected_type=doc_type,
                confidence=confidence,
                raw_text=text,
                extracted_data={},
                image=image
            ))
            status = "미확인(스캔)" if doc_type == DocType.UNKNOWN else doc_type.value
            print(f"    페이지 {page_num}: {status} (텍스트 {text_len}자)")
        
        print(f"    → 미확인 페이지: {unknown_count}장 (AI 분석 필요)\n")
        
        # 3단계: AI 분석
        print(">>> [3단계] AI 상세 분석...")
        analysis_start = time.time()
        documents = self._analyze_with_ai(page_analyses, announcement_date)
        analysis_time = time.time() - analysis_start
        
        # 문서 병합
        documents = self._merge_documents_by_type(documents)
        
        # 4단계: 결과 생성
        print("\n>>> [4단계] 결과 생성...")
        result = self._build_result(documents, announcement_date)
        
        # 통계
        elapsed = time.time() - start_time
        throughput = self._page_count / elapsed if elapsed > 0 else 0
        
        meta = {
            "total_pages": len(pages),
            "documents_found": [
                {"type": d.doc_type.value, "pages": d.pages, "confidence": d.confidence}
                for d in documents
            ],
            "analysis_time": elapsed,
            "ai_analysis_time": analysis_time,
            "api_calls": self._api_call_count,
            "total_wait_time": self._total_wait_time,
            "throughput_pages_per_sec": throughput,
        }
        
        print(f"\n{'='*70}")
        print(f"[분석 완료]")
        print(f"  총 소요 시간: {elapsed:.1f}초")
        print(f"  AI 분석: {analysis_time:.1f}초")
        print(f"  API 호출: {self._api_call_count}회")
        print(f"  감지 문서: {len(documents)}종")
        for doc in documents:
            print(f"    - {doc.doc_type.value}: 페이지 {doc.pages}")
        print(f"{'='*70}\n")
        
        return result, meta
    
    def _extract_pages_direct(self, pdf_path: str) -> List[Tuple[Image.Image, str]]:
        """PDF에서 직접 이미지+텍스트 추출 (대용량 이미지 안전 처리)"""
        if not HAS_PYMUPDF:
            raise RuntimeError("PyMuPDF 필요")
        
        doc = fitz.open(pdf_path)
        pages = []
        
        for page_num in range(min(len(doc), self.MAX_PAGES)):
            page = doc.load_page(page_num)
            
            # 텍스트 추출
            text = page.get_text("text") or ""
            
            # 페이지 크기 확인 후 적응형 DPI 결정
            page_rect = page.rect
            page_width_pt = page_rect.width
            page_height_pt = page_rect.height
            
            # 예상 픽셀 수 계산
            def estimate_pixels(dpi):
                w = int(page_width_pt * dpi / 72)
                h = int(page_height_pt * dpi / 72)
                return w * h
            
            # 적응형 DPI 선택
            dpi = self.DEFAULT_DPI
            if estimate_pixels(dpi) > self.MAX_SAFE_PIXELS:
                # 매우 큰 페이지: 목표 픽셀 수에 맞는 DPI 계산
                target_pixels = self.MAX_SAFE_PIXELS * 0.8  # 안전 마진
                area_pt = page_width_pt * page_height_pt
                dpi = int((target_pixels * 72 * 72 / area_pt) ** 0.5)
                dpi = max(72, min(dpi, self.DEFAULT_DPI))  # 72~150 DPI 범위
            
            try:
                # 이미지 추출
                mat = fitz.Matrix(dpi / 72, dpi / 72)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img_bytes = pix.tobytes("png")
                image = Image.open(io.BytesIO(img_bytes))
                
                # RGB 변환
                if image.mode != 'RGB':
                    image = image.convert('RGB')
                
                # 리사이즈
                w, h = image.size
                if max(w, h) > self.MAX_IMAGE_PX:
                    scale = self.MAX_IMAGE_PX / max(w, h)
                    new_size = (int(w * scale), int(h * scale))
                    try:
                        resample = Image.Resampling.LANCZOS
                    except AttributeError:
                        resample = Image.LANCZOS
                    image = image.resize(new_size, resample)
                
                # 대비/선명도 강화 (스캔 문서/손글씨 인식 향상)
                image = ImageEnhance.Contrast(image).enhance(1.5)  # 1.3 → 1.5
                image = ImageEnhance.Sharpness(image).enhance(2.0)  # 1.5 → 2.0
                # 밝기 조정 (너무 어두운 스캔 보정)
                try:
                    image = ImageEnhance.Brightness(image).enhance(1.1)
                except:
                    pass
                
                pages.append((image, text))
                
            except Exception as e:
                print(f"    페이지 {page_num + 1} 추출 오류: {e}")
                # 오류 시 최저 DPI로 재시도
                try:
                    mat = fitz.Matrix(72 / 72, 72 / 72)
                    pix = page.get_pixmap(matrix=mat, alpha=False)
                    img_bytes = pix.tobytes("png")
                    image = Image.open(io.BytesIO(img_bytes))
                    if image.mode != 'RGB':
                        image = image.convert('RGB')
                    pages.append((image, text))
                except Exception as e2:
                    print(f"    페이지 {page_num + 1} 완전 실패: {e2}")
                    dummy = Image.new('RGB', (100, 100), color='white')
                    pages.append((dummy, text))
        
        doc.close()
        return pages
    
    def _detect_document_type(self, text: str) -> Tuple[DocType, float]:
        """텍스트 기반 문서 유형 감지"""
        if not text:
            return DocType.UNKNOWN, 0.0
        
        normalized = text.replace(" ", "").replace("\n", "")
        
        if len(normalized) < 20:
            return DocType.UNKNOWN, 0.0
        
        # 키워드 매칭 (긴 키워드 우선)
        keyword_rules = [
            ("매도신청주택임대현황", DocType.RENTAL_STATUS, 0.92),
            ("주택매도신청서", DocType.HOUSING_SALE_APPLICATION, 0.90),
            ("매도신청서", DocType.HOUSING_SALE_APPLICATION, 0.88),
            ("개인정보동의서", DocType.CONSENT_FORM, 0.90),
            ("청렴서약서", DocType.INTEGRITY_PLEDGE, 0.90),
            ("공사직원확인서", DocType.LH_EMPLOYEE_CONFIRM, 0.90),
            ("인감증명서", DocType.SEAL_CERTIFICATE, 0.90),
            ("위임장", DocType.POWER_OF_ATTORNEY, 0.90),
            ("건축물대장총괄", DocType.BUILDING_LEDGER_SUMMARY, 0.90),
            ("총괄표제부", DocType.BUILDING_LEDGER_SUMMARY, 0.88),
            ("건축물대장전유부", DocType.BUILDING_LEDGER_EXCLUSIVE, 0.90),
            ("전유부", DocType.BUILDING_LEDGER_EXCLUSIVE, 0.85),
            ("건축물대장", DocType.BUILDING_LEDGER_TITLE, 0.85),
            ("건축물현황도", DocType.BUILDING_LAYOUT, 0.90),
            ("토지이용계획", DocType.LAND_USE_PLAN, 0.90),
            ("토지대장", DocType.LAND_LEDGER, 0.88),
            ("토지등기부등본", DocType.LAND_REGISTRY, 0.90),
            ("건물등기부등본", DocType.BUILDING_REGISTRY, 0.90),
            ("등기사항전부증명서", DocType.BUILDING_REGISTRY, 0.85),
            ("준공도면", DocType.AS_BUILT_DRAWING, 0.90),
            ("시험성적서", DocType.TEST_CERTIFICATE, 0.90),
            ("시험성적", DocType.TEST_CERTIFICATE, 0.85),
            ("납품확인서", DocType.DELIVERY_CONFIRMATION, 0.90),
            ("납품확인", DocType.DELIVERY_CONFIRMATION, 0.85),
        ]
        
        for keyword, doc_type, score in keyword_rules:
            if keyword in normalized:
                return doc_type, score
        
        # 조합 키워드
        if "개인정보" in normalized and "동의" in normalized:
            return DocType.CONSENT_FORM, 0.82
        if "토지" in normalized and ("등기" in normalized or "등본" in normalized):
            return DocType.LAND_REGISTRY, 0.82
        
        return DocType.UNKNOWN, 0.0
    
    def _calculate_adaptive_delay(self, page_count: int) -> float:
        if page_count <= 5:
            return self.MIN_RPM_DELAY
        elif page_count >= 30:
            return self.MAX_RPM_DELAY
        else:
            ratio = (page_count - 5) / (30 - 5)
            return self.MIN_RPM_DELAY + (self.MAX_RPM_DELAY - self.MIN_RPM_DELAY) * ratio
    
    def _is_rate_limit_error(self, e: Exception) -> bool:
        if google_exceptions and isinstance(e, google_exceptions.ResourceExhausted):
            return True
        msg = (getattr(e, "message", "") or str(e)).lower()
        return "429" in msg or "resource exhausted" in msg or "rate limit" in msg or "overloaded" in msg
    
    def _throttle_before_request(self) -> None:
        delay = self._current_rpm_delay + random.uniform(-self.RPM_DELAY_JITTER, self.RPM_DELAY_JITTER)
        delay = max(0.3, delay)
        time.sleep(delay)
        self._total_wait_time += delay
    
    def _call_vision_api(self, prompt: str, images: List[Image.Image]) -> str:
        """Vision API 호출 (재시도 포함)"""
        self._throttle_before_request()
        self._api_call_count += 1
        
        for attempt in range(self.MAX_RETRIES_429):
            try:
                result = self._vision_client.generate_json(prompt, images)
                return result
            except Exception as e:
                if not self._is_rate_limit_error(e) or attempt == self.MAX_RETRIES_429 - 1:
                    raise
                
                delay = min(self.RETRY_BASE_DELAY * (2 ** attempt), self.RETRY_MAX_DELAY)
                delay *= (1.0 + random.uniform(-self.RETRY_JITTER, self.RETRY_JITTER))
                print(f"      [Rate limit] {delay:.1f}초 후 재시도...")
                time.sleep(delay)
                self._total_wait_time += delay
        
        return ""
    
    def _analyze_with_ai(
        self,
        page_analyses: List[PageAnalysis],
        announcement_date: str
    ) -> List[DocumentInfo]:
        """AI 분석"""
        
        # 유형별 그룹화
        type_pages: Dict[DocType, List[PageAnalysis]] = {}
        for pa in page_analyses:
            if pa.detected_type not in type_pages:
                type_pages[pa.detected_type] = []
            type_pages[pa.detected_type].append(pa)
        
        documents = []
        
        for doc_type, pages in type_pages.items():
            if doc_type == DocType.UNKNOWN:
                # 미확인 페이지 AI 분석
                n_unknown = len(pages)
                print(f"    미확인 페이지 {n_unknown}장 AI 유형 판별 중...")
                
                detected_list = self._identify_unknown_pages(pages)
                
                print(f"    → AI 판별 결과: {[d.value for d in detected_list]}")
                
                # 같은 유형끼리 그룹화
                type_to_pages: Dict[DocType, List[PageAnalysis]] = {}
                for pa, detected in zip(pages, detected_list):
                    if detected != DocType.UNKNOWN:
                        if detected not in type_to_pages:
                            type_to_pages[detected] = []
                        type_to_pages[detected].append(pa)
                
                # 그룹별 상세 분석
                for detected_type, grouped_pages in type_to_pages.items():
                    print(f"    {detected_type.value} 분석 중 (페이지 {[p.page_num for p in grouped_pages]})...")
                    merged_data = self._analyze_document_pages(detected_type, grouped_pages, announcement_date)
                    documents.append(DocumentInfo(
                        doc_type=detected_type,
                        pages=[p.page_num for p in grouped_pages],
                        merged_data=merged_data or {},
                        confidence=0.75
                    ))
                continue
            
            # 이미 감지된 유형
            print(f"    {doc_type.value} 분석 중 (페이지 {[p.page_num for p in pages]})...")
            merged_data = self._analyze_document_pages(doc_type, pages, announcement_date)
            documents.append(DocumentInfo(
                doc_type=doc_type,
                pages=[p.page_num for p in pages],
                merged_data=merged_data,
                confidence=max(p.confidence for p in pages)
            ))
        
        return documents
    
    def _identify_unknown_pages(self, pages: List[PageAnalysis]) -> List[DocType]:
        """미확인 페이지 AI 유형 판별"""
        results = []
        batch_size = self.UNKNOWN_BATCH_SIZE
        
        for start in range(0, len(pages), batch_size):
            chunk = pages[start:start + batch_size]
            images = [p.image for p in chunk if p.image is not None]
            
            if not images:
                results.extend([DocType.UNKNOWN] * len(chunk))
                continue
            
            prompt = f"""★★★ 이 {len(images)}개 이미지를 꼼꼼히 스캔하여 문서 유형을 정확히 판별하세요! ★★★

[문서 유형 목록 - 반드시 아래 중 하나를 선택]
주택매도신청서, 매도신청주택임대현황, 위임장, 개인정보동의서, 청렴서약서, 공사직원확인서, 인감증명서, 건축물대장표제부, 건축물대장총괄표제부, 건축물대장전유부, 건축물현황도, 토지대장, 토지이용계획확인원, 건물등기부등본, 토지등기부등본, 준공도면, 시험성적서, 납품확인서, 기타

[판별 힌트 - 문서 제목과 내용으로 판단]
1. 주택매도신청서: 표 형식, "소유자/소유주", "매도주택", "인감", "대지면적" 등
2. 매도신청주택임대현황: "임대현황", 호별 표
3. 위임장: "위임장", "위임인", "수임인"
4. 개인정보동의서: "개인정보", "동의서", "수집이용"
5. 청렴서약서: "청렴", "서약서", "부정청탁"
6. 공사직원확인서: "공사직원", "LH", "한국토지주택공사"
7. 인감증명서: "인감증명", 관공서 양식, 도장 이미지
8. 건축물대장표제부: "건축물대장", "표제부", 건물정보 표
9. 건축물대장총괄표제부: "총괄표제부", 여러 동
10. 건축물대장전유부: "전유부", 호별 면적
11. 건축물현황도: 평면도, 배치도
12. 토지대장: "토지대장", 지목, 면적
13. 토지이용계획확인원: "토지이용계획", 용도지역
14. 건물등기부등본: "등기사항전부증명서" + "건물", 갑구/을구
15. 토지등기부등본: "등기사항전부증명서" + "토지"
16. 준공도면: 설계도면, 단면도, 자재표
17. 시험성적서: "시험", "성적서", 시험결과표
18. 납품확인서: "납품", "확인서"

★★★ 스캔된 문서도 꼼꼼히 읽어서 제목/내용으로 유형을 판별하세요! ★★★

[출력] JSON 배열만 출력:
[{{"document_type": "주택매도신청서"}}, {{"document_type": "인감증명서"}}]"""

            try:
                response_text = self._call_vision_api(prompt, images)
                print(f"      [AI 응답] {response_text[:200]}...")
                
                parsed = self._parse_json_response(response_text)
                print(f"      [파싱 결과] {parsed}")
                
                chunk_results = []
                if isinstance(parsed, list):
                    for item in parsed[:len(chunk)]:
                        if isinstance(item, dict):
                            type_str = item.get("document_type", "")
                        elif isinstance(item, str):
                            type_str = item
                        else:
                            type_str = ""
                        detected = self._map_type_string(type_str)
                        print(f"      '{type_str}' → {detected.value}")
                        chunk_results.append(detected)
                
                # 부족한 만큼 채우기
                while len(chunk_results) < len(chunk):
                    chunk_results.append(DocType.UNKNOWN)
                
                results.extend(chunk_results[:len(chunk)])
                
            except Exception as e:
                print(f"      [오류] {e}")
                import traceback
                traceback.print_exc()
                results.extend([DocType.UNKNOWN] * len(chunk))
        
        return results
    
    def _parse_json_response(self, text: str) -> Any:
        """JSON 응답 파싱 (강화)"""
        if not text:
            return []
        
        text = text.strip()
        
        # 마크다운 코드블록 제거
        text = re.sub(r'```json\s*', '', text)
        text = re.sub(r'```\s*', '', text)
        text = text.strip()
        
        # JSON 배열 찾기
        array_match = re.search(r'\[[\s\S]*?\]', text)
        if array_match:
            try:
                return json.loads(array_match.group())
            except json.JSONDecodeError:
                pass
        
        # JSON 객체들 찾기
        objects = re.findall(r'\{\s*"document_type"\s*:\s*"([^"]+)"\s*\}', text)
        if objects:
            return [{"document_type": dt} for dt in objects]
        
        # document_type 값만 추출
        types = re.findall(r'"document_type"\s*:\s*"([^"]+)"', text)
        if types:
            return [{"document_type": dt} for dt in types]
        
        return []
    
    def _map_type_string(self, type_str: str) -> DocType:
        """문자열을 DocType으로 매핑"""
        if not type_str:
            return DocType.UNKNOWN
        
        s = type_str.replace(" ", "").replace("\n", "").strip().lower()
        
        if not s or s in ("기타", "other", "unknown", "미확인", "none", "null"):
            return DocType.UNKNOWN
        
        # 정확한 매핑
        exact = {
            "주택매도신청서": DocType.HOUSING_SALE_APPLICATION,
            "매도신청서": DocType.HOUSING_SALE_APPLICATION,
            "매도신청주택임대현황": DocType.RENTAL_STATUS,
            "임대현황": DocType.RENTAL_STATUS,
            "위임장": DocType.POWER_OF_ATTORNEY,
            "개인정보동의서": DocType.CONSENT_FORM,
            "청렴서약서": DocType.INTEGRITY_PLEDGE,
            "공사직원확인서": DocType.LH_EMPLOYEE_CONFIRM,
            "공사직원여부확인서": DocType.LH_EMPLOYEE_CONFIRM,
            "인감증명서": DocType.SEAL_CERTIFICATE,
            "건축물대장표제부": DocType.BUILDING_LEDGER_TITLE,
            "건축물대장총괄표제부": DocType.BUILDING_LEDGER_SUMMARY,
            "건축물대장전유부": DocType.BUILDING_LEDGER_EXCLUSIVE,
            "건축물현황도": DocType.BUILDING_LAYOUT,
            "토지대장": DocType.LAND_LEDGER,
            "토지이용계획확인원": DocType.LAND_USE_PLAN,
            "건물등기부등본": DocType.BUILDING_REGISTRY,
            "토지등기부등본": DocType.LAND_REGISTRY,
            "준공도면": DocType.AS_BUILT_DRAWING,
            "시험성적서": DocType.TEST_CERTIFICATE,
            "납품확인서": DocType.DELIVERY_CONFIRMATION,
            "중개사무소등록증": DocType.REALTOR_REGISTRATION,
            "사업자등록증": DocType.BUSINESS_REGISTRATION,
        }
        
        for key, doc_type in exact.items():
            if key in s:
                return doc_type
        
        # 부분 매칭
        if "시험" in s or "성적" in s or "test" in s:
            return DocType.TEST_CERTIFICATE
        if "납품" in s:
            return DocType.DELIVERY_CONFIRMATION
        if "인감" in s:
            return DocType.SEAL_CERTIFICATE
        if "위임" in s:
            return DocType.POWER_OF_ATTORNEY
        if "청렴" in s:
            return DocType.INTEGRITY_PLEDGE
        if "동의" in s and "개인" in s:
            return DocType.CONSENT_FORM
        if "공사직원" in s or "직원확인" in s:
            return DocType.LH_EMPLOYEE_CONFIRM
        if "총괄" in s:
            return DocType.BUILDING_LEDGER_SUMMARY
        if "전유" in s:
            return DocType.BUILDING_LEDGER_EXCLUSIVE
        if "건축물대장" in s or "표제부" in s:
            return DocType.BUILDING_LEDGER_TITLE
        if "현황도" in s:
            return DocType.BUILDING_LAYOUT
        if "토지이용" in s or "이용계획" in s:
            return DocType.LAND_USE_PLAN
        if "토지대장" in s:
            return DocType.LAND_LEDGER
        if "토지" in s and ("등기" in s or "등본" in s):
            return DocType.LAND_REGISTRY
        if "건물" in s and ("등기" in s or "등본" in s):
            return DocType.BUILDING_REGISTRY
        if "등기" in s or "등본" in s:
            return DocType.BUILDING_REGISTRY
        if "준공" in s or "도면" in s:
            return DocType.AS_BUILT_DRAWING
        if "매도" in s:
            return DocType.HOUSING_SALE_APPLICATION
        
        return DocType.UNKNOWN
    
    def _analyze_document_pages(
        self,
        doc_type: DocType,
        pages: List[PageAnalysis],
        announcement_date: str
    ) -> Dict[str, Any]:
        """문서 유형별 상세 분석"""
        from core.unified_pdf_analyzer import UnifiedPDFAnalyzer
        temp_analyzer = UnifiedPDFAnalyzer(self.provider, self.model_name)
        prompt = temp_analyzer._get_analysis_prompt(doc_type, announcement_date)
        
        images = [p.image for p in pages[:5] if p.image is not None]
        
        if not images:
            return {"exists": True}
        
        try:
            result_text = self._call_vision_api(prompt, images)
            data = self._parse_json_response(result_text)
            if isinstance(data, dict):
                data["exists"] = True
                return data
            elif isinstance(data, list) and len(data) > 0:
                result = data[0] if isinstance(data[0], dict) else {}
                result["exists"] = True
                return result
            return {"exists": True}
        except Exception as e:
            print(f"      상세 분석 오류: {e}")
            return {"exists": True}
    
    def _merge_documents_by_type(self, documents: List[DocumentInfo]) -> List[DocumentInfo]:
        """문서 유형별 병합"""
        type_map: Dict[DocType, DocumentInfo] = {}
        
        for doc in documents:
            if doc.doc_type in type_map:
                existing = type_map[doc.doc_type]
                existing.pages.extend(doc.pages)
                existing.merged_data.update(doc.merged_data)
                existing.confidence = max(existing.confidence, doc.confidence)
            else:
                type_map[doc.doc_type] = doc
        
        return list(type_map.values())
    
    def _build_result(
        self,
        documents: List[DocumentInfo],
        announcement_date: str
    ) -> PublicHousingReviewResult:
        """결과 생성"""
        from core.unified_pdf_analyzer import UnifiedPDFAnalyzer
        temp_analyzer = UnifiedPDFAnalyzer(self.provider, self.model_name)
        return temp_analyzer._build_result(documents, announcement_date)
