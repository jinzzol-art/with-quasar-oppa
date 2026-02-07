"""
공공임대 기존주택 매입심사 - PDF 프로세서

핵심 기능:
1. PDF → 이미지 변환 (Gemini Vision용)
2. 텍스트 추출 (가능한 경우)
3. 문서 유형 사전 감지
"""
from __future__ import annotations

import io
import base64
from pathlib import Path
from typing import Optional, Union
from dataclasses import dataclass

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


@dataclass
class PDFContent:
    """PDF 추출 결과"""
    file_path: str
    total_pages: int
    text_content: str
    has_text: bool
    images: list
    has_images: bool
    extraction_method: str
    document_type: Optional[str] = None


class ImprovedPDFProcessor:
    """PDF 프로세서"""
    
    IMAGE_DPI = 150
    MAX_PAGES = 30
    
    def __init__(self):
        pass
    
    def extract(self, pdf_path: str) -> PDFContent:
        """PDF 내용 추출"""
        pdf_path = str(pdf_path)
        
        if HAS_PYMUPDF:
            return self._extract_with_pymupdf(pdf_path)
        
        raise RuntimeError("PyMuPDF가 설치되지 않았습니다. pip install pymupdf")
    
    def _extract_with_pymupdf(self, pdf_path: str) -> PDFContent:
        """PyMuPDF로 추출"""
        doc = fitz.open(pdf_path)
        total_pages = min(len(doc), self.MAX_PAGES)
        
        text_parts = []
        images = []
        has_meaningful_text = False
        
        for page_num in range(total_pages):
            page = doc.load_page(page_num)
            
            # 텍스트 추출
            text = page.get_text("text")
            text_parts.append(text)
            
            if len(text.strip()) > 50:
                has_meaningful_text = True
            
            # 이미지로 변환
            mat = fitz.Matrix(self.IMAGE_DPI / 72, self.IMAGE_DPI / 72)
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("png")
            images.append(img_bytes)
        
        doc.close()
        
        full_text = "\n".join(text_parts)
        extraction_method = "mixed" if has_meaningful_text else "image"
        document_type = self._detect_document_type(full_text)
        
        return PDFContent(
            file_path=pdf_path,
            total_pages=total_pages,
            text_content=full_text,
            has_text=has_meaningful_text,
            images=images,
            has_images=len(images) > 0,
            extraction_method=extraction_method,
            document_type=document_type
        )
    
    def _detect_document_type(self, text: str) -> Optional[str]:
        """문서 유형 감지"""
        if "건축물대장" in text or "표제부" in text:
            return "건축물대장"
        if "등기사항전부증명서" in text or "갑구" in text:
            return "등기부등본"
        if "토지대장" in text:
            return "토지대장"
        if "토지이용계획" in text:
            return "토지이용계획확인원"
        return None
    
    def get_images_as_pil(self, content: PDFContent) -> list:
        """이미지를 PIL Image로 변환"""
        if not HAS_PIL:
            raise RuntimeError("PIL이 설치되지 않았습니다")
        
        pil_images = []
        for img_bytes in content.images:
            img = Image.open(io.BytesIO(img_bytes))
            pil_images.append(img)
        
        return pil_images


# =============================================================================
# 프롬프트 (export)
# =============================================================================

BUILDING_LEDGER_PROMPT = """당신은 한국 건축물대장을 분석하는 전문가입니다.

## 건축물대장 표제부 분석

다음 항목을 찾아주세요:
- 대지위치
- 사용승인일
- **내진설계적용여부**: "적용" → true, "해당없음" → false
- **승강기**: 숫자가 있으면 true
- **지하층**: "지하"가 있으면 true, 없으면 false
- 주차장 대수

## 출력 (JSON)

```json
{
  "found": true,
  "location": "대지위치",
  "approval_date": "YYYY-MM-DD",
  "seismic_design": true,
  "has_basement": false,
  "basement_floors": 0,
  "has_elevator": true,
  "elevator_count": 1,
  "outdoor_parking": 3,
  "indoor_parking": 5,
  "mechanical_parking": 0
}
```

JSON만 출력."""


def get_document_specific_prompt(document_type: Optional[str]) -> str:
    """문서 유형별 프롬프트"""
    if document_type == "건축물대장":
        return BUILDING_LEDGER_PROMPT
    return "이 문서를 분석하여 JSON으로 정보를 추출하세요."


# =============================================================================
# 기존 함수 호환
# =============================================================================

def extract_content_from_pdf(pdf_path: str) -> Union[str, list]:
    """
    기존 함수 호환
    
    텍스트가 충분하면 텍스트 반환,
    아니면 이미지 리스트 반환
    """
    processor = ImprovedPDFProcessor()
    content = processor.extract(pdf_path)
    
    # 텍스트가 충분하면 텍스트 반환
    if content.has_text and len(content.text_content.strip()) > 200:
        return content.text_content
    
    # 아니면 이미지 반환
    if content.has_images:
        try:
            return processor.get_images_as_pil(content)
        except:
            return content.images
    
    return ""
