"""
Vision API 공통 인터페이스 — Gemini / Claude Opus 선택 가능

- 429 한도에 걸리지 않도록 Claude Opus 4.5를 기본 옵션으로 사용 가능
- .env: GOOGLE_API_KEY (Gemini), ANTHROPIC_API_KEY (Claude)
"""
from __future__ import annotations

import base64
import io
import os
from abc import ABC, abstractmethod
from typing import List, Optional, Any

from pathlib import Path

from dotenv import load_dotenv
from core.api_rate_limiter import get_global_limiter

# 프로젝트 루트(.env 위치)에서 로드 — UI 등 다른 cwd에서 실행해도 적용
def _load_env_from_project_root() -> None:
    load_dotenv()
    for d in [Path(__file__).resolve().parent.parent, Path.cwd()]:
        env_path = d / ".env"
        if env_path.is_file():
            load_dotenv(env_path, override=False)
            break

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class VisionClientBase(ABC):
    """Vision API 공통 인터페이스: 프롬프트 + 이미지 → JSON 텍스트"""

    @abstractmethod
    def generate_json(self, prompt: str, images: List[Any]) -> str:
        """
        Args:
            prompt: JSON 형식으로 답하라고 지시한 프롬프트
            images: PIL Image 리스트 (0장 가능)
        Returns:
            응답 본문 텍스트 (JSON 문자열)
        """
        pass


class GeminiVisionClient(VisionClientBase):
    """Google Gemini Vision (기존)"""

    def __init__(self, api_key: Optional[str] = None, model_name: str = "gemini-2.0-flash"):
        load_dotenv()
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise RuntimeError(".env에 GOOGLE_API_KEY가 없습니다.")
        import google.generativeai as genai
        genai.configure(api_key=self.api_key, transport="rest")
        self.model = genai.GenerativeModel(model_name)
        self._genai = genai

    def generate_json(self, prompt: str, images: List[Any]) -> str:
        limiter = get_global_limiter()
        limiter.acquire()
        try:
            content = [prompt] + (images or [])
            config = self._genai.types.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1,
            )
            response = self.model.generate_content(content, generation_config=config)
            return getattr(response, "text", str(response))
        except Exception as e:
            err_msg = (getattr(e, "message", "") or str(e)).lower()
            if "429" in err_msg or "resource exhausted" in err_msg or "rate" in err_msg:
                limiter.report_rate_limit()
            raise
        finally:
            limiter.release()


class ClaudeVisionClient(VisionClientBase):
    """Anthropic Claude Vision — 429 한도 완화·고성능 (Opus 4.5 우선)"""

    OPUS_45_MODEL = "claude-opus-4-5-20251101"
    FALLBACK_MODELS = ("claude-sonnet-4-20250514", "claude-3-5-sonnet-20241022", "claude-3-opus-20240229")

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        load_dotenv()
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError(".env에 ANTHROPIC_API_KEY가 없습니다.")
        try:
            from anthropic import Anthropic
        except ImportError:
            raise RuntimeError("Claude 사용 시: pip install anthropic")
        self.client = Anthropic(api_key=self.api_key)
        self.model_name = (
            model_name
            or os.getenv("VISION_MODEL")
            or self.OPUS_45_MODEL
        )

    @staticmethod
    def _pil_to_base64(image: Any) -> str:
        if not HAS_PIL or image is None:
            return ""
        buf = io.BytesIO()
        # JPEG 사용 — PNG 대비 페이로드 3~5배 감소
        rgb = image.convert("RGB") if image.mode != "RGB" else image
        rgb.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    def generate_json(self, prompt: str, images: List[Any]) -> str:
        limiter = get_global_limiter()
        limiter.acquire()
        try:
            content: List[dict] = [{"type": "text", "text": prompt}]
            for img in images or []:
                b64 = self._pil_to_base64(img)
                if b64:
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64,
                        },
                    })
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=8192,
                messages=[{"role": "user", "content": content}],
            )
            if response.content and len(response.content) > 0:
                return getattr(response.content[0], "text", "") or ""
            return "{}"
        except Exception as e:
            err_msg = (getattr(e, "message", "") or str(e)).lower()
            if "429" in err_msg or "rate" in err_msg or "overloaded" in err_msg:
                limiter.report_rate_limit()
            raise
        finally:
            limiter.release()


def create_vision_client(
    provider: str = "claude",
    model_name: Optional[str] = None,
    api_key: Optional[str] = None,
) -> VisionClientBase:
    """
    provider: "gemini" | "claude"
    model_name: 미지정 시 claude=Opus 4.5, gemini=gemini-2.0-flash
    Claude 키 없으면 자동으로 Gemini(GOOGLE_API_KEY) 사용.
    """
    _load_env_from_project_root()
    provider = (provider or "claude").strip().lower()
    if provider == "claude":
        key = (api_key or os.getenv("ANTHROPIC_API_KEY") or "").strip()
        if not key:
            print("[Vision] Gemini 사용 (GOOGLE_API_KEY). Claude 쓰려면 .env에 ANTHROPIC_API_KEY 추가.")
            return GeminiVisionClient(api_key=os.getenv("GOOGLE_API_KEY"), model_name=model_name or "gemini-2.0-flash")
        return ClaudeVisionClient(api_key=key, model_name=model_name)
    if provider == "gemini":
        return GeminiVisionClient(api_key=api_key or os.getenv("GOOGLE_API_KEY"), model_name=model_name or "gemini-2.0-flash")
    raise ValueError(f"지원하지 않는 provider: {provider}. 'gemini' 또는 'claude' 사용.")
