"""
공공임대 기존주택 매입심사 - 고성능 Gemini 클라이언트 v5.0

핵심 개선사항:
1. 문서 유형별 특화 프롬프트 (주택매도신청서, 위임장 등)
2. 페이지별 개별 분석 후 병합
3. 손글씨/체크박스/인감 인식 최적화
4. 재시도 로직 및 에러 복구
5. 분석 결과 신뢰도 평가
"""
from __future__ import annotations

import json
import os
import io
import re
import time
from datetime import datetime
from typing import Optional, Union, Any, List, Dict, Tuple
from dataclasses import dataclass, field

from dotenv import load_dotenv

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

import google.generativeai as genai
from google.api_core import exceptions as google_exceptions

from core.data_models import PublicHousingReviewResult
from core.high_quality_pdf_processor import (
    HighQualityPDFProcessor,
    PDFExtractionResult,
    PageContent,
    DocumentType,
)


# =============================================================================
# 문서 유형별 전용 프롬프트
# =============================================================================

PROMPT_HOUSING_SALE_APPLICATION = """당신은 LH 공공임대 주택매도신청서를 분석하는 전문가입니다.

## 분석 대상
이 이미지는 **주택매도 신청서** 양식입니다.

## 반드시 확인할 항목

### 1. 소유자 정보 (표 상단)
- **성명**: 손글씨로 기재된 소유자 이름
- **생년월일**: 주민등록번호 앞 6자리
- **현거주지 주소**: 손글씨 또는 인쇄된 주소
- **휴대전화번호**: 010-XXXX-XXXX 형식
- **이메일주소**: xxx@xxx.xxx 형식

### 2. 매도주택 정보
- **소재지**: 매도할 주택 주소
- **대지면적**: ___㎡ (숫자 확인)
- **건물사용승인일**: YYYY년 MM월 DD일 또는 YYYY.MM.DD

### 3. 인감도장
- 소유자 인감도장이 날인되어 있는지 확인
- 원형 또는 타원형의 붉은색 도장 찾기
- 도장 안의 한자/한글 이름 읽기

### 4. 대리인 정보 (해당시)
- 대리인 성명
- 대리인 연락처

### 5. 작성일자
- 문서 하단의 날짜: YYYY년 MM월 DD일

### 6. 체크박스
- ☑ 체크된 항목 확인
- ☐ 미체크 항목 확인

## 손글씨 인식 지침
- 한글 손글씨를 정확히 읽으세요
- 숫자는 특히 주의 (0과 O, 1과 I, 6과 8 구분)
- 불분명한 글자는 "불분명"으로 표시

## 출력 형식 (JSON)

```json
{
  "document_type": "주택매도신청서",
  "exists": true,
  "owner_info": {
    "name": "소유자 성명 또는 null",
    "birth_date": "YYMMDD 또는 null",
    "address": "현거주지 주소 또는 null",
    "phone": "전화번호 또는 null",
    "email": "이메일 또는 null",
    "is_complete": true/false
  },
  "property_info": {
    "location": "매도주택 소재지",
    "land_area": 대지면적(숫자) 또는 null,
    "land_area_unit": "㎡",
    "approval_date": "YYYY-MM-DD 또는 null"
  },
  "seal_info": {
    "has_seal": true/false,
    "seal_name": "도장에서 읽은 이름 또는 null",
    "seal_condition": "양호/흐림/번짐/없음"
  },
  "agent_info": {
    "has_agent": true/false,
    "agent_name": "대리인 이름 또는 null"
  },
  "written_date": "YYYY-MM-DD 또는 null",
  "checkboxes": {
    "checked_items": ["체크된 항목들"],
    "unchecked_items": ["미체크 항목들"]
  }
}
```

## 중요 규칙
1. 손글씨는 최대한 정확히 읽되, 확신 없으면 null
2. 인감도장이 보이면 has_seal: true
3. 빈칸은 null로 표시
4. 날짜는 반드시 YYYY-MM-DD 형식으로 변환

JSON만 출력하세요."""


PROMPT_RENTAL_STATUS = """당신은 LH 매도신청주택 임대현황표를 분석하는 전문가입니다.

## 분석 대상
이 이미지는 **매도신청주택 임대현황** 양식입니다.

## 반드시 확인할 항목

### 호별 정보 (표 형식)
각 행에서 다음 정보를 추출:
- **호수**: 101호, 102호 등
- **전용면적**: ___㎡
- **임대보증금**: ___원 (있는 경우)
- **월임대료**: ___원 (있는 경우)
- **입주현황**: 공실/입주 등

## 출력 형식 (JSON)

```json
{
  "document_type": "매도신청주택임대현황",
  "exists": true,
  "units": [
    {
      "unit_number": "101",
      "exclusive_area": 25.5,
      "deposit": 50000000,
      "monthly_rent": 0,
      "status": "입주"
    }
  ],
  "total_units": 15,
  "total_area_sum": 350.5
}
```

JSON만 출력하세요."""


