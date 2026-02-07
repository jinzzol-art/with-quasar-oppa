"""
공공임대 기존주택 매입심사 - 개선된 Gemini 클라이언트 v3

개선사항:
1. 환각(hallucination) 방지 프롬프트
2. "확인 안 되면 null" 강제
3. 필드별 명시적 추출 지시
4. 자가학습 시스템 연동
5. 단일 분석 옵션 (속도 향상)
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional, Union

from dotenv import load_dotenv
from PIL import Image
from pydantic import ValidationError

import google.generativeai as genai

from core.data_models import PublicHousingReviewResult
from core.learning_system import LearningDatabase, ResultPostProcessor


class ImprovedGeminiClient:
    """
    개선된 Gemini 클라이언트 v3
    
    - 환각 방지
    - 자가학습 연동
    - 속도/정확도 균형 옵션
    """
    
    def __init__(self, model_name: str = "gemini-2.5-flash"):
        load_dotenv()
        
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError(".env 파일에 GOOGLE_API_KEY가 없습니다.")
        
        genai.configure(api_key=api_key, transport="rest")
        self.model_name = model_name
        
        # 자가학습 시스템
        self.learning_db = LearningDatabase()
        self.post_processor = ResultPostProcessor()
    
    def _build_anti_hallucination_prompt(self) -> str:
        """환각 방지 시스템 프롬프트"""
        return """당신은 한국토지주택공사(LH)의 공공임대 기존주택 매입심사 전문가입니다.

## ⚠️ 가장 중요한 규칙: 환각(Hallucination) 금지

1. **문서에서 직접 확인되지 않은 정보는 절대 추측하지 마세요**
2. **확인할 수 없는 필드는 반드시 null로 표시하세요**
3. **"아마도", "추정" 등의 불확실한 판단을 하지 마세요**
4. **문서에 명시적으로 적혀있는 내용만 추출하세요**

## 필드별 추출 규칙

### 내진설계 적용 여부 (seismic_design)
- "적용", "해당", "Y", "예", "O" → true
- "미적용", "해당없음", "N", "아니오", "X" → false  
- **문서에서 "내진설계" 관련 텍스트를 찾을 수 없으면 → null**

### 지하층 유무 (has_basement)
- "지하 N층" (N > 0) → true
- "지하 없음", "지하 0층", 또는 지하 관련 언급 없음 → false
- **"지하"라는 단어가 문서에 없으면 → false (null 아님)**

### 승강기 설치 여부 (has_elevator)
- "승강기 N대" (N > 0), "엘리베이터 있음" → true
- "승강기 없음", "승강기 0대" → false
- **승강기 관련 언급을 찾을 수 없으면 → null**

### 주차장 대수 (outdoor_parking, indoor_parking, mechanical_parking)
- 숫자가 명시되어 있으면 그 숫자
- "없음" → 0
- **확인 안 되면 → null (0으로 추측하지 마세요)**

### 날짜 필드
- 명시된 날짜를 YYYY-MM-DD 형식으로 변환
- **날짜를 찾을 수 없으면 → null**

### 면적 필드
- 숫자만 추출 (단위 제외)
- **면적을 찾을 수 없으면 → null**

## 출력 규칙

1. **확실한 것만 값을 채우세요**
2. **불확실하면 null**
3. **추측 금지**
4. **JSON 형식으로만 응답**"""

    def _build_field_specific_prompt(self) -> str:
        """필드별 명시적 추출 프롬프트"""
        
        # 학습된 예시 가져오기
        seismic_examples = self.learning_db.get_learned_examples("seismic_design", 3)
        basement_examples = self.learning_db.get_learned_examples("has_basement", 3)
        
        examples_text = ""
        
        if seismic_examples:
            examples_text += "\n### 내진설계 학습된 예시:\n"
            for ex in seismic_examples:
                examples_text += f"- \"{ex.get('raw', '')}\" → {ex.get('value')}\n"
        
        if basement_examples:
            examples_text += "\n### 지하층 학습된 예시:\n"
            for ex in basement_examples:
                examples_text += f"- \"{ex.get('raw', '')}\" → {ex.get('value')}\n"
        
        return f"""
## 추출 예시
{examples_text}

### 건축물대장 표제부에서 찾아야 할 항목:
1. 사용승인일 - "사용승인일", "사용승인" 근처의 날짜
2. 내진설계 - "내진설계적용여부", "내진설계" 항목
3. 주차장 - "주차장" 항목에서 옥외/옥내/기계식 대수
4. 승강기 - "승강기", "엘리베이터" 항목
5. 층수 - "층수" 항목에서 지상/지하 층수

### 특별 주의사항:
- 건축물대장에 "지하"가 언급되지 않으면 지하층은 **없는 것**입니다 (has_basement: false)
- 내진설계 항목에 "적용"이라고 적혀있으면 **적용된 것**입니다 (seismic_design: true)
- 숫자 0과 null은 다릅니다. 0은 "없음이 확인됨", null은 "확인 불가"
"""

    def _build_analysis_prompt(self, announcement_date: str) -> str:
        """분석 프롬프트"""
        json_schema = PublicHousingReviewResult.model_json_schema()
        json_schema_str = json.dumps(json_schema, ensure_ascii=False, indent=2)
        
        return f"""
## 분석 대상
다음 이미지/텍스트는 공공임대 기존주택 매입심사 서류입니다.

## 기준 공고일: {announcement_date}

## 분석 지시

### 1단계: 서류 종류 파악
각 페이지가 어떤 서류인지 먼저 확인하세요.

### 2단계: 해당 서류에서 필요한 정보만 추출
- 건축물대장 표제부 → 사용승인일, 내진설계, 주차장, 승강기, 층수
- 토지대장 → 대지면적
- 등기부등본 → 소유자, 근저당, 압류, 신탁

### 3단계: 확인되지 않은 정보는 null
**추측하지 마세요. 문서에서 직접 확인된 정보만 채우세요.**

## JSON 출력 스키마
```json
{json_schema_str}
```

## 응답 형식
- 반드시 JSON만 출력
- 설명이나 주석 금지
- 확인 안 된 필드는 null"""

    def analyze(
        self,
        content: Union[str, list[Image.Image]],
        announcement_date: str = "2025-07-05",
        enable_dual_validation: bool = False,  # 이중검증 옵션
        enable_post_processing: bool = True,   # 후처리 옵션
    ) -> tuple[PublicHousingReviewResult, dict]:
        """
        문서 분석
        
        Args:
            content: 텍스트 또는 이미지 리스트
            announcement_date: 공고일
            enable_dual_validation: 이중검증 활성화 (느리지만 정확)
            enable_post_processing: 패턴 기반 후처리 활성화
        
        Returns:
            (분석 결과, 메타 정보)
        """
        print(">>> Gemini 분석 시작...")
        
        # 원본 텍스트 저장 (후처리용)
        raw_text = content if isinstance(content, str) else None
        
        # 분석 실행
        if enable_dual_validation:
            result_dict = self._dual_analysis(content, announcement_date)
        else:
            result_dict = self._single_analysis(content, announcement_date)
        
        # 후처리 (패턴 기반 교정)
        corrections_report = ""
        if enable_post_processing and result_dict:
            result_dict = self.post_processor.process(result_dict, raw_text)
            corrections_report = self.post_processor.get_corrections_report()
        
        # PublicHousingReviewResult로 변환
        try:
            result = PublicHousingReviewResult(**result_dict)
        except Exception as e:
            print(f">>> 결과 변환 오류: {e}")
            result = PublicHousingReviewResult(
                review_date=datetime.now().strftime("%Y-%m-%d"),
                review_summary=f"분석 완료 (일부 오류: {e})"
            )
        
        # 메타 정보
        meta = {
            "analysis_mode": "dual" if enable_dual_validation else "single",
            "post_processing": enable_post_processing,
            "corrections_report": corrections_report,
            "model": self.model_name,
        }
        
        return result, meta
    
    def _single_analysis(
        self,
        content: Union[str, list[Image.Image]],
        announcement_date: str
    ) -> dict:
        """단일 분석"""
        system_prompt = self._build_anti_hallucination_prompt()
        field_prompt = self._build_field_specific_prompt()
        user_prompt = self._build_analysis_prompt(announcement_date)
        
        full_system = system_prompt + "\n" + field_prompt
        
        if isinstance(content, str):
            content_parts = [user_prompt + "\n\n## 문서 텍스트:\n" + content]
        else:
            content_parts = [user_prompt] + list(content)
        
        generation_config = genai.types.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1,  # 낮은 temperature로 일관성 향상
        )
        
        try:
            model = genai.GenerativeModel(
                self.model_name,
                system_instruction=full_system,
            )
            response = model.generate_content(
                content_parts,
                generation_config=generation_config,
            )
            
            result_text = getattr(response, "text", str(response))
            return json.loads(result_text)
            
        except Exception as e:
            print(f">>> 분석 오류: {e}")
            return {}
    
    def _dual_analysis(
        self,
        content: Union[str, list[Image.Image]],
        announcement_date: str
    ) -> dict:
        """이중 분석 (정확도 우선)"""
        print(">>> 1차 분석...")
        first = self._single_analysis(content, announcement_date)
        
        print(">>> 2차 분석...")
        # 2차는 약간 다른 temperature
        second = self._single_analysis_with_temp(content, announcement_date, 0.2)
        
        # 결과 병합 (일치하는 값 우선)
        return self._merge_analyses(first, second)
    
    def _single_analysis_with_temp(
        self,
        content: Union[str, list[Image.Image]],
        announcement_date: str,
        temperature: float
    ) -> dict:
        """지정된 temperature로 분석"""
        system_prompt = self._build_anti_hallucination_prompt()
        user_prompt = self._build_analysis_prompt(announcement_date)
        
        if isinstance(content, str):
            content_parts = [user_prompt + "\n\n## 문서 텍스트:\n" + content]
        else:
            content_parts = [user_prompt] + list(content)
        
        generation_config = genai.types.GenerationConfig(
            response_mime_type="application/json",
            temperature=temperature,
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
            
            return json.loads(getattr(response, "text", "{}"))
        except Exception:
            return {}
    
    def _merge_analyses(self, first: dict, second: dict) -> dict:
        """두 분석 결과 병합"""
        if not first:
            return second
        if not second:
            return first
        
        # 기본적으로 first 사용, 불일치 시 null 우선 (환각 방지)
        merged = first.copy()
        
        def merge_nested(d1: dict, d2: dict, path: str = "") -> dict:
            result = d1.copy()
            for key, val1 in d1.items():
                val2 = d2.get(key)
                
                if isinstance(val1, dict) and isinstance(val2, dict):
                    result[key] = merge_nested(val1, val2, f"{path}.{key}")
                elif val1 != val2:
                    # 불일치 시: null 우선 (환각 방지)
                    if val1 is None:
                        result[key] = None
                    elif val2 is None:
                        result[key] = None
                    else:
                        # 둘 다 값이 있지만 다르면 → first 값 유지하되 로그
                        print(f">>> 불일치 감지 ({path}.{key}): {val1} vs {val2}")
                        result[key] = val1
            
            return result
        
        return merge_nested(merged, second)
    
    def submit_correction(
        self,
        field_name: str,
        ai_value: any,
        correct_value: any,
        document_type: str = "건축물대장 표제부"
    ):
        """
        사용자 교정 제출
        
        AI가 틀렸을 때 사용자가 호출하여 학습시킴
        """
        self.post_processor.submit_user_correction(
            field_name=field_name,
            ai_value=ai_value,
            correct_value=correct_value,
            document_type=document_type
        )
        print(f">>> 학습 완료: {field_name} ({ai_value} → {correct_value})")
