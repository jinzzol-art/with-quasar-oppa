"""
공공임대 기존주택 매입심사 - 고성능 PDF 프로세서 v5.0

핵심 개선사항:
1. 고해상도 이미지 추출 (300 DPI)
2. 이미지 전처리 (대비, 선명도, 노이즈 제거)
3. 페이지별 개별 분석 지원
4. 문서 유형 자동 감지 강화
5. 손글씨/체크박스 인식 최적화
"""
from __future__ import annotations

import io
import base64
from pathlib import Path
from typing import Optional, Union, List, Tuple
from dataclasses import dataclass, field
from enum import Enum

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    import numpy as np
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


class DocumentType(str, Enum):
    """문서 유형"""
    HOUSING_SALE_APPLICATION = "주택매도신청서"
    RENTAL_STATUS = "매도신청주택임대현황"
    POWER_OF_ATTORNEY = "위임장"
    SEAL_CERTIFICATE = "인감증명서"
    ID_CARD = "신분증"
    CONSENT_FORM = "개인정보동의서"
    INTEGRITY_PLEDGE = "청렴서약서"
    LH_EMPLOYEE_CONFIRMATION = "공사직원확인서"
    BUILDING_LEDGER_SUMMARY = "건축물대장총괄표제부"
    BUILDING_LEDGER_TITLE = "건축물대장표제부"
    BUILDING_LEDGER_EXCLUSIVE = "건축물대장전유부"
    BUILDING_LAYOUT = "건축물현황도"
    LAND_LEDGER = "토지대장"
    LAND_USE_PLAN = "토지이용계획확인원"
    LAND_REGISTRY = "토지등기부등본"
    BUILDING_REGISTRY = "건물등기부등본"
    REALTOR_REGISTRATION = "중개사무소등록증"
    BUSINESS_REGISTRATION = "사업자등록증"
    CORPORATE_REGISTRY = "법인등기사항증명서"
    TRUST_CONTRACT = "신탁원부계약서"
    UNKNOWN = "미확인"


@dataclass
class PageContent:
    """개별 페이지 콘텐츠"""
    page_number: int
    image_bytes: bytes
    image_pil: Optional[Image.Image] = None
    text_content: str = ""
    detected_type: DocumentType = DocumentType.UNKNOWN
    confidence: float = 0.0
    
    # 전처리된 이미지
    enhanced_image_bytes: Optional[bytes] = None
    enhanced_image_pil: Optional[Image.Image] = None


@dataclass
class PDFExtractionResult:
    """PDF 추출 결과"""
    file_path: str
    total_pages: int
    pages: List[PageContent] = field(default_factory=list)
    
    # 문서 유형별 페이지 그룹
    document_groups: dict = field(default_factory=dict)
    
    # 메타 정보
    extraction_dpi: int = 300
    preprocessing_applied: bool = False
    