PROMPT_POWER_OF_ATTORNEY = """당신은 위임장을 분석하는 전문가입니다.

## 분석 대상
이 이미지는 **위임장** 양식입니다.

## 반드시 확인할 항목

### 1. 위임인 정보 (소유자)
- 성명
- 주민등록번호 (일부 마스킹 가능)
- 주소
- 인감도장 날인 여부

### 2. 수임인 정보 (대리인)
- 성명
- 주민등록번호
- 주소
- 인감도장 날인 여부

### 3. 위임 내용
- 매도할 주택 소재지
- 대지면적

### 4. 작성일자

## 출력 형식 (JSON)

```json
{
  "document_type": "위임장",
  "exists": true,
  "delegator": {
    "name": "위임인 성명",
    "address": "주소",
    "has_seal": true/false,
    "info_complete": true/false
  },
  "delegatee": {
    "name": "수임인 성명",
    "address": "주소",
    "has_seal": true/false,
    "info_complete": true/false
  },
  "property": {
    "location": "소재지",
    "land_area": 대지면적
  },
  "written_date": "YYYY-MM-DD"
}
```

JSON만 출력하세요."""


PROMPT_CONSENT_FORM = """당신은 개인정보 수집 이용 동의서를 분석하는 전문가입니다.

## 분석 대상
이 이미지는 **개인정보 수집 이용 및 제공 동의서** 양식입니다.

## 반드시 확인할 항목

### 1. 소유자 작성란
- 성명 (자필)
- 서명 또는 인감
- 작성일자

### 2. 대리인 작성란 (해당시)
- 성명 (자필)
- 서명 또는 인감
- 작성일자

### 3. 동의 체크박스
- ☑ 동의함
- ☐ 동의하지 않음

## 출력 형식 (JSON)

```json
{
  "document_type": "개인정보동의서",
  "exists": true,
  "owner_section": {
    "name": "소유자 성명",
    "has_signature_or_seal": true/false,
    "written_date": "YYYY-MM-DD",
    "all_checked": true/false
  },
  "agent_section": {
    "name": "대리인 성명 또는 null",
    "has_signature_or_seal": true/false,
    "written_date": "YYYY-MM-DD 또는 null"
  }
}
```

JSON만 출력하세요."""


PROMPT_INTEGRITY_PLEDGE = """당신은 청렴서약서를 분석하는 전문가입니다.

## 분석 대상
이 이미지는 **청렴서약서** 양식입니다.

## 반드시 확인할 항목

### 1. 소유자 작성란
- 성명
- 주민등록번호 또는 사업자등록번호
- 인감도장
- 작성일자

### 2. 대리인 작성란 (해당시)

### 3. 중개사 작성란 (해당시)

## 출력 형식 (JSON)

```json
{
  "document_type": "청렴서약서",
  "exists": true,
  "owner": {
    "name": "성명",
    "id_number": "주민번호 앞자리 또는 사업자번호",
    "id_type": "주민등록번호/사업자등록번호",
    "has_seal": true/false,
    "written_date": "YYYY-MM-DD"
  },
  "agent": {
    "submitted": true/false,
    "name": "대리인명 또는 null"
  },
  "realtor": {
    "submitted": true/false,
    "name": "중개사명 또는 null"
  }
}
```

JSON만 출력하세요."""


PROMPT_LH_EMPLOYEE_CONFIRMATION = """당신은 공사직원여부 확인서를 분석하는 전문가입니다.

## 분석 대상
이 이미지는 **공사직원여부 확인서** 양식입니다.

## 반드시 확인할 항목

### 1. 소유자 정보
- 성명
- 인감도장

### 2. 확인 내용
- LH 공사 직원 여부 체크

### 3. 작성일자

## 출력 형식 (JSON)

```json
{
  "document_type": "공사직원확인서",
  "exists": true,
  "owner_name": "성명",
  "has_seal": true/false,
  "is_lh_employee_checked": true/false,
  "written_date": "YYYY-MM-DD"
}
```

JSON만 출력하세요."""


