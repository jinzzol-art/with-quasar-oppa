"""
소유자 정보 전용 추출기 v1.0

★★★ 핵심 설계 원칙 ★★★
1. 텍스트 추출 무시 - 무조건 이미지 기반 분석
2. 고해상도 이미지 (300 DPI)
3. 다중 패스 분석 - 소유자 정보 전용 집중 추출
4. 개별 필드 추출 - 한 번에 모든 정보를 추출하려 하지 않음
5. 인감 유사도 분석 - 시각적 비교

이 모듈은 기존 분석기와 독립적으로 동작하며,
소유자 정보 추출 실패 시 이 모듈을 호출하여 재추출합니다.
"""
from __future__ import annotations

import io
import json
import re
import time
import random
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
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

from dotenv import load_dotenv
from core.vision_client import create_vision_client


@dataclass
class OwnerInfoResult:
    """소유자 정보 추출 결과"""
    name: Optional[str] = None
    birth_date: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    is_corporation: bool = False
    
    # 인감 관련
    has_seal: bool = False
    seal_image: Optional[Image.Image] = None
    seal_similarity: Optional[float] = None
    
    # 신뢰도
    confidence: float = 0.0
    extraction_method: str = "image"
    raw_response: Optional[str] = None


class OwnerInfoExtractor:
    """
    소유자 정보 전용 추출기
    
    ★★★ 설계 원칙 ★★★
    1. 무조건 이미지 기반 분석 (텍스트 추출 무시)
    2. 300 DPI 고해상도 이미지
    3. 다중 패스 분석:
       - 1차: 소유자 이름 + 법인 여부
       - 2차: 생년월일 + 주소
       - 3차: 연락처 + 이메일
       - 4차: 인감 분석
    4. 각 패스에서 실패 시 재시도 (최대 3회)
    """
    
    # ★ v2.0 고속 설정 (300→180 DPI, 모든 페이지→최대 5페이지)
    HIGH_DPI = 180  # 300 → 180 (여전히 충분한 해상도, 3배 빠름)
    MAX_IMAGE_PX = 1500  # 2000 → 1500
    MAX_EXTRACT_PAGES = 5  # 추출할 최대 페이지 수
    
    # API 설정
    MAX_RETRIES = 3
    RETRY_DELAY = 2.0
    
    def __init__(self, provider: str = "claude", model_name: Optional[str] = None):
        load_dotenv()
        self.provider = (provider or "claude").strip().lower()
        self._vision_client = create_vision_client(self.provider, model_name)
        self.model_name = getattr(self._vision_client, "model_name", model_name or "claude-opus-4-5")
        
        print(f"[OwnerInfoExtractor] 초기화: {self.provider} ({self.model_name})")
    
    def extract_from_pdf(
        self, 
        pdf_path: str, 
        page_numbers: Optional[List[int]] = None
    ) -> OwnerInfoResult:
        """
        PDF에서 소유자 정보 추출
        
        Args:
            pdf_path: PDF 파일 경로
            page_numbers: 분석할 페이지 번호 (1-indexed), None이면 처음 3페이지
        
        Returns:
            OwnerInfoResult
        """
        print(f"\n{'='*70}")
        print(f"[OwnerInfoExtractor] 소유자 정보 전용 추출 시작")
        print(f"파일: {pdf_path}")
        print(f"{'='*70}\n")
        
        # 1단계: 고해상도 이미지 추출
        print(">>> [1단계] 고해상도 이미지 추출 (300 DPI)...")
        images = self._extract_high_quality_images(pdf_path, page_numbers)
        print(f"    총 {len(images)}페이지 추출 완료\n")
        
        if not images:
            print("    [오류] 이미지 추출 실패")
            return OwnerInfoResult()
        
        # 2단계: 통합 1회 API 호출로 전체 소유자 정보 추출
        result = OwnerInfoResult()

        print(">>> [2단계] 통합 소유자 정보 추출 (1회 API 호출)...")
        unified = self._extract_all_owner_info_unified(images)
        if unified:
            result.name = unified.get("name")
            result.is_corporation = unified.get("is_corporation", False)
            result.birth_date = unified.get("birth_date")
            result.address = unified.get("address")
            result.phone = unified.get("phone")
            result.email = unified.get("email")
            result.has_seal = unified.get("has_seal", False)
            print(f"    이름: {result.name}")
            print(f"    법인 여부: {result.is_corporation}")
            print(f"    생년월일: {result.birth_date}")
            print(f"    주소: {result.address}")
            print(f"    연락처: {result.phone}")
            print(f"    이메일: {result.email}")
            print(f"    인감 존재: {result.has_seal}")

        # 통합 추출 결과가 불완전하면 (3개 미만 필드) 개별 추출 폴백
        filled_fields = sum(bool(v) for v in [result.name, result.birth_date, result.address, result.phone, result.email])
        if filled_fields < 3:
            print(f"\n>>> [폴백] 통합 추출 불완전 ({filled_fields}/5개 필드) → 개별 추출 시작...")

            if not result.name:
                print("    [폴백 2-1] 소유자 이름 추출...")
                name_result = self._extract_name_and_corporation(images)
                if name_result:
                    result.name = name_result.get("name")
                    result.is_corporation = name_result.get("is_corporation", False)

            if not result.birth_date or not result.address:
                print("    [폴백 2-2] 생년월일/주소 추출...")
                birth_addr_result = self._extract_birth_and_address(images)
                if birth_addr_result:
                    result.birth_date = result.birth_date or birth_addr_result.get("birth_date")
                    result.address = result.address or birth_addr_result.get("address")

            if not result.phone or not result.email:
                print("    [폴백 2-3] 연락처/이메일 추출...")
                contact_result = self._extract_contact_info(images)
                if contact_result:
                    result.phone = result.phone or contact_result.get("phone")
                    result.email = result.email or contact_result.get("email")
        
        # 신뢰도 계산
        filled_count = sum([
            bool(result.name),
            bool(result.birth_date),
            bool(result.address),
            bool(result.phone),
            bool(result.email),
        ])
        result.confidence = filled_count / 5.0
        
        print(f"\n{'='*70}")
        print(f"[추출 완료] 신뢰도: {result.confidence:.0%}")
        print(f"  이름: {result.name}")
        print(f"  생년월일: {result.birth_date}")
        print(f"  주소: {result.address}")
        print(f"  연락처: {result.phone}")
        print(f"  이메일: {result.email}")
        print(f"  법인 여부: {result.is_corporation}")
        print(f"  인감 존재: {result.has_seal}")
        print(f"{'='*70}\n")
        
        return result
    
    def _extract_high_quality_images(
        self, 
        pdf_path: str, 
        page_numbers: Optional[List[int]] = None
    ) -> List[Image.Image]:
        """★ v2.0 고속 이미지 추출 (180 DPI, 최대 5페이지)"""
        if not HAS_PYMUPDF:
            raise RuntimeError("PyMuPDF 필요")
        
        doc = fitz.open(pdf_path)
        images = []
        
        # ★ v2.0: 최대 페이지 수 제한 (전체 문서 스캔 방지)
        if page_numbers:
            pages_to_extract = [p - 1 for p in page_numbers if 0 < p <= len(doc)]
        else:
            # 첫 MAX_EXTRACT_PAGES 페이지만 (주택매도신청서는 대체로 앞쪽)
            max_pages = min(len(doc), self.MAX_EXTRACT_PAGES)
            pages_to_extract = list(range(max_pages))
            print(f"    [v2.0 고속] 최대 {max_pages}페이지 분석 (전체 {len(doc)}페이지)")
        
        for page_num in pages_to_extract:
            page = doc.load_page(page_num)
            
            mat = fitz.Matrix(self.HIGH_DPI / 72, self.HIGH_DPI / 72)
            
            try:
                pix = page.get_pixmap(matrix=mat, alpha=False)
                
                # ★ v2.0: samples 직접 변환 (PNG 인코딩 건너뜀)
                try:
                    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                except Exception:
                    img_bytes = pix.tobytes("png")
                    image = Image.open(io.BytesIO(img_bytes))
                    if image.mode != 'RGB':
                        image = image.convert('RGB')
                
                # ★ v2.0: 경량 전처리 (OpenCV 노이즈 제거 생략)
                image = self._advanced_preprocess(image)
                
                images.append(image)
                print(f"    페이지 {page_num + 1}: {image.size[0]}x{image.size[1]} 픽셀")
                
            except Exception as e:
                print(f"    페이지 {page_num + 1} 추출 오류: {e}")
        
        doc.close()
        return images
    
    def _advanced_preprocess(self, image: Image.Image) -> Image.Image:
        """★ v2.0 경량 전처리 — PIL only (OpenCV 노이즈 제거 생략, 5-10배 빠름)
        
        fastNlMeansDenoisingColored는 CPU 집약적 (페이지당 1-3초 소요)
        → Vision API는 약간의 노이즈에도 잘 인식하므로 생략
        """
        # 대비 + 선명도 (PIL 경량 처리)
        image = ImageEnhance.Contrast(image).enhance(1.6)
        image = ImageEnhance.Sharpness(image).enhance(2.0)
        
        # 리사이즈 (API 전송용)
        w, h = image.size
        if max(w, h) > self.MAX_IMAGE_PX:
            scale = self.MAX_IMAGE_PX / max(w, h)
            new_size = (int(w * scale), int(h * scale))
            try:
                resample = Image.Resampling.LANCZOS
            except AttributeError:
                resample = Image.LANCZOS
            image = image.resize(new_size, resample)
        
        return image
    
    def _call_vision_api(self, prompt: str, images: List[Image.Image]) -> str:
        """Vision API 호출 (재시도 포함) — v2.0: 고정 딜레이 제거"""
        for attempt in range(self.MAX_RETRIES):
            try:
                result = self._vision_client.generate_json(prompt, images)
                return result
                
            except Exception as e:
                err_msg = str(e).lower()
                if "429" in err_msg or "rate" in err_msg:
                    wait = self.RETRY_DELAY * (2 ** attempt)
                    print(f"    [Rate limit] {wait:.1f}초 대기 후 재시도...")
                    time.sleep(wait)
                else:
                    if attempt == self.MAX_RETRIES - 1:
                        print(f"    [오류] API 호출 실패: {e}")
                        raise
        return ""
    
    def _parse_json(self, text: str) -> Dict:
        """JSON 파싱 - 배열 응답도 처리"""
        if not text:
            return {}
        
        text = text.strip()
        
        # 마크다운 코드블록 제거
        text = re.sub(r'```json\s*', '', text)
        text = re.sub(r'```\s*', '', text)
        text = text.strip()
        
        # 1. 배열 형식 처리 (첫 번째 객체 추출)
        arr_match = re.search(r'\[[\s\S]*\]', text)
        if arr_match:
            try:
                arr = json.loads(arr_match.group())
                if arr and isinstance(arr, list) and len(arr) > 0:
                    return arr[0] if isinstance(arr[0], dict) else {}
            except json.JSONDecodeError:
                pass
        
        # 2. 객체 형식 처리
        obj_match = re.search(r'\{[\s\S]*\}', text)
        if obj_match:
            try:
                return json.loads(obj_match.group())
            except json.JSONDecodeError:
                pass
        
        return {}
    
    def _extract_all_owner_info_unified(self, images: List[Image.Image]) -> Optional[Dict]:
        """통합 1회 API 호출로 소유자 전체 정보 + 인감 여부 추출"""

        prompt = """# 주택매도신청서 - 소유주 전체 정보 통합 추출

이 이미지는 한국의 "[양식1] 주택매도신청서" 문서입니다.

## ★★★ 중요: 문서 하단의 "* 소유주" 섹션을 찾으세요! ★★★

문서 아래쪽(하단)에 "* 소유주"라고 표시된 섹션이 있습니다.
그 섹션의 구조:

* 소유주    성    명 : [회사명 또는 개인명]
           생 년 월 일 : [법인등록번호 또는 생년월일]
           현거주지 주소 : [한국 주소]
           휴대 전화번호 : [010-XXXX-XXXX]
           E-Mail 주소 : [이메일@도메인]
           (인)

## 추출 작업 - 아래 모든 필드를 한 번에 추출하세요

1. "성 명" 오른쪽 텍스트 → name
2. 법인 여부 판단 → is_corporation
   - 주식회사, (주), ㈜, 유한회사, 건설, 종합건설, 개발, 산업, 법인, 대표이사 포함 시 true
3. "생 년 월 일" 오른쪽 → birth_date
   - 개인: 6자리 숫자 (예: 750315)
   - 법인: 법인등록번호 (예: 131411-0485310)
4. "현거주지 주소" 오른쪽 → address
   - 한국 주소 전체 (예: 경기도 안산시 상록구 예술대학로 9길 1, 201호)
5. "휴대 전화번호" 오른쪽 → phone
   - 한국 휴대폰 (예: 010-6226-6626)
6. "E-Mail 주소" 오른쪽 → email
   - 이메일 주소 (예: ub5310@naver.com)
7. "(인)" 란에 빨간색/주황색 도장이 찍혀 있는지 → has_seal

## 출력 (JSON만)
```json
{
  "name": "성명 (필수)",
  "is_corporation": false,
  "birth_date": "생년월일 또는 법인등록번호",
  "address": "현거주지 주소 전체",
  "phone": "010-XXXX-XXXX",
  "email": "xxx@xxx.com",
  "has_seal": true
}
```"""

        try:
            response = self._call_vision_api(prompt, images[:1])
            data = self._parse_json(response)

            result = {}
            for key in ("name", "birth_date", "address", "phone", "email"):
                val = data.get(key)
                if val and str(val).strip() and str(val).lower() not in ("null", "none", "-"):
                    result[key] = str(val).strip()

            # email 추가 검증
            if "email" in result and "@" not in result["email"]:
                del result["email"]

            result["is_corporation"] = data.get("is_corporation", False)
            result["has_seal"] = data.get("has_seal", False)

            return result if result.get("name") else None

        except Exception as e:
            print(f"    [오류] 통합 추출 실패: {e}")
            return None

    def _extract_name_and_corporation(self, images: List[Image.Image]) -> Optional[Dict]:
        """소유자 이름 + 법인 여부 추출"""
        
        prompt = """# 주택매도신청서 - 소유주 정보 추출

이 이미지는 한국의 "[양식1] 주택매도신청서" 문서입니다.

## ★★★ 중요: 문서 하단의 "* 소유주" 섹션을 찾으세요! ★★★

문서 아래쪽(하단)에 "* 소유주"라고 표시된 섹션이 있습니다.
그 섹션의 구조:

* 소유주    성    명 : [여기에 회사명 또는 개인명]
           생 년 월 일 : [법인등록번호 또는 생년월일]
           현거주지 주소 : [한국 주소]
           휴대 전화번호 : [010-XXXX-XXXX]
           E-Mail 주소 : [이메일@도메인]

## 추출 작업

"성 명" 오른쪽의 텍스트를 읽으세요. 예시:
- "주식회사 유비종합건설 대표이사 천정우"
- "홍길동"
- "(주)대한개발"

## 법인 여부 판단

아래 단어가 포함되면 is_corporation = true:
- 주식회사, (주), ㈜, 유한회사
- 건설, 종합건설, 개발, 산업
- 법인, 대표이사, 사업자등록번호

## 출력 (JSON만)
```json
{
  "name": "성명 칸에서 읽은 텍스트 (필수)",
  "is_corporation": true,
  "found_at": "* 소유주 섹션"
}
```"""

        for attempt in range(3):  # 최대 3회 시도
            try:
                response = self._call_vision_api(prompt, images[:1])  # 첫 페이지만
                data = self._parse_json(response)
                
                name = data.get("name")
                if name and str(name).strip() and str(name).lower() not in ("null", "none", "-"):
                    return {
                        "name": str(name).strip(),
                        "is_corporation": data.get("is_corporation", False)
                    }
                
                print(f"    [시도 {attempt + 1}] 이름 추출 실패, 재시도...")
                
            except Exception as e:
                print(f"    [시도 {attempt + 1}] 오류: {e}")
        
        return None
    
    def _extract_birth_and_address(self, images: List[Image.Image]) -> Optional[Dict]:
        """생년월일 + 주소 추출"""
        
        prompt = """# 주택매도신청서 - 생년월일/주소 추출

이 이미지는 한국의 "[양식1] 주택매도신청서" 문서입니다.

## ★★★ 문서 하단의 "* 소유주" 섹션을 찾으세요! ★★★

* 소유주    성    명 : ...
           생 년 월 일 : [여기! 법인등록번호 또는 생년월일]
           현거주지 주소 : [여기! 한국 주소]
           휴대 전화번호 : ...

## 추출 작업

1. "생 년 월 일" 오른쪽:
   - 개인: 6자리 숫자 (예: 750315)
   - 법인: "(법인등록번호) 131411-0485310" 같은 형식
   - 법인등록번호가 있으면 그것을 추출

2. "현거주지 주소" 오른쪽:
   - 한국 주소 전체를 읽으세요
   - 예: "경기도 안산시 상록구 예술대학로 9길 1, 201호"
   - 예: "서울특별시 강남구 테헤란로 123"

## 출력 (JSON만)
```json
{
  "birth_date": "생년월일 또는 법인등록번호",
  "address": "현거주지 주소 전체"
}
```"""

        try:
            response = self._call_vision_api(prompt, images[:1])
            data = self._parse_json(response)
            
            birth = data.get("birth_date")
            addr = data.get("address")
            
            result = {}
            if birth and str(birth).strip() and str(birth).lower() not in ("null", "none", "-"):
                result["birth_date"] = str(birth).strip()
            if addr and str(addr).strip() and str(addr).lower() not in ("null", "none", "-"):
                result["address"] = str(addr).strip()
            
            return result if result else None
            
        except Exception as e:
            print(f"    [오류] 생년월일/주소 추출 실패: {e}")
            return None
    
    def _extract_contact_info(self, images: List[Image.Image]) -> Optional[Dict]:
        """연락처 + 이메일 추출"""
        
        prompt = """# 주택매도신청서 - 연락처/이메일 추출

이 이미지는 한국의 "[양식1] 주택매도신청서" 문서입니다.

## ★★★ 문서 하단의 "* 소유주" 섹션을 찾으세요! ★★★

* 소유주    성    명 : ...
           생 년 월 일 : ...
           현거주지 주소 : ...
           휴대 전화번호 : [여기! 010-XXXX-XXXX]
           E-Mail 주소 : [여기! xxx@xxx.com]

## 추출 작업

1. "휴대 전화번호" 오른쪽:
   - 한국 휴대폰: 010-XXXX-XXXX
   - 예: "010 6226 6626" 또는 "010-6226-6626"
   - 공백이나 하이픈 포함 가능

2. "E-Mail 주소" 오른쪽:
   - 이메일 주소 (@ 포함)
   - 예: "ub5310@naver.com"
   - 예: "hong123@gmail.com"

## 출력 (JSON만)
```json
{
  "phone": "010-XXXX-XXXX 형식",
  "email": "xxx@xxx.com 형식"
}
```"""

        try:
            response = self._call_vision_api(prompt, images[:1])
            data = self._parse_json(response)
            
            phone = data.get("phone")
            email = data.get("email")
            
            result = {}
            if phone and str(phone).strip() and str(phone).lower() not in ("null", "none", "-"):
                result["phone"] = str(phone).strip()
            if email and str(email).strip() and "@" in str(email):
                result["email"] = str(email).strip()
            
            return result if result else None
            
        except Exception as e:
            print(f"    [오류] 연락처/이메일 추출 실패: {e}")
            return None
    
    def _extract_seal_info(self, images: List[Image.Image]) -> Optional[Dict]:
        """인감 정보 추출"""
        
        prompt = """★★★ 이 문서에서 인감도장을 찾아주세요! ★★★

주택매도신청서 하단의 "(인)" 또는 서명란을 확인하세요.

[확인 항목]
1. 빨간색 또는 주황색 도장이 찍혀 있는가?
2. 도장에 글자가 새겨져 있는가?
3. 도장이 선명하게 보이는가?

[출력 형식 - JSON만]
```json
{
  "has_seal": true,
  "seal_color": "빨간색/주황색/기타",
  "seal_text": "도장에 새겨진 글자 (읽을 수 있으면)",
  "seal_clarity": "선명/흐릿"
}
```"""

        try:
            response = self._call_vision_api(prompt, images[:1])
            data = self._parse_json(response)
            
            return {
                "has_seal": data.get("has_seal", False),
                "seal_text": data.get("seal_text"),
                "seal_clarity": data.get("seal_clarity")
            }
            
        except Exception as e:
            print(f"    [오류] 인감 분석 실패: {e}")
            return None