class HighQualityPDFProcessor:
    """
    고성능 PDF 프로세서 v5.0
    
    - 고해상도 추출 (300 DPI)
    - 이미지 전처리 파이프라인
    - 문서 유형 자동 감지
    - 페이지별 개별 처리
    """
    
    # 고해상도 DPI (손글씨/체크박스 인식용)
    HIGH_DPI = 300
    STANDARD_DPI = 200
    
    # 최대 페이지 수
    MAX_PAGES = 100
    
    # 문서 유형 감지용 키워드
    DOCUMENT_KEYWORDS = {
        DocumentType.HOUSING_SALE_APPLICATION: [
            "주택매도신청서", "주택매도 신청서", "매도신청서", "매도 신청서",
            "소유자", "매도주택", "대지면적", "건물사용승인일", "현거주지"
        ],
        DocumentType.RENTAL_STATUS: [
            "임대현황", "매도신청주택 임대현황", "호별현황", "전용면적", "임대보증금"
        ],
        DocumentType.POWER_OF_ATTORNEY: [
            "위임장", "위 임 장", "위임합니다", "수임인", "위임인"
        ],
        DocumentType.SEAL_CERTIFICATE: [
            "인감증명서", "인감증명", "본인발급", "법인인감"
        ],
        DocumentType.ID_CARD: [
            "주민등록증", "운전면허증", "여권", "외국인등록증"
        ],
        DocumentType.CONSENT_FORM: [
            "개인정보", "수집", "이용", "동의서", "제공 동의"
        ],
        DocumentType.INTEGRITY_PLEDGE: [
            "청렴서약서", "청렴 서약서", "서약합니다", "부정청탁"
        ],
        DocumentType.LH_EMPLOYEE_CONFIRMATION: [
            "공사직원", "직원여부", "LH", "한국토지주택공사"
        ],
        DocumentType.BUILDING_LEDGER_SUMMARY: [
            "총괄표제부", "건축물대장", "총괄 표제부"
        ],
        DocumentType.BUILDING_LEDGER_TITLE: [
            "표제부", "건축물대장", "대지위치", "주용도", "주구조", 
            "사용승인일", "내진설계", "승강기"
        ],
        DocumentType.BUILDING_LEDGER_EXCLUSIVE: [
            "전유부", "전유부분", "호수", "전용면적"
        ],
        DocumentType.BUILDING_LAYOUT: [
            "건축물현황도", "현황도", "평면도", "배치도"
        ],
        DocumentType.LAND_LEDGER: [
            "토지대장", "지목", "면적", "소유자"
        ],
        DocumentType.LAND_USE_PLAN: [
            "토지이용계획", "토지이용계획확인원", "도시계획", "용도지역"
        ],
        DocumentType.LAND_REGISTRY: [
            "토지", "등기사항전부증명서", "등기부등본", "갑구", "을구"
        ],
        DocumentType.BUILDING_REGISTRY: [
            "건물", "등기사항전부증명서", "등기부등본", "갑구", "을구"
        ],
        DocumentType.REALTOR_REGISTRATION: [
            "중개사무소", "등록증", "공인중개사"
        ],
        DocumentType.BUSINESS_REGISTRATION: [
            "사업자등록증", "사업자등록번호", "대표자"
        ],
        DocumentType.CORPORATE_REGISTRY: [
            "법인등기", "등기사항전부증명서", "법인", "이사", "감사"
        ],
        DocumentType.TRUST_CONTRACT: [
            "신탁", "신탁계약", "수탁자", "위탁자"
        ],
    }
    
    def __init__(self, use_high_dpi: bool = True, enable_preprocessing: bool = True):
        """
        Args:
            use_high_dpi: 고해상도 모드 사용 (기본 True)
            enable_preprocessing: 이미지 전처리 활성화 (기본 True)
        """
        self.dpi = self.HIGH_DPI if use_high_dpi else self.STANDARD_DPI
        self.enable_preprocessing = enable_preprocessing
        
        if not HAS_PYMUPDF:
            raise RuntimeError("PyMuPDF가 필요합니다. pip install pymupdf")
        if not HAS_PIL:
            raise RuntimeError("Pillow가 필요합니다. pip install Pillow")
    
    def extract(self, pdf_path: str) -> PDFExtractionResult:
        """
        PDF에서 고품질 이미지 추출
        
        Args:
            pdf_path: PDF 파일 경로
            
        Returns:
            PDFExtractionResult: 추출 결과
        """
        pdf_path = str(pdf_path)
        print(f">>> [PDF Processor] 파일 로드: {pdf_path}")
        print(f">>> [PDF Processor] DPI: {self.dpi}, 전처리: {self.enable_preprocessing}")
        
        doc = fitz.open(pdf_path)
        total_pages = min(len(doc), self.MAX_PAGES)
        
        result = PDFExtractionResult(
            file_path=pdf_path,
            total_pages=total_pages,
            extraction_dpi=self.dpi,
            preprocessing_applied=self.enable_preprocessing
        )
        
        for page_num in range(total_pages):
            page = doc.load_page(page_num)
            
            # 1. 텍스트 추출
            text = page.get_text("text")
            
            # 2. 고해상도 이미지 추출
            mat = fitz.Matrix(self.dpi / 72, self.dpi / 72)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_bytes = pix.tobytes("png")
            
            # 3. PIL Image로 변환
            pil_image = Image.open(io.BytesIO(img_bytes))
            
            # 4. 문서 유형 감지
            doc_type, confidence = self._detect_document_type(text, pil_image)
            
            # 5. 이미지 전처리
            enhanced_bytes = None
            enhanced_pil = None
            if self.enable_preprocessing:
                enhanced_pil = self._preprocess_image(pil_image, doc_type)
                buf = io.BytesIO()
                enhanced_pil.save(buf, format="PNG", optimize=True)
                enhanced_bytes = buf.getvalue()
            
            # 6. 페이지 정보 저장
            page_content = PageContent(
                page_number=page_num + 1,
                image_bytes=img_bytes,
                image_pil=pil_image,
                text_content=text,
                detected_type=doc_type,
                confidence=confidence,
                enhanced_image_bytes=enhanced_bytes,
                enhanced_image_pil=enhanced_pil
            )
            result.pages.append(page_content)
            
            print(f">>> [PDF Processor] 페이지 {page_num + 1}/{total_pages}: {doc_type.value} (신뢰도: {confidence:.1%})")
        
        doc.close()
        
        # 문서 유형별 그룹화
        result.document_groups = self._group_by_document_type(result.pages)
        
        return result
    
    def _detect_document_type(self, text: str, image: Image.Image) -> Tuple[DocumentType, float]:
        """
        문서 유형 감지
        
        Args:
            text: 추출된 텍스트
            image: 페이지 이미지
            
        Returns:
            (문서 유형, 신뢰도)
        """
        text_lower = text.lower().replace(" ", "")
        
        scores = {}
        for doc_type, keywords in self.DOCUMENT_KEYWORDS.items():
            score = 0
            matched_keywords = []
            for keyword in keywords:
                keyword_normalized = keyword.replace(" ", "").lower()
                if keyword_normalized in text_lower:
                    score += 1
                    matched_keywords.append(keyword)
            
            if score > 0:
                # 키워드 매칭률
                scores[doc_type] = score / len(keywords)
        
        if not scores:
            return DocumentType.UNKNOWN, 0.0
        
        # 가장 높은 점수의 문서 유형
        best_type = max(scores, key=scores.get)
        best_score = scores[best_type]
        
        # 건축물대장 세부 유형 구분
        if best_type in [DocumentType.BUILDING_LEDGER_TITLE, 
                         DocumentType.BUILDING_LEDGER_SUMMARY,
                         DocumentType.BUILDING_LEDGER_EXCLUSIVE]:
            if "총괄표제부" in text or "총괄 표제부" in text:
                best_type = DocumentType.BUILDING_LEDGER_SUMMARY
            elif "전유부" in text or "전유부분" in text:
                best_type = DocumentType.BUILDING_LEDGER_EXCLUSIVE
            else:
                best_type = DocumentType.BUILDING_LEDGER_TITLE
        
        # 등기부 구분 (토지/건물)
        if best_type in [DocumentType.LAND_REGISTRY, DocumentType.BUILDING_REGISTRY]:
            if "토지" in text[:200]:
                best_type = DocumentType.LAND_REGISTRY
            elif "건물" in text[:200]:
                best_type = DocumentType.BUILDING_REGISTRY
        
        return best_type, best_score
    
    def _preprocess_image(self, image: Image.Image, doc_type: DocumentType) -> Image.Image:
        """
        문서 유형별 이미지 전처리
        
        Args:
            image: 원본 이미지
            doc_type: 문서 유형
            
        Returns:
            전처리된 이미지
        """
        # 1. RGB 변환 (RGBA인 경우)
        if image.mode == 'RGBA':
            background = Image.new('RGB', image.size, (255, 255, 255))
            background.paste(image, mask=image.split()[3])
            image = background
        elif image.mode != 'RGB':
            image = image.convert('RGB')
        
        # 2. 문서 유형별 처리
        if doc_type in [DocumentType.HOUSING_SALE_APPLICATION, 
                        DocumentType.POWER_OF_ATTORNEY,
                        DocumentType.CONSENT_FORM,
                        DocumentType.INTEGRITY_PLEDGE,
                        DocumentType.LH_EMPLOYEE_CONFIRMATION]:
            # 손글씨 양식: 선명도 강화, 대비 증가
            image = self._enhance_handwriting(image)
        
        elif doc_type in [DocumentType.BUILDING_LEDGER_TITLE,
                          DocumentType.BUILDING_LEDGER_SUMMARY,
                          DocumentType.BUILDING_LEDGER_EXCLUSIVE]:
            # 정부 발급 문서: 표 구조 선명화
            image = self._enhance_table_document(image)
        
        elif doc_type in [DocumentType.BUILDING_REGISTRY,
                          DocumentType.LAND_REGISTRY]:
            # 등기부등본: 텍스트 선명화
            image = self._enhance_registry(image)
        
        elif doc_type == DocumentType.SEAL_CERTIFICATE:
            # 인감증명서: 도장 선명화
            image = self._enhance_seal_document(image)
        
        else:
            # 기본 처리
            image = self._enhance_general(image)
        
        return image
    
    def _enhance_handwriting(self, image: Image.Image) -> Image.Image:
        """손글씨 양식 이미지 개선"""
        # 1. 대비 강화
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(1.5)
        
        # 2. 선명도 강화
        enhancer = ImageEnhance.Sharpness(image)
        image = enhancer.enhance(2.0)
        
        # 3. 밝기 조정 (약간 밝게)
        enhancer = ImageEnhance.Brightness(image)
        image = enhancer.enhance(1.1)
        
        # 4. OpenCV로 추가 처리 (가능한 경우)
        if HAS_CV2:
            img_array = np.array(image)
            
            # 적응형 이진화 (체크박스/도장 인식 개선)
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
            
            # CLAHE (Contrast Limited Adaptive Histogram Equalization)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced_gray = clahe.apply(gray)
            
            # 다시 RGB로
            img_array = cv2.cvtColor(enhanced_gray, cv2.COLOR_GRAY2RGB)
            image = Image.fromarray(img_array)
        
        return image
    
    def _enhance_table_document(self, image: Image.Image) -> Image.Image:
        """표 문서 (건축물대장) 이미지 개선"""
        # 1. 대비 강화
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(1.3)
        
        # 2. 선명도 강화
        enhancer = ImageEnhance.Sharpness(image)
        image = enhancer.enhance(1.5)
        
        # 3. 언샤프 마스크 (경계선 선명화)
        image = image.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
        
        return image
    
    def _enhance_registry(self, image: Image.Image) -> Image.Image:
        """등기부등본 이미지 개선"""
        # 1. 대비 강화
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(1.4)
        
        # 2. 선명도 강화
        enhancer = ImageEnhance.Sharpness(image)
        image = enhancer.enhance(1.8)
        
        return image
    
    def _enhance_seal_document(self, image: Image.Image) -> Image.Image:
        """인감증명서 이미지 개선 (도장 부분 강조)"""
        # 1. 대비 강화 (도장의 붉은색 강조)
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(1.5)
        
        # 2. 채도 강화 (붉은색 도장 선명화)
        enhancer = ImageEnhance.Color(image)
        image = enhancer.enhance(1.3)
        
        # 3. 선명도
        enhancer = ImageEnhance.Sharpness(image)
        image = enhancer.enhance(1.5)
        
        return image
    
    def _enhance_general(self, image: Image.Image) -> Image.Image:
        """기본 이미지 개선"""
        # 1. 대비 조정
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(1.2)
        
        # 2. 선명도 조정
        enhancer = ImageEnhance.Sharpness(image)
        image = enhancer.enhance(1.3)
        
        return image
    
    def _group_by_document_type(self, pages: List[PageContent]) -> dict:
        """문서 유형별로 페이지 그룹화"""
        groups = {}
        for page in pages:
            doc_type = page.detected_type
            if doc_type not in groups:
                groups[doc_type] = []
            groups[doc_type].append(page)
        return groups
    
    def get_pages_for_analysis(
        self, 
        result: PDFExtractionResult, 
        use_enhanced: bool = True
    ) -> List[Image.Image]:
        """
        분석용 이미지 리스트 반환
        
        Args:
            result: PDF 추출 결과
            use_enhanced: 전처리된 이미지 사용 여부
            
        Returns:
            PIL Image 리스트
        """
        images = []
        for page in result.pages:
            if use_enhanced and page.enhanced_image_pil:
                images.append(page.enhanced_image_pil)
            elif page.image_pil:
                images.append(page.image_pil)
            else:
                images.append(Image.open(io.BytesIO(page.image_bytes)))
        return images
    
    def get_page_image(
        self, 
        page: PageContent, 
        use_enhanced: bool = True
    ) -> Image.Image:
        """
        단일 페이지 이미지 반환
        
        Args:
            page: 페이지 콘텐츠
            use_enhanced: 전처리된 이미지 사용 여부
            
        Returns:
            PIL Image
        """
        if use_enhanced and page.enhanced_image_pil:
            return page.enhanced_image_pil
        elif page.image_pil:
            return page.image_pil
        else:
            return Image.open(io.BytesIO(page.image_bytes))
    
    def get_images_by_type(
        self, 
        result: PDFExtractionResult, 
        doc_type: DocumentType,
        use_enhanced: bool = True
    ) -> List[Image.Image]:
        """
        특정 문서 유형의 이미지만 반환
        
        Args:
            result: PDF 추출 결과
            doc_type: 문서 유형
            use_enhanced: 전처리된 이미지 사용 여부
            
        Returns:
            PIL Image 리스트
        """
        images = []
        if doc_type in result.document_groups:
            for page in result.document_groups[doc_type]:
                images.append(self.get_page_image(page, use_enhanced))
        return images


# =============================================================================
# 기존 인터페이스 호환 함수
# =============================================================================

def extract_content_from_pdf_v2(pdf_path: str) -> Union[str, List[Image.Image]]:
    """
    기존 함수 호환 - 고품질 PDF 추출
    
    Returns:
        텍스트가 충분하면 텍스트, 아니면 PIL Image 리스트
    """
    processor = HighQualityPDFProcessor(use_high_dpi=True, enable_preprocessing=True)
    result = processor.extract(pdf_path)
    
    # 모든 페이지 텍스트 병합
    all_text = "\n".join([p.text_content for p in result.pages])
    
    # 텍스트가 충분하면 텍스트 반환
    if len(all_text.strip()) > 500:
        return all_text
    
    # 아니면 이미지 반환
    return processor.get_pages_for_analysis(result, use_enhanced=True)


def extract_with_metadata_v2(pdf_path: str) -> PDFExtractionResult:
    """메타데이터 포함 추출"""
    processor = HighQualityPDFProcessor(use_high_dpi=True, enable_preprocessing=True)
    return processor.extract(pdf_path)
