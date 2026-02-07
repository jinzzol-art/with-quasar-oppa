"""
공공임대 기존주택 매입심사 - 이중 검증 Gemini 클라이언트

99.99% 정확도를 위해:
1. 같은 문서를 2번 분석
2. 결과 비교하여 불일치 항목 플래그
3. Few-shot 예제 포함 강화 프롬프트
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional, Union

from dotenv import load_dotenv
from PIL import Image
from pydantic import BaseModel, Field, ValidationError

import google.generativeai as genai

from core.data_models import PublicHousingReviewResult
from core.verification_rules import RULE_COUNT
from core.advanced_validator import (
    AdvancedValidator, 
    DualValidationResult,
    ConfidenceLevel
)


class DualAnalysisGeminiClient:
    """
    이중 검증 Gemini 클라이언트
    
    - 같은 문서를 2번 분석하여 결과 비교
    - 불일치 항목은 자동으로 "수동확인필요" 플래그
    - Few-shot 예제 포함으로 정확도 향상
    """
    
    def __init__(self, model_name: str = "gemini-2.5-flash"):
        load_dotenv()
        
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError(".env 파일에 GOOGLE_API_KEY가 없습니다.")
        
        genai.configure(api_key=api_key, transport="rest")
        self.model_name = model_name
        self._model = genai.GenerativeModel(model_name)
    
    def _build_enhanced_system_prompt(self) -> str:
        """Few-shot 예제가 포함된 강화 시스템 프롬프트"""
        return f"""당신은 한국토지주택공사(LH)의 공공임대 기존주택 매입심사 전문가입니다.

## 핵심 역할
제출된 서류를 검토하여 {RULE_COUNT}개 검증 요구 조건에 따라 정확하게 분석합니다.

## 정확한 데이터 추출 지침

### 1. 날짜 추출
- 형식: YYYY-MM-DD로 통일
- 예시: "2025년 7월 5일" → "2025-07-05"
- 예시: "2025.7.5" → "2025-07-05"
- 손글씨 날짜도 정확히 인식

### 2. 면적 추출  
- 형식: 숫자만 추출 (단위 제외)
- 예시: "123.45㎡" → 123.45
- 예시: "123.45 제곱미터" → 123.45
- 콤마가 있으면 제거: "1,234.56㎡" → 1234.56

### 3. 인감 인식
- 원형/타원형 붉은색 도장 식별
- 인감 안의 글자(한글/한자) 읽기
- 두 인감 비교 시 유사도를 0~100%로 수치화

### 4. 체크박스 인식
- ☑, ✓, V, ○ 표시 = 체크됨 (true)
- ☐, □, 빈칸 = 체크안됨 (false)

### 5. 손글씨 인식
- 숫자: 0~9 정확히 구분 (특히 1과 7, 6과 0 주의)
- 한글: 받침 있는 글자 주의
- 불분명한 경우 "불분명" 표시

## Few-shot 예제

### 예제 1: 주택매도 신청서
입력 이미지에서 다음과 같은 표가 보이면:
```
┌─────────────────────────────────────┐
│ 소유자 성명: 홍길동                  │
│ 생년월일: 1980.05.15                │
│ 현거주지: 서울시 강남구 역삼동 123   │
│ 전화번호: 010-1234-5678             │
│ 이메일: hong@email.com              │
│ (인)                                │
└─────────────────────────────────────┘
```
추출 결과:
```json
{
  "owner_info": {
    "name": "홍길동",
    "birth_date": "1980-05-15",
    "address": "서울시 강남구 역삼동 123",
    "phone": "010-1234-5678",
    "email": "hong@email.com",
    "is_complete": true
  },
  "seal_verification": {
    "seal_exists": true
  }
}
```

### 예제 2: 대지면적 추출
여러 서류에서 대지면적이 다음과 같이 보이면:
- 주택매도신청서: "대지면적: 1,234.56㎡"
- 토지대장: "면적 1234.56제곱미터"
- 토지이용계획확인서: "1,234.56 m²"