class SealComparator:
    """
    인감 유사도 비교기
    
    주택매도신청서의 인감과 인감증명서의 인감을 비교하여
    유사도 (0-100%)를 반환합니다.
    """
    
    # 유사도 기준 (45% 이상이면 통과)
    SIMILARITY_THRESHOLD = 45.0
    
    def __init__(self, provider: str = "claude", model_name: Optional[str] = None):
        load_dotenv()
        self.provider = (provider or "claude").strip().lower()
        self._vision_client = create_vision_client(self.provider, model_name)
    
    def compare_seals(
        self, 
        application_image: Image.Image, 
        certificate_image: Image.Image
    ) -> Tuple[float, str]:
        """
        두 인감 이미지 비교
        
        Args:
            application_image: 주택매도신청서 페이지 이미지
            certificate_image: 인감증명서 페이지 이미지
        
        Returns:
            (유사도 %, 비교 설명)
        """
        prompt = """★★★ 두 이미지에서 인감도장을 찾아서 비교해주세요! ★★★

[이미지 1] 주택매도신청서 - 문서 하단의 "(인)" 란에 찍힌 인감도장
[이미지 2] 인감증명서 - 인감증명서에 등록된 인감도장

[비교 항목]
1. 두 인감의 모양이 같은가? (원형, 타원형, 사각형 등)
2. 두 인감에 새겨진 글자가 같은가?
3. 두 인감의 크기가 비슷한가?
4. 전체적인 인상이 동일한 인감인가?

[유사도 판단 기준]
- 90-100%: 동일한 인감으로 확실함
- 70-89%: 매우 유사함, 동일 인감으로 추정
- 45-69%: 유사함, 동일 인감 가능성 있음
- 20-44%: 다소 다름, 확인 필요
- 0-19%: 다른 인감으로 보임

★★★ 기준: 45% 이상이면 통과 ★★★

[출력 형식 - JSON만]
```json
{
  "similarity_percent": 75,
  "seal1_found": true,
  "seal2_found": true,
  "seal1_text": "인감에 새겨진 글자",
  "seal2_text": "인감에 새겨진 글자",
  "seal1_shape": "원형",
  "seal2_shape": "원형",
  "comparison_note": "비교 결과 설명"
}
```"""

        try:
            # 약간의 딜레이
            time.sleep(0.5)
            
            result = self._vision_client.generate_json(prompt, [application_image, certificate_image])
            data = self._parse_json(result)
            
            similarity = float(data.get("similarity_percent", 0))
            note = data.get("comparison_note", "비교 불가")
            
            return similarity, note
            
        except Exception as e:
            print(f"    [오류] 인감 비교 실패: {e}")
            return 0.0, f"비교 실패: {e}"
    
    def _parse_json(self, text: str) -> Dict:
        """JSON 파싱"""
        if not text:
            return {}
        
        text = text.strip()
        text = re.sub(r'```json\s*', '', text)
        text = re.sub(r'```\s*', '', text)
        text = text.strip()
        
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        
        return {}
    
    def compare_seals_from_pdfs(
        self, 
        application_pdf: str, 
        certificate_pdf: str,
        application_page: int = 1,
        certificate_page: int = 1
    ) -> Tuple[float, str]:
        """
        두 PDF에서 인감 비교
        
        Args:
            application_pdf: 주택매도신청서 PDF 경로
            certificate_pdf: 인감증명서 PDF 경로
            application_page: 주택매도신청서 페이지 번호 (1-indexed)
            certificate_page: 인감증명서 페이지 번호 (1-indexed)
        
        Returns:
            (유사도 %, 비교 설명)
        """
        if not HAS_PYMUPDF:
            return 0.0, "PyMuPDF 필요"
        
        try:
            # 주택매도신청서 이미지 추출
            doc1 = fitz.open(application_pdf)
            page1 = doc1.load_page(application_page - 1)
            mat1 = fitz.Matrix(200 / 72, 200 / 72)  # 200 DPI
            pix1 = page1.get_pixmap(matrix=mat1, alpha=False)
            img1 = Image.open(io.BytesIO(pix1.tobytes("png")))
            if img1.mode != 'RGB':
                img1 = img1.convert('RGB')
            doc1.close()
            
            # 인감증명서 이미지 추출
            doc2 = fitz.open(certificate_pdf)
            page2 = doc2.load_page(certificate_page - 1)
            mat2 = fitz.Matrix(200 / 72, 200 / 72)
            pix2 = page2.get_pixmap(matrix=mat2, alpha=False)
            img2 = Image.open(io.BytesIO(pix2.tobytes("png")))
            if img2.mode != 'RGB':
                img2 = img2.convert('RGB')
            doc2.close()
            
            return self.compare_seals(img1, img2)
            
        except Exception as e:
            return 0.0, f"PDF 처리 오류: {e}"


def extract_owner_info_from_pdf(
    pdf_path: str,
    provider: str = "claude",
    model_name: Optional[str] = None,
    page_numbers: Optional[List[int]] = None
) -> OwnerInfoResult:
    """
    PDF에서 소유자 정보 추출 (편의 함수)
    
    사용 예:
        result = extract_owner_info_from_pdf("document.pdf")
        print(f"이름: {result.name}")
        print(f"법인 여부: {result.is_corporation}")
    """
    extractor = OwnerInfoExtractor(provider=provider, model_name=model_name)
    return extractor.extract_from_pdf(pdf_path, page_numbers)


def compare_seal_similarity(
    application_pdf: str,
    certificate_pdf: str,
    provider: str = "claude",
    model_name: Optional[str] = None
) -> Tuple[float, str]:
    """
    인감 유사도 비교 (편의 함수)
    
    사용 예:
        similarity, note = compare_seal_similarity("application.pdf", "certificate.pdf")
        print(f"유사도: {similarity}%")
        print(f"결과: {'통과' if similarity >= 45 else '미달'}")
    """
    comparator = SealComparator(provider=provider, model_name=model_name)
    return comparator.compare_seals_from_pdfs(application_pdf, certificate_pdf)
