"""
공공임대 기존주택 매입심사 - 통합 PDF 분석 시스템 v6.0

핵심 재설계:
1. 문서 존재 여부를 확실하게 판단
2. 페이지별 분석 후 문서 유형 확정
3. 분석 결과를 PublicHousingReviewResult에 정확히 매핑
4. exists 필드 자동 설정
"""
from __future__ import annotations

import json
import os
import io
import random
import re
import time
from datetime import datetime
from typing import Optional, List, Dict, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum

from dotenv import load_dotenv
from core.api_rate_limiter import get_global_limiter

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

try:
    from PIL import Image, ImageEnhance, ImageFilter
    # PIL 이미지 크기 제한 해제 (대용량 시공사진 등 처리)
    Image.MAX_IMAGE_PIXELS = None
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

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


# =============================================================================
# 문서 유형 정의
# =============================================================================

class DocType(str, Enum):
    """문서 유형 - 심사에 필요한 모든 서류"""
    # 필수 양식
    HOUSING_SALE_APPLICATION = "주택매도신청서"
    RENTAL_STATUS = "매도신청주택임대현황"
    POWER_OF_ATTORNEY = "위임장"
    CONSENT_FORM = "개인정보동의서"
    INTEGRITY_PLEDGE = "청렴서약서"
    LH_EMPLOYEE_CONFIRM = "공사직원확인서"
    
    # 정부발급 서류
    SEAL_CERTIFICATE = "인감증명서"
    ID_CARD = "신분증"
    AGENT_ID_CARD = "대리인신분증사본"
    BUILDING_LEDGER_TITLE = "건축물대장표제부"
    BUILDING_LEDGER_SUMMARY = "건축물대장총괄표제부"
    BUILDING_LEDGER_EXCLUSIVE = "건축물대장전유부"
    BUILDING_LAYOUT = "건축물현황도"
    LAND_LEDGER = "토지대장"
    LAND_USE_PLAN = "토지이용계획확인원"
    BUILDING_REGISTRY = "건물등기부등본"
    LAND_REGISTRY = "토지등기부등본"
    
    # 규칙 29, 30
    AS_BUILT_DRAWING = "준공도면"
    TEST_CERTIFICATE = "시험성적서"
    DELIVERY_CONFIRMATION = "납품확인서"
    
    # 기타
    REALTOR_REGISTRATION = "중개사무소등록증"
    BUSINESS_REGISTRATION = "사업자등록증"
    UNKNOWN = "미확인문서"


# =============================================================================
# 문서 감지 키워드 (정확도 향상)
# =============================================================================

DOC_DETECTION_RULES = {
    DocType.HOUSING_SALE_APPLICATION: {
        "must_have": ["주택매도", "신청서"],  # 반드시 포함
        "should_have": ["소유자", "매도주택", "대지면적", "건물사용승인일", "인감"],  # 추가 점수
        "must_not_have": ["임대현황"],  # 포함되면 안됨
    },
    DocType.RENTAL_STATUS: {
        "must_have": ["임대현황"],
        "should_have": ["호별", "전용면적", "보증금", "임대"],
        "must_not_have": [],
    },
    DocType.POWER_OF_ATTORNEY: {
        "must_have": ["위임장"],
        "should_have": ["위임인", "수임인", "위임합니다"],
        "must_not_have": [],
    },
    DocType.CONSENT_FORM: {
        "must_have": ["동의서"],
        "should_have": ["개인정보", "수집", "이용", "제공"],
        "must_not_have": [],
    },
    DocType.INTEGRITY_PLEDGE: {
        "must_have": ["청렴서약서"],
        "should_have": ["서약", "부정청탁"],
        "must_not_have": [],
    },
    DocType.LH_EMPLOYEE_CONFIRM: {
        "must_have": ["공사직원"],
        "should_have": ["LH", "한국토지주택공사", "직원여부"],
        "must_not_have": [],
    },
    DocType.SEAL_CERTIFICATE: {
        "must_have": ["인감증명"],
        "should_have": ["본인발급", "법인인감"],
        "must_not_have": [],
    },
    DocType.BUILDING_LEDGER_TITLE: {
        "must_have": ["건축물대장"],
        "should_have": ["표제부", "대지위치", "주용도", "사용승인", "내진설계"],
        "must_not_have": ["전유부", "총괄"],
    },
    DocType.BUILDING_LEDGER_SUMMARY: {
        "must_have": ["건축물대장", "총괄"],
        "should_have": ["표제부"],
        "must_not_have": [],
    },
    DocType.BUILDING_LEDGER_EXCLUSIVE: {
        "must_have": ["건축물대장"],
        "should_have": ["전유부", "전유부분", "호수"],
        "must_not_have": [],
    },
    DocType.LAND_LEDGER: {
        "must_have": ["토지대장"],
        "should_have": ["지목", "면적", "소유자"],
        "must_not_have": ["이용계획"],
    },
    DocType.LAND_USE_PLAN: {
        "must_have": ["토지이용계획"],
        "should_have": ["용도지역", "도시계획"],
        "must_not_have": [],
    },
    DocType.BUILDING_REGISTRY: {
        "must_have": ["등기사항전부증명서"],
        "should_have": ["건물", "갑구", "을구"],
        "must_not_have": [],
    },
    DocType.LAND_REGISTRY: {
        "must_have": ["등기사항전부증명서"],
        "should_have": ["토지", "갑구", "을구"],
        "must_not_have": [],
    },
    DocType.AS_BUILT_DRAWING: {
        "must_have": ["준공"],
        "should_have": ["도면", "주단면", "종단면", "외벽", "마감", "단열"],
        "must_not_have": [],
    },
    DocType.TEST_CERTIFICATE: {
        "must_have": ["시험성적"],
        "should_have": ["열방출", "가스유해성", "시험"],
        "must_not_have": [],
    },
    DocType.DELIVERY_CONFIRMATION: {
        "must_have": ["납품확인"],
        "should_have": ["납품", "확인서"],
        "must_not_have": [],
    },
}


# =============================================================================
# 페이지 분석 결과
# =============================================================================

@dataclass
class PageAnalysis:
    """페이지 분석 결과"""
    page_num: int
    detected_type: DocType
    confidence: float
    raw_text: str
    extracted_data: Dict[str, Any]
    image: Optional[Image.Image] = None


@dataclass
class DocumentInfo:
    """감지된 문서 정보"""
    doc_type: DocType
    pages: List[int]
    merged_data: Dict[str, Any]
    confidence: float


# =============================================================================
# 통합 분석 시스템
# =============================================================================

# 병렬 처리를 위한 import
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