추출 결과 (모두 동일한 값으로 정규화):
```json
{
  "land_area_application": 1234.56,
  "land_area_land_ledger": 1234.56,
  "land_area_land_use_plan": 1234.56,
  "land_area_match": true
}
```

### 예제 3: 인감 일치 판정
인감증명서의 인감과 신청서의 인감을 비교:
- 형태가 매우 유사 → 80~100%
- 형태가 유사하나 일부 차이 → 45~80%  
- 형태가 다름 → 45% 미만

기준: 45% 이상이면 일치로 판정

## 중요 규칙
1. 확실하지 않은 값은 null 대신 추출하되 confidence를 낮게
2. 숫자는 반드시 숫자 타입으로 (문자열 아님)
3. 날짜는 반드시 YYYY-MM-DD 형식으로
4. 불분명한 손글씨는 "불분명:{추정값}" 형식으로"""

    def _build_analysis_prompt(self, announcement_date: str, attempt: int) -> str:
        """분석 프롬프트 (시도 번호 포함)"""
        json_schema = PublicHousingReviewResult.model_json_schema()
        json_schema_str = json.dumps(json_schema, ensure_ascii=False, indent=2)
        
        # 2차 시도에서는 더 신중하게 분석하도록 지시
        careful_instruction = ""
        if attempt == 2:
            careful_instruction = """
## ⚠️ 2차 검증 분석
이것은 이중 검증을 위한 2차 분석입니다.
- 1차 분석과 독립적으로 처음부터 다시 분석하세요
- 특히 숫자, 날짜, 이름의 정확성에 집중하세요
- 불확실한 부분은 더 신중하게 판단하세요
"""
        
        return f"""{careful_instruction}
## 분석 대상
다음 이미지들은 공공임대 기존주택 매입심사를 위해 제출된 서류입니다.

## 기준 공고일: {announcement_date}
이 날짜 이전에 작성/발급된 서류는 "보완서류"입니다.

## 분석 순서

### 1단계: 서류 종류 식별
각 페이지가 어떤 서류인지 먼저 파악

### 2단계: 핵심 정보 추출
- 날짜 (YYYY-MM-DD 형식으로)
- 이름 (정확한 한글)
- 면적 (숫자만, 단위 제외)
- 인감 유무 및 일치율

### 3단계: 교차 검증
- 대지면적: 주택매도신청서 = 토지대장 = 토지이용계획확인서
- 사용승인일: 주택매도신청서 = 건축물대장 표제부
- 소유자명: 모든 서류에서 동일

### 4단계: 보완서류 판정
규칙 위반 시 보완서류로 분류

## JSON 출력 스키마
```json
{json_schema_str}
```

반드시 위 스키마 형식으로만 응답하세요."""

    def analyze_with_dual_validation(
        self,
        content: Union[str, list[Image.Image]],
        announcement_date: str = "2025-07-05"
    ) -> tuple[PublicHousingReviewResult, dict]:
        """
        이중 검증 분석
        
        Args:
            content: 텍스트 또는 이미지 리스트
            announcement_date: 공고일
            
        Returns:
            (최종 검증 결과, 이중검증 리포트)
        """
        # 이중검증 가속: 1차=전체 분석, 2차=핵심 필드만 경량 분석 (병렬) → 2차 소요 시간 단축
        print(">>> 1차(전체) · 2차(경량) 분석 병렬 시작 (이중 검증)...")
        with ThreadPoolExecutor(max_workers=2) as executor:
            fut1 = executor.submit(self._single_analysis, content, announcement_date, 1)
            fut2 = executor.submit(self._single_analysis_lightweight, content, announcement_date)
            first_result = fut1.result()
            second_result = fut2.result()
        
        print(">>> 결과 비교 및 검증...")
        final_result, dual_report = self._compare_and_merge(
            first_result, 
            second_result,
            announcement_date
        )
        
        return final_result, dual_report
    
    def _single_analysis(
        self,
        content: Union[str, list[Image.Image]],
        announcement_date: str,
        attempt: int
    ) -> dict:
        """단일 분석 실행"""
        system_prompt = self._build_enhanced_system_prompt()
        user_prompt = self._build_analysis_prompt(announcement_date, attempt)
        
        if isinstance(content, str):
            full_prompt = f"{user_prompt}\n\n## 문서 텍스트:\n{content}"
            content_parts = [full_prompt]
        else:
            content_parts = [user_prompt] + list(content)
        
        generation_config = genai.types.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1 if attempt == 1 else 0.2,  # 2차는 약간 다른 temperature
        )
        
        try:
            model = genai.GenerativeModel(
                self.model_name,
                system_instruction=system_prompt,
            )
            response = model.generate_content(
                content_parts,
                generation_config=generation_config,
            )
            
            result_text = getattr(response, "text", str(response))
            return json.loads(result_text)
            
        except Exception as e:
            print(f">>> {attempt}차 분석 오류: {e}")
            return {}
    
    # 이중검증 시 2차에서 비교하는 필드 (경량 2차 호출용)
    _DUAL_KEY_PATHS = [
        "property_address",
        "housing_sale_application.issue_date",
        "housing_sale_application.land_area",
        "housing_sale_application.approval_date",
        "housing_sale_application.owner_info.name",
        "housing_sale_application.owner_info.phone",
        "housing_sale_application.owner_info.email",
        "land_ledger.land_area",
        "land_use_plan.land_area",
        "building_ledger_title.approval_date",
    ]
    
    def _build_lightweight_prompt(self, announcement_date: str) -> str:
        """2차 경량 분석용: 비교용 핵심 필드만 요청 (응답 짧음 → 소요 시간 단축)"""
        return f"""기준 공고일: {announcement_date}