PROMPT_BUILDING_LEDGER = """당신은 한국 건축물대장을 분석하는 전문가입니다.

## 분석 대상
이 이미지는 정부24에서 발급된 **건축물대장**입니다.

## 건축물대장 유형 구분
1. **총괄표제부**: 여러 동이 있는 건물의 전체 정보
2. **표제부**: 단일 건물 또는 개별 동의 정보
3. **전유부**: 집합건물의 호수별 정보

## 표제부에서 찾을 항목

| 항목 | 위치 | 예시 |
|------|------|------|
| 대지위치 | 상단 | 경기도 수원시 장안구 연무동 216-5 |
| 건물명칭 | 대지위치 아래 | (없을 수 있음) |
| 주용도 | 표 중간 | 공동주택, 다가구주택 |
| 주구조 | 표 중간 | 철근콘크리트구조 |
| 층수 | 층수 행 | 지상 5층, 지하 1층 |
| 사용승인일 | 하단 | 2015년 03월 20일 |
| **내진설계적용여부** | 표 중간~하단 | **적용** 또는 **해당없음** |
| 승강기 | 표 중간 | 승용: 1대 |
| 주차장 | 표 하단 | 옥내자주식: 5대, 옥외: 3대 |

## ⚠️ 중요: 내진설계 확인 방법
"내진설계적용여부" 또는 "내진설계" 텍스트를 찾고:
- "적용" → seismic_design: true
- "해당없음", "미적용", "-" → seismic_design: false
- 찾을 수 없음 → seismic_design: null

## ⚠️ 중요: 지하층 확인 방법
- "지하 1층", "지하1층" 있으면 → has_basement: true
- "지상"만 있고 "지하" 없으면 → has_basement: false

## ⚠️ 중요: 승강기 확인 방법
- "승용: 1대" 등 숫자 있으면 → has_elevator: true
- "없음", "-", 빈칸 → has_elevator: false

## 전유부에서 찾을 항목 (집합건물)
- 호수, 전용면적

## 출력 형식 (JSON)

```json
{
  "document_type": "건축물대장표제부/전유부/총괄표제부",
  "exists": true,
  "ledger_type": "표제부/전유부/총괄표제부",
  "building_info": {
    "location": "대지위치",
    "building_name": "건물명칭 또는 null",
    "main_use": "주용도",
    "structure": "주구조",
    "ground_floors": 지상층수(숫자),
    "basement_floors": 지하층수(숫자, 없으면 0),
    "has_basement": true/false,
    "approval_date": "YYYY-MM-DD",
    "seismic_design": true/false/null,
    "has_elevator": true/false/null,
    "elevator_count": 숫자 또는 null,
    "outdoor_parking": 숫자,
    "indoor_parking": 숫자,
    "mechanical_parking": 숫자
  },
  "exclusive_units": [
    {"unit_number": "101", "area": 25.5}
  ],
  "issue_date": "YYYY-MM-DD"
}
```

## 중요 규칙
1. 표에서 직접 확인된 값만 기재
2. 추측 금지 - 확인 안 되면 null
3. 내진설계: "적용"이라고 적혀있으면 반드시 true

JSON만 출력하세요."""


PROMPT_REGISTRY = """당신은 한국 부동산 등기부등본을 분석하는 전문가입니다.

## 분석 대상
이 이미지는 **등기사항전부증명서(등기부등본)**입니다.

## 등기부등본 구분
- **토지** 등기부등본: 토지의 권리관계
- **건물** 등기부등본: 건물의 권리관계

## 구조
1. **표제부**: 소재지, 건물/토지 내역
2. **갑구**: 소유권 관련 (소유자, 가등기, 가압류 등)
3. **을구**: 소유권 외의 권리 (근저당, 전세권, 임차권 등)

## 찾을 항목

### 표제부
- 소재지번
- 건물/토지 면적

### 갑구 (소유권)
- 현재 소유자
- 압류, 가압류 여부
- 경매개시결정 여부

### 을구 (권리)
- 근저당권 설정 여부 (채권최고액, 근저당권자)
- 신탁 여부
- 전세권 설정 여부

## 출력 형식 (JSON)

```json
{
  "document_type": "건물등기부등본/토지등기부등본",
  "exists": true,
  "registry_type": "건물/토지",
  "title_section": {
    "location": "소재지",
    "area": 면적
  },
  "gap_section": {
    "current_owner": "소유자명",
    "has_seizure": true/false,
    "has_provisional_seizure": true/false,
    "has_auction": true/false,
    "seizure_details": ["상세내역"]
  },
  "eul_section": {
    "has_mortgage": true/false,
    "mortgage_amount": "채권최고액 또는 null",
    "mortgage_holder": "근저당권자 또는 null",
    "has_trust": true/false,
    "trust_details": "신탁 상세 또는 null"
  },
  "issue_date": "YYYY-MM-DD"
}
```

JSON만 출력하세요."""