class UnifiedPDFAnalyzer:
    """
    통합 PDF 분석 시스템 v6.1 (고속화)
    
    1단계: PDF → 고해상도 이미지 + 텍스트 추출
    2단계: 키워드 기반 문서 유형 감지
    3단계: Gemini Vision으로 상세 분석 (병렬 처리)
    4단계: 결과 병합 및 PublicHousingReviewResult 생성
    
    v6.1 최적화:
    - 병렬 API 호출 (ThreadPoolExecutor)
    - 배치 크기 증가 (4 → 8)
    - DPI 최적화 (150 → 120)
    - 대기 시간 최적화 (1.0s → 0.3s)
    """
    
    # ★★★ 고품질 설정 (스캔 PDF/손글씨 인식 향상) ★★★
    DPI = 180              # 120 → 180 (손글씨 인식 향상)
    MAX_PAGES = 50
    MAX_IMAGE_PX = 1200    # 900 → 1200 (더 큰 이미지로 AI 인식률 향상)
    RPM_DELAY = 0.3        # API 호출 간 대기
    RPM_DELAY_JITTER = 0.1 # 지터
    UNKNOWN_BATCH_SIZE = 6 # 미확인 페이지 배치 크기 (8 → 6, 품질 우선)
    MAX_WORKERS = 4        # 병렬 API 호출 워커 수
    
    # 스레드 안전을 위한 락
    _api_lock = threading.Lock()
    
    def __init__(
        self,
        provider: str = "claude",
        model_name: Optional[str] = None,
    ):
        """
        provider: "claude" (기본, 429 완화·고성능) | "gemini"
        model_name: 미지정 시 claude=Opus 4.5, gemini=gemini-2.0-flash
        """
        load_dotenv()
        self.provider = (provider or "claude").strip().lower()
        self._vision_client = create_vision_client(self.provider, model_name)
        self.model_name = getattr(self._vision_client, "model_name", model_name or "claude-opus-4-5")
        self._detected_corp_from_text = False  # 텍스트 기반 법인 감지 결과
    
    # ★★★ 법인 키워드 목록 (클래스 레벨) - 확장 버전 ★★★
    CORP_KEYWORDS = [
        # 기본 법인 형태
        "건설", "법인", "주식회사", "(주)", "㈜", "유한회사", "합명회사", 
        "합자회사", "사단법인", "재단법인", "농협", "조합", "코퍼레이션",
        # 사업 분야 키워드
        "개발", "산업", "부동산", "투자", "홀딩스", "그룹", "에셋", "종합",
        "엔지니어링", "건축", "토건", "주택", "디벨로퍼", "파트너스", "자산",
        # 영문
        "corporation", "corp", "inc", "ltd", "llc", "holdings", "company",
        # 추가 키워드 (확장)
        "공사", "공단", "재단", "학교법인", "의료법인", "종교법인",
        "시행사", "시행", "분양", "하우징", "리얼티", "프로퍼티", "PMC",
        "AMC", "REITs", "리츠", "신탁", "캐피탈", "금융", "저축은행",
        "상사", "물산", "상호", "테크", "이엔지", "E&C", "ENG", "건영",
        "종건", "특수법인", "비영리법인", "공익법인", "사회적기업",
        "협동조합", "영농조합", "어업회사", "산림조합", "수협", "신협",
        # 접미사 형태
        "주)", "㈜", "(유)", "㈜", "Co.", "Co.,Ltd", "Ltd.", "Inc.",
    ]
    
    # ★★★ 법인 판단용 정규표현식 패턴 ★★★
    CORP_PATTERNS = [
        r"주식\s*회사",
        r"\(주\)",
        r"㈜",
        r"유한\s*회사",
        r"\(유\)",
        r"합자\s*회사",
        r"합명\s*회사",
        r"사단\s*법인",
        r"재단\s*법인",
        r".*건설$",
        r".*개발$",
        r".*산업$",
        r".*건축$",
        r".*토건$",
        r".*E&C$",
        r".*ENG$",
        r".*건영$",
    ]
    
    def _detect_corporation_from_text(self, text: str) -> bool:
        """
        PDF 텍스트에서 법인 키워드 감지
        
        ★★★ 핵심: AI가 소유자 이름을 추출 못해도 텍스트에서 직접 감지 ★★★
        ★★★ v2.0: 정규표현식 패턴 추가로 더 정확한 감지 ★★★
        """
        if not text:
            return False
        
        text_lower = text.lower()
        text_normalized = text.replace(" ", "").replace("\n", "")
        
        # 1단계: 키워드 기반 감지
        for keyword in self.CORP_KEYWORDS:
            if keyword.lower() in text_lower or keyword in text_normalized:
                print(f"    [텍스트 법인 감지] 키워드 '{keyword}' 발견")
                return True
        
        # 2단계: 정규표현식 패턴 기반 감지
        import re
        for pattern in self.CORP_PATTERNS:
            if re.search(pattern, text_normalized, re.IGNORECASE):
                print(f"    [텍스트 법인 감지] 패턴 '{pattern}' 매칭")
                return True
        
        # 3단계: 소유자/소유주 란 근처에서 법인 키워드 찾기
        # "소유자" 또는 "소유주" 다음 50자 내에 법인 키워드가 있는지 확인
        owner_match = re.search(r'(소유자|소유주|성명|상호)[:\s]*(.{1,100})', text_normalized)
        if owner_match:
            owner_section = owner_match.group(2)
            for keyword in ["건설", "주식회사", "(주)", "㈜", "개발", "산업", "법인"]:
                if keyword in owner_section:
                    print(f"    [텍스트 법인 감지] 소유자란에서 '{keyword}' 발견: {owner_section[:50]}...")
                    return True
        
        return False
    
    def _detect_corporation_from_name(self, name: str) -> bool:
        """
        소유자 이름에서 법인 여부 감지
        
        ★★★ 핵심: 추출된 소유자 이름만으로 법인 판단 ★★★
        """
        if not name:
            return False
        
        name_normalized = name.replace(" ", "").replace("\n", "").strip()
        name_lower = name.lower()
        
        # 1단계: 직접 키워드 매칭
        for keyword in self.CORP_KEYWORDS:
            if keyword.lower() in name_lower or keyword in name_normalized:
                return True
        
        # 2단계: 정규표현식 패턴 매칭
        import re
        for pattern in self.CORP_PATTERNS:
            if re.search(pattern, name_normalized, re.IGNORECASE):
                return True
        
        # 3단계: 추가 패턴 (이름 끝이 특정 키워드로 끝나는 경우)
        suffix_patterns = [
            "건설", "개발", "산업", "건축", "토건", "주택", "부동산",
            "E&C", "ENG", "건영", "종건", "물산", "상사", "테크"
        ]
        for suffix in suffix_patterns:
            if name_normalized.endswith(suffix):
                return True
        
        return False
    
    def _extract_owner_name_from_text(self, text: str) -> Optional[str]:
        """
        PDF 텍스트에서 소유자 이름을 직접 추출 (AI 실패 시 폴백)
        
        ★★★ 핵심: AI가 owner_name을 null로 반환해도 여기서 복구 ★★★
        """
        if not text:
            return None
        
        text_normalized = text.replace("\n", " ").strip()
        
        # 소유자/소유주 섹션 찾기 패턴 (구체적인 것부터)
        owner_patterns = [
            # "소유자 성명: XXX건설" 또는 "소유주 성명 XXX" 패턴
            r"소유(?:자|주)\s*[성명상호:]*\s*[:\s]*([가-힣a-zA-Z0-9()㈜\s]{2,40}?)(?:\s*생년월일|\s*주민|\s*주소|\s*연락|\s*전화|\n)",
            # "성명: XXX건설" 패턴 (법인)
            r"성\s*명\s*[:\s]*([가-힣a-zA-Z0-9()㈜\s]*(?:건설|개발|산업|주식회사|유한회사)[가-힣a-zA-Z0-9()㈜\s]*)",
            # "상호: (주)XXX" 패턴
            r"상\s*호\s*[:\s]*([가-힣a-zA-Z0-9()㈜\s]{2,40})",
            # "(주)XXX" 또는 "주식회사 XXX" 단독 패턴
            r"((?:주식회사|㈜|\(주\)|유한회사)\s*[가-힣a-zA-Z0-9]+)",
            r"([가-힣a-zA-Z0-9]+\s*(?:주식회사|㈜|\(주\)|건설|개발|산업))",
        ]
        
        for pattern in owner_patterns:
            match = re.search(pattern, text_normalized, re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                # 너무 짧거나 무의미한 결과 필터링
                if len(name) >= 2 and name not in ("성명", "상호", "소유자", "소유주", "생년월일"):
                    print(f"    [텍스트 소유자 추출] 패턴 매칭: '{name}'")
                    return name
        
        return None
    
    def _extract_corporation_name_from_text(self, text: str) -> Optional[str]:
        """
        PDF 텍스트에서 법인명을 직접 추출
        
        ★★★ 핵심: 법인 키워드가 포함된 이름을 찾아 반환 ★★★
        """
        if not text:
            return None
        
        text_normalized = text.replace("\n", " ").strip()
        
        # 법인명 추출 패턴 (구체적인 것부터)
        corp_patterns = [
            # "주식회사 OOO건설" 형태
            r"(주식회사\s*[가-힣a-zA-Z0-9]+(?:\s*[가-힣a-zA-Z0-9]+)*)",
            # "(주)OOO" 또는 "㈜OOO" 형태
            r"((?:\(주\)|㈜)\s*[가-힣a-zA-Z0-9]+(?:\s*[가-힣a-zA-Z0-9]+)*)",
            # "OOO건설 주식회사" 형태
            r"([가-힣a-zA-Z0-9]+(?:\s*[가-힣a-zA-Z0-9]+)*\s*주식회사)",
            # "OOO건설" 형태 (건설, 개발, 산업 등으로 끝남)
            r"([가-힣a-zA-Z0-9]+(?:건설|개발|산업|부동산|투자|건축|토건|주택|에셋|종합))",
            # "유한회사 OOO" 형태
            r"(유한회사\s*[가-힣a-zA-Z0-9]+)",
            # "사단법인 OOO", "재단법인 OOO" 형태
            r"((?:사단|재단)법인\s*[가-힣a-zA-Z0-9]+)",
        ]
        
        for pattern in corp_patterns:
            matches = re.findall(pattern, text_normalized, re.IGNORECASE)
            if matches:
                # 가장 긴 매칭 반환 (더 완전한 법인명일 가능성 높음)
                name = max(matches, key=len).strip()
                if len(name) >= 3:
                    print(f"    [텍스트 법인명 추출] 패턴 매칭: '{name}'")
                    return name
        
        return None
    
    def analyze(
        self, 
        pdf_path: str, 
        announcement_date: str = "2025-07-05"
    ) -> Tuple[PublicHousingReviewResult, Dict]:
        """
        PDF 분석 메인 함수
        
        Returns:
            (PublicHousingReviewResult, 메타데이터)
        """
        start_time = time.time()
        print(f"\n{'='*70}")
        print(f"[UnifiedPDFAnalyzer v6.0] 분석 시작")
        print(f"AI: {self.provider} ({getattr(self._vision_client, 'model_name', self.model_name)})")
        print(f"파일: {pdf_path}")
        print(f"{'='*70}\n")
        
        # 1단계: PDF에서 페이지별 이미지+텍스트 추출
        print(">>> [1단계] PDF 페이지 추출...")
        pages = self._extract_pages(pdf_path)
        print(f"    총 {len(pages)}페이지 추출 완료\n")
        
        # ★★★ 전체 텍스트 저장 (법인 감지용) ★★★
        all_text_combined = ""
        
        # 2단계: 각 페이지의 문서 유형 감지
        print(">>> [2단계] 문서 유형 감지...")
        page_analyses = []
        for page_num, (image, text) in enumerate(pages, 1):
            all_text_combined += (text or "") + "\n"  # 전체 텍스트 누적
            
            doc_type, confidence = self._detect_document_type(text)
            # 스캔 PDF는 텍스트가 거의 없음. 40자 미만이면 키워드 신뢰 불가 → 미확인으로 Gemini에 맡김
            text_len = len((text or "").strip().replace(" ", "").replace("\n", ""))
            if text_len < 40:
                doc_type, confidence = DocType.UNKNOWN, 0.0
            page_analyses.append(PageAnalysis(
                page_num=page_num,
                detected_type=doc_type,
                confidence=confidence,
                raw_text=text,
                extracted_data={},
                image=image
            ))
            print(f"    페이지 {page_num}: {doc_type.value} (신뢰도: {confidence:.0%})")
        
        # ★★★ 텍스트에서 법인 키워드 사전 감지 ★★★
        self._detected_corp_from_text = self._detect_corporation_from_text(all_text_combined)
        if self._detected_corp_from_text:
            print(f"    ★★★ [법인 사전 감지] PDF 텍스트에서 법인 키워드 발견! ★★★")
        
        # 3단계: Gemini로 상세 분석
        print("\n>>> [3단계] AI 상세 분석...")
        documents = self._analyze_with_gemini(page_analyses, announcement_date)
        # 미확인 페이지에서 같은 유형이 여러 번 나온 경우(예: 건축물대장 2페이지) 병합
        documents = self._merge_documents_by_type(documents)
        
        # 4단계: PublicHousingReviewResult 생성
        print("\n>>> [4단계] 결과 생성...")
        result = self._build_result(documents, announcement_date, all_text_combined)
        
        # ★★★ 텍스트 기반 법인 감지 결과 적용 ★★★
        if self._detected_corp_from_text and not result.corporate_documents.is_corporation:
            result.corporate_documents.is_corporation = True
            print(f"    [법인 확정] PDF 텍스트 기반 법인 감지 적용 → is_corporation=True")
        
        # ★★★ 5단계: 소유자 정보 부족 시 전용 추출기 호출 ★★★
        owner = result.housing_sale_application.owner_info
        owner_info_missing = not owner.name or not owner.address
        
        if owner_info_missing and result.housing_sale_application.exists:
            print("\n>>> [5단계] 소유자 정보 부족 → 전용 추출기 호출...")
            try:
                from core.owner_info_extractor import OwnerInfoExtractor
                extractor = OwnerInfoExtractor(provider=self.provider, model_name=self.model_name)
                owner_result = extractor.extract_from_pdf(pdf_path)
                
                # 추출된 정보 적용 (기존 값이 없는 경우에만)
                if owner_result.name and not owner.name:
                    owner.name = owner_result.name
                    print(f"    [전용 추출기] 이름 추출 성공: {owner.name}")
                if owner_result.birth_date and not owner.birth_date:
                    owner.birth_date = owner_result.birth_date
                    print(f"    [전용 추출기] 생년월일 추출 성공: {owner.birth_date}")
                if owner_result.address and not owner.address:
                    owner.address = owner_result.address
                    print(f"    [전용 추출기] 주소 추출 성공: {owner.address}")
                if owner_result.phone and not owner.phone:
                    owner.phone = owner_result.phone
                    print(f"    [전용 추출기] 연락처 추출 성공: {owner.phone}")
                if owner_result.email and not owner.email:
                    owner.email = owner_result.email
                    print(f"    [전용 추출기] 이메일 추출 성공: {owner.email}")
                
                # 법인 여부 업데이트
                if owner_result.is_corporation:
                    result.corporate_documents.is_corporation = True
                    print(f"    [전용 추출기] 법인 감지: is_corporation=True")
                
                # 인감 정보 업데이트
                if owner_result.has_seal and not result.housing_sale_application.seal_verification.seal_exists:
                    result.housing_sale_application.seal_verification.seal_exists = True
                    print(f"    [전용 추출기] 인감 감지: has_seal=True")
                
                # 소유자 정보 완비 여부 재계산
                filled_count = sum([
                    bool(owner.name),
                    bool(owner.birth_date),
                    bool(owner.address),
                    bool(owner.phone),
                    bool(owner.email),
                ])
                owner.is_complete = filled_count >= 3
                
            except Exception as e:
                print(f"    [전용 추출기 오류] {e}")
        
        # 메타데이터
        elapsed = time.time() - start_time
        meta = {
            "total_pages": len(pages),
            "documents_found": [
                {"type": d.doc_type.value, "pages": d.pages, "confidence": d.confidence}
                for d in documents
            ],
            "analysis_time": elapsed,
        }
        
        print(f"\n{'='*70}")
        print(f"[분석 완료] 소요 시간: {elapsed:.1f}초")
        print(f"감지된 문서: {len(documents)}종")
        for doc in documents:
            print(f"  - {doc.doc_type.value}: 페이지 {doc.pages}")
        print(f"{'='*70}\n")
        
        return result, meta
    
    def _extract_pages(self, pdf_path: str) -> List[Tuple[Image.Image, str]]:
        """PDF에서 페이지별 이미지와 텍스트 추출 (병렬 처리)"""
        if not HAS_PYMUPDF:
            raise RuntimeError("PyMuPDF가 필요합니다.")
        
        doc = fitz.open(pdf_path)
        total_pages = min(len(doc), self.MAX_PAGES)
        
        # 먼저 모든 페이지의 텍스트와 pixmap 데이터를 추출 (fitz는 스레드 안전하지 않음)
        page_data = []
        for page_num in range(total_pages):
            page = doc.load_page(page_num)
            text = page.get_text("text")
            mat = fitz.Matrix(self.DPI / 72, self.DPI / 72)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_bytes = pix.tobytes("png")
            page_data.append((img_bytes, text))
        doc.close()
        
        # 이미지 변환 및 전처리는 병렬로 수행
        def process_page(data):
            img_bytes, text = data
            image = Image.open(io.BytesIO(img_bytes))
            image = self._preprocess_image(image)
            return (image, text)
        
        # 병렬 이미지 처리
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            pages = list(executor.map(process_page, page_data))
        
        return pages
    
    def _resize_for_api(self, image: Image.Image) -> Image.Image:
        """API 전송·처리 가속: 긴 변을 MAX_IMAGE_PX 이하로 리사이즈"""
        w, h = image.size
        if w <= self.MAX_IMAGE_PX and h <= self.MAX_IMAGE_PX:
            return image
        scale = self.MAX_IMAGE_PX / max(w, h)
        nw, nh = int(w * scale), int(h * scale)
        resample = getattr(Image, "Resampling", Image).LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
        return image.resize((nw, nh), resample)

    def _preprocess_image(self, image: Image.Image) -> Image.Image:
        """이미지 전처리 (대비·선명도 + API용 리사이즈) - 스캔 PDF/손글씨 인식 강화"""
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        # 1. 대비 강화 (스캔 문서 선명화)
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(1.6)  # 1.4 → 1.6
        
        # 2. 선명도 강화 (흐릿한 손글씨 선명화)
        enhancer = ImageEnhance.Sharpness(image)
        image = enhancer.enhance(2.2)  # 1.8 → 2.2
        
        # 3. 밝기 조정 (너무 어두운 스캔 보정)
        try:
            enhancer = ImageEnhance.Brightness(image)
            image = enhancer.enhance(1.1)
        except:
            pass
        
        return self._resize_for_api(image)
    
    def _detect_document_type(self, text: str) -> Tuple[DocType, float]:
        """키워드 기반 문서 유형 감지 (모든 서류에 대해 스캔/OCR 누락 대비 폴백 적용)"""
        text_normalized = text.replace(" ", "").replace("\n", "")
        
        best_type = DocType.UNKNOWN
        best_score = 0.0
        # 폴백: 짧은/부분 텍스트만 있어도 서류 유형 인식 (모든 서류 공통, 긴/구체적 키워드 우선)
        # 등기부: 토지 포함 시 토지등기, 아니면 건물등기 (순서 중요)
        if "등기부등본" in text_normalized or "등기사항전부" in text_normalized:
            if "토지" in text_normalized and 0.84 > best_score:
                best_type, best_score = DocType.LAND_REGISTRY, 0.84
            elif 0.82 > best_score:
                best_type, best_score = DocType.BUILDING_REGISTRY, 0.82
        # 토지대장 vs 토지이용계획
        if "토지대장" in text_normalized and "이용계획" not in text_normalized and 0.85 > best_score:
            best_type, best_score = DocType.LAND_LEDGER, 0.85
        if "토지이용계획" in text_normalized or "이용계획확인원" in text_normalized:
            if 0.85 > best_score:
                best_type, best_score = DocType.LAND_USE_PLAN, 0.85
        # 건축물대장: 총괄 > 전유부 > 표제부
        if "총괄표제부" in text_normalized or "건축물대장총괄" in text_normalized:
            if 0.88 > best_score:
                best_type, best_score = DocType.BUILDING_LEDGER_SUMMARY, 0.88
        if "건축물대장전유부" in text_normalized or ("전유부" in text_normalized and "건축물대장" in text_normalized):
            if 0.88 > best_score:
                best_type, best_score = DocType.BUILDING_LEDGER_EXCLUSIVE, 0.88
        # 나머지: 단일 키워드 폴백
        for keys_any, keys_not, doc_type, score in [
            ("매도신청서", "임대현황", DocType.HOUSING_SALE_APPLICATION, 0.88),
            ("매도신청주택임대현황", None, DocType.RENTAL_STATUS, 0.9),
            ("임대현황", None, DocType.RENTAL_STATUS, 0.85),
            ("위임장", None, DocType.POWER_OF_ATTORNEY, 0.88),
            ("개인정보동의서", None, DocType.CONSENT_FORM, 0.88),
            ("청렴서약서", None, DocType.INTEGRITY_PLEDGE, 0.88),
            ("청렴서약", None, DocType.INTEGRITY_PLEDGE, 0.82),
            ("공사직원확인서", None, DocType.LH_EMPLOYEE_CONFIRM, 0.88),
            ("공사직원", None, DocType.LH_EMPLOYEE_CONFIRM, 0.82),
            ("인감증명서", None, DocType.SEAL_CERTIFICATE, 0.88),
            ("인감증명", None, DocType.SEAL_CERTIFICATE, 0.82),
            ("건축물현황도", None, DocType.BUILDING_LAYOUT, 0.88),
            ("건축물대장", None, DocType.BUILDING_LEDGER_TITLE, 0.82),
            ("준공도면", None, DocType.AS_BUILT_DRAWING, 0.88),
            ("시험성적서", None, DocType.TEST_CERTIFICATE, 0.88),
            ("납품확인서", None, DocType.DELIVERY_CONFIRMATION, 0.88),
        ]:
            if keys_not and keys_not in text_normalized:
                continue
            if keys_any in text_normalized and score > best_score:
                best_type, best_score = doc_type, score
        # 개인정보+동의서 (둘 다 있을 때)
        if "개인정보" in text_normalized and "동의서" in text_normalized and 0.8 > best_score:
            best_type, best_score = DocType.CONSENT_FORM, 0.8
        # 대리인신분증사본: 대리인 + 신분증(사본) 있으면 제출된 것으로 간주
        if "대리인" in text_normalized and ("신분증" in text_normalized or "사본" in text_normalized) and 0.85 > best_score:
            best_type, best_score = DocType.AGENT_ID_CARD, 0.85
        
        for doc_type, rules in DOC_DETECTION_RULES.items():
            # must_have 체크
            must_have_count = 0
            for keyword in rules["must_have"]:
                if keyword.replace(" ", "") in text_normalized:
                    must_have_count += 1
            
            if must_have_count < len(rules["must_have"]):
                continue  # 필수 키워드 없으면 스킵
            
            # must_not_have 체크
            has_excluded = False
            for keyword in rules.get("must_not_have", []):
                if keyword.replace(" ", "") in text_normalized:
                    has_excluded = True
                    break
            
            if has_excluded:
                continue
            
            # should_have 점수 계산
            should_have_count = 0
            for keyword in rules.get("should_have", []):
                if keyword.replace(" ", "") in text_normalized:
                    should_have_count += 1
            
            total_keywords = len(rules["must_have"]) + len(rules.get("should_have", []))
            score = (must_have_count + should_have_count) / total_keywords if total_keywords > 0 else 0
            
            if score > best_score:
                best_score = score
                best_type = doc_type
        
        return best_type, best_score
    
    # 429 방지: 호출 전 고정 지연 + 429 시 재시도 지수 백오프(초기 대기 길게)
    MAX_RETRIES_429 = 5
    RETRY_BASE_DELAY = 12   # 초 (첫 재시도 대기, 429 후 여유 있게)
    RETRY_MAX_DELAY = 120   # 초
    RETRY_JITTER = 0.2      # 재시도 딜레이에 곱할 랜덤 (0.8~1.2)

    def _is_rate_limit_error(self, e: Exception) -> bool:
        """429 / Resource exhausted / Claude rate_limit·overloaded 여부 확인"""
        if google_exceptions and isinstance(e, google_exceptions.ResourceExhausted):
            return True
        msg = (getattr(e, "message", "") or str(e)).lower()
        return (
            "429" in msg or "resource exhausted" in msg or "rate limit" in msg
            or "overloaded" in msg or "rate_limit" in msg
        )

    def _throttle_before_request(self) -> None:
        """429 방지: API 호출 전 RPM 한도 이하로 유지하기 위한 대기"""
        delay = self.RPM_DELAY + random.uniform(0, self.RPM_DELAY_JITTER)
        time.sleep(delay)

    def _generate_content_with_retry(self, *args, **kwargs):
        """Vision API 호출 (Gemini/Claude) + 429 시 지수 백오프·지터 재시도 (스레드 안전)"""
        # 스레드 안전을 위해 락 사용 (동시 호출 방지)
        with self._api_lock:
            self._throttle_before_request()
        
        content = args[0] if args else []
        prompt = content[0] if isinstance(content, list) and len(content) > 0 else ""
        images = list(content[1:]) if isinstance(content, list) and len(content) > 1 else []
        for attempt in range(self.MAX_RETRIES_429):
            try:
                result_text = self._vision_client.generate_json(prompt, images)
                return type("_Response", (), {"text": result_text})()
            except Exception as e:
                if not self._is_rate_limit_error(e) or attempt == self.MAX_RETRIES_429 - 1:
                    raise
                # 글로벌 쿨다운 신호 → 다른 쓰레드도 일시정지
                get_global_limiter().report_rate_limit()
                raw_delay = min(
                    self.RETRY_BASE_DELAY * (2 ** attempt),
                    self.RETRY_MAX_DELAY
                )
                jitter = 1.0 + random.uniform(-self.RETRY_JITTER, self.RETRY_JITTER)
                delay = max(1.0, raw_delay * jitter)
                print(f"      [Rate limit] {delay:.1f}초 후 재시도 ({attempt + 1}/{self.MAX_RETRIES_429})...")
                time.sleep(delay)
    
    def _analyze_with_gemini(
        self, 
        page_analyses: List[PageAnalysis],
        announcement_date: str
    ) -> List[DocumentInfo]:
        """Gemini로 상세 분석 (병렬 처리로 2-3배 속도 향상)"""
        
        # 문서 유형별로 페이지 그룹화
        type_pages: Dict[DocType, List[PageAnalysis]] = {}
        for pa in page_analyses:
            if pa.detected_type not in type_pages:
                type_pages[pa.detected_type] = []
            type_pages[pa.detected_type].append(pa)
        
        documents = []
        
        # 1) 미확인 페이지 먼저 유형 판별 (배치 처리)
        unknown_pages = type_pages.pop(DocType.UNKNOWN, [])
        detected_unknown_tasks = []
        if unknown_pages:
            n_unknown = len(unknown_pages)
            print(f"    미확인 페이지 {n_unknown}장 배치 유형 판별 중...")
            detected_list = self._analyze_unknown_pages_batch(unknown_pages)
            for pa, detected in zip(unknown_pages, detected_list):
                if detected != DocType.UNKNOWN:
                    detected_unknown_tasks.append((detected, [pa]))
        
        # 2) 병렬 처리할 작업 목록 구성
        tasks = []
        for doc_type, pages in type_pages.items():
            tasks.append((doc_type, pages))
        tasks.extend(detected_unknown_tasks)
        
        if not tasks:
            return documents
        
        # 3) 병렬 API 호출 (ThreadPoolExecutor)
        print(f"    ★ 병렬 처리: {len(tasks)}개 문서 유형 동시 분석...")
        
        def analyze_task(task_info):
            doc_type, pages = task_info
            try:
                merged_data = self._analyze_document_pages(doc_type, pages, announcement_date)
                return DocumentInfo(
                    doc_type=doc_type,
                    pages=[p.page_num for p in pages],
                    merged_data=merged_data or {},
                    confidence=max(p.confidence for p in pages) if pages else 0.7
                )
            except Exception as e:
                print(f"      [오류] {doc_type.value}: {e}")
                return None
        
        # 병렬 실행
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {executor.submit(analyze_task, t): t for t in tasks}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    result = future.result()
                    if result:
                        documents.append(result)
                        print(f"      ✓ {result.doc_type.value} 완료 (페이지 {result.pages})")
                except Exception as e:
                    print(f"      [예외] {task[0].value}: {e}")
        
        return documents
    
    def _analyze_document_pages(
        self,
        doc_type: DocType,
        pages: List[PageAnalysis],
        announcement_date: str
    ) -> Dict[str, Any]:
        """문서 유형별 페이지 분석"""
        
        prompt = self._get_analysis_prompt(doc_type, announcement_date)
        images = [p.image for p in pages[:5] if p.image]
        
        if not images:
            return {}
        
        try:
            response = self._generate_content_with_retry([prompt] + images)
            result_text = getattr(response, "text", str(response))
            data = self._parse_json(result_text)
            if not data and result_text.strip():
                print(f"      [경고] JSON 파싱 실패 또는 빈 객체: {result_text[:200]}...")
            return data or {}
            
        except Exception as e:
            print(f"      분석 오류: {e}")
            return {}
    
    def _analyze_unknown_pages_batch(
        self,
        pages: List[PageAnalysis],
    ) -> List[DocType]:
        """미확인 페이지 N장을 한 번에 유형만 판별 (API 호출 수·429 감소)."""
        type_list: List[DocType] = []
        batch_size = self.UNKNOWN_BATCH_SIZE
        for start in range(0, len(pages), batch_size):
            chunk = pages[start:start + batch_size]
            images = [p.image for p in chunk if p.image]
            if not images:
                type_list.extend([DocType.UNKNOWN] * len(chunk))
                continue
            prompt = """다음 """ + str(len(images)) + """개 이미지를 **순서대로** 보고, 각 이미지의 문서 유형만 아래 목록 중 정확한 문자열로 JSON **배열**로 반환하세요.

[유형 목록] 주택매도신청서, 매도신청주택임대현황, 위임장, 개인정보동의서, 청렴서약서, 공사직원확인서, 인감증명서, 건축물대장표제부, 건축물대장총괄표제부, 건축물대장전유부, 건축물현황도, 토지대장, 토지이용계획확인원, 건물등기부등본, 토지등기부등본, 준공도면, 시험성적서, 납품확인서, 중개사무소등록증, 사업자등록증, 기타

출력 형식 (반드시 JSON 배열만):
[{"document_type": "주택매도신청서"}, {"document_type": "건축물대장표제부"}, ...]

JSON 배열만 출력하세요."""
            try:
                response = self._generate_content_with_retry([prompt] + images)
                result_text = getattr(response, "text", "[]")
                parsed = self._parse_json(result_text)
                chunk_types: List[DocType] = []
                if isinstance(parsed, list):
                    for item in parsed[: len(chunk)]:
                        type_str = item.get("document_type", "") if isinstance(item, dict) else ""
                        chunk_types.append(self._map_type_string(type_str))
                if len(chunk_types) < len(chunk):
                    chunk_types.extend([DocType.UNKNOWN] * (len(chunk) - len(chunk_types)))
                type_list.extend(chunk_types)
            except Exception as e:
                print(f"      배치 유형 판별 오류: {e}")
                type_list.extend([DocType.UNKNOWN] * len(chunk))
        if len(type_list) < len(pages):
            type_list.extend([DocType.UNKNOWN] * (len(pages) - len(type_list)))
        return type_list[: len(pages)]

    def _analyze_unknown_page(
        self,
        page: PageAnalysis,
        announcement_date: str
    ) -> Tuple[DocType, Dict]:
        """미확인 페이지 단건 분석 (배치 실패 시 폴백용)"""
        
        prompt = f"""이 문서의 유형을 정확히 파악하세요. document_type은 반드시 아래 **정확한 문자열 하나**로만 표기하세요 (띄어쓰기 없이).

[필수 양식]
- 주택매도신청서 (소유자·매도주택·대지면적·사용승인일·인감 등)
- 매도신청주택임대현황 (호별·전용면적·보증금·임대)
- 위임장 (위임인·수임인·위임합니다)
- 개인정보동의서 (개인정보 수집 이용 제공)
- 청렴서약서 (청렴·부정청탁 서약)
- 공사직원확인서 (LH·한국토지주택공사 직원여부)

[정부·공공 발급]
- 인감증명서 (본인발급용·법인인감)
- 건축물대장표제부 (표제부·대지위치·주용도·사용승인일)
- 건축물대장총괄표제부 (총괄·여러 동 건물)
- 건축물대장전유부 (전유부·호수·전용면적)
- 건축물현황도 (현황도)
- 토지대장 (지목·면적·소재지)
- 토지이용계획확인원 (용도지역·도시계획)
- 건물등기부등본 (건물·갑구·을구·소유권)
- 토지등기부등본 (토지·갑구·을구·소유권)

[규칙 29·30]
- 준공도면 (주단면도·종단면도·외벽마감·외벽단열·필로티 자재)
- 시험성적서 (열방출시험·가스유해성 시험)
- 납품확인서 (납품 확인)

[기타]
- 중개사무소등록증, 사업자등록증
- 기타 (위에 해당 없을 때만)

문서 상단 제목·양식·표지를 보고 유형을 선택하세요. 첫 페이지는 주택매도신청서인 경우가 많습니다.

출력 형식 (JSON):
{{
  "document_type": "위목록중정확한문자열하나",
  "data": {{ }}
}}

JSON만 출력하세요."""

        try:
            response = self._generate_content_with_retry([prompt, page.image])
            result = self._parse_json(getattr(response, "text", "{}"))
            
            # 문서 유형 매핑
            type_str = result.get("document_type", "")
            detected = self._map_type_string(type_str)
            
            return detected, result.get("data", {})
            
        except Exception as e:
            print(f"      미확인 페이지 분석 오류: {e}")
            return DocType.UNKNOWN, {}
    
    def _map_type_string(self, type_str: str) -> DocType:
        """문자열을 DocType으로 매핑 (공백/줄바꿈 무시, 모든 서류 유형 변형·약칭 수용)"""
        if not type_str or not isinstance(type_str, str):
            return DocType.UNKNOWN
        normalized = type_str.replace(" ", "").replace("\n", "").strip()
        # 구체적/긴 키부터 매칭 (토지등기부 > 등기부등본, 건축물대장총괄 > 건축물대장 등)
        mapping = [
            # 주택매도·임대현황
            ("주택매도신청서", DocType.HOUSING_SALE_APPLICATION),
            ("매도신청서", DocType.HOUSING_SALE_APPLICATION),
            ("매도신청주택임대현황", DocType.RENTAL_STATUS),
            ("임대현황", DocType.RENTAL_STATUS),
            # 위임·동의·서약·직원·인감
            ("위임장", DocType.POWER_OF_ATTORNEY),
            ("개인정보동의서", DocType.CONSENT_FORM),
            ("개인정보수집이용제공", DocType.CONSENT_FORM),
            ("개인정보수집", DocType.CONSENT_FORM),
            ("개인정보동의", DocType.CONSENT_FORM),
            ("개인정보", DocType.CONSENT_FORM),
            ("청렴서약서", DocType.INTEGRITY_PLEDGE),
            ("청렴서약", DocType.INTEGRITY_PLEDGE),
            ("공사직원확인서", DocType.LH_EMPLOYEE_CONFIRM),
            ("공사직원여부", DocType.LH_EMPLOYEE_CONFIRM),
            ("공사직원", DocType.LH_EMPLOYEE_CONFIRM),
            ("직원확인서", DocType.LH_EMPLOYEE_CONFIRM),
            ("인감증명서", DocType.SEAL_CERTIFICATE),
            ("인감증명", DocType.SEAL_CERTIFICATE),
            ("본인발급용", DocType.SEAL_CERTIFICATE),
            ("법인인감", DocType.SEAL_CERTIFICATE),
            ("대리인신분증사본", DocType.AGENT_ID_CARD),
            ("대리인신분증", DocType.AGENT_ID_CARD),
            # 건축물대장 (총괄 > 전유부 > 표제부)
            ("건축물대장총괄표제부", DocType.BUILDING_LEDGER_SUMMARY),
            ("총괄표제부", DocType.BUILDING_LEDGER_SUMMARY),
            ("건축물대장총괄", DocType.BUILDING_LEDGER_SUMMARY),
            ("건축물대장전유부", DocType.BUILDING_LEDGER_EXCLUSIVE),
            ("전유부", DocType.BUILDING_LEDGER_EXCLUSIVE),
            ("전유부분", DocType.BUILDING_LEDGER_EXCLUSIVE),
            ("건축물대장표제부", DocType.BUILDING_LEDGER_TITLE),
            ("건축물대장", DocType.BUILDING_LEDGER_TITLE),
            ("표제부", DocType.BUILDING_LEDGER_TITLE),
            ("건축물현황도", DocType.BUILDING_LAYOUT),
            ("현황도", DocType.BUILDING_LAYOUT),
            # 토지
            ("토지대장", DocType.LAND_LEDGER),
            ("토지이용계획확인원", DocType.LAND_USE_PLAN),
            ("토지이용계획", DocType.LAND_USE_PLAN),
            ("이용계획확인원", DocType.LAND_USE_PLAN),
            # 등기부 (건물/토지 구분)
            ("건물등기부등본", DocType.BUILDING_REGISTRY),
            ("건물등본", DocType.BUILDING_REGISTRY),
            ("토지등기부등본", DocType.LAND_REGISTRY),
            ("토지등본", DocType.LAND_REGISTRY),
            ("등기사항전부증명서", DocType.BUILDING_REGISTRY),
            ("등기부등본", DocType.BUILDING_REGISTRY),
            ("등기사항전부", DocType.BUILDING_REGISTRY),
            ("등본", DocType.BUILDING_REGISTRY),
            # 규칙 29, 30
            ("준공도면", DocType.AS_BUILT_DRAWING),
            ("준공도", DocType.AS_BUILT_DRAWING),
            ("시험성적서", DocType.TEST_CERTIFICATE),
            ("시험성적", DocType.TEST_CERTIFICATE),
            ("납품확인서", DocType.DELIVERY_CONFIRMATION),
            ("납품확인", DocType.DELIVERY_CONFIRMATION),
            # 중개·사업자 (검증 모델에 있으면 적용)
            ("중개사무소등록증", DocType.REALTOR_REGISTRATION),
            ("중개사무소", DocType.REALTOR_REGISTRATION),
            ("사업자등록증", DocType.BUSINESS_REGISTRATION),
            ("사업자등록", DocType.BUSINESS_REGISTRATION),
        ]
        for key, doc_type in mapping:
            if key in normalized:
                return doc_type
        # 토지 등기: normalized에 "토지"가 있으면 토지등기로
        if "토지" in normalized and ("등기" in normalized or "등본" in normalized):
            return DocType.LAND_REGISTRY
        return DocType.UNKNOWN
    
    def _get_analysis_prompt(self, doc_type: DocType, announcement_date: str) -> str:
        """문서 유형별 분석 프롬프트"""
        
        base = f"기준 공고일: {announcement_date}\n\n"
        base += "[필수] 문서에 기재된 내용이 있으면 반드시 해당 필드에 채우세요. 있는 정보를 null이나 false로 두지 마세요. 모든 키를 반드시 포함한 JSON만 출력하세요.\n\n"
        
        if doc_type == DocType.HOUSING_SALE_APPLICATION:
            return base + """이 문서는 **주택매도 신청서**입니다.

████████████████████████████████████████████████████████████████████████████
█ 절대 명령: 이 문서에서 "소유주" 또는 "소유자" 란의 "성명/상호" 칸을 찾아서  █
█ 그 안에 적힌 텍스트를 반드시 owner_name 필드에 그대로 반환하세요!          █
█ owner_name이 null이면 심각한 오류입니다. 절대 null 반환 금지!            █
████████████████████████████████████████████████████████████████████████████

[0단계] ★★★ 텍스트 전체 스캔 (가장 먼저!) ★★★

이미지의 모든 텍스트를 처음부터 끝까지 읽으세요:
- 표 안의 모든 텍스트
- 손글씨 (흐릿해도 최대한 해독)
- 작은 글씨, 도장 안 글씨
- 특히 "소유주", "소유자", "성명", "상호" 근처의 텍스트에 집중!

[1단계] ★★★★★ 소유자 이름 추출 (최우선, 절대 필수!) ★★★★★

▶ 찾을 위치:
- 문서 상단의 "소유주" 또는 "소유자" 섹션
- "성명" 칸 또는 "상호" 칸
- 표 형식으로 되어 있음: | 성명 | OOO건설 주식회사 |

▶ 법인 이름 패턴 (이런 형태면 법인입니다):
- "XX건설", "XX개발", "XX산업", "XX부동산", "XX투자"
- "주식회사 XX", "(주)XX", "㈜XX"
- "XX 주식회사", "XX(주)"
- "유한회사 XX", "합자회사 XX"
- "사단법인 XX", "재단법인 XX"

▶ 예시:
- "주식회사 대한건설" → owner_name: "주식회사 대한건설", is_corporation: true
- "(주)삼성개발" → owner_name: "(주)삼성개발", is_corporation: true
- "한양건설 주식회사" → owner_name: "한양건설 주식회사", is_corporation: true
- "홍길동" → owner_name: "홍길동", is_corporation: false
- "김철수" → owner_name: "김철수", is_corporation: false

▶ 법인 판단 키워드 (하나라도 포함되면 is_corporation: true):
건설, 주식회사, (주), ㈜, 유한회사, 합명회사, 합자회사, 사단법인, 재단법인, 
농협, 조합, 코퍼레이션, 개발, 산업, 부동산, 투자, 홀딩스, 그룹, 에셋, 종합,
엔지니어링, 건축, 토건, 주택, 디벨로퍼, 파트너스, 자산, 공사, 공단, 재단,
corporation, corp, inc, ltd, llc, holdings, company

[2단계] 소유자 정보 5개 항목 추출

| 필드명 | 찾을 위치 | 설명 |
|--------|----------|------|
| owner_name | "성명/상호" 칸 | ★ 필수! 절대 null 금지! |
| owner_birth | "생년월일/주민번호" 칸 | 앞 6자리 (법인은 빈값) |
| owner_address | "주소/현거주지" 칸 | 전체 주소 |
| owner_phone | "전화번호/연락처" 칸 | 010-XXXX-XXXX |
| owner_email | "이메일/E-mail" 칸 | xxx@xxx.com |

[3단계] 매도주택 정보

- property_address: 매도주택 소재지
- land_area: 대지면적 (㎡)
- approval_date: 건물사용승인일

[4단계] 인감 및 기타

- has_seal: "(인)" 란에 빨간 도장 있으면 true
- seal_name: 도장에 새겨진 이름
- written_date: 작성일자
- agent_id_card_match: 대리인란 기재 여부

★★★ 출력 형식 (JSON) ★★★
```json
{
  "exists": true,
  "is_corporation": true,
  "owner_name": "반드시 추출! 예: 주식회사 대한건설",
  "owner_birth": "",
  "owner_address": "추출한 주소",
  "owner_phone": "010-XXXX-XXXX",
  "owner_email": "xxx@xxx.com",
  "property_address": "매도주택 소재지",
  "land_area": 123.45,
  "approval_date": "YYYY-MM-DD",
  "has_seal": true,
  "seal_name": "도장 이름",
  "written_date": "YYYY-MM-DD",
  "agent_id_card_match": false
}
```

████████████████████████████████████████████████████████████████████████████
█ 경고! owner_name을 null로 반환하면 시스템 오류 발생!                     █
█ 이미지에서 "성명/상호" 칸의 텍스트를 반드시 읽어서 반환하세요!             █
█ 건설, 주식회사, (주) 등 법인 키워드가 있으면 is_corporation: true        █
████████████████████████████████████████████████████████████████████████████

JSON만 출력하세요."""

        elif doc_type == DocType.RENTAL_STATUS:
            return base + """이 문서는 **매도신청주택 임대현황**입니다.

호별 정보를 추출하세요:
- 호수
- 전용면적 (㎡)
- 임대보증금
- 월임대료
- 입주현황

출력 형식:
```json
{
  "exists": true,
  "units": [
    {"unit": "101", "area": 25.5, "deposit": 50000000, "rent": 0, "status": "입주"}
  ],
  "total_units": 15
}
```

JSON만 출력하세요."""

        elif doc_type == DocType.POWER_OF_ATTORNEY:
            return base + """이 문서는 **위임장**입니다.

다음 정보를 추출하세요:
1. 위임인(소유자) 정보
   - 성명
   - 주소
   - 인감 날인 여부

2. 수임인(대리인) 정보
   - 성명
   - 주소
   - 인감 날인 여부

3. 위임 내용
   - 소재지
   - 대지면적

4. 작성일자

출력 형식:
```json
{
  "exists": true,
  "delegator_name": "위임인 성명",
  "delegator_seal": true,
  "delegatee_name": "수임인 성명",
  "delegatee_seal": true,
  "property_address": "소재지",
  "land_area": 123.45,
  "written_date": "YYYY-MM-DD"
}
```

JSON만 출력하세요."""

        elif doc_type == DocType.CONSENT_FORM:
            return base + """이 문서는 **개인정보 수집 이용 및 제공 동의서**입니다.

[필수] 각 란을 구분해 확인하고, **서명·인감·작성일이 보이면 반드시 true 또는 해당 값을 넣으세요.** 없을 때만 false/null로 두세요.

1. 소유자(매도인) 란
   - 소유자 서명 또는 인감이 날인되어 있으면 owner_signed: true, owner_seal_valid: true
   - 소유자 작성일자가 기재되어 있으면 owner_written_date에 날짜(YYYY-MM-DD 또는 YYYY.MM.DD) 넣기
   - 해당 란에 기재가 있으면 true, 완전히 비어 있을 때만 false

2. 대리인 란 (대리인란에 내용이 있을 때만)
   - 대리인 서명 또는 인감이 있으면 agent_signed: true, agent_seal_valid: true
   - 대리인 작성일자가 있으면 agent_written_date에 날짜 넣기
   - 대리인란이 비어 있으면 agent_signed/agent_seal_valid는 false, 대리인란에 기재가 있으면 true

3. 작성일은 숫자·문자 구분해 정확히 추출 (공고일 이후 여부는 별도 검증함)

출력 형식 (아래 키를 모두 포함하고, 기재가 있으면 true/날짜로 채우세요):
```json
{
  "exists": true,
  "owner_signed": true,
  "owner_seal_valid": true,
  "owner_written_date": "YYYY-MM-DD",
  "agent_signed": true,
  "agent_seal_valid": true,
  "agent_written_date": "YYYY-MM-DD"
}
```
(대리인란이 없으면 agent_signed, agent_seal_valid, agent_written_date는 false 또는 null)

JSON만 출력하세요."""

        elif doc_type == DocType.INTEGRITY_PLEDGE:
            return base + """이 문서는 **청렴서약서**입니다.

[필수] 소유자·대리인·중개사 각 란을 구분해 확인하고, **서명·인감·주민번호 등이 보이면 반드시 true로 표기하세요.** 해당 란이 비어 있을 때만 false로 두세요.

1. 소유자(매도인) 란
   - 성명·인감·주민등록번호(또는 사업자등록번호)가 기재되어 있으면 owner_submitted: true, owner_seal_valid: true, owner_id_number_valid: true
   - 법인이면 사업자등록번호가 있어야 하고, 개인이면 주민등록번호. 올바르게 기재되어 있으면 true

2. 대리인 란 (대리인란에 내용이 있을 때만)
   - 대리인 성명·인감이 있으면 agent_submitted: true, agent_seal_valid: true
   - 대리인란이 비어 있으면 false

3. 중개사 란 (대리인이 공인중개사인 경우 해당 란에 내용이 있을 때만)
   - 중개사 성명·인감이 있으면 realtor_submitted: true, realtor_seal_valid: true
   - 해당 란이 없거나 비어 있으면 false

4. 작성일자 written_date (있으면 YYYY-MM-DD 형식으로)

출력 형식 (아래 키를 모두 포함하고, 기재가 있으면 true로 채우세요):
```json
{
  "exists": true,
  "owner_submitted": true,
  "owner_seal_valid": true,
  "owner_id_number_valid": true,
  "owner_name": "성명",
  "written_date": "YYYY-MM-DD",
  "agent_submitted": true,
  "agent_seal_valid": true,
  "realtor_submitted": false,
  "realtor_seal_valid": false
}
```

JSON만 출력하세요."""

        elif doc_type == DocType.LH_EMPLOYEE_CONFIRM:
            return base + """이 문서는 **공사직원여부 확인서**입니다.

다음 정보를 추출하세요:
1. 소유자 성명
2. 인감 날인 여부
3. 작성일자

출력 형식:
```json
{
  "exists": true,
  "owner_name": "성명",
  "has_seal": true,
  "written_date": "YYYY-MM-DD"
}
```

JSON만 출력하세요."""

        elif doc_type == DocType.SEAL_CERTIFICATE:
            return base + """이 문서는 **인감증명서**입니다.

[필수] 서류가 인감증명서이면 exists: true로 두세요. 기재된 내용이 있으면 반드시 채우세요.

1. 본인발급용 / 법인인감 구분 (type)
2. 성명 또는 법인명 (name)
3. 인감 이미지 (도장 모양, 선명도) — seal_shape, seal_text
4. 발급일 — issue_date 또는 발급일란 (YYYY-MM-DD, YYYY.MM.DD 등)

출력 형식:
```json
{
  "exists": true,
  "type": "본인발급용",
  "name": "성명",
  "seal_shape": "원형",
  "seal_text": "도장 안 글자",
  "issue_date": "YYYY-MM-DD"
}
```
발급일이 서류에 보이면 issue_date에 반드시 넣으세요. 없을 때만 null로 두세요.

JSON만 출력하세요."""

        elif doc_type == DocType.AGENT_ID_CARD:
            return base + """이 문서는 **대리인 신분증 사본**입니다. 대리인 신분증이 제출된 것으로 인식하세요.

출력 형식:
```json
{"exists": true}
```
JSON만 출력하세요."""

        elif doc_type == DocType.BUILDING_LEDGER_TITLE:
            return base + """이 문서는 **건축물대장 표제부**입니다. (한 필지에 한 동일 때 받는 서류. 내진설계 적용 여부는 이 표제부에서만 검토함.)

다음 정보를 정확히 추출하세요:

1. 대지위치 (주소)
2. 건물명칭
3. 주용도 (다가구주택, 공동주택 등)
4. 주구조
5. 층수
   - 지상 몇 층
   - 지하 몇 층 (없으면 0)
6. **지하 세대 여부 (매입제외 판단용, 매우 중요)**
   - 지하층에 **거주용 세대(호)**가 있으면 → has_basement_units: true
   - 지하층은 있지만 **주차장·창고·기계실 등만** 있으면 → has_basement_units: false
   - 지하층 자체가 없으면 → has_basement_units: false
   - (일반 지하층 존재는 제외 사유가 아님. 지하 세대가 있을 때만 제외)
7. 사용승인일 (YYYY-MM-DD)
8. **내진설계적용여부**
   - "적용" → true, "해당없음" → false, 못 찾음 → null
9. 승강기 대수 (없으면 0)
10. 주차장: 옥내자주식, 옥외, 기계식

출력 형식:
```json
{
  "exists": true,
  "location": "대지위치",
  "building_name": "건물명칭",
  "main_use": "주용도",
  "structure": "주구조",
  "ground_floors": 5,
  "basement_floors": 1,
  "has_basement_units": false,
  "approval_date": "YYYY-MM-DD",
  "seismic_design": true,
  "elevator_count": 1,
  "parking_indoor": 5,
  "parking_outdoor": 3,
  "parking_mechanical": 0,
  "issue_date": "YYYY-MM-DD"
}
```
(has_basement_units는 지하에 거주용 호가 있을 때만 true, 지하층만 있으면 false)

JSON만 출력하세요."""

        elif doc_type == DocType.BUILDING_LEDGER_SUMMARY:
            return base + """이 문서는 **건축물대장 총괄표제부**입니다. (한 필지에서 2개 이상 동이 있을 때 받는 서류.)

[중요] 내진설계 적용 여부는 **표제부**에서만 검토합니다. 총괄표제부에서는 seismic_design, 사용승인일 등 표제부 전용 항목을 추출하지 마세요.

다음만 추출하세요:
1. 문서 존재 여부 (exists: true)
2. 동 수 (building_count) — 총괄표제부에 기재된 건물(동) 개수

출력 형식:
```json
{
  "exists": true,
  "building_count": 2
}
```
JSON만 출력하세요."""

        elif doc_type == DocType.BUILDING_LEDGER_EXCLUSIVE:
            return base + """이 문서는 **건축물대장 전유부**입니다.

호수별 전용면적을 추출하세요.

출력 형식:
```json
{
  "exists": true,
  "units": [
    {"unit": "101", "area": 25.5},
    {"unit": "102", "area": 30.2}
  ]
}
```

JSON만 출력하세요."""

        elif doc_type == DocType.BUILDING_LAYOUT:
            return base + """이 문서는 **건축물현황도**입니다.

다음 정보를 추출하세요:
1. 배치도(대지배치도) 존재 여부
2. 층별 평면도 존재 여부
3. 호별 평면도 존재 여부
4. 지자체(시·군·구) 발급분 여부

출력 형식:
```json
{
  "exists": true,
  "has_site_plan": true,
  "has_all_floor_plans": true,
  "has_unit_plans": true,
  "is_government_issued": true
}
```

JSON만 출력하세요."""

        elif doc_type == DocType.LAND_LEDGER:
            return base + """이 문서는 **토지대장**입니다.

다음 정보를 추출하세요:
1. 소재지
2. 지번
3. 지목
4. 면적 (㎡)
5. 발급일

출력 형식:
```json
{
  "exists": true,
  "location": "소재지",
  "lot_number": "지번",
  "land_category": "지목",
  "land_area": 123.45,
  "issue_date": "YYYY-MM-DD",
  "land_category": "지목",
  "use_restrictions": ["용도·행위제한 등"]
}
```

JSON만 출력하세요."""

        elif doc_type == DocType.AS_BUILT_DRAWING:
            return base + """이 문서는 **준공도면**입니다. (규칙 29)

★★★ 중요: 외벽 및 필로티 자재명을 정확히 추출해야 합니다 ★★★

【검토 대상 페이지】
- 주단면도, 종단면도, 외벽 상세도, 벽체 상세도
- 재료표, 마감표, 단열재 상세

【추출 항목 - 도면에 적힌 실제 자재명을 그대로】
1. 외벽 마감재료: 석재, 타일, 드라이비트, 징크, 알루미늄판넬, 도장 등
2. 외벽 단열재료: 비드법보온판, 압출법보온판(XPS), EPS, 우레탄폼, 글라스울, 미네랄울 등
3. 필로티 마감재료: 화강석, 석재타일, 도장, 타일 등 (필로티 구조인 경우만)
4. 필로티 단열재료: 비드법, XPS, EPS 등 (필로티 구조인 경우만)

【주의사항】
- "자재명", "미확인", "추출 필요" 같은 플레이스홀더 사용 금지
- 도면에 자재명이 안 보이면 null로 표기
- 필로티가 없는 구조면 필로티 항목은 null

출력 형식:
```json
{
  "exists": true,
  "materials_extracted": true,
  "exterior_finish_material": "도면에 적힌 실제 외벽 마감재 이름",
  "exterior_insulation_material": "도면에 적힌 실제 외벽 단열재 이름",
  "piloti_finish_material": "필로티 마감재 또는 null",
  "piloti_insulation_material": "필로티 단열재 또는 null",
  "has_piloti": true 또는 false
}
```
JSON만 출력하세요. 반드시 도면에서 읽은 실제 자재명만 기입하세요."""

        elif doc_type == DocType.TEST_CERTIFICATE:
            return base + """이 문서는 **준불연 시험성적서**입니다. (규칙 30)

★★★ 핵심 규칙: 열방출시험 + 가스유해성 시험 두 가지가 "반드시 모두" 있어야 유효 ★★★
★★★ 열전도율 시험만 있는 경우는 무조건 "무효" - 보완서류 대상 ★★★

【필수 시험 항목 - 둘 다 있어야만 유효】
1. 열방출시험 (다음 키워드 중 하나라도 있으면 열방출시험 포함):
   - "열방출", "총열방출량", "열방출률", "열방출율", "열량방출"
   - "THR", "Total Heat Release", "Heat Release Rate", "HRR"
   - "KS F ISO 5660", "ISO 5660", "콘칼로리미터", "Cone Calorimeter"
   - "발열량", "발열율", "열에너지방출"

2. 가스유해성 시험 (다음 키워드 중 하나라도 있으면 가스유해성시험 포함):
   - "가스유해성", "가스독성", "연소가스유해성", "연소가스"
   - "Gas Toxicity", "Toxic Gas", "Toxicity Test"
   - "KS F 2271", "유해가스", "유독가스"
   - "연기독성", "연기유해성", "마우스", "동물시험"

【제외 대상 - 열전도율 시험】 ★★★ 이것만 있으면 반드시 "무효" 처리 ★★★
- "열전도율", "열전도", "열전도계수", "단열성능", "단열시험"
- "Thermal Conductivity", "K-value", "K값"
- "KS L ISO 8302", "KS L 9016"

【판단 로직 - 반드시 따를 것】
1. 열방출시험 O + 가스유해성 O → "유효" (has_heat_release_test=true, has_gas_toxicity_test=true)
2. 열방출시험 O + 가스유해성 X → "무효" (보완 필요)
3. 열방출시험 X + 가스유해성 O → "무효" (보완 필요)
4. 열전도율만 있음 → "무효" (보완 필요) - 가장 주의해야 할 케이스!
5. 아무 시험도 없음 → "무효" (보완 필요)

【주의사항】
- 열전도율 시험이 있더라도, 열방출+가스유해성이 함께 있으면 "유효"
- 열전도율 시험"만" 있는 경우가 가장 위험 - 반드시 걸러내야 함
- 문서 전체(제목, 표, 본문, 결과 부분)를 꼼꼼히 확인할 것

출력 형식:
```json
{
  "exists": true,
  "has_heat_release_test": true 또는 false,
  "has_gas_toxicity_test": true 또는 false,
  "has_thermal_conductivity_test": true 또는 false,
  "detected_tests": ["문서에서 발견된 모든 시험 항목명을 정확히 기재"],
  "material_name": "대상 자재명 (있으면)",
  "validation_note": "유효/무효 판정 이유 간략히"
}
```
JSON만 출력하세요."""

        elif doc_type == DocType.DELIVERY_CONFIRMATION:
            return base + """이 문서는 **납품확인서**입니다. (규칙 30)

출력 형식:
```json
{
  "exists": true,
  "has_delivery_confirmation": true
}
```
JSON만 출력하세요."""

        elif doc_type == DocType.LAND_USE_PLAN:
            return base + """이 문서는 **토지이용계획확인원**입니다.

다음 정보를 추출하세요:
1. 소재지
2. 면적 (㎡)
3. 해당 지역/지구
   - 재정비촉진지구 여부
   - 정비구역 여부
   - 공공주택지구 여부
   - 택지개발예정지구 여부
4. 발급일

출력 형식:
```json
{
  "exists": true,
  "location": "소재지",
  "land_area": 123.45,
  "is_redevelopment_zone": false,
  "is_maintenance_zone": false,
  "is_public_housing_zone": false,
  "is_housing_development_zone": false,
  "regulations": ["해당 용도지역"],
  "issue_date": "YYYY-MM-DD"
}
```

JSON만 출력하세요."""

        elif doc_type in [DocType.BUILDING_REGISTRY, DocType.LAND_REGISTRY]:
            return base + """이 문서는 **등기부등본**입니다.

다음 정보를 추출하세요:
1. 소재지
2. 소유자
3. 갑구 (소유권)
   - 압류 여부
   - 가압류 여부
   - 경매 여부
4. 을구 (권리)
   - 근저당 여부
   - 채권최고액
   - 근저당권자
   - 신탁 여부
5. 발급일

출력 형식:
```json
{
  "exists": true,
  "type": "건물/토지",
  "location": "소재지",
  "owner": "소유자",
  "has_seizure": false,
  "has_provisional_seizure": false,
  "has_auction": false,
  "has_mortgage": false,
  "mortgage_amount": null,
  "mortgage_holder": null,
  "has_trust": false,
  "issue_date": "YYYY-MM-DD"
}
```

JSON만 출력하세요."""

        else:
            return base + """이 문서의 정보를 추출하세요.

출력 형식:
```json
{
  "exists": true,
  "document_type": "문서유형",
  "main_info": {}
}
```

JSON만 출력하세요."""
    
    def _parse_json(self, text: str):
        """JSON 파싱 (오류 처리 포함). 객체면 Dict, 배열이면 List 반환."""
        text = text.strip()
        if "```" in text:
            match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
            if match:
                text = match.group(1)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # [ ] 배열 추출 (배치 유형 판별 응답용)
            arr = re.search(r'\[[\s\S]*\]', text)
            if arr:
                try:
                    return json.loads(arr.group())
                except Exception:
                    pass
            obj = re.search(r'\{[\s\S]*\}', text)
            if obj:
                try:
                    return json.loads(obj.group())
                except Exception:
                    pass
            return {}
    
    def _merge_documents_by_type(self, documents: List[DocumentInfo]) -> List[DocumentInfo]:
        """동일 문서 유형이 여러 페이지에서 나온 경우 하나로 병합 (exists/필드 누락 방지)"""
        by_type: Dict[DocType, List[DocumentInfo]] = {}
        for doc in documents:
            if doc.doc_type not in by_type:
                by_type[doc.doc_type] = []
            by_type[doc.doc_type].append(doc)
        merged_list = []
        for doc_type, group in by_type.items():
            if len(group) == 1:
                merged_list.append(group[0])
                continue
            all_pages = []
            merged_data = {}
            for d in group:
                all_pages.extend(d.pages)
                raw = d.merged_data
                if isinstance(raw, list) and raw and isinstance(raw[0], dict):
                    raw = raw[0]
                if not isinstance(raw, dict):
                    continue
                for k, v in raw.items():
                    if v is None or v == "" or v == []:
                        continue
                    if k not in merged_data or merged_data[k] in (None, "", []):
                        merged_data[k] = v
            merged_list.append(DocumentInfo(
                doc_type=doc_type,
                pages=sorted(all_pages),
                merged_data=merged_data,
                confidence=max(d.confidence for d in group),
            ))
        return merged_list

    @staticmethod
    def _get_first(data: Dict, *keys: str):
        """여러 키 이름으로 첫 번째 비어 있지 않은 값을 반환. 있는 정보를 누락하지 않도록 함."""
        for k in keys:
            v = data.get(k)
            if v is None:
                continue
            if isinstance(v, str) and (not v.strip() or v.strip().lower() in ("null", "none", "-", "없음")):
                continue
            if isinstance(v, (int, float)) and v == 0 and "area" not in k and "count" not in k:
                continue
            return v
        return None

    @staticmethod
    def _parse_float(val) -> Optional[float]:
        """숫자 또는 문자열을 float으로 변환."""
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            s = re.sub(r"[^\d.-]", "", val.replace(",", ""))
            try:
                return float(s) if s else None
            except ValueError:
                return None
        return None

    def _build_result(
        self,
        documents: List[DocumentInfo],
        announcement_date: str,
        raw_pdf_text: str = ""
    ) -> PublicHousingReviewResult:
        """분석 결과를 PublicHousingReviewResult로 변환
        
        Args:
            documents: 감지된 문서 정보 리스트
            announcement_date: 공고일
            raw_pdf_text: PDF 원본 텍스트 (소유자 정보 폴백 추출용)
        """
        
        result = PublicHousingReviewResult(
            review_date=datetime.now().strftime("%Y-%m-%d"),
            announcement_date=announcement_date,
        )
        
        # 주택매도 신청서를 먼저 적용해 소유자 정보를 채운 뒤, 다른 서류에서 참조할 수 있도록 함
        ordered = sorted(
            documents,
            key=lambda d: (0 if d.doc_type == DocType.HOUSING_SALE_APPLICATION else 1, str(d.doc_type)),
        )
        
        for doc in ordered:
            data = doc.merged_data
            # AI가 JSON을 리스트로 반환한 경우(예: [{ "exists": true, ... }]) dict로 정규화
            if isinstance(data, list):
                data = (data[0] if data and isinstance(data[0], dict) else {}) or {}
            if not isinstance(data, dict):
                data = {}
            
            if doc.doc_type == DocType.HOUSING_SALE_APPLICATION:
                # ★ PDF 원본 텍스트 전달 (AI 추출 실패 시 폴백용)
                self._apply_housing_application(result, data, raw_pdf_text)
            
            elif doc.doc_type == DocType.RENTAL_STATUS:
                self._apply_rental_status(result, data)
            
            elif doc.doc_type == DocType.POWER_OF_ATTORNEY:
                self._apply_power_of_attorney(result, data)
            
            elif doc.doc_type == DocType.CONSENT_FORM:
                self._apply_consent_form(result, data)
            
            elif doc.doc_type == DocType.INTEGRITY_PLEDGE:
                self._apply_integrity_pledge(result, data)
            
            elif doc.doc_type == DocType.LH_EMPLOYEE_CONFIRM:
                self._apply_lh_confirm(result, data)
            
            elif doc.doc_type == DocType.SEAL_CERTIFICATE:
                self._apply_seal_certificate(result, data)
            
            elif doc.doc_type == DocType.BUILDING_LEDGER_TITLE:
                self._apply_building_ledger_title(result, data)
            elif doc.doc_type == DocType.BUILDING_LEDGER_SUMMARY:
                self._apply_building_ledger_summary(result, data)
            
            elif doc.doc_type == DocType.BUILDING_LEDGER_EXCLUSIVE:
                self._apply_building_ledger_exclusive(result, data)
            
            elif doc.doc_type == DocType.BUILDING_LAYOUT:
                self._apply_building_layout(result, data)
            
            elif doc.doc_type == DocType.LAND_LEDGER:
                self._apply_land_ledger(result, data)
            
            elif doc.doc_type == DocType.LAND_USE_PLAN:
                self._apply_land_use_plan(result, data)
            
            elif doc.doc_type == DocType.BUILDING_REGISTRY:
                self._apply_building_registry(result, data)
            
            elif doc.doc_type == DocType.LAND_REGISTRY:
                self._apply_land_registry(result, data)
            
            elif doc.doc_type == DocType.AGENT_ID_CARD:
                # 대리인신분증사본이 있으면 제출·이름 일치된 것으로 간주
                if data.get("exists", True):
                    result.housing_sale_application.agent_info.exists = True
                    result.housing_sale_application.agent_info.id_card_match = True
            
            # 🔥 법인 서류 처리 추가
            elif doc.doc_type == DocType.BUSINESS_REGISTRATION:
                if data.get("exists", True):
                    result.corporate_documents.business_registration.exists = True
                    result.corporate_documents.is_corporation = True
                    print(f"    [법인 서류 감지] 사업자등록증 발견 → is_corporation=True")
            
            elif doc.doc_type == DocType.AS_BUILT_DRAWING:
                self._apply_as_built_drawing(result, data)
            elif doc.doc_type == DocType.TEST_CERTIFICATE:
                self._apply_test_certificate(result, data)
            elif doc.doc_type == DocType.DELIVERY_CONFIRMATION:
                self._apply_delivery_confirmation(result, data)
        
        self._reconcile_result(result, announcement_date)
        return result
    
    def _reconcile_result(self, result: PublicHousingReviewResult, announcement_date: str):
        """서류 간 일치·날짜 검증. 있는 값을 기준으로 일치/유효만 설정하고, 없으면 보완서류로 넘기지 않음."""
        try:
            ann = datetime.strptime(announcement_date, "%Y-%m-%d").date()
        except Exception:
            ann = None
        
        # 대지면적 일치: 2개 이상 있으면 비교. 일치하면 match=True(있는데 불일치라고 하지 않음)
        la_app = self._parse_float(result.housing_sale_application.land_area)
        la_land = self._parse_float(getattr(result.land_ledger, "land_area", None))
        la_plan = self._parse_float(getattr(result.land_use_plan, "land_area", None))
        tol = 0.1  # 단위·반올림 차이 허용
        vals = [v for v in (la_app, la_land, la_plan) if v is not None]
        if len(vals) >= 2 and all(abs(vals[0] - v) <= tol for v in vals):
            result.housing_sale_application.land_area_match = True
            if la_land is not None:
                result.land_ledger.land_area_match = True
            if la_plan is not None:
                result.land_use_plan.land_area_match = True
        
        # 사용승인일 일치: 둘 다 있어야만 비교, 하나라도 없으면 일치로 간주
        app_date = (result.housing_sale_application.approval_date or "").strip()
        title_date = (getattr(result.building_ledger_title, "approval_date", None) or "").strip()
        
        def _norm_date(s: str) -> str:
            """날짜 문자열에서 숫자만 추출 (YYYYMMDD)"""
            if not s:
                return ""
            digits = re.sub(r"\D", "", s)
            # 6자리면 YYMMDD → YYYYMMDD
            if len(digits) == 6:
                digits = "20" + digits
            # 7자리면 잘못된 형식이지만 최대한 처리
            if len(digits) == 7:
                digits = "20" + digits[1:]  # 앞자리 추가
            return digits[:8]
        
        def _parse_to_ymd(s: str) -> tuple:
            """날짜 문자열 → (년, 월, 일) 튜플"""
            if not s:
                return None
            # 직접 파싱 시도
            for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y. %m. %d", "%Y년 %m월 %d일", "%Y년%m월%d일"):
                try:
                    d = datetime.strptime(s.strip()[:24], fmt)
                    return (d.year, d.month, d.day)
                except (ValueError, TypeError):
                    continue
            # 정규식으로 추출
            import re
            m = re.match(r"(\d{4})\s*[년./-]\s*(\d{1,2})\s*[월./-]\s*(\d{1,2})", s)
            if m:
                try:
                    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except:
                    pass
            # 숫자만 추출
            nd = _norm_date(s)
            if len(nd) >= 8:
                try:
                    return (int(nd[:4]), int(nd[4:6]), int(nd[6:8]))
                except:
                    pass
            return None
        
        # 디버그 로그
        print(f"    [사용승인일 비교] 매도신청서: '{app_date}', 표제부: '{title_date}'")
        
        # 비교 로직: 둘 다 있을 때만 실제 비교, 하나라도 없으면 일치로 간주
        if app_date and title_date:
            app_ymd = _parse_to_ymd(app_date)
            title_ymd = _parse_to_ymd(title_date)
            print(f"    [사용승인일 비교] 파싱 결과: {app_ymd} vs {title_ymd}")
            
            if app_ymd and title_ymd:
                if app_ymd == title_ymd:
                    result.housing_sale_application.approval_date_match = True
                    print(f"    [사용승인일 비교] → 완전 일치")
                elif app_ymd[:2] == title_ymd[:2]:
                    # 연월만 같으면 일치로 간주 (일자 오타 허용)
                    result.housing_sale_application.approval_date_match = True
                    print(f"    [사용승인일 비교] → 연월 일치 (일자 차이 허용)")
                else:
                    # 숫자로 비교 (폴백)
                    nd_app, nd_title = _norm_date(app_date), _norm_date(title_date)
                    if nd_app == nd_title:
                        result.housing_sale_application.approval_date_match = True
                        print(f"    [사용승인일 비교] → 숫자 비교 일치: {nd_app}")
                    elif nd_app[:6] == nd_title[:6]:
                        result.housing_sale_application.approval_date_match = True
                        print(f"    [사용승인일 비교] → 연월 숫자 일치")
                    else:
                        # 명시적으로 False 설정 (실제 불일치)
                        result.housing_sale_application.approval_date_match = False
                        print(f"    [사용승인일 비교] → 불일치: {nd_app} != {nd_title}")
            else:
                # 파싱 실패 시 일치로 간주
                result.housing_sale_application.approval_date_match = True
                print(f"    [사용승인일 비교] → 파싱 실패, 일치로 간주")
        else:
            # 둘 중 하나라도 없으면 일치로 간주 (추출 실패)
            result.housing_sale_application.approval_date_match = True
            print(f"    [사용승인일 비교] → 날짜 미추출, 일치로 간주")
        
        # 위임장: 대지면적·작성일
        poa = result.power_of_attorney
        if poa.exists and la_app is not None:
            poa_la = self._parse_float(poa.land_area)
            if poa_la is not None and abs(poa_la - la_app) <= 0.01:
                poa.land_area_match = True
        if poa.exists and poa.written_date and ann:
            try:
                for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
                    try:
                        d = datetime.strptime(poa.written_date.strip()[:10], fmt).date()
                        if d >= ann:
                            poa.is_after_announcement = True
                        break
                    except ValueError:
                        continue
            except Exception:
                pass
        
        # 개인정보동의서 작성일 유효 (문서 있으면 있는 것으로 간주. 날짜 있으면 유효로 간주)
        if result.consent_form.exists:
            for date_attr, valid_attr in [
                ("owner_written_date", "owner_date_valid"),
                ("agent_written_date", "agent_date_valid"),
            ]:
                date_val = getattr(result.consent_form, date_attr, None)
                if date_val and isinstance(date_val, str):
                    try:
                        for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
                            try:
                                d = datetime.strptime(date_val.strip()[:10], fmt).date()
                                setattr(result.consent_form, valid_attr, d >= ann if ann else True)
                                break
                            except ValueError:
                                continue
                        else:
                            setattr(result.consent_form, valid_attr, True)  # 파싱 실패해도 문서 있으면 유효로 간주
                    except Exception:
                        setattr(result.consent_form, valid_attr, True)
                else:
                    setattr(result.consent_form, valid_attr, True)  # 작성일 없어도 문서 있으면 유효로 간주
        # 공사직원확인서 작성일 유효
        if ann and result.lh_employee_confirmation.exists and result.lh_employee_confirmation.written_date:
            try:
                for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
                    try:
                        d = datetime.strptime(result.lh_employee_confirmation.written_date.strip()[:10], fmt).date()
                        result.lh_employee_confirmation.date_valid = d >= ann
                        break
                    except ValueError:
                        continue
            except Exception:
                pass
        
        # 토지대장 발급일 공고일 이후
        if ann and result.land_ledger.exists and getattr(result.land_ledger, "issue_date", None):
            try:
                for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
                    try:
                        d = datetime.strptime(str(result.land_ledger.issue_date).strip()[:10], fmt).date()
                        result.land_ledger.is_after_announcement = d >= ann
                        break
                    except ValueError:
                        continue
            except Exception:
                pass
        
        # 임대현황 vs 전유부: 호·면적 비교 (둘 다 있을 때만 불일치 목록 설정)
        rent_units = getattr(result.rental_status, "units", []) or []
        excl_units = getattr(result.building_ledger_exclusive, "units", []) or []
        if rent_units and excl_units:
            excl_map = {getattr(u, "unit_number", None) or getattr(u, "unit", str(u)): getattr(u, "exclusive_area", None) or getattr(u, "area", None) for u in excl_units}
            mismatched = []
            for ru in rent_units:
                unum = getattr(ru, "unit_number", None) or getattr(ru, "unit", "")
                uarea = self._parse_float(getattr(ru, "exclusive_area", None) or getattr(ru, "area", None))
                if unum not in excl_map or uarea is None:
                    continue
                if self._parse_float(excl_map[unum]) is None:
                    continue
                if abs(uarea - self._parse_float(excl_map[unum])) > 0.01:
                    mismatched.append(str(unum))
            result.rental_status.mismatched_units = mismatched
        else:
            result.rental_status.mismatched_units = []
        
        # 인감증명서: 신청서에 인감이 있으면 제출된 것으로 간주(있는 걸 없다고 하지 않음)
        seal = result.housing_sale_application.seal_verification
        if result.housing_sale_application.exists and seal.seal_exists:
            if not result.owner_identity.seal_certificate.exists:
                result.owner_identity.seal_certificate.exists = True
                result.owner_identity.seal_certificate.status = DocumentStatus.VALID
            if not seal.certificate_exists:
                seal.certificate_exists = True
        # 소유자 신분증: 인감증명서 있거나 소유자 1명이면 제출된 것으로 간주(있는 것 처리)
        owner_count = getattr(result.owner_identity, "owner_count", 1)
        if result.owner_identity.seal_certificate.exists or owner_count <= 1:
            result.owner_identity.all_ids_submitted = True
        # 규칙 30: 외벽 마감재가 석재일 경우 시험성적서 없이 납품확인서만 필요
        if result.as_built_drawing.exists and (result.as_built_drawing.exterior_finish_material or "").strip():
            if "석재" in (result.as_built_drawing.exterior_finish_material or ""):
                result.test_certificate_delivery.stone_exterior_exception = True
    
    def _apply_housing_application(self, result: PublicHousingReviewResult, data: Dict, raw_text: str = ""):
        """주택매도신청서 적용. 여러 키 이름·중첩 객체를 허용해 있는 정보를 누락 없이 반영.
        
        Args:
            result: 결과 객체
            data: AI가 추출한 데이터
            raw_text: PDF 원본 텍스트 (AI 실패 시 폴백용)
        """
        result.housing_sale_application.exists = data.get("exists", True)
        result.housing_sale_application.status = DocumentStatus.VALID
        
        # ★★★ 1단계: AI가 직접 추출한 is_corporation 값 먼저 적용 ★★★
        is_corp_from_ai = data.get("is_corporation")
        if is_corp_from_ai is True:
            result.corporate_documents.is_corporation = True
            print(f"    [법인 감지 1단계] AI가 is_corporation=true 반환 → 법인으로 설정")
        
        # 소유자 정보: 최상위 키 + owner_info 중첩 객체 + 다양한 한글/영문 키 모두 반영
        owner = result.housing_sale_application.owner_info
        
        # 이름 추출 (다양한 키 이름 지원)
        name = self._get_first(data, 
            "owner_name", "name", "성명", "소유자", "소유주", 
            "applicant_name", "신청인", "매도인", "성명(한글)", "상호"
        )
        if not name and isinstance(data.get("owner_info"), dict):
            name = self._get_first(data["owner_info"], 
                "name", "owner_name", "성명", "소유자", "소유주", "상호"
            )
        if not name and isinstance(data.get("applicant"), dict):
            name = self._get_first(data["applicant"], "name", "성명", "상호")
        
        # ★★★ AI가 소유자 이름을 추출하지 못한 경우 PDF 텍스트에서 직접 추출 (폴백) ★★★
        if not name and raw_text:
            print(f"    [소유자 추출 폴백] AI가 owner_name 미반환 → PDF 텍스트에서 직접 추출 시도...")
            name = self._extract_owner_name_from_text(raw_text)
            if name:
                print(f"    [소유자 추출 폴백] 텍스트에서 소유자 이름 추출 성공: '{name}'")
        
        # ★★★ 여전히 없으면 법인명 추출 시도 ★★★
        if not name and raw_text:
            corp_name = self._extract_corporation_name_from_text(raw_text)
            if corp_name:
                name = corp_name
                result.corporate_documents.is_corporation = True
                print(f"    [소유자 추출 폴백] 텍스트에서 법인명 추출 성공: '{name}'")
        
        if name and str(name).strip():
            owner.name = str(name).strip()
            
            # ★★★ 2단계: 소유자 이름에서 법인 여부 자동 감지 (강화된 로직) ★★★
            if self._detect_corporation_from_name(owner.name):
                result.corporate_documents.is_corporation = True
                print(f"    [법인 감지 2단계] 소유자 이름에서 법인 감지: '{owner.name}' → is_corporation=True")
        
        # 생년월일 추출
        birth = self._get_first(data, 
            "owner_birth", "birth_date", "생년월일", "birth", 
            "resident_number", "주민번호", "주민등록번호"
        )
        if not birth and isinstance(data.get("owner_info"), dict):
            birth = self._get_first(data["owner_info"], 
                "birth_date", "owner_birth", "생년월일", "birth"
            )
        if birth:
            # 주민번호 형식이면 앞 6자리만 추출
            birth_str = str(birth).strip()
            if "-" in birth_str and len(birth_str) >= 6:
                birth_str = birth_str.split("-")[0][:6]
            if birth_str and birth_str.lower() not in ("null", "none", "-"):
                owner.birth_date = birth_str
        
        # 주소 추출
        addr = self._get_first(data, 
            "owner_address", "address", "주소", "현거주지", 
            "home_address", "거주지", "현주소", "주소지"
        )
        if not addr and isinstance(data.get("owner_info"), dict):
            addr = self._get_first(data["owner_info"], 
                "address", "owner_address", "주소", "현거주지"
            )
        if addr and str(addr).strip():
            owner.address = str(addr).strip()
        
        # 전화번호 추출
        phone = self._get_first(data, 
            "owner_phone", "phone", "휴대전화", "연락처", "전화번호", 
            "휴대폰", "mobile", "contact", "핸드폰", "휴대전화번호"
        )
        if not phone and isinstance(data.get("owner_info"), dict):
            phone = self._get_first(data["owner_info"], 
                "phone", "owner_phone", "휴대전화", "연락처", "전화번호"
            )
        if phone:
            phone_str = str(phone).strip()
            # 전화번호 정규화 (010-XXXX-XXXX 형식으로)
            phone_digits = re.sub(r"[^\d]", "", phone_str)
            if len(phone_digits) >= 10 and phone_digits.startswith("010"):
                owner.phone = phone_str
            elif phone_str and phone_str.lower() not in ("null", "none", "-"):
                owner.phone = phone_str
        
        # 이메일 추출
        email = self._get_first(data, 
            "owner_email", "email", "이메일", "이메일주소", 
            "email_address", "e-mail", "mail"
        )
        if not email and isinstance(data.get("owner_info"), dict):
            email = self._get_first(data["owner_info"], 
                "email", "owner_email", "이메일", "이메일주소"
            )
        if email:
            email_str = str(email).strip()
            # 이메일 형식 검증
            if "@" in email_str:
                owner.email = email_str
        
        # 소유자 정보 완비 판정 (3개 이상이면 완비로 간주)
        filled_count = sum([
            bool(owner.name),
            bool(owner.birth_date),
            bool(owner.address),
            bool(owner.phone),
            bool(owner.email),
        ])
        owner.is_complete = filled_count >= 3 or (
            result.housing_sale_application.exists and filled_count >= 1
        )
        
        # 매도주택 정보
        prop_addr = self._get_first(data, 
            "property_address", "소재지", "주소", "물건소재지", "매도주택소재지"
        )
        if prop_addr and not result.property_address:
            result.property_address = str(prop_addr).strip()
        
        land_area = self._parse_float(self._get_first(data, 
            "land_area", "대지면적", "면적", "토지면적"
        ))
        if land_area is not None:
            result.housing_sale_application.land_area = land_area
        
        app_date = self._get_first(data, 
            "approval_date", "사용승인일", "승인일", "건물사용승인일", "준공일"
        )
        if app_date:
            result.housing_sale_application.approval_date = str(app_date).strip()
        
        # 인감·작성일 (문서 있으면 인감 있음으로 간주, AI가 명시적으로 false만 반환했을 때만 미날인)
        if result.housing_sale_application.exists:
            has_seal = data.get("has_seal", data.get("seal", None))
            if has_seal is False:
                result.housing_sale_application.seal_verification.seal_exists = False
            else:
                result.housing_sale_application.seal_verification.seal_exists = True
        
        written = self._get_first(data, 
            "written_date", "작성일", "issue_date", "작성일자", "신청일"
        )
        if written:
            result.housing_sale_application.written_date = str(written).strip()
        
        # 대리인 신분증: 문서 있으면 있는 것으로 간주. AI가 명시적으로 false만 반환했을 때만 false
        if data.get("exists", True):
            agent_match = data.get("agent_id_card_match", data.get("agent_id_card_submitted", None))
            if agent_match is False:
                result.housing_sale_application.agent_info.id_card_match = False
            else:
                result.housing_sale_application.agent_info.id_card_match = True
        
        # 소유자가 개인이 아닐 때(법인·건설 등): 인식된 명칭을 저장하고 유형 표시
        if owner.name and isinstance(owner.name, str):
            name_trimmed = owner.name.strip()
            if name_trimmed:
                non_individual_keywords = ("법인", "건설", "주식회사", "(주)", "주)", "㈜", "사단법인", "재단법인", "농협", "조합", "코퍼레이션", "corp", "inc")
                if any(kw in name_trimmed for kw in non_individual_keywords):
                    result.applicant_type = ApplicantType.CORPORATION
                    result.applicant_type_display = name_trimmed
    
    def _apply_rental_status(self, result: PublicHousingReviewResult, data: Dict):
        """임대현황 적용"""
        result.rental_status.exists = data.get("exists", True)
        result.rental_status.status = DocumentStatus.VALID
        
        units = data.get("units", [])
        if units:
            from core.data_models import UnitInfo
            result.rental_status.units = []
            for u in units:
                unit_num = str(u.get("unit") or u.get("unit_number") or u.get("호") or "")
                area = self._parse_float(u.get("area") or u.get("exclusive_area") or u.get("전용면적"))
                result.rental_status.units.append(UnitInfo(unit_number=unit_num, exclusive_area=area))
    
    def _apply_power_of_attorney(self, result: PublicHousingReviewResult, data: Dict):
        """위임장 적용. 있는 정보를 그대로 반영."""
        result.power_of_attorney.exists = data.get("exists", True)
        result.power_of_attorney.status = DocumentStatus.VALID
        d_name = self._get_first(data, "delegator_name", "위임인", "위임자")
        d_seal = data.get("delegator_seal", data.get("delegator_seal_valid", False))
        e_name = self._get_first(data, "delegatee_name", "수임인", "수임자")
        e_seal = data.get("delegatee_seal", data.get("delegatee_seal_valid", False))
        if d_name:
            result.power_of_attorney.delegator.personal_info_complete = True
        result.power_of_attorney.delegator.seal_valid = bool(d_seal)
        if e_name:
            result.power_of_attorney.delegatee.personal_info_complete = True
        result.power_of_attorney.delegatee.seal_valid = bool(e_seal)
        loc = self._get_first(data, "property_address", "location", "소재지")
        if loc:
            result.power_of_attorney.location = str(loc).strip()
        la = self._parse_float(self._get_first(data, "land_area", "대지면적"))
        if la is not None:
            result.power_of_attorney.land_area = la
        wd = self._get_first(data, "written_date", "작성일", "issue_date")
        if wd:
            result.power_of_attorney.written_date = str(wd).strip()
        result.housing_sale_application.agent_info.exists = True
        if e_name:
            result.housing_sale_application.agent_info.name = str(e_name).strip()
            result.housing_sale_application.agent_info.agent_type = AgentType.INDIVIDUAL
        # 위임장 있으면 대리인 신분증 제출·이름 일치된 것으로 간주(있는 것 처리)
        result.housing_sale_application.agent_info.id_card_match = data.get("agent_id_card_match", True) is not False
    
    def _apply_consent_form(self, result: PublicHousingReviewResult, data: Dict):
        """개인정보동의서 적용. 문서 있으면 소유자/대리인 작성·인감·작성일 있는 것으로 간주(기본 true)."""
        result.consent_form.exists = data.get("exists", True)
        result.consent_form.status = DocumentStatus.VALID
        exists = result.consent_form.exists
        result.consent_form.owner_signed = data.get("owner_signed", data.get("owner_seal", True)) is True if exists else False
        result.consent_form.owner_seal_valid = data.get("owner_seal_valid", data.get("owner_seal", True)) is True if exists else False
        result.consent_form.agent_signed = data.get("agent_signed", data.get("agent_seal", True)) is True if exists else False
        result.consent_form.agent_seal_valid = data.get("agent_seal_valid", data.get("agent_seal", True)) is True if exists else False
        ow = self._get_first(data, "owner_written_date", "owner_date", "작성일")
        if ow:
            result.consent_form.owner_written_date = str(ow).strip()
        aw = self._get_first(data, "agent_written_date", "agent_date")
        if aw:
            result.consent_form.agent_written_date = str(aw).strip()
    
    def _apply_integrity_pledge(self, result: PublicHousingReviewResult, data: Dict):
        """청렴서약서 적용. 문서 있으면 소유자/대리인 작성·인감 있는 것으로 간주(기본 true)."""
        result.integrity_pledge.exists = data.get("exists", True)
        result.integrity_pledge.status = DocumentStatus.VALID
        exists = result.integrity_pledge.exists
        result.integrity_pledge.owner_submitted = data.get("owner_submitted", data.get("owner_signed", True)) is True if exists else False
        result.integrity_pledge.owner_seal_valid = data.get("owner_seal_valid", data.get("has_seal", True)) is True if exists else False
        result.integrity_pledge.owner_id_number_valid = data.get("owner_id_number_valid", data.get("id_number_ok", True)) is not False
        result.integrity_pledge.agent_submitted = data.get("agent_submitted", data.get("agent_signed", True)) is True if exists else False
        result.integrity_pledge.agent_seal_valid = data.get("agent_seal_valid", data.get("agent_seal", True)) is True if exists else False
        result.integrity_pledge.realtor_submitted = data.get("realtor_submitted", data.get("realtor_signed", True)) is True if exists else False
        result.integrity_pledge.realtor_seal_valid = data.get("realtor_seal_valid", data.get("realtor_seal", True)) is True if exists else False
    
    def _apply_lh_confirm(self, result: PublicHousingReviewResult, data: Dict):
        """공사직원여부 확인서 적용. 문서가 있으면 기본적으로 유효하게 처리 (있는 것을 없다고 하지 않음)."""
        result.lh_employee_confirmation.exists = data.get("exists", True)
        result.lh_employee_confirmation.status = DocumentStatus.VALID
        
        # 소유자 이름 추출
        lh_name = self._get_first(data, "owner_name", "name", "소유자")
        app_name = result.housing_sale_application.owner_info.name
        
        # 이름 비교 로직 개선
        if lh_name and app_name:
            # 둘 다 있으면 비교
            a, b = str(lh_name).strip(), str(app_name).strip()
            # 부분 일치도 허용 (OCR 오류 감안)
            if a == b or a in b or b in a or (len(a) >= 2 and len(b) >= 2 and a[:2] == b[:2]):
                result.lh_employee_confirmation.owner_name_match = True
            else:
                result.lh_employee_confirmation.owner_name_match = False
        elif lh_name or app_name:
            # 한쪽만 있으면 일치로 간주 (추출 실패일 수 있음)
            result.lh_employee_confirmation.owner_name_match = True
        else:
            # 둘 다 없으면 일치로 간주 (문서가 있으므로)
            result.lh_employee_confirmation.owner_name_match = True
        
        # 인감 확인 - 문서가 있으면 기본적으로 인감 있는 것으로 간주
        has_seal_data = data.get("has_seal", data.get("seal_valid", None))
        if has_seal_data is False:
            # 명시적으로 False인 경우만 인감 없음
            result.lh_employee_confirmation.seal_valid = False
        else:
            # True이거나 None(미확인)이면 인감 있는 것으로 간주
            result.lh_employee_confirmation.seal_valid = True
        
        # 작성일자
        wd = self._get_first(data, "written_date", "작성일", "issue_date")
        if wd:
            result.lh_employee_confirmation.written_date = str(wd).strip()
        
        # 문서가 있으면 date_valid는 나중에 _reconcile_result에서 공고일과 비교하여 설정
        # 여기서는 기본값 True로 설정 (날짜 검증 전)

    def _apply_seal_certificate(self, result: PublicHousingReviewResult, data: Dict):
        """인감증명서 적용 (owner_identity.seal_certificate). 법인/개인 구분."""
        exists = data.get("exists", True)
        
        # 🔥 법인인감증명서 vs 본인발급용 구분
        cert_type = data.get("certificate_type", data.get("type", ""))
        is_corporate_seal = "법인" in str(cert_type)
        
        if is_corporate_seal:
            # 법인인감증명서
            result.corporate_documents.corporate_seal_certificate.exists = exists
            result.corporate_documents.is_corporation = True
            print(f"    [법인 서류 감지] 법인인감증명서 발견 → is_corporation=True")
        else:
            # 본인발급용 인감증명서
            result.owner_identity.seal_certificate.exists = exists
            result.owner_identity.seal_certificate.status = DocumentStatus.VALID
            issue_d = self._get_first(data, "issue_date", "발급일", "작성일")
            if issue_d:
                result.owner_identity.seal_certificate.issue_date = str(issue_d).strip()
                result.owner_identity.seal_certificate_issue_date = str(issue_d).strip()
            # 인감 검증에도 적용
            result.housing_sale_application.seal_verification.certificate_exists = True
    
    def _apply_building_ledger_summary(self, result: PublicHousingReviewResult, data: Dict):
        """건축물대장 총괄표제부 적용. 한 필지 2개 이상 동일 때 받는 서류. 내진설계·사용승인일 등은 표제부에서만 검토하므로 여기서는 설정하지 않음."""
        result.building_ledger_summary.exists = data.get("exists", True)
        result.building_ledger_summary.status = DocumentStatus.VALID
        result.building_ledger_summary.required = True  # 총괄표제부가 제출됐다 = 한 필지에 2개 이상 동이 있다는 의미
        bc = data.get("building_count", data.get("동수", data.get("building_count", 2)))
        if bc is not None:
            try:
                result.building_ledger_summary.building_count = int(bc) if int(bc) >= 1 else 2
            except (TypeError, ValueError):
                result.building_ledger_summary.building_count = 2

    def _apply_building_ledger_title(self, result: PublicHousingReviewResult, data: Dict):
        """건축물대장 표제부 적용. 내진설계·사용승인일 등은 이 표제부 데이터로만 검토함."""
        result.building_ledger_title.exists = data.get("exists", True)
        result.building_ledger_title.status = DocumentStatus.VALID
        
        app_d = self._get_first(data, "approval_date", "사용승인일", "승인일", "use_approval_date")
        if app_d:
            result.building_ledger_title.approval_date = str(app_d).strip()
        
        if "seismic_design" in data:
            result.building_ledger_title.seismic_design = data["seismic_design"]
        
        if data.get("basement_floors") is not None:
            result.building_ledger_title.basement_floors = data["basement_floors"]
            result.building_ledger_title.has_basement = data["basement_floors"] > 0
        else:
            result.building_ledger_title.has_basement = False
            result.building_ledger_title.basement_floors = 0
        # 지하 세대(거주용 호) 여부. 지하층 유무와 별개. 지하 세대가 있을 때만 제외 대상.
        if "has_basement_units" in data:
            result.building_ledger_title.has_basement_units = data["has_basement_units"] is True
        else:
            result.building_ledger_title.has_basement_units = False
        
        if data.get("elevator_count"):
            result.building_ledger_title.elevator_count = data["elevator_count"]
            result.building_ledger_title.has_elevator = data["elevator_count"] > 0
        
        if data.get("parking_indoor"):
            result.building_ledger_title.indoor_parking = data["parking_indoor"]
        if data.get("parking_outdoor"):
            result.building_ledger_title.outdoor_parking = data["parking_outdoor"]
        if data.get("parking_mechanical"):
            result.building_ledger_title.mechanical_parking = data["parking_mechanical"]
        
        if data.get("location") and not result.property_address:
            result.property_address = data["location"]
        if "has_worker_living_facility" in data:
            result.building_ledger_title.has_worker_living_facility = data["has_worker_living_facility"] is True
        elif self._get_first(data, "근생", "근로자생활시설", "worker_living") is not None:
            result.building_ledger_title.has_worker_living_facility = True
    
    def _apply_building_ledger_exclusive(self, result: PublicHousingReviewResult, data: Dict):
        """건축물대장 전유부 적용. 호별 면적 있으면 units에 반영 (reconcile에서 비교용)."""
        result.building_ledger_exclusive.exists = data.get("exists", True)
        result.building_ledger_exclusive.status = DocumentStatus.VALID
        units = data.get("units", [])
        if units:
            from core.data_models import ExclusiveUnit
            result.building_ledger_exclusive.units = []
            for u in units:
                unit_num = str(u.get("unit") or u.get("unit_number") or u.get("호") or "")
                area = self._parse_float(u.get("area") or u.get("exclusive_area") or u.get("전용면적")) or 0.0
                result.building_ledger_exclusive.units.append(ExclusiveUnit(unit_number=unit_num, exclusive_area=area))
    
    def _apply_building_layout(self, result: PublicHousingReviewResult, data: Dict):
        """건축물현황도 적용. 문서가 있으면 배치도·층별·호별·지자체발급은 기본 true(있는 것으로 간주)."""
        result.building_layout_plan.exists = data.get("exists", True)
        result.building_layout_plan.status = DocumentStatus.VALID
        # 문서 있으면 기본값 true. AI가 명시적으로 false만 반환했을 때만 false.
        exists = result.building_layout_plan.exists
        result.building_layout_plan.has_site_plan = data.get("has_site_plan", data.get("site_plan", True)) is True if exists else False
        result.building_layout_plan.has_all_floor_plans = data.get("has_all_floor_plans", data.get("floor_plans", True)) is True if exists else False
        result.building_layout_plan.has_unit_plans = data.get("has_unit_plans", data.get("unit_plans", True)) is True if exists else False
        result.building_layout_plan.is_government_issued = data.get("is_government_issued", data.get("government_issued", True)) is True if exists else False
    
    def _apply_land_ledger(self, result: PublicHousingReviewResult, data: Dict):
        """토지대장 적용. 문서 있으면 필지·대지면적은 있는 것으로 간주(기본 true)."""
        result.land_ledger.exists = data.get("exists", True)
        result.land_ledger.status = DocumentStatus.VALID
        la = self._parse_float(self._get_first(data, "land_area", "면적", "대지면적"))
        if la is not None:
            result.land_ledger.land_area = la
        issue = self._get_first(data, "issue_date", "발급일", "작성일")
        if issue:
            result.land_ledger.issue_date = str(issue).strip()
        lc = self._get_first(data, "land_category", "지목", "지목명")
        if lc:
            result.land_ledger.land_category = str(lc).strip()
        restrictions = data.get("use_restrictions", data.get("행위제한", data.get("regulations", [])))
        if isinstance(restrictions, list) and restrictions:
            result.land_ledger.use_restrictions = [str(r).strip() for r in restrictions]
        elif isinstance(restrictions, str) and restrictions.strip():
            result.land_ledger.use_restrictions = [restrictions.strip()]
        # 문서 있으면 필지 전부 제출된 것으로 간주. AI가 명시적으로 false만 반환했을 때만 false.
        exists = result.land_ledger.exists
        result.land_ledger.all_parcels_submitted = (
            data.get("all_parcels_submitted") if "all_parcels_submitted" in data
            else (data.get("all_parcels") if "all_parcels" in data else True)
        ) if exists else False
        if not isinstance(result.land_ledger.all_parcels_submitted, bool):
            result.land_ledger.all_parcels_submitted = bool(result.land_ledger.all_parcels_submitted)
    
    def _apply_land_use_plan(self, result: PublicHousingReviewResult, data: Dict):
        """토지이용계획확인원 적용. 문서 있으면 필지·대지면적 있는 것으로 간주(기본 true)."""
        result.land_use_plan.exists = data.get("exists", True)
        result.land_use_plan.status = DocumentStatus.VALID
        la_plan = self._parse_float(self._get_first(data, "land_area", "면적", "대지면적"))
        if la_plan is not None:
            result.land_use_plan.land_area = la_plan
        exists = result.land_use_plan.exists
        result.land_use_plan.all_parcels_submitted = (
            data.get("all_parcels_submitted") if "all_parcels_submitted" in data
            else (data.get("all_parcels") if "all_parcels" in data else True)
        ) if exists else False
        if isinstance(result.land_use_plan.all_parcels_submitted, bool) is False:
            result.land_use_plan.all_parcels_submitted = bool(result.land_use_plan.all_parcels_submitted)
        if "is_redevelopment_zone" in data:
            result.land_use_plan.is_redevelopment_zone = data["is_redevelopment_zone"]
        if "is_maintenance_zone" in data:
            result.land_use_plan.is_maintenance_zone = data["is_maintenance_zone"]
        if "is_public_housing_zone" in data:
            result.land_use_plan.is_public_housing_zone = data["is_public_housing_zone"]
        if "is_housing_development_zone" in data:
            result.land_use_plan.is_housing_development_zone = data["is_housing_development_zone"]
    
    def _apply_building_registry(self, result: PublicHousingReviewResult, data: Dict):
        """건물등기부등본 적용. 문서 있으면 호수 전부 있는 것으로 간주(기본 true)."""
        result.building_registry.exists = data.get("exists", True)
        result.building_registry.status = DocumentStatus.VALID
        exists = result.building_registry.exists
        result.building_registry.all_units_submitted = (
            data.get("all_units_submitted") if "all_units_submitted" in data
            else (data.get("all_units") if "all_units" in data else True)
        ) if exists else False
        if not isinstance(result.building_registry.all_units_submitted, bool):
            result.building_registry.all_units_submitted = bool(result.building_registry.all_units_submitted)
        if "has_seizure" in data:
            result.building_registry.has_seizure = data["has_seizure"]
        if "has_mortgage" in data:
            result.building_registry.has_mortgage = data["has_mortgage"]
        if "has_trust" in data:
            result.building_registry.has_trust = data["has_trust"]
        if "is_private_rental_stated" in data:
            result.building_registry.is_private_rental_stated = data["is_private_rental_stated"] is True
        elif self._get_first(data, "민간임대용", "민간임대", "private_rental") is not None:
            result.building_registry.is_private_rental_stated = True
    
    def _apply_land_registry(self, result: PublicHousingReviewResult, data: Dict):
        """토지등기부등본 적용. 문서 있으면 필지 전부 있는 것으로 간주(기본 true)."""
        result.land_registry.exists = data.get("exists", True)
        result.land_registry.status = DocumentStatus.VALID
        exists = result.land_registry.exists
        result.land_registry.all_parcels_submitted = (
            data.get("all_parcels_submitted") if "all_parcels_submitted" in data
            else (data.get("all_parcels") if "all_parcels" in data else True)
        ) if exists else False
        if not isinstance(result.land_registry.all_parcels_submitted, bool):
            result.land_registry.all_parcels_submitted = bool(result.land_registry.all_parcels_submitted)
    
    # 플레이스홀더로 간주해 제외할 값 (실제 도면 자재명이 아님)
    _AS_BUILT_PLACEHOLDERS = ("자재명", "미확인", "추출 필요", "추출필요", "없음", "-", "null", "none", "?")

    def _is_real_material(self, val: Optional[str]) -> bool:
        if not val or not str(val).strip():
            return False
        s = str(val).strip().lower()
        if s in ("null", "none", "-", "없음"):
            return False
        for ph in self._AS_BUILT_PLACEHOLDERS:
            if ph in s and len(s) <= len(ph) + 2:
                return False
        return True

    def _apply_as_built_drawing(self, result: PublicHousingReviewResult, data: Dict):
        """준공도면 적용 (규칙 29). 도면에서 읽은 실제 자재명만 반영."""
        result.as_built_drawing.exists = data.get("exists", True)
        result.as_built_drawing.status = DocumentStatus.VALID
        result.as_built_drawing.materials_extracted = data.get("materials_extracted", False)
        ext_finish = self._get_first(data, "exterior_finish_material", "외벽마감", "외벽마감재료")
        if ext_finish and self._is_real_material(ext_finish):
            result.as_built_drawing.exterior_finish_material = str(ext_finish).strip()
        ext_insul = self._get_first(data, "exterior_insulation_material", "외벽단열", "외벽단열재료")
        if ext_insul and self._is_real_material(ext_insul):
            result.as_built_drawing.exterior_insulation_material = str(ext_insul).strip()
        piloti_f = self._get_first(data, "piloti_finish_material", "필로티마감", "필로티마감재료")
        if piloti_f and self._is_real_material(piloti_f):
            result.as_built_drawing.piloti_finish_material = str(piloti_f).strip()
        piloti_i = self._get_first(data, "piloti_insulation_material", "필로티단열", "필로티단열재료")
        if piloti_i and self._is_real_material(piloti_i):
            result.as_built_drawing.piloti_insulation_material = str(piloti_i).strip()
        if any([
            result.as_built_drawing.exterior_finish_material,
            result.as_built_drawing.exterior_insulation_material,
            result.as_built_drawing.piloti_finish_material,
            result.as_built_drawing.piloti_insulation_material,
        ]):
            result.as_built_drawing.materials_extracted = True
    
    def _apply_test_certificate(self, result: PublicHousingReviewResult, data: Dict):
        """시험성적서 적용 (규칙 30). 
        ★ 핵심: 열방출시험 + 가스유해성 시험 둘 다 있어야 유효
        ★ 열전도율 시험만 있으면 무효
        ★ 텍스트 기반 추가 검증으로 AI 분석 보완
        """
        result.test_certificate_delivery.exists = True
        result.test_certificate_delivery.status = DocumentStatus.VALID
        # ★ 시험성적서 파일이 실제로 제출되었음을 표시
        result.test_certificate_delivery.test_cert_file_exists = True
        
        # ========================================
        # 1단계: AI 분석 결과 수집
        # ========================================
        has_heat = data.get("has_heat_release_test", data.get("열방출", False)) is True
        has_gas = data.get("has_gas_toxicity_test", data.get("가스유해성", False)) is True
        has_thermal = data.get("has_thermal_conductivity_test", data.get("열전도율", False)) is True
        
        # detected_tests 저장
        detected = data.get("detected_tests", [])
        if detected and isinstance(detected, list):
            result.test_certificate_delivery.detected_tests = [str(d) for d in detected]
        
        # ========================================
        # 2단계: detected_tests 텍스트 기반 추가 검증
        # ========================================
        # AI가 놓칠 수 있는 시험 항목을 텍스트 분석으로 보완
        detected_text = " ".join([str(d).lower() for d in detected]) if detected else ""
        
        # 열방출시험 키워드 (대소문자 무시)
        heat_keywords = [
            "열방출", "총열방출량", "열방출률", "열방출율", "열량방출",
            "thr", "total heat release", "heat release rate", "hrr",
            "발열량", "발열율", "열에너지",
            "cone calorimeter", "콘칼로리미터",
            "5660", "iso 5660", "ks f iso 5660"
        ]
        
        # 가스유해성시험 키워드 (대소문자 무시)
        gas_keywords = [
            "가스유해성", "가스유해", "가스독성", "연소가스유해성", "연소가스",
            "gas toxicity", "gas toxic", "toxicity test",
            "유해가스", "유독가스", "연기독성", "연기유해성",
            "2271", "ks f 2271",
            "마우스", "mouse", "동물시험"
        ]
        
        # 열전도율시험 키워드 (제외 대상)
        thermal_keywords = [
            "열전도율", "열전도", "열전도계수", "단열성능", "단열시험",
            "thermal conductivity", "k-value", "k값",
            "8302", "ks l iso 8302", "9016", "ks l 9016"
        ]
        
        # 텍스트 기반 추가 검출 (OR 조건으로 병합)
        for kw in heat_keywords:
            if kw.lower() in detected_text:
                has_heat = True
                break
        
        for kw in gas_keywords:
            if kw.lower() in detected_text:
                has_gas = True
                break
        
        for kw in thermal_keywords:
            if kw.lower() in detected_text:
                has_thermal = True
                break
        
        # ========================================
        # 3단계: 최종 결과 적용
        # ========================================
        result.test_certificate_delivery.has_heat_release_test = has_heat
        result.test_certificate_delivery.has_gas_toxicity_test = has_gas
        result.test_certificate_delivery.has_thermal_conductivity_test = has_thermal
        
        # ========================================
        # 4단계: 검증 결과 로깅
        # ========================================
        if detected:
            print(f"[시험성적서] 감지된 시험 항목: {', '.join(detected)}")
        
        # 유효성 판정 로깅
        if has_heat and has_gas:
            print(f"[시험성적서] ✅ 유효: 열방출시험 + 가스유해성 시험 둘 다 있음")
        elif has_thermal and not has_heat and not has_gas:
            print(f"[시험성적서] ❌ 무효: 열전도율 시험만 있음 (열방출+가스유해성 필요)")
        else:
            missing = []
            if not has_heat:
                missing.append("열방출시험")
            if not has_gas:
                missing.append("가스유해성 시험")
            print(f"[시험성적서] ❌ 무효: {', '.join(missing)} 없음")
        
        # ========================================
        # 5단계: 자재명 및 납품확인서 처리
        # ========================================
        if not result.test_certificate_delivery.has_delivery_confirmation:
            result.test_certificate_delivery.has_delivery_confirmation = data.get("has_delivery_confirmation", False) is True
        mat = data.get("material_name") or data.get("대상자재") or data.get("자재명")
        if mat is not None:
            if isinstance(mat, list):
                for m in mat:
                    if m and str(m).strip() and str(m).strip() not in result.test_certificate_delivery.materials_with_test_cert:
                        result.test_certificate_delivery.materials_with_test_cert.append(str(m).strip())
            elif str(mat).strip() and str(mat).strip() not in result.test_certificate_delivery.materials_with_test_cert:
                result.test_certificate_delivery.materials_with_test_cert.append(str(mat).strip())

    def _apply_delivery_confirmation(self, result: PublicHousingReviewResult, data: Dict):
        """납품확인서 적용 (규칙 30). 자재별 납품확인서 미비 보고용 materials_with_delivery_conf 수집."""
        result.test_certificate_delivery.exists = True
        result.test_certificate_delivery.status = DocumentStatus.VALID
        result.test_certificate_delivery.has_delivery_confirmation = data.get("exists", data.get("has_delivery_confirmation", True)) is True
        # ★ 납품확인서 파일이 실제로 제출되었음을 표시
        result.test_certificate_delivery.delivery_conf_file_exists = True
        mat = data.get("material_name") or data.get("대상자재") or data.get("자재명")
        if mat is not None:
            if isinstance(mat, list):
                for m in mat:
                    if m and str(m).strip() and str(m).strip() not in result.test_certificate_delivery.materials_with_delivery_conf:
                        result.test_certificate_delivery.materials_with_delivery_conf.append(str(m).strip())
            elif str(mat).strip() and str(mat).strip() not in result.test_certificate_delivery.materials_with_delivery_conf:
                result.test_certificate_delivery.materials_with_delivery_conf.append(str(mat).strip())


# =============================================================================
# 기존 인터페이스 호환
# =============================================================================

def analyze_pdf_unified(
    pdf_path: str,
    announcement_date: str = "2025-07-05",
    provider: str = "claude",
    model_name: Optional[str] = None,
    precision_mode: bool = True,  # 고정밀 모드 (기본 활성화)
    fast_mode: bool = False,      # ★ 초고속 모드 (이중검증과 함께 사용)
) -> Tuple[PublicHousingReviewResult, Dict]:
    """
    통합 PDF 분석.
    
    Args:
        pdf_path: PDF 파일 경로
        announcement_date: 공고일 (YYYY-MM-DD)
        provider: 'claude'(기본, 429 완화) | 'gemini'
        model_name: 미지정 시 자동 (claude=Opus 4.5)
        precision_mode: True면 고정밀 분석기 사용 (99.99% 정확도 목표)
        fast_mode: True면 초고속 병렬 분석기 사용 (5분 이내 목표)
    
    Returns:
        (PublicHousingReviewResult, 메타데이터)
    """
    result = None
    meta = {}
    
    # ★ 초고속 모드 우선
    if fast_mode:
        try:
            from core.async_parallel_analyzer import analyze_pdf_fast
            result, meta = analyze_pdf_fast(
                pdf_path, 
                announcement_date, 
                provider=provider or "gemini",
                model_name=model_name,
                dual_check=True
            )
        except ImportError:
            pass  # 폴백
    
    if result is None and precision_mode:
        try:
            from core.precision_pdf_analyzer import PrecisionPDFAnalyzer
            analyzer = PrecisionPDFAnalyzer(provider=provider, model_name=model_name)
            result, meta = analyzer.analyze(pdf_path, announcement_date)
        except ImportError:
            # 고정밀 분석기 없으면 기존 분석기 사용
            pass
    
    if result is None:
        # 기존 분석기 (폴백)
        from core.ultra_unified_pdf_analyzer import UltraUnifiedPDFAnalyzer
        analyzer = UltraUnifiedPDFAnalyzer(provider=provider, model_name=model_name)
        result, meta = analyzer.analyze(pdf_path, announcement_date)
    
    # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
    # [소유자 정보 전용 추출기] - 무조건 호출 (항상!)
    # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
    print("\n" + "=" * 70)
    print("[소유자 전용 추출기] 무조건 호출 시작")
    print("=" * 70)
    
    if result is not None:
        owner = result.housing_sale_application.owner_info
        
        # ★★★ 무조건 전용 추출기 호출 (조건 없이) ★★★
        try:
            from core.owner_info_extractor import OwnerInfoExtractor
            extractor = OwnerInfoExtractor(provider=provider, model_name=model_name)
            owner_result = extractor.extract_from_pdf(pdf_path)
            
            print(f"\n[전용 추출기 결과]")
            print(f"  이름: {owner_result.name}")
            print(f"  생년월일: {owner_result.birth_date}")
            print(f"  주소: {owner_result.address}")
            print(f"  연락처: {owner_result.phone}")
            print(f"  이메일: {owner_result.email}")
            print(f"  법인 여부: {owner_result.is_corporation}")
            
            # 추출된 정보 무조건 적용 (기존 값 덮어쓰기)
            if owner_result.name:
                owner.name = owner_result.name
                print(f"  → 이름 적용: {owner.name}")
            
            if owner_result.birth_date:
                owner.birth_date = owner_result.birth_date
                print(f"  → 생년월일 적용: {owner.birth_date}")
            
            if owner_result.address:
                owner.address = owner_result.address
                print(f"  → 주소 적용: {owner.address}")
            
            if owner_result.phone:
                owner.phone = owner_result.phone
                print(f"  → 연락처 적용: {owner.phone}")
            
            if owner_result.email:
                owner.email = owner_result.email
                print(f"  → 이메일 적용: {owner.email}")
            
            # 법인 여부 업데이트
            if owner_result.is_corporation:
                result.corporate_documents.is_corporation = True
                print(f"  → 법인 감지: is_corporation=True")
            
            # 인감 정보 업데이트
            if owner_result.has_seal:
                result.housing_sale_application.seal_verification.seal_exists = True
                print(f"  → 인감 감지: seal_exists=True")
            
            # 소유자 정보 완비 여부 재계산
            new_filled = sum([
                bool(owner.name and str(owner.name).strip()),
                bool(owner.birth_date and str(owner.birth_date).strip()),
                bool(owner.address and str(owner.address).strip()),
                bool(owner.phone and str(owner.phone).strip()),
                bool(owner.email and str(owner.email).strip()),
            ])
            owner.is_complete = new_filled >= 3
            
            print(f"\n[최종] 소유자 정보 {new_filled}/5 채워짐")
            print("=" * 70 + "\n")
            
        except Exception as e:
            print(f"\n[전용 추출기 오류] {e}")
            import traceback
            traceback.print_exc()
    
    return result, meta