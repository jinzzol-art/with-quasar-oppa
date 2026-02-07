"""
공공임대 기존주택 매입심사 검증 시스템 - Gemini 클라이언트

34개 검증 요구 조건에 따른 문서 분석 프롬프트
- " "(따옴표) 안 내용 = 필수 서류 목록
- 서류 간 수치 일치 검증, 발급/작성일자 확인
- 보완서류 = 제대로 준비되지 않은 서류
"""
from __future__ import annotations

import io
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from dotenv import load_dotenv
from PIL import Image
from pydantic import BaseModel, Field, ValidationError

import google.generativeai as genai

from core.data_models import PublicHousingReviewResult
from core.verification_rules import get_verification_rules_text, RULE_COUNT


class GeminiSettings(BaseModel):
    """Gemini API 설정"""
    api_key: str = Field(..., description="Google Generative AI API Key")
    model_name: str = Field(default="gemini-2.5-flash", description="사용할 Gemini 모델")


class PublicHousingGeminiClient:
    """
    공공임대 기존주택 매입심사 전용 Gemini 클라이언트
    
    34개 검증 요구 조건에 맞춰 문서를 분석하고 결과를 반환.
    " "(따옴표) 안 내용은 반드시 있어야 할 서류 목록. 보완서류 = 제대로 준비되지 않은 서류.
    """
    
    # LH 청약플러스 공고일 (예시 - 실제 운영 시 동적으로 조회 필요)
    DEFAULT_ANNOUNCEMENT_DATE = "2025-07-05"
    CORRECTION_ANNOUNCEMENT_DATE = None  # 정정공고가 있는 경우 설정
    
    def __init__(self, model_name: Optional[str] = None) -> None:
        load_dotenv()
        
        api_key = os.getenv("GOOGLE_API_KEY")
        data = {"api_key": api_key}
        if model_name:
            data["model_name"] = model_name
            
        try:
            self.settings = GeminiSettings(**data)
        except ValidationError as e:
            raise RuntimeError(
                "Gemini 설정 초기화 오류. .env 파일에 GOOGLE_API_KEY가 있는지 확인하세요."
            ) from e
        
        genai.configure(api_key=self.settings.api_key, transport="rest")
        self._model = genai.GenerativeModel(self.settings.model_name)
    
    def _build_system_prompt(self) -> str:
        """검증 요구 조건 기반 시스템 프롬프트 생성 (규칙 개수: verification_rules.RULE_COUNT)"""
        return """당신은 한국토지주택공사(LH)의 공공임대 기존주택 매입심사 전문가입니다.

## 핵심 원칙
- 문서에서 " "(따옴표) 안에 있는 내용은 반드시 있어야 할 서류 목록입니다.
- 여러 서류가 서로 수치가 일치하는지 검증 절차를 거쳐야 합니다.
- 대전제: 모든 서류는 발급날짜 혹은 작성일자를 확인합니다.
- 보완서류로 지정된 항목은 제대로 준비되지 않은 서류입니다.
- 전문가답게 처리하고, 요구하지 않은 불필요한 분석·내용은 출력하지 마세요.

## 문서 인식 지침
- 손글씨·숫자(금액, 면적, 날짜)·인감도장·체크박스·표 구조를 정확히 인식하세요.
- 날짜는 공고일·정정공고일 규칙(기존 공고 이후 접수 유효)을 적용하여 검사하세요.

""" + get_verification_rules_text() + """

## 출력 형식
반드시 JSON 형식으로만 결과를 반환하세요. 제공된 스키마를 정확히 따르고, 요청하지 않은 추가 분석이나 설명은 포함하지 마세요."""

    def _build_analysis_prompt(self, announcement_date: str) -> str:
        """문서 분석용 사용자 프롬프트 생성 (RULE_COUNT개 요구 조건 적용)"""
        json_schema = PublicHousingReviewResult.model_json_schema()
        json_schema_str = json.dumps(json_schema, ensure_ascii=False, indent=2)
        
        return f"""## 분석 대상 문서
다음 이미지들은 공공임대 기존주택 매입심사를 위해 제출된 서류입니다.

## 적용 공고일
- 기준 공고일: {announcement_date}
- 정정공고가 있으면 정정공고 이전 접수(기존 공고 이후)도 유효. 이 날짜 이전에 작성/발급된 서류는 "보완서류"로 분류하세요.

## 분석 지시
- 위 시스템 프롬프트의 {RULE_COUNT}개 검증 요구 조건에 따라 서류를 식별·정보 추출·교차 검증·보완서류 판정을 수행하세요.
- " "(따옴표) 안 서류명은 필수 서류. 여러 서류 간 수치 일치 검증 필수. 모든 서류 발급/작성일 확인. 보완서류 = 제대로 준비되지 않은 서류.
- 요청하지 않은 추가 분석은 하지 마세요.

## 출력 JSON 스키마
```json
{json_schema_str}
```

반드시 위 JSON 스키마 형식으로만 응답하세요. 확인되지 않은 정보는 null, 보완서류 사유는 구체적으로 기재하세요."""

    def analyze_documents(
        self,
        content: Union[str, list[Image.Image]],
        announcement_date: Optional[str] = None
    ) -> str:
        """
        문서 분석 실행
        
        Args:
            content: 분석할 텍스트 또는 이미지 리스트
            announcement_date: 공고일 (기본값: DEFAULT_ANNOUNCEMENT_DATE)
            
        Returns:
            JSON 형식의 분석 결과
        """
        if announcement_date is None:
            announcement_date = self.DEFAULT_ANNOUNCEMENT_DATE
            
        print(f">>> 공고일 기준: {announcement_date}")
        print(">>> Gemini 분석 시작...")
        
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_analysis_prompt(announcement_date)
        
        # 입력 타입에 따라 처리
        if isinstance(content, str):
            text = (content or "").strip()
            if not text:
                return json.dumps({
                    "error": "추출된 텍스트가 없습니다.",
                    "supplementary_documents": [],
                    "review_summary": "텍스트 추출 실패"
                }, ensure_ascii=False)
            
            full_prompt = f"{user_prompt}\n\n## 문서 텍스트:\n{text}"
            content_parts = [full_prompt]
            
        elif isinstance(content, list) and len(content) > 0 and isinstance(content[0], Image.Image):
            print(f">>> 이미지 모드: {len(content)}개 페이지 분석")
            content_parts = [user_prompt] + list(content)
            
        else:
            raise ValueError(f"지원하지 않는 입력 타입: {type(content)}")
        
        # JSON 응답 강제
        generation_config = genai.types.GenerationConfig(
            response_mime_type="application/json",
        )
        
        # 429 (Resource Exhausted) 재시도 (RPM 한도 완화용)
        max_retries = 5
        base_delay = 5
        max_delay = 90
        last_exc = None
        
        for attempt in range(max_retries):
            try:
                # 시스템 프롬프트 지원 여부에 따라 분기
                try:
                    model = genai.GenerativeModel(
                        self.settings.model_name,
                        system_instruction=system_prompt,
                    )
                    response = model.generate_content(
                        content_parts,
                        generation_config=generation_config,
                    )
                except TypeError:
                    # system_instruction 미지원 시
                    model = genai.GenerativeModel(self.settings.model_name)
                    role_prompt = f"[시스템 역할]\n{system_prompt}\n\n{content_parts[0]}"
                    if isinstance(content, list):
                        response = model.generate_content(
                            [role_prompt] + content_parts[1:],
                            generation_config=generation_config,
                        )
                    else:
                        response = model.generate_content(
                            role_prompt,
                            generation_config=generation_config,
                        )
                
                print(">>> Gemini 응답 수신 완료")
                return getattr(response, "text", str(response))
                
            except Exception as e:
                last_exc = e
                msg = (getattr(e, "message", "") or str(e)).lower()
                is_429 = "429" in msg or "resource exhausted" in msg or "rate limit" in msg
                if not is_429 or attempt == max_retries - 1:
                    print(f">>> Gemini API 오류: {repr(e)}")
                    raise
                delay = min(base_delay * (2 ** attempt), max_delay)
                print(f">>> [429 Rate limit] {delay}초 후 재시도 ({attempt + 1}/{max_retries})...")
                time.sleep(delay)
        
        if last_exc:
            raise last_exc

    def validate_result(self, json_result: str) -> tuple[bool, PublicHousingReviewResult | dict]:
        """
        분석 결과 유효성 검증
        
        Args:
            json_result: JSON 형식의 분석 결과
            
        Returns:
            (유효 여부, 파싱된 결과 또는 에러 정보)
        """
        try:
            data = json.loads(json_result)
            result = PublicHousingReviewResult(**data)
            return True, result
        except json.JSONDecodeError as e:
            return False, {"error": f"JSON 파싱 오류: {e}", "raw": json_result}
        except ValidationError as e:
            return False, {"error": f"데이터 검증 오류: {e}", "raw": json_result}


# =============================================================================
# 기존 GeminiClient와의 호환성을 위한 래퍼
# =============================================================================

class GeminiClient(PublicHousingGeminiClient):
    """
    기존 코드와의 호환성을 위한 래퍼 클래스
    
    analyze_document 메서드를 그대로 사용할 수 있음
    """
    
    def analyze_document(self, content: Union[str, list[Image.Image]]) -> str:
        """기존 인터페이스 호환용 메서드"""
        return self.analyze_documents(content)