PROMPT_LAND_USE_PLAN = """당신은 한국 토지이용계획확인원을 분석하는 전문가입니다.

## 분석 대상
이 이미지는 **토지이용계획확인원**입니다.

## 매입제외 관련 핵심 항목

다음 항목이 있으면 매입제외 대상입니다:

| 항목 | 키워드 | 제외 유형 |
|------|--------|----------|
| 재정비촉진지구 | "재정비촉진지구", "재정비촉진" | 조건부 제외 |
| 정비구역 | "정비구역", "주거환경정비" | 조건부 제외 |
| 공공주택지구 | "공공주택지구" | 절대 제외 |
| 택지개발예정지구 | "택지개발", "택지개발예정" | 절대 제외 |
| 도시개발구역 | "도시개발구역" | 확인 필요 |

## 출력 형식 (JSON)

```json
{
  "document_type": "토지이용계획확인원",
  "exists": true,
  "location": "소재지",
  "land_area": 면적(㎡),
  "zoning": {
    "is_redevelopment_zone": true/false,
    "is_maintenance_zone": true/false,
    "is_public_housing_zone": true/false,
    "is_housing_development_zone": true/false,
    "is_urban_development_zone": true/false
  },
  "land_use_regulations": ["해당 용도지역/지구 목록"],
  "issue_date": "YYYY-MM-DD"
}
```

JSON만 출력하세요."""


PROMPT_LAND_LEDGER = """당신은 토지대장을 분석하는 전문가입니다.

## 분석 대상
이 이미지는 **토지대장**입니다.

## 찾을 항목
- 소재지
- 지번
- 지목
- 면적 (㎡)
- 소유자
- 발급일

## 출력 형식 (JSON)

```json
{
  "document_type": "토지대장",
  "exists": true,
  "location": "소재지",
  "lot_number": "지번",
  "land_category": "지목",
  "land_area": 면적(㎡),
  "owner": "소유자",
  "issue_date": "YYYY-MM-DD"
}
```

JSON만 출력하세요."""


PROMPT_SEAL_CERTIFICATE = """당신은 인감증명서를 분석하는 전문가입니다.

## 분석 대상
이 이미지는 **인감증명서**입니다.

## 찾을 항목
1. 본인발급용 / 법인인감 구분
2. 성명 / 법인명
3. 인감 이미지 (원형 붉은색 도장)
4. 발급일

## 인감 이미지 분석
- 도장 모양 (원형/사각형)
- 도장 내 문자 (한자/한글)
- 도장 선명도

## 출력 형식 (JSON)

```json
{
  "document_type": "인감증명서",
  "exists": true,
  "certificate_type": "본인발급용/법인인감",
  "name": "성명 또는 법인명",
  "seal_image": {
    "exists": true/false,
    "shape": "원형/사각형/기타",
    "text_in_seal": "도장 안 문자",
    "clarity": "선명/흐림/번짐"
  },
  "issue_date": "YYYY-MM-DD"
}
```

JSON만 출력하세요."""


PROMPT_GENERAL = """당신은 공공임대 기존주택 매입심사 서류를 분석하는 전문가입니다.

## 분석 대상
이 이미지는 매입심사 관련 문서입니다.

## 분석 지침
1. 문서 유형 식별
2. 주요 정보 추출
3. 날짜 정보 확인
4. 서명/인감 여부 확인

## 출력 형식 (JSON)

```json
{
  "document_type": "문서 유형",
  "exists": true,
  "main_content": {
    "key1": "value1",
    "key2": "value2"
  },
  "dates": {
    "written_date": "YYYY-MM-DD 또는 null",
    "issue_date": "YYYY-MM-DD 또는 null"
  },
  "signatures_seals": {
    "has_signature": true/false,
    "has_seal": true/false
  }
}
```

JSON만 출력하세요."""


# =============================================================================
# 프롬프트 매핑
# =============================================================================

DOCUMENT_PROMPTS = {
    DocumentType.HOUSING_SALE_APPLICATION: PROMPT_HOUSING_SALE_APPLICATION,
    DocumentType.RENTAL_STATUS: PROMPT_RENTAL_STATUS,
    DocumentType.POWER_OF_ATTORNEY: PROMPT_POWER_OF_ATTORNEY,
    DocumentType.CONSENT_FORM: PROMPT_CONSENT_FORM,
    DocumentType.INTEGRITY_PLEDGE: PROMPT_INTEGRITY_PLEDGE,
    DocumentType.LH_EMPLOYEE_CONFIRMATION: PROMPT_LH_EMPLOYEE_CONFIRMATION,
    DocumentType.BUILDING_LEDGER_SUMMARY: PROMPT_BUILDING_LEDGER,
    DocumentType.BUILDING_LEDGER_TITLE: PROMPT_BUILDING_LEDGER,
    DocumentType.BUILDING_LEDGER_EXCLUSIVE: PROMPT_BUILDING_LEDGER,
    DocumentType.LAND_LEDGER: PROMPT_LAND_LEDGER,
    DocumentType.LAND_USE_PLAN: PROMPT_LAND_USE_PLAN,
    DocumentType.LAND_REGISTRY: PROMPT_REGISTRY,
    DocumentType.BUILDING_REGISTRY: PROMPT_REGISTRY,
    DocumentType.SEAL_CERTIFICATE: PROMPT_SEAL_CERTIFICATE,
    DocumentType.UNKNOWN: PROMPT_GENERAL,
}