아래 문서에서 **다음 필드만** 추출해 JSON으로 답하세요. 없으면 null.

- property_address (물건 소재지)
- housing_sale_application.issue_date 또는 written_date (작성일)
- housing_sale_application.land_area (대지면적, 숫자)
- housing_sale_application.approval_date (사용승인일)
- housing_sale_application.owner_info.name (소유자 성명)
- housing_sale_application.owner_info.phone (전화번호)
- housing_sale_application.owner_info.email (이메일)
- land_ledger.land_area (토지대장 면적)
- land_use_plan.land_area (토지이용계획 면적)
- building_ledger_title.approval_date (건축물대장 사용승인일)

출력 형식 (중첩 구조 유지):
{{
  "property_address": "주소",
  "housing_sale_application": {{
    "issue_date": "YYYY-MM-DD",
    "land_area": 123.45,
    "approval_date": "YYYY-MM-DD",
    "owner_info": {{ "name": "홍길동", "phone": "010-...", "email": "..." }}
  }},
  "land_ledger": {{ "land_area": 123.45 }},
  "land_use_plan": {{ "land_area": 123.45 }},
  "building_ledger_title": {{ "approval_date": "YYYY-MM-DD" }}
}}

JSON만 출력하세요."""
    
    # 2차 경량 분석 시 이미지 수 상한 (소요 시간 단축)
    LIGHTWEIGHT_MAX_IMAGES = 5
    
    def _single_analysis_lightweight(
        self,
        content: Union[str, list[Image.Image]],
        announcement_date: str
    ) -> dict:
        """2차 경량 분석: 핵심 필드만 요청 + 이미지 수 제한 → 응답 시간 단축"""
        user_prompt = self._build_lightweight_prompt(announcement_date)
        if isinstance(content, str):
            content_parts = [f"{user_prompt}\n\n## 문서 텍스트:\n{content}"]
        else:
            images = list(content)[: self.LIGHTWEIGHT_MAX_IMAGES]
            content_parts = [user_prompt] + images
        
        try:
            model = genai.GenerativeModel(self.model_name)
            response = model.generate_content(
                content_parts,
                generation_config=genai.types.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            result_text = getattr(response, "text", str(response))
            return json.loads(result_text)
        except Exception as e:
            print(f">>> 2차(경량) 분석 오류: {e}")
            return {}
    
    def _compare_and_merge(
        self,
        first: dict,
        second: dict,
        announcement_date: str
    ) -> tuple[PublicHousingReviewResult, dict]:
        """두 결과 비교 및 병합"""
        validator = AdvancedValidator(announcement_date)
        
        # 핵심 필드 비교
        key_fields = [
            "property_address",
            "announcement_date",
        ]
        
        # 중첩 필드 비교 (경로로 접근)
        nested_comparisons = [
            ("housing_sale_application.issue_date", "주택매도신청서 작성일"),
            ("housing_sale_application.land_area", "대지면적(신청서)"),
            ("housing_sale_application.approval_date", "사용승인일(신청서)"),
            ("housing_sale_application.owner_info.name", "소유자 성명"),
            ("housing_sale_application.owner_info.phone", "소유자 전화번호"),
            ("housing_sale_application.owner_info.email", "소유자 이메일"),
            ("land_ledger.land_area", "대지면적(토지대장)"),
            ("land_use_plan.land_area", "대지면적(토지이용계획)"),
            ("building_ledger_title.approval_date", "사용승인일(건축물대장)"),
        ]
        
        inconsistencies = []
        
        for path, field_name in nested_comparisons:
            first_val = self._get_nested_value(first, path)
            second_val = self._get_nested_value(second, path)
            
            dual_result = validator.compare_dual_results(
                str(first_val) if first_val is not None else None,
                str(second_val) if second_val is not None else None,
                field_name
            )
            
            validator.add_dual_validation(dual_result)
            
            if not dual_result.is_consistent:
                inconsistencies.append({
                    "field": field_name,
                    "first_analysis": first_val,
                    "second_analysis": second_val,
                    "action": "수동확인필요"
                })
        
        # 일관된 값으로 최종 결과 생성
        merged = self._merge_results(first, second, validator)
        
        # PublicHousingReviewResult로 변환
        try:
            final_result = PublicHousingReviewResult(**merged)
        except Exception as e:
            print(f">>> 결과 변환 오류: {e}")
            final_result = PublicHousingReviewResult(
                review_date=datetime.now().strftime("%Y-%m-%d"),
                review_summary=f"분석 완료 (일부 오류: {e})"
            )
        
        # 이중검증 리포트
        dual_report = {
            "dual_validation_performed": True,
            "total_fields_compared": len(nested_comparisons),
            "consistent_fields": len(nested_comparisons) - len(inconsistencies),
            "inconsistent_fields": len(inconsistencies),
            "inconsistencies": inconsistencies,
            "validation_report": validator.generate_report(),
            "confidence_summary": {
                "high": sum(1 for r in validator.dual_results if r.confidence == ConfidenceLevel.HIGH),
                "medium": sum(1 for r in validator.dual_results if r.confidence == ConfidenceLevel.MEDIUM),
                "low": sum(1 for r in validator.dual_results if r.confidence == ConfidenceLevel.LOW),
                "manual_check": sum(1 for r in validator.dual_results if r.confidence == ConfidenceLevel.MANUAL_CHECK),
            }
        }
        
        return final_result, dual_report
    
    def _get_nested_value(self, data: dict, path: str) -> any:
        """중첩 딕셔너리에서 경로로 값 가져오기"""
        keys = path.split(".")
        value = data
        
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return None
        
        return value
    
    def _merge_results(
        self, 
        first: dict, 
        second: dict, 
        validator: AdvancedValidator
    ) -> dict:
        """두 결과 병합 (일관된 값 우선)"""
        # 기본적으로 first 결과 사용, 불일치 시 first 값 + 플래그
        merged = first.copy() if first else second.copy() if second else {}
        
        # review_date 설정
        merged["review_date"] = datetime.now().strftime("%Y-%m-%d")
        
        # 불일치 항목에 대한 메모 추가
        if validator.dual_results:
            inconsistent_notes = []
            for r in validator.dual_results:
                if not r.is_consistent:
                    inconsistent_notes.append(
                        f"[수동확인필요] {r.field_name}: "
                        f"1차='{r.first_value}', 2차='{r.second_value}'"
                    )
            
            if inconsistent_notes:
                current_summary = merged.get("review_summary", "")
                merged["review_summary"] = (
                    f"{current_summary}\n\n"
                    f"⚠️ 이중검증 불일치 항목 ({len(inconsistent_notes)}건):\n" +
                    "\n".join(inconsistent_notes)
                )
        
        return merged
