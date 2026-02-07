"""
공공임대 기존주택 매입심사 - 자가학습 시스템

기능:
1. 오류 케이스 로깅 및 저장
2. 사용자 피드백 학습
3. 필드별 추출 패턴 학습
4. 프롬프트 자동 개선
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, field, asdict


# 학습 데이터 저장 경로
LEARNING_DATA_DIR = Path("learning_data")
PATTERNS_FILE = LEARNING_DATA_DIR / "extraction_patterns.json"
FEEDBACK_FILE = LEARNING_DATA_DIR / "user_feedback.json"
ERROR_LOG_FILE = LEARNING_DATA_DIR / "error_log.json"


@dataclass
class ExtractionPattern:
    """필드 추출 패턴"""
    field_name: str                    # 필드명
    document_type: str                 # 서류 종류
    patterns: list[str]                # 인식 패턴들 (정규식)
    true_values: list[str]             # True로 판단할 값들
    false_values: list[str]            # False로 판단할 값들
    null_values: list[str]             # None으로 판단할 값들
    examples: list[dict] = field(default_factory=list)  # 실제 추출 예시


@dataclass
class UserFeedback:
    """사용자 피드백"""
    timestamp: str
    field_name: str
    ai_value: Any                      # AI가 추출한 값
    correct_value: Any                 # 사용자가 수정한 올바른 값
    document_type: str
    raw_text: Optional[str] = None     # 원본 텍스트 (있는 경우)


@dataclass
class ErrorLog:
    """오류 로그"""
    timestamp: str
    field_name: str
    error_type: str                    # hallucination, wrong_format, missing 등
    ai_value: Any
    expected_value: Optional[Any]
    context: Optional[str] = None      # 주변 텍스트


class LearningDatabase:
    """
    학습 데이터베이스
    
    - 추출 패턴 저장/로드
    - 사용자 피드백 저장/학습
    - 오류 로그 관리
    """
    
    def __init__(self):
        self._ensure_data_dir()
        self.patterns = self._load_patterns()
        self.feedback_history = self._load_feedback()
        self.error_logs = self._load_errors()
    
    def _ensure_data_dir(self):
        """데이터 디렉토리 생성"""
        LEARNING_DATA_DIR.mkdir(exist_ok=True)
    
    def _load_patterns(self) -> dict[str, ExtractionPattern]:
        """저장된 패턴 로드"""
        if PATTERNS_FILE.exists():
            try:
                with open(PATTERNS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return {k: ExtractionPattern(**v) for k, v in data.items()}
            except Exception:
                pass
        return self._get_default_patterns()
    
    def _save_patterns(self):
        """패턴 저장"""
        data = {k: asdict(v) for k, v in self.patterns.items()}
        with open(PATTERNS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def _load_feedback(self) -> list[UserFeedback]:
        """피드백 로드"""
        if FEEDBACK_FILE.exists():
            try:
                with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return [UserFeedback(**item) for item in data]
            except Exception:
                pass
        return []
    
    def _save_feedback(self):
        """피드백 저장"""
        data = [asdict(f) for f in self.feedback_history]
        with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def _load_errors(self) -> list[ErrorLog]:
        """오류 로그 로드"""
        if ERROR_LOG_FILE.exists():
            try:
                with open(ERROR_LOG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return [ErrorLog(**item) for item in data]
            except Exception:
                pass
        return []
    
    def _save_errors(self):
        """오류 로그 저장"""
        data = [asdict(e) for e in self.error_logs]
        with open(ERROR_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def _get_default_patterns(self) -> dict[str, ExtractionPattern]:
        """기본 추출 패턴 (초기값)"""
        return {
            # === 건축물대장 표제부 패턴 ===
            "seismic_design": ExtractionPattern(
                field_name="내진설계 적용 여부",
                document_type="건축물대장 표제부",
                patterns=[
                    r"내진.*?설계[^\n]*?(적용|해당|Y|예|O|○|있음)",
                    r"내진.*?설계[^\n]*?(미적용|해당없음|N|아니오|X|×|없음)",
                    r"내진[설계]?\s*[:：]?\s*(적용|미적용|해당|해당없음)",
                ],
                true_values=["적용", "해당", "Y", "예", "O", "○", "있음", "적용됨", "true", "True"],
                false_values=["미적용", "해당없음", "N", "아니오", "X", "×", "없음", "false", "False"],
                null_values=["", "-", "미확인", "확인불가", None],
                examples=[
                    {"raw": "내진설계적용여부: 적용", "value": True},
                    {"raw": "내진설계 : 적용", "value": True},
                    {"raw": "내진설계적용여부: 해당없음", "value": False},
                ]
            ),
            
            "has_basement": ExtractionPattern(
                field_name="지하층 유무",
                document_type="건축물대장 표제부",
                patterns=[
                    r"지하\s*(\d+)\s*층",
                    r"지하층\s*[:：]?\s*(\d+|없음|있음)",
                    r"층수[^\n]*?지하\s*(\d+)",
                ],
                true_values=["있음", "1", "2", "3", "4", "5"],  # 숫자가 있으면 True
                false_values=["없음", "0", "-", "해당없음"],
                null_values=["", "미확인", "확인불가", None],
                examples=[
                    {"raw": "지상5층 지하1층", "value": True},
                    {"raw": "지상3층", "value": False},  # 지하 언급 없으면 없는 것
                    {"raw": "층수: 지상 5층, 지하 없음", "value": False},
                ]
            ),
            
            "has_elevator": ExtractionPattern(
                field_name="승강기 설치 여부",
                document_type="건축물대장 표제부",
                patterns=[
                    r"승강기[^\n]*?(\d+|있음|없음|설치|미설치)",
                    r"엘리베이터[^\n]*?(\d+|있음|없음|설치|미설치)",
                    r"승강기\s*[:：]?\s*(\d+)\s*대",
                ],
                true_values=["있음", "설치", "1", "2", "3", "4", "5"],
                false_values=["없음", "미설치", "0", "-", "해당없음"],
                null_values=["", "미확인", "확인불가", None],
                examples=[
                    {"raw": "승강기: 2대", "value": True},
                    {"raw": "승강기 없음", "value": False},
                ]
            ),
            
            "outdoor_parking": ExtractionPattern(
                field_name="옥외 주차장 대수",
                document_type="건축물대장 표제부",
                patterns=[
                    r"옥외[^\n]*?(\d+)\s*대",
                    r"옥외주차[^\n]*?(\d+)",
                    r"주차장[^\n]*?옥외\s*(\d+)",
                ],
                true_values=[],
                false_values=[],
                null_values=["", "-", "미확인", None],
                examples=[
                    {"raw": "주차장: 옥외 10대, 옥내 5대", "value": 10},
                ]
            ),
            
            "indoor_parking": ExtractionPattern(
                field_name="옥내 주차장 대수",
                document_type="건축물대장 표제부",
                patterns=[
                    r"옥내[^\n]*?(\d+)\s*대",
                    r"옥내주차[^\n]*?(\d+)",
                    r"주차장[^\n]*?옥내\s*(\d+)",
                ],
                true_values=[],
                false_values=[],
                null_values=["", "-", "미확인", None],
                examples=[]
            ),
            
            "mechanical_parking": ExtractionPattern(
                field_name="기계식 주차장 대수",
                document_type="건축물대장 표제부",
                patterns=[
                    r"기계식[^\n]*?(\d+)\s*대",
                    r"기계[식]?주차[^\n]*?(\d+)",
                ],
                true_values=[],
                false_values=[],
                null_values=["", "-", "미확인", "없음", None],
                examples=[]
            ),
            
            # === 날짜 패턴 ===
            "approval_date": ExtractionPattern(
                field_name="사용승인일",
                document_type="건축물대장 표제부",
                patterns=[
                    r"사용승인일[^\n]*?(\d{4}[-./년]\s*\d{1,2}[-./월]\s*\d{1,2})",
                    r"사용승인[^\n]*?(\d{4}[-./년]\s*\d{1,2}[-./월]\s*\d{1,2})",
                ],
                true_values=[],
                false_values=[],
                null_values=["", "-", "미확인", None],
                examples=[
                    {"raw": "사용승인일: 2015.03.20", "value": "2015-03-20"},
                ]
            ),
        }
    
    def add_feedback(
        self, 
        field_name: str, 
        ai_value: Any, 
        correct_value: Any,
        document_type: str,
        raw_text: Optional[str] = None
    ):
        """사용자 피드백 추가 및 학습"""
        feedback = UserFeedback(
            timestamp=datetime.now().isoformat(),
            field_name=field_name,
            ai_value=ai_value,
            correct_value=correct_value,
            document_type=document_type,
            raw_text=raw_text
        )
        self.feedback_history.append(feedback)
        self._save_feedback()
        
        # 패턴 학습
        self._learn_from_feedback(feedback)
    
    def log_error(
        self,
        field_name: str,
        error_type: str,
        ai_value: Any,
        expected_value: Optional[Any] = None,
        context: Optional[str] = None
    ):
        """오류 로그 추가"""
        error = ErrorLog(
            timestamp=datetime.now().isoformat(),
            field_name=field_name,
            error_type=error_type,
            ai_value=ai_value,
            expected_value=expected_value,
            context=context
        )
        self.error_logs.append(error)
        self._save_errors()
    
    def _learn_from_feedback(self, feedback: UserFeedback):
        """피드백으로부터 패턴 학습"""
        field_key = self._get_field_key(feedback.field_name)
        
        if field_key not in self.patterns:
            # 새로운 필드에 대한 패턴 생성
            self.patterns[field_key] = ExtractionPattern(
                field_name=feedback.field_name,
                document_type=feedback.document_type,
                patterns=[],
                true_values=[],
                false_values=[],
                null_values=[],
                examples=[]
            )
        
        pattern = self.patterns[field_key]
        
        # 올바른 값을 패턴에 추가
        if feedback.correct_value is True:
            if feedback.raw_text and feedback.raw_text not in pattern.true_values:
                pattern.true_values.append(feedback.raw_text)
        elif feedback.correct_value is False:
            if feedback.raw_text and feedback.raw_text not in pattern.false_values:
                pattern.false_values.append(feedback.raw_text)
        
        # 예시 추가
        if feedback.raw_text:
            pattern.examples.append({
                "raw": feedback.raw_text,
                "value": feedback.correct_value,
                "ai_was": feedback.ai_value
            })
        
        self._save_patterns()
    
    def _get_field_key(self, field_name: str) -> str:
        """필드명을 키로 변환"""
        # 한글 필드명을 영문 키로 매핑
        mapping = {
            "내진설계": "seismic_design",
            "내진설계 적용 여부": "seismic_design",
            "지하층": "has_basement",
            "지하층 유무": "has_basement",
            "승강기": "has_elevator",
            "승강기 설치 여부": "has_elevator",
            "옥외 주차장": "outdoor_parking",
            "옥내 주차장": "indoor_parking",
            "기계식 주차장": "mechanical_parking",
            "사용승인일": "approval_date",
        }
        return mapping.get(field_name, field_name.lower().replace(" ", "_"))
    
    def get_pattern(self, field_name: str) -> Optional[ExtractionPattern]:
        """필드에 대한 패턴 가져오기"""
        key = self._get_field_key(field_name)
        return self.patterns.get(key)
    
    def get_learned_examples(self, field_name: str, limit: int = 5) -> list[dict]:
        """학습된 예시 가져오기 (Few-shot용)"""
        pattern = self.get_pattern(field_name)
        if pattern and pattern.examples:
            return pattern.examples[-limit:]  # 최근 예시
        return []
    
    def get_error_statistics(self) -> dict:
        """오류 통계"""
        stats = {
            "total_errors": len(self.error_logs),
            "by_field": {},
            "by_type": {},
            "recent_errors": []
        }
        
        for error in self.error_logs:
            # 필드별 집계
            if error.field_name not in stats["by_field"]:
                stats["by_field"][error.field_name] = 0
            stats["by_field"][error.field_name] += 1
            
            # 타입별 집계
            if error.error_type not in stats["by_type"]:
                stats["by_type"][error.error_type] = 0
            stats["by_type"][error.error_type] += 1
        
        # 최근 오류 5건
        stats["recent_errors"] = [asdict(e) for e in self.error_logs[-5:]]
        
        return stats


class PatternBasedExtractor:
    """
    패턴 기반 추출기
    
    Gemini 결과를 패턴으로 후처리하여 정확도 향상
    """
    
    def __init__(self, learning_db: LearningDatabase):
        self.db = learning_db
    
    def extract_boolean(
        self, 
        field_name: str, 
        raw_value: Any,
        context_text: Optional[str] = None
    ) -> tuple[Optional[bool], str]:
        """
        Boolean 값 추출
        
        Returns:
            (추출된 값, 신뢰도)
        """
        pattern = self.db.get_pattern(field_name)
        
        if pattern is None:
            return self._guess_boolean(raw_value), "low"
        
        # 문자열로 변환
        str_value = str(raw_value).strip() if raw_value is not None else ""
        
        # True 값 체크
        for true_val in pattern.true_values:
            if true_val.lower() == str_value.lower():
                return True, "high"
            if true_val.lower() in str_value.lower():
                return True, "medium"
        
        # False 값 체크
        for false_val in pattern.false_values:
            if false_val.lower() == str_value.lower():
                return False, "high"
            if false_val.lower() in str_value.lower():
                return False, "medium"
        
        # Null 값 체크
        for null_val in pattern.null_values:
            if null_val is not None and str_value.lower() == str(null_val).lower():
                return None, "high"
        
        # 컨텍스트 텍스트에서 패턴 매칭 시도
        if context_text:
            for regex in pattern.patterns:
                match = re.search(regex, context_text, re.IGNORECASE)
                if match:
                    matched_value = match.group(1) if match.groups() else match.group(0)
                    return self._interpret_matched_value(matched_value, pattern), "medium"
        
        # 추측
        return self._guess_boolean(raw_value), "low"
    
    def extract_number(
        self,
        field_name: str,
        raw_value: Any,
        context_text: Optional[str] = None
    ) -> tuple[Optional[int], str]:
        """
        숫자 값 추출
        
        Returns:
            (추출된 값, 신뢰도)
        """
        if raw_value is None:
            return None, "low"
        
        # 이미 숫자인 경우
        if isinstance(raw_value, (int, float)):
            return int(raw_value), "high"
        
        # 문자열에서 숫자 추출
        str_value = str(raw_value)
        numbers = re.findall(r'\d+', str_value)
        
        if numbers:
            return int(numbers[0]), "medium"
        
        # 없음, 0 등 처리
        if any(x in str_value.lower() for x in ["없음", "없", "-", "해당없음"]):
            return 0, "medium"
        
        return None, "low"
    
    def _guess_boolean(self, value: Any) -> Optional[bool]:
        """Boolean 추측"""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        
        str_val = str(value).lower().strip()
        
        if str_val in ["true", "1", "yes", "y", "적용", "있음", "해당", "o", "○"]:
            return True
        if str_val in ["false", "0", "no", "n", "미적용", "없음", "해당없음", "x", "×"]:
            return False
        
        return None
    
    def _interpret_matched_value(
        self, 
        matched: str, 
        pattern: ExtractionPattern
    ) -> Optional[bool]:
        """매칭된 값 해석"""
        matched_lower = matched.lower().strip()
        
        for true_val in pattern.true_values:
            if true_val.lower() in matched_lower or matched_lower in true_val.lower():
                return True
        
        for false_val in pattern.false_values:
            if false_val.lower() in matched_lower or matched_lower in false_val.lower():
                return False
        
        # 숫자가 있으면 True (예: 지하1층 → 지하층 있음)
        if re.search(r'\d+', matched) and int(re.search(r'\d+', matched).group()) > 0:
            return True
        
        return None


class ResultPostProcessor:
    """
    결과 후처리기
    
    Gemini 결과를 패턴 기반으로 교정
    """
    
    def __init__(self):
        self.learning_db = LearningDatabase()
        self.extractor = PatternBasedExtractor(self.learning_db)
        self.corrections_made = []
    
    def process(self, result: dict, raw_text: Optional[str] = None) -> dict:
        """
        결과 후처리
        
        Args:
            result: Gemini 분석 결과 (dict)
            raw_text: PDF에서 추출한 원본 텍스트
        
        Returns:
            교정된 결과
        """
        self.corrections_made = []
        
        # 건축물대장 표제부 필드 교정
        if "building_ledger_title" in result:
            bld = result["building_ledger_title"]
            
            # 내진설계
            if "seismic_design" in bld:
                original = bld["seismic_design"]
                corrected, confidence = self.extractor.extract_boolean(
                    "seismic_design", original, raw_text
                )
                if corrected != original and confidence != "low":
                    bld["seismic_design"] = corrected
                    self._log_correction("seismic_design", original, corrected, confidence)
            
            # 지하층
            if "has_basement" in bld:
                original = bld["has_basement"]
                corrected, confidence = self.extractor.extract_boolean(
                    "has_basement", original, raw_text
                )
                
                # 특별 규칙: 텍스트에 "지하" 언급이 없으면 False
                if raw_text and "지하" not in raw_text:
                    corrected = False
                    confidence = "high"
                
                if corrected != original:
                    bld["has_basement"] = corrected
                    self._log_correction("has_basement", original, corrected, confidence)
            
            # 승강기
            if "has_elevator" in bld:
                original = bld["has_elevator"]
                corrected, confidence = self.extractor.extract_boolean(
                    "has_elevator", original, raw_text
                )
                if corrected != original and confidence != "low":
                    bld["has_elevator"] = corrected
                    self._log_correction("has_elevator", original, corrected, confidence)
            
            # 주차장 대수
            for parking_field in ["outdoor_parking", "indoor_parking", "mechanical_parking"]:
                if parking_field in bld:
                    original = bld[parking_field]
                    corrected, confidence = self.extractor.extract_number(
                        parking_field, original, raw_text
                    )
                    if corrected != original and confidence != "low":
                        bld[parking_field] = corrected
                        self._log_correction(parking_field, original, corrected, confidence)
        
        return result
    
    def _log_correction(
        self, 
        field: str, 
        original: Any, 
        corrected: Any, 
        confidence: str
    ):
        """교정 내역 로깅"""
        self.corrections_made.append({
            "field": field,
            "original": original,
            "corrected": corrected,
            "confidence": confidence
        })
    
    def get_corrections_report(self) -> str:
        """교정 내역 리포트"""
        if not self.corrections_made:
            return ""
        
        lines = [
            "",
            "=" * 50,
            "🔧 AI 결과 자동 교정 내역",
            "=" * 50,
        ]
        
        for c in self.corrections_made:
            lines.append(f"• {c['field']}: {c['original']} → {c['corrected']} (신뢰도: {c['confidence']})")
        
        return "\n".join(lines)
    
    def submit_user_correction(
        self,
        field_name: str,
        ai_value: Any,
        correct_value: Any,
        document_type: str = "건축물대장 표제부",
        raw_text: Optional[str] = None
    ):
        """
        사용자 교정 제출 (학습용)
        
        사용자가 AI 결과를 수정했을 때 호출
        """
        self.learning_db.add_feedback(
            field_name=field_name,
            ai_value=ai_value,
            correct_value=correct_value,
            document_type=document_type,
            raw_text=raw_text
        )
        
        # 오류 로그에도 추가
        self.learning_db.log_error(
            field_name=field_name,
            error_type="user_correction",
            ai_value=ai_value,
            expected_value=correct_value,
            context=raw_text
        )