# =============================================================================
# 분석 결과 데이터 클래스
# =============================================================================

@dataclass
class PageAnalysisResult:
    """페이지 분석 결과"""
    page_number: int
    document_type: DocumentType
    raw_response: str
    parsed_data: dict
    confidence: float
    error: Optional[str] = None


@dataclass
class DocumentAnalysisResult:
    """전체 문서 분석 결과"""
    file_path: str
    total_pages: int
    page_results: List[PageAnalysisResult] = field(default_factory=list)
    merged_data: dict = field(default_factory=dict)
    analysis_time_seconds: float = 0.0
    errors: List[str] = field(default_factory=list)


# =============================================================================
# 고성능 Gemini 클라이언트
# =============================================================================

class HighPerformanceGeminiClient:
    """
    고성능 Gemini 클라이언트 v5.0
    
    - 문서 유형별 특화 프롬프트
    - 페이지별 개별 분석
    - 재시도 로직
    - 결과 병합
    """
    
    # 기본 모델
    DEFAULT_MODEL = "gemini-2.0-flash"
    
    # 재시도 설정
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # seconds
    
    # API 호출 간격 (Rate Limit 방지)
    API_CALL_INTERVAL = 0.5  # seconds
    
    def __init__(self, model_name: Optional[str] = None):
        load_dotenv()
        
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError(".env 파일에 GOOGLE_API_KEY가 없습니다.")
        
        genai.configure(api_key=api_key, transport="rest")
        self.model_name = model_name or self.DEFAULT_MODEL
        self.model = genai.GenerativeModel(self.model_name)
        
        # PDF 프로세서
        self.pdf_processor = HighQualityPDFProcessor(
            use_high_dpi=True,
            enable_preprocessing=True
        )
        
        self._last_api_call = 0
    
    def _wait_for_rate_limit(self):
        """Rate Limit 방지를 위한 대기"""
        elapsed = time.time() - self._last_api_call
        if elapsed < self.API_CALL_INTERVAL:
            time.sleep(self.API_CALL_INTERVAL - elapsed)
        self._last_api_call = time.time()
    
    def analyze_pdf(
        self,
        pdf_path: str,
        announcement_date: str = "2025-07-05",
        analyze_per_page: bool = True,
        max_pages_per_batch: int = 5
    ) -> DocumentAnalysisResult:
        """
        PDF 파일 분석
        
        Args:
            pdf_path: PDF 파일 경로
            announcement_date: 공고일
            analyze_per_page: 페이지별 개별 분석 여부
            max_pages_per_batch: 배치당 최대 페이지 수
            
        Returns:
            DocumentAnalysisResult
        """
        start_time = time.time()
        print(f"\n{'='*60}")
        print(f"[HighPerformanceGeminiClient] PDF 분석 시작")
        print(f"파일: {pdf_path}")
        print(f"모델: {self.model_name}")
        print(f"{'='*60}\n")
        
        # 1. PDF 추출
        print(">>> [1단계] PDF 추출 중...")
        pdf_result = self.pdf_processor.extract(pdf_path)
        print(f">>> 총 {pdf_result.total_pages}페이지 추출 완료\n")
        
        # 2. 결과 객체 초기화
        result = DocumentAnalysisResult(
            file_path=pdf_path,
            total_pages=pdf_result.total_pages
        )
        
        # 3. 페이지별 분석 또는 배치 분석
        if analyze_per_page:
            print(">>> [2단계] 페이지별 개별 분석 시작...")
            for page in pdf_result.pages:
                page_result = self._analyze_single_page(page, announcement_date)
                result.page_results.append(page_result)
                
                if page_result.error:
                    result.errors.append(f"페이지 {page.page_number}: {page_result.error}")
        else:
            print(">>> [2단계] 배치 분석 시작...")
            result.page_results = self._analyze_batch(
                pdf_result.pages, 
                announcement_date,
                max_pages_per_batch
            )
        
        # 4. 결과 병합
        print("\n>>> [3단계] 결과 병합 중...")
        result.merged_data = self._merge_results(result.page_results)
        
        # 5. 완료
        result.analysis_time_seconds = time.time() - start_time
        print(f"\n{'='*60}")
        print(f"[분석 완료] 소요 시간: {result.analysis_time_seconds:.1f}초")
        print(f"성공: {len([r for r in result.page_results if not r.error])}페이지")
        print(f"오류: {len(result.errors)}건")
        print(f"{'='*60}\n")
        
        return result
    
    def _analyze_single_page(
        self, 
        page: PageContent, 
        announcement_date: str
    ) -> PageAnalysisResult:
        """단일 페이지 분석"""
        print(f"  - 페이지 {page.page_number} 분석 중... (유형: {page.detected_type.value})")
        
        # 프롬프트 선택
        prompt = DOCUMENT_PROMPTS.get(page.detected_type, PROMPT_GENERAL)
        prompt = f"## 기준 공고일: {announcement_date}\n\n{prompt}"
        
        # 이미지 준비
        image = self.pdf_processor.get_page_image(page, use_enhanced=True)
        
        # API 호출 (재시도 로직)
        for attempt in range(self.MAX_RETRIES):
            try:
                self._wait_for_rate_limit()
                
                response = self.model.generate_content(
                    [prompt, image],
                    generation_config=genai.types.GenerationConfig(
                        response_mime_type="application/json",
                        temperature=0.1,
                    )
                )
                
                raw_text = getattr(response, "text", str(response))
                parsed = self._parse_json_response(raw_text)
                
                print(f"    ✓ 페이지 {page.page_number} 완료")
                
                return PageAnalysisResult(
                    page_number=page.page_number,
                    document_type=page.detected_type,
                    raw_response=raw_text,
                    parsed_data=parsed,
                    confidence=page.confidence
                )
                
            except google_exceptions.ResourceExhausted:
                print(f"    ⚠ Rate limit, 재시도 {attempt + 1}/{self.MAX_RETRIES}")
                time.sleep(self.RETRY_DELAY * (attempt + 1))
                
            except Exception as e:
                if attempt == self.MAX_RETRIES - 1:
                    print(f"    ✗ 페이지 {page.page_number} 오류: {e}")
                    return PageAnalysisResult(
                        page_number=page.page_number,
                        document_type=page.detected_type,
                        raw_response="",
                        parsed_data={},
                        confidence=0.0,
                        error=str(e)
                    )
                time.sleep(self.RETRY_DELAY)
        
        # 모든 재시도 실패
        return PageAnalysisResult(
            page_number=page.page_number,
            document_type=page.detected_type,
            raw_response="",
            parsed_data={},
            confidence=0.0,
            error="최대 재시도 횟수 초과"
        )
    
    def _analyze_batch(
        self,
        pages: List[PageContent],
        announcement_date: str,
        batch_size: int
    ) -> List[PageAnalysisResult]:
        """배치 분석 (여러 페이지를 한번에)"""
        results = []
        
        for i in range(0, len(pages), batch_size):
            batch = pages[i:i+batch_size]
            print(f"  - 배치 {i//batch_size + 1} 분석 중... (페이지 {batch[0].page_number}-{batch[-1].page_number})")
            
            # 이미지 리스트
            images = [self.pdf_processor.get_page_image(p, use_enhanced=True) for p in batch]
            
            # 종합 프롬프트
            prompt = self._build_batch_prompt(batch, announcement_date)
            
            try:
                self._wait_for_rate_limit()
                
                response = self.model.generate_content(
                    [prompt] + images,
                    generation_config=genai.types.GenerationConfig(
                        response_mime_type="application/json",
                        temperature=0.1,
                    )
                )
                
                raw_text = getattr(response, "text", str(response))
                parsed = self._parse_json_response(raw_text)
                
                # 각 페이지 결과 생성
                for j, page in enumerate(batch):
                    results.append(PageAnalysisResult(
                        page_number=page.page_number,
                        document_type=page.detected_type,
                        raw_response=raw_text if j == 0 else "",
                        parsed_data=parsed if j == 0 else {},
                        confidence=page.confidence
                    ))
                    
            except Exception as e:
                print(f"    ✗ 배치 오류: {e}")
                for page in batch:
                    results.append(PageAnalysisResult(
                        page_number=page.page_number,
                        document_type=page.detected_type,
                        raw_response="",
                        parsed_data={},
                        confidence=0.0,
                        error=str(e)
                    ))
        
        return results
    
    def _build_batch_prompt(self, pages: List[PageContent], announcement_date: str) -> str:
        """배치 분석용 프롬프트 생성"""
        doc_types = set(p.detected_type for p in pages)
        
        prompt = f"""당신은 공공임대 기존주택 매입심사 서류를 분석하는 전문가입니다.

## 기준 공고일: {announcement_date}

## 분석할 문서 ({len(pages)}페이지)
"""
        for i, page in enumerate(pages):
            prompt += f"- 이미지 {i+1}: {page.detected_type.value}\n"
        
        prompt += """
## 각 페이지에서 추출할 정보

### 주택매도신청서
- 소유자 정보 (성명, 생년월일, 주소, 전화번호, 이메일)
- 매도주택 정보 (소재지, 대지면적, 사용승인일)
- 인감도장 날인 여부
- 작성일자

### 건축물대장
- 대지위치, 주용도, 주구조
- 층수 (지상/지하)
- 사용승인일
- 내진설계적용여부 ("적용" → true)
- 승강기 (있으면 true)
- 주차장 대수

### 등기부등본
- 소유자
- 근저당, 압류, 가압류, 신탁 여부

### 토지이용계획확인원
- 재정비촉진지구, 정비구역, 공공주택지구 등 해당 여부

## 출력 형식
각 페이지의 정보를 종합한 JSON을 출력하세요.

JSON만 출력하세요."""
        
        return prompt
    
    def _parse_json_response(self, text: str) -> dict:
        """JSON 응답 파싱"""
        text = text.strip()
        
        # 코드 블록 제거
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            if text.startswith("json"):
                text = text[4:].strip()
        
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 부분 JSON 추출 시도
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                try:
                    return json.loads(match.group())
                except:
                    pass
            return {"raw_text": text, "parse_error": True}
    
    def _merge_results(self, page_results: List[PageAnalysisResult]) -> dict:
        """페이지별 결과 병합"""
        merged = {
            "documents_found": [],
            "housing_sale_application": {},
            "rental_status": {},
            "power_of_attorney": {},
            "consent_form": {},
            "integrity_pledge": {},
            "lh_employee_confirmation": {},
            "building_ledger_title": {},
            "building_ledger_exclusive": {},
            "land_ledger": {},
            "land_use_plan": {},
            "building_registry": {},
            "land_registry": {},
            "seal_certificate": {},
        }
        
        for result in page_results:
            if result.error:
                continue
            
            doc_type = result.document_type
            data = result.parsed_data
            
            if not data or data.get("parse_error"):
                continue
            
            # 문서 유형 기록
            if doc_type != DocumentType.UNKNOWN:
                merged["documents_found"].append({
                    "type": doc_type.value,
                    "page": result.page_number,
                    "confidence": result.confidence
                })
            
            # 유형별 데이터 병합
            if doc_type == DocumentType.HOUSING_SALE_APPLICATION:
                merged["housing_sale_application"] = self._deep_merge(
                    merged["housing_sale_application"], data
                )
            
            elif doc_type == DocumentType.RENTAL_STATUS:
                merged["rental_status"] = self._deep_merge(
                    merged["rental_status"], data
                )
            
            elif doc_type == DocumentType.POWER_OF_ATTORNEY:
                merged["power_of_attorney"] = self._deep_merge(
                    merged["power_of_attorney"], data
                )
            
            elif doc_type == DocumentType.CONSENT_FORM:
                merged["consent_form"] = self._deep_merge(
                    merged["consent_form"], data
                )
            
            elif doc_type == DocumentType.INTEGRITY_PLEDGE:
                merged["integrity_pledge"] = self._deep_merge(
                    merged["integrity_pledge"], data
                )
            
            elif doc_type == DocumentType.LH_EMPLOYEE_CONFIRMATION:
                merged["lh_employee_confirmation"] = self._deep_merge(
                    merged["lh_employee_confirmation"], data
                )
            
            elif doc_type in [DocumentType.BUILDING_LEDGER_TITLE, 
                             DocumentType.BUILDING_LEDGER_SUMMARY]:
                merged["building_ledger_title"] = self._deep_merge(
                    merged["building_ledger_title"], 
                    data.get("building_info", data)
                )
            
            elif doc_type == DocumentType.BUILDING_LEDGER_EXCLUSIVE:
                if "exclusive_units" in data:
                    if "units" not in merged["building_ledger_exclusive"]:
                        merged["building_ledger_exclusive"]["units"] = []
                    merged["building_ledger_exclusive"]["units"].extend(
                        data["exclusive_units"]
                    )
            
            elif doc_type == DocumentType.LAND_LEDGER:
                merged["land_ledger"] = self._deep_merge(
                    merged["land_ledger"], data
                )
            
            elif doc_type == DocumentType.LAND_USE_PLAN:
                merged["land_use_plan"] = self._deep_merge(
                    merged["land_use_plan"], data
                )
            
            elif doc_type == DocumentType.BUILDING_REGISTRY:
                merged["building_registry"] = self._deep_merge(
                    merged["building_registry"], data
                )
            
            elif doc_type == DocumentType.LAND_REGISTRY:
                merged["land_registry"] = self._deep_merge(
                    merged["land_registry"], data
                )
            
            elif doc_type == DocumentType.SEAL_CERTIFICATE:
                merged["seal_certificate"] = self._deep_merge(
                    merged["seal_certificate"], data
                )
        
        return merged
    
    def _deep_merge(self, base: dict, update: dict) -> dict:
        """딕셔너리 깊은 병합 (update가 base보다 우선)"""
        result = base.copy()
        
        for key, value in update.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            elif value is not None:  # None이 아닌 값만 업데이트
                result[key] = value
        
        return result
    
    def convert_to_review_result(
        self, 
        analysis: DocumentAnalysisResult,
        announcement_date: str
    ) -> PublicHousingReviewResult:
        """분석 결과를 PublicHousingReviewResult로 변환"""
        data = analysis.merged_data
        
        result = PublicHousingReviewResult(
            review_date=datetime.now().strftime("%Y-%m-%d"),
            announcement_date=announcement_date,
        )
        
        # 주택매도신청서
        if data.get("housing_sale_application"):
            app = data["housing_sale_application"]
            result.housing_sale_application.exists = True
            
            if app.get("owner_info"):
                oi = app["owner_info"]
                result.housing_sale_application.owner_info.name = oi.get("name")
                result.housing_sale_application.owner_info.birth_date = oi.get("birth_date")
                result.housing_sale_application.owner_info.address = oi.get("address")
                result.housing_sale_application.owner_info.phone = oi.get("phone")
                result.housing_sale_application.owner_info.email = oi.get("email")
                result.housing_sale_application.owner_info.is_complete = oi.get("is_complete", False)
            
            if app.get("property_info"):
                pi = app["property_info"]
                result.property_address = pi.get("location")
                result.housing_sale_application.land_area = pi.get("land_area")
                result.housing_sale_application.approval_date = pi.get("approval_date")
            
            if app.get("seal_info"):
                si = app["seal_info"]
                result.housing_sale_application.seal_verification.seal_exists = si.get("has_seal", False)
            
            result.housing_sale_application.written_date = app.get("written_date")
        
        # 건축물대장
        if data.get("building_ledger_title"):
            blt = data["building_ledger_title"]
            result.building_ledger_title.exists = True
            result.building_ledger_title.approval_date = blt.get("approval_date")
            result.building_ledger_title.seismic_design = blt.get("seismic_design")
            result.building_ledger_title.has_basement = blt.get("has_basement", False)
            result.building_ledger_title.basement_floors = blt.get("basement_floors", 0)
            result.building_ledger_title.has_elevator = blt.get("has_elevator")
            result.building_ledger_title.elevator_count = blt.get("elevator_count")
            result.building_ledger_title.outdoor_parking = blt.get("outdoor_parking")
            result.building_ledger_title.indoor_parking = blt.get("indoor_parking")
            result.building_ledger_title.mechanical_parking = blt.get("mechanical_parking")
            
            if blt.get("location") and not result.property_address:
                result.property_address = blt.get("location")
        
        # 등기부등본
        if data.get("building_registry"):
            reg = data["building_registry"]
            result.building_registry.exists = True
            
            gap = reg.get("gap_section", {})
            result.building_registry.has_seizure = gap.get("has_seizure", False)
            
            eul = reg.get("eul_section", {})
            result.building_registry.has_mortgage = eul.get("has_mortgage", False)
            result.building_registry.has_trust = eul.get("has_trust", False)
        
        # 토지이용계획
        if data.get("land_use_plan"):
            lup = data["land_use_plan"]
            result.land_use_plan.exists = True
            
            zoning = lup.get("zoning", {})
            result.land_use_plan.is_redevelopment_zone = zoning.get("is_redevelopment_zone", False)
            result.land_use_plan.is_maintenance_zone = zoning.get("is_maintenance_zone", False)
            result.land_use_plan.is_public_housing_zone = zoning.get("is_public_housing_zone", False)
            result.land_use_plan.is_housing_development_zone = zoning.get("is_housing_development_zone", False)
        
        return result


# =============================================================================
# 기존 인터페이스 호환 함수
# =============================================================================

def analyze_pdf_high_quality(
    pdf_path: str,
    announcement_date: str = "2025-07-05"
) -> Tuple[PublicHousingReviewResult, dict]:
    """
    고품질 PDF 분석 (기존 인터페이스 호환)
    
    Returns:
        (PublicHousingReviewResult, 메타데이터 dict)
    """
    client = HighPerformanceGeminiClient()
    analysis = client.analyze_pdf(pdf_path, announcement_date)
    result = client.convert_to_review_result(analysis, announcement_date)
    
    meta = {
        "total_pages": analysis.total_pages,
        "documents_found": analysis.merged_data.get("documents_found", []),
        "analysis_time": analysis.analysis_time_seconds,
        "errors": analysis.errors,
    }
    
    return result, meta
