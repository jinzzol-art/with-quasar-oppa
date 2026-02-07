"""
공공임대 기존주택 매입심사 고도화 검증 시스템

목표: 99.99% 검증 정확성
- 이중 검증 (2회 분석 후 비교)
- 정규식 기반 값 검증
- 불확실 항목 자동 플래그
- 인감 일치율 45% 기준

Author: AI Document Verifier
Version: 2.0
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any


class ConfidenceLevel(str, Enum):
    """신뢰도 수준"""
    HIGH = "높음"           # 99% 이상 확신
    MEDIUM = "중간"         # 80~99% 확신
    LOW = "낮음"            # 60~80% 확신
    MANUAL_CHECK = "수동확인필요"  # 60% 미만, 반드시 사람이 확인


@dataclass
class ValidationItem:
    """개별 검증 항목 결과"""
    field_name: str                          # 필드명
    extracted_value: Optional[str]           # 추출된 값
    is_valid: bool                           # 유효 여부
    confidence: ConfidenceLevel              # 신뢰도
    validation_method: str                   # 검증 방법
    issues: list[str] = field(default_factory=list)  # 발견된 문제
    manual_check_reason: Optional[str] = None  # 수동확인 필요 사유


@dataclass  
class DualValidationResult:
    """이중 검증 결과"""
    field_name: str
    first_value: Optional[str]
    second_value: Optional[str]
    is_consistent: bool          # 두 결과 일치 여부
    final_value: Optional[str]   # 최종 채택 값
    confidence: ConfidenceLevel


class AdvancedValidator:
    """
    고도화 검증기
    
    99.99% 정확도를 위한 다층 검증 시스템
    """
    
    # 인감 일치율 기준
    SEAL_MATCH_THRESHOLD = 45.0
    
    # 정규식 패턴 정의
    PATTERNS = {
        # 날짜 패턴들
        "date_yyyy_mm_dd": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
        "date_yyyy_dot": re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}$"),
        "date_korean": re.compile(r"^\d{4}년\s*\d{1,2}월\s*\d{1,2}일$"),
        
        # 면적 패턴 (㎡, m², 제곱미터)
        "area": re.compile(r"^[\d,]+\.?\d*\s*(㎡|m²|m2|제곱미터)?$"),
        
        # 금액 패턴
        "amount": re.compile(r"^[\d,]+\s*(원|만원|억원)?$"),
        
        # 전화번호 패턴
        "phone": re.compile(r"^0\d{1,2}-?\d{3,4}-?\d{4}$"),
        
        # 이메일 패턴
        "email": re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"),
        
        # 주민등록번호 패턴 (앞자리만)
        "resident_id_front": re.compile(r"^\d{6}$"),
        
        # 사업자등록번호 패턴
        "business_id": re.compile(r"^\d{3}-\d{2}-\d{5}$"),
        
        # 호수 패턴
        "unit_number": re.compile(r"^\d{1,4}호?$"),
        
        # 층수 패턴
        "floor": re.compile(r"^(지하\s*)?\d{1,3}\s*층?$"),
    }
    
    # 전용면적 기준 (16㎡ 이상 85㎡ 이하)
    MIN_EXCLUSIVE_AREA = 16.0
    MAX_EXCLUSIVE_AREA = 85.0
    
    def __init__(self, announcement_date: str):
        """
        Args:
            announcement_date: 공고일 (YYYY-MM-DD)
        """
        self.announcement_date = datetime.strptime(announcement_date, "%Y-%m-%d").date()
        self.validation_results: list[ValidationItem] = []
        self.dual_results: list[DualValidationResult] = []
        self.manual_check_items: list[ValidationItem] = []
    
    # =========================================================================
    # 1. 정규식 기반 형식 검증
    # =========================================================================
    
    def validate_date_format(self, value: Optional[str], field_name: str) -> ValidationItem:
        """날짜 형식 검증"""
        if not value:
            return ValidationItem(
                field_name=field_name,
                extracted_value=None,
                is_valid=False,
                confidence=ConfidenceLevel.MANUAL_CHECK,
                validation_method="정규식",
                issues=["날짜 미기재"],
                manual_check_reason="날짜가 추출되지 않음"
            )
        
        value = value.strip()
        
        # 여러 날짜 형식 체크
        is_valid = any([
            self.PATTERNS["date_yyyy_mm_dd"].match(value),
            self.PATTERNS["date_yyyy_dot"].match(value),
            self.PATTERNS["date_korean"].match(value),
        ])
        
        if is_valid:
            return ValidationItem(
                field_name=field_name,
                extracted_value=value,
                is_valid=True,
                confidence=ConfidenceLevel.HIGH,
                validation_method="정규식"
            )
        else:
            return ValidationItem(
                field_name=field_name,
                extracted_value=value,
                is_valid=False,
                confidence=ConfidenceLevel.LOW,
                validation_method="정규식",
                issues=[f"날짜 형식 불명확: {value}"],
                manual_check_reason="날짜 형식이 표준과 다름"
            )
    
    def validate_date_after_announcement(
        self, 
        value: Optional[str], 
        field_name: str
    ) -> ValidationItem:
        """날짜가 공고일 이후인지 검증"""
        format_result = self.validate_date_format(value, field_name)
        
        if not format_result.is_valid or not value:
            return format_result
        
        # 날짜 파싱 시도
        parsed_date = self._parse_date(value)
        
        if parsed_date is None:
            return ValidationItem(
                field_name=field_name,
                extracted_value=value,
                is_valid=False,
                confidence=ConfidenceLevel.MANUAL_CHECK,
                validation_method="날짜비교",
                issues=["날짜 파싱 실패"],
                manual_check_reason=f"날짜 형식 파싱 불가: {value}"
            )
        
        if parsed_date >= self.announcement_date:
            return ValidationItem(
                field_name=field_name,
                extracted_value=value,
                is_valid=True,
                confidence=ConfidenceLevel.HIGH,
                validation_method="날짜비교",
                issues=[]
            )
        else:
            return ValidationItem(
                field_name=field_name,
                extracted_value=value,
                is_valid=False,
                confidence=ConfidenceLevel.HIGH,  # 확실히 틀림
                validation_method="날짜비교",
                issues=[f"공고일({self.announcement_date}) 이전 작성: {parsed_date}"]
            )
    
    def validate_area_format(self, value: Optional[str], field_name: str) -> ValidationItem:
        """면적 형식 검증"""
        if not value:
            return ValidationItem(
                field_name=field_name,
                extracted_value=None,
                is_valid=False,
                confidence=ConfidenceLevel.MANUAL_CHECK,
                validation_method="정규식",
                issues=["면적 미기재"],
                manual_check_reason="면적이 추출되지 않음"
            )
        
        value = str(value).strip()
        
        if self.PATTERNS["area"].match(value):
            return ValidationItem(
                field_name=field_name,
                extracted_value=value,
                is_valid=True,
                confidence=ConfidenceLevel.HIGH,
                validation_method="정규식"
            )
        else:
            return ValidationItem(
                field_name=field_name,
                extracted_value=value,
                is_valid=False,
                confidence=ConfidenceLevel.LOW,
                validation_method="정규식",
                issues=[f"면적 형식 불명확: {value}"],
                manual_check_reason="면적 형식이 표준과 다름"
            )
    
    def validate_exclusive_area_range(
        self, 
        value: Optional[str], 
        field_name: str
    ) -> ValidationItem:
        """전용면적 범위 검증 (16~85㎡)"""
        format_result = self.validate_area_format(value, field_name)
        
        if not value:
            return format_result
        
        # 숫자 추출
        area_value = self._extract_number(value)
        
        if area_value is None:
            return ValidationItem(
                field_name=field_name,
                extracted_value=value,
                is_valid=False,
                confidence=ConfidenceLevel.MANUAL_CHECK,
                validation_method="범위검증",
                issues=["면적 숫자 추출 실패"],
                manual_check_reason=f"면적 값 파싱 불가: {value}"
            )
        
        if self.MIN_EXCLUSIVE_AREA <= area_value <= self.MAX_EXCLUSIVE_AREA:
            return ValidationItem(
                field_name=field_name,
                extracted_value=value,
                is_valid=True,
                confidence=ConfidenceLevel.HIGH,
                validation_method="범위검증",
                issues=[]
            )
        else:
            return ValidationItem(
                field_name=field_name,
                extracted_value=value,
                is_valid=False,
                confidence=ConfidenceLevel.HIGH,
                validation_method="범위검증",
                issues=[f"전용면적 기준 미충족: {area_value}㎡ (기준: 16~85㎡)"]
            )
    
    def validate_phone_format(self, value: Optional[str], field_name: str) -> ValidationItem:
        """전화번호 형식 검증"""
        if not value:
            return ValidationItem(
                field_name=field_name,
                extracted_value=None,
                is_valid=False,
                confidence=ConfidenceLevel.MANUAL_CHECK,
                validation_method="정규식",
                issues=["전화번호 미기재"],
                manual_check_reason="전화번호가 추출되지 않음"
            )
        
        # 공백, 하이픈 정규화
        normalized = re.sub(r"[\s\-]", "", value.strip())
        
        if self.PATTERNS["phone"].match(normalized) or self.PATTERNS["phone"].match(value):
            return ValidationItem(
                field_name=field_name,
                extracted_value=value,
                is_valid=True,
                confidence=ConfidenceLevel.HIGH,
                validation_method="정규식"
            )
        else:
            return ValidationItem(
                field_name=field_name,
                extracted_value=value,
                is_valid=False,
                confidence=ConfidenceLevel.LOW,
                validation_method="정규식",
                issues=[f"전화번호 형식 불명확: {value}"],
                manual_check_reason="전화번호 형식 확인 필요"
            )
    
    def validate_email_format(self, value: Optional[str], field_name: str) -> ValidationItem:
        """이메일 형식 검증"""
        if not value:
            return ValidationItem(
                field_name=field_name,
                extracted_value=None,
                is_valid=False,
                confidence=ConfidenceLevel.MANUAL_CHECK,
                validation_method="정규식",
                issues=["이메일 미기재"],
                manual_check_reason="이메일이 추출되지 않음"
            )
        
        value = value.strip()
        
        if self.PATTERNS["email"].match(value):
            return ValidationItem(
                field_name=field_name,
                extracted_value=value,
                is_valid=True,
                confidence=ConfidenceLevel.HIGH,
                validation_method="정규식"
            )
        else:
            return ValidationItem(
                field_name=field_name,
                extracted_value=value,
                is_valid=False,
                confidence=ConfidenceLevel.LOW,
                validation_method="정규식",
                issues=[f"이메일 형식 불명확: {value}"],
                manual_check_reason="이메일 형식 확인 필요"
            )
    
    # =========================================================================
    # 2. 인감 일치율 검증 (45% 기준)
    # =========================================================================
    
    def validate_seal_match(
        self, 
        match_rate: Optional[float], 
        field_name: str
    ) -> ValidationItem:
        """인감 일치율 검증 (기준: 45%)"""
        if match_rate is None:
            return ValidationItem(
                field_name=field_name,
                extracted_value=None,
                is_valid=False,
                confidence=ConfidenceLevel.MANUAL_CHECK,
                validation_method="인감비교",
                issues=["인감 일치율 측정 불가"],
                manual_check_reason="인감 이미지 인식 실패"
            )
        
        if match_rate >= self.SEAL_MATCH_THRESHOLD:
            # 45% 이상: 정상
            confidence = ConfidenceLevel.HIGH if match_rate >= 70 else ConfidenceLevel.MEDIUM
            return ValidationItem(
                field_name=field_name,
                extracted_value=f"{match_rate:.1f}%",
                is_valid=True,
                confidence=confidence,
                validation_method="인감비교",
                issues=[]
            )
        elif match_rate >= 42:
            # 42~45%: 경계선 - 수동확인 권장
            return ValidationItem(
                field_name=field_name,
                extracted_value=f"{match_rate:.1f}%",
                is_valid=False,
                confidence=ConfidenceLevel.MANUAL_CHECK,
                validation_method="인감비교",
                issues=[f"인감 일치율 경계: {match_rate:.1f}% (기준: 45%)"],
                manual_check_reason="인감 일치율이 기준치 근처 - 육안 확인 필요"
            )
        else:
            # 45% 미만: 불일치
            return ValidationItem(
                field_name=field_name,
                extracted_value=f"{match_rate:.1f}%",
                is_valid=False,
                confidence=ConfidenceLevel.HIGH,
                validation_method="인감비교",
                issues=[f"인감 불일치: {match_rate:.1f}% (기준: 45%)"]
            )
    
    # =========================================================================
    # 3. 교차 검증 (여러 서류 간 값 비교)
    # =========================================================================
    
    def validate_cross_match(
        self,
        values: dict[str, Optional[str]],
        field_name: str,
        tolerance: float = 0.01  # 숫자 비교 시 오차 허용 (1%)
    ) -> ValidationItem:
        """
        교차 검증: 여러 서류의 같은 필드 값 비교
        
        Args:
            values: {"서류명": "값"} 딕셔너리
            field_name: 검증 필드명
            tolerance: 숫자 비교 시 허용 오차율
        """
        non_null_values = {k: v for k, v in values.items() if v is not None}
        
        if len(non_null_values) < 2:
            return ValidationItem(
                field_name=field_name,
                extracted_value=str(non_null_values),
                is_valid=False,
                confidence=ConfidenceLevel.MANUAL_CHECK,
                validation_method="교차검증",
                issues=["비교할 서류 부족"],
                manual_check_reason="2개 이상 서류에서 값 추출 필요"
            )
        
        # 숫자 값들 추출 시도
        numeric_values = {}
        for doc, val in non_null_values.items():
            num = self._extract_number(val)
            if num is not None:
                numeric_values[doc] = num
        
        # 숫자 비교 가능한 경우
        if len(numeric_values) >= 2:
            nums = list(numeric_values.values())
            base = nums[0]
            all_match = all(
                abs(n - base) / base <= tolerance if base > 0 else n == base
                for n in nums
            )
            
            if all_match:
                return ValidationItem(
                    field_name=field_name,
                    extracted_value=str(non_null_values),
                    is_valid=True,
                    confidence=ConfidenceLevel.HIGH,
                    validation_method="교차검증",
                    issues=[]
                )
            else:
                return ValidationItem(
                    field_name=field_name,
                    extracted_value=str(non_null_values),
                    is_valid=False,
                    confidence=ConfidenceLevel.HIGH,
                    validation_method="교차검증",
                    issues=[f"서류 간 값 불일치: {non_null_values}"]
                )
        
        # 문자열 비교
        str_values = list(non_null_values.values())
        normalized = [self._normalize_string(v) for v in str_values]
        
        if len(set(normalized)) == 1:
            return ValidationItem(
                field_name=field_name,
                extracted_value=str(non_null_values),
                is_valid=True,
                confidence=ConfidenceLevel.HIGH,
                validation_method="교차검증",
                issues=[]
            )
        else:
            return ValidationItem(
                field_name=field_name,
                extracted_value=str(non_null_values),
                is_valid=False,
                confidence=ConfidenceLevel.MEDIUM,
                validation_method="교차검증",
                issues=[f"서류 간 값 불일치: {non_null_values}"],
                manual_check_reason="값 불일치 - 어떤 서류가 정확한지 확인 필요"
            )
    
    # =========================================================================
    # 4. 이중 검증 (2회 분석 결과 비교)
    # =========================================================================
    
    def compare_dual_results(
        self,
        first_value: Optional[str],
        second_value: Optional[str],
        field_name: str
    ) -> DualValidationResult:
        """
        이중 검증: 같은 필드를 2번 추출한 결과 비교
        
        일치하면 높은 신뢰도, 불일치하면 수동확인 필요
        """
        if first_value is None and second_value is None:
            return DualValidationResult(
                field_name=field_name,
                first_value=None,
                second_value=None,
                is_consistent=True,  # 둘 다 없음 = 일관됨
                final_value=None,
                confidence=ConfidenceLevel.MANUAL_CHECK
            )
        
        if first_value is None or second_value is None:
            # 하나만 있음 - 불일치
            return DualValidationResult(
                field_name=field_name,
                first_value=first_value,
                second_value=second_value,
                is_consistent=False,
                final_value=first_value or second_value,
                confidence=ConfidenceLevel.LOW
            )
        
        # 정규화 후 비교
        norm1 = self._normalize_string(first_value)
        norm2 = self._normalize_string(second_value)
        
        if norm1 == norm2:
            return DualValidationResult(
                field_name=field_name,
                first_value=first_value,
                second_value=second_value,
                is_consistent=True,
                final_value=first_value,
                confidence=ConfidenceLevel.HIGH
            )
        
        # 숫자 비교 시도
        num1 = self._extract_number(first_value)
        num2 = self._extract_number(second_value)
        
        if num1 is not None and num2 is not None:
            if abs(num1 - num2) / max(num1, num2, 1) < 0.01:  # 1% 이내 오차
                return DualValidationResult(
                    field_name=field_name,
                    first_value=first_value,
                    second_value=second_value,
                    is_consistent=True,
                    final_value=first_value,
                    confidence=ConfidenceLevel.HIGH
                )
        
        # 불일치
        return DualValidationResult(
            field_name=field_name,
            first_value=first_value,
            second_value=second_value,
            is_consistent=False,
            final_value=None,
            confidence=ConfidenceLevel.MANUAL_CHECK
        )
    
    # =========================================================================
    # 5. 유틸리티 메서드
    # =========================================================================
    
    def _parse_date(self, value: str) -> Optional[datetime.date]:
        """다양한 형식의 날짜 파싱"""
        value = value.strip()
        
        formats = [
            "%Y-%m-%d",
            "%Y.%m.%d",
            "%Y. %m. %d",
            "%Y년 %m월 %d일",
            "%Y년%m월%d일",
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        
        # 숫자만 추출해서 시도
        numbers = re.findall(r"\d+", value)
        if len(numbers) >= 3:
            try:
                year = int(numbers[0])
                month = int(numbers[1])
                day = int(numbers[2])
                return datetime(year, month, day).date()
            except (ValueError, IndexError):
                pass
        
        return None
    
    def _extract_number(self, value: str) -> Optional[float]:
        """문자열에서 숫자 추출"""
        if not value:
            return None
        
        # 콤마 제거하고 숫자 추출
        cleaned = re.sub(r"[,\s]", "", str(value))
        match = re.search(r"[\d.]+", cleaned)
        
        if match:
            try:
                return float(match.group())
            except ValueError:
                pass
        
        return None
    
    def _normalize_string(self, value: str) -> str:
        """문자열 정규화 (비교용)"""
        if not value:
            return ""
        
        # 공백, 특수문자 제거, 소문자 변환
        normalized = re.sub(r"[\s\-_.,]", "", str(value).lower())
        return normalized
    
    # =========================================================================
    # 6. 종합 검증 리포트
    # =========================================================================
    
    def add_validation(self, item: ValidationItem) -> None:
        """검증 결과 추가"""
        self.validation_results.append(item)
        if item.confidence == ConfidenceLevel.MANUAL_CHECK:
            self.manual_check_items.append(item)
    
    def add_dual_validation(self, result: DualValidationResult) -> None:
        """이중 검증 결과 추가"""
        self.dual_results.append(result)
    
    def generate_report(self) -> dict:
        """종합 검증 리포트 생성"""
        total = len(self.validation_results)
        valid_count = sum(1 for v in self.validation_results if v.is_valid)
        manual_check_count = len(self.manual_check_items)
        
        high_confidence = sum(
            1 for v in self.validation_results 
            if v.confidence == ConfidenceLevel.HIGH
        )
        
        return {
            "summary": {
                "total_validations": total,
                "valid_count": valid_count,
                "invalid_count": total - valid_count,
                "manual_check_required": manual_check_count,
                "high_confidence_rate": f"{(high_confidence/total*100):.1f}%" if total > 0 else "N/A",
            },
            "manual_check_items": [
                {
                    "field": item.field_name,
                    "value": item.extracted_value,
                    "reason": item.manual_check_reason,
                    "issues": item.issues,
                }
                for item in self.manual_check_items
            ],
            "dual_validation_inconsistencies": [
                {
                    "field": r.field_name,
                    "first": r.first_value,
                    "second": r.second_value,
                }
                for r in self.dual_results if not r.is_consistent
            ],
            "all_validations": [
                {
                    "field": v.field_name,
                    "value": v.extracted_value,
                    "valid": v.is_valid,
                    "confidence": v.confidence.value,
                    "method": v.validation_method,
                    "issues": v.issues,
                }
                for v in self.validation_results
            ]
        }
