"""
공공임대 기존주택 매입심사 - 1단계 매입제외 검증 엔진

이 엔진은 공고문(헌법) 기준으로 매입제외 여부를 최우선 판단합니다.
매입제외 대상이면 즉시 X 표시하고 서류 검토를 진행하지 않습니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any
from enum import Enum

from core.exclusion_rules import (
    ExclusionRule,
    ExclusionCategory,
    ExclusionSeverity,
    ExclusionCheckResult,
    AnnouncementConfig,
    AnnouncementConfigManager,
)


class ExclusionVerdict(str, Enum):
    """최종 판정"""
    EXCLUDED = "매입제외"           # ❌ 즉시 제외
    CONDITIONAL = "조건부_검토"     # ⚠️ 조건 충족 시 가능
    PASSED = "1단계_통과"           # ✅ 2단계 서류검증으로 진행


@dataclass
class HousingExclusionData:
    """
    매입제외 검증용 주택 데이터
    
    이 데이터는 AI 분석 또는 서류에서 추출됩니다.
    """
    # === 지리적 요건 ===
    # 토지이용계획
    is_redevelopment_zone: bool = False           # 재정비촉진지구
    is_maintenance_zone: bool = False             # 정비구역
    is_public_housing_zone: bool = False          # 공공주택지구
    is_housing_development_zone: bool = False     # 택지개발예정지구
    is_small_housing_redevelopment_zone: bool = False  # 소규모주택정비구역
    
    # 기반시설
    has_city_gas: Optional[bool] = None           # 도시가스 설치
    has_water_sewage: Optional[bool] = None       # 상하수도 설치
    
    # 인접 시설
    near_military_or_crematorium_500m: bool = False   # 군부대/화장장 500m
    near_harmful_facility_50m: bool = False           # 유해시설 50m
    near_gas_station_25m: bool = False                # 주유소 25m
    near_entertainment_25m: bool = False              # 숙박/위락시설 25m
    
    # 토지 상태
    is_landlocked: bool = False                   # 맹지
    has_access_road: bool = True                  # 진입도로 확보
    
    # === 주택 요건 ===
    has_basement_units: bool = False              # 지하/반지하 세대 존재
    meets_minimum_housing_standard: bool = True   # 최저주거기준 충족
    is_illegal_construction: bool = False         # 불법건축물
    
    # 등기부 권리관계
    has_seizure: bool = False                     # 압류/가압류
    has_auction: bool = False                     # 경매개시
    
    # 건물 상태
    exterior_material_compliant: bool = True      # 외벽 마감재 적합
    permit_date: Optional[str] = None             # 건축허가일
    has_seismic_design: Optional[bool] = None     # 내진설계 적용
    has_individual_meters: bool = True            # 세대별 계량기
    has_adequate_living_space: bool = True        # 주거공간 확보
    has_elevator: Optional[bool] = None           # 승강기 설치
    bathroom_pipes_embedded: bool = False         # 욕실배관 매립
    
    # 건물 유형
    is_officetel: bool = False                    # 오피스텔 여부
    is_unsold_apartment: bool = False             # 미분양 아파트
    is_urban_lifestyle_housing: bool = False      # 도시형생활주택
    is_jeonse_fraud_housing: bool = False         # 전세사기피해 주택
    
    # === 기타 요건 ===
    has_unresolved_rights: bool = False           # 권리관계 미해소
    is_lh_employee_or_family: bool = False        # LH 직원/가족
    has_fraud_sanction: bool = False              # 부정행위 제재
    exclusion_count: int = 0                      # 이전 제외 횟수
    
    # === 임대유형 ===
    housing_type: str = "일반"                    # 일반, 청년, 신혼신생아1, 신혼신생아2, 다자녀
    
    # === 면적 정보 ===
    exclusive_area: Optional[float] = None        # 전용면적
    total_units: Optional[int] = None             # 총 세대수


@dataclass
class ExclusionVerificationResult:
    """매입제외 검증 최종 결과"""
    verdict: ExclusionVerdict                     # 최종 판정
    total_rules_checked: int                      # 검증된 규칙 수
    excluded_count: int                           # 제외 해당 수
    conditional_count: int                        # 조건부 해당 수
    passed_count: int                             # 통과 수
    
    check_results: list[ExclusionCheckResult]     # 개별 검증 결과
    excluded_rules: list[ExclusionCheckResult]    # 제외 해당 규칙들
    conditional_rules: list[ExclusionCheckResult] # 조건부 해당 규칙들
    
    summary: str                                  # 요약
    recommendation: str                           # 권고사항


class ExclusionVerificationEngine:
    """
    1단계 매입제외 검증 엔진
    
    공고문(헌법) 기준으로 매입제외 여부를 최우선 판단
    """
    
    def __init__(self, config: Optional[AnnouncementConfig] = None):
        self.config_manager = AnnouncementConfigManager()
        
        if config:
            self.config = config
        else:
            # 기본 설정 로드 또는 생성
            self.config = self.config_manager.create_default_config("경기남부")
    
    def load_config(self, announcement_id: str) -> bool:
        """공고문 설정 로드"""
        config = self.config_manager.load_config(announcement_id)
        if config:
            self.config = config
            return True
        return False
    
    def verify(self, housing_data: HousingExclusionData) -> ExclusionVerificationResult:
        """
        매입제외 검증 실행
        
        Args:
            housing_data: 검증 대상 주택 데이터
        
        Returns:
            ExclusionVerificationResult: 검증 결과
        """
        check_results = []
        excluded_rules = []
        conditional_rules = []
        
        for rule in self.config.exclusion_rules:
            if not rule.is_active:
                continue
            
            result = self._check_rule(rule, housing_data)
            check_results.append(result)
            
            if result.is_excluded:
                if result.severity == ExclusionSeverity.ABSOLUTE:
                    excluded_rules.append(result)
                elif result.severity == ExclusionSeverity.CONDITIONAL:
                    conditional_rules.append(result)
        
        # 최종 판정
        if excluded_rules:
            verdict = ExclusionVerdict.EXCLUDED
            summary = f"❌ 매입제외: {len(excluded_rules)}개 절대 제외 요건 해당"
            recommendation = "매입 불가합니다. 제외 요건을 확인하세요."
        elif conditional_rules:
            verdict = ExclusionVerdict.CONDITIONAL
            summary = f"⚠️ 조건부: {len(conditional_rules)}개 조건부 요건 해당"
            recommendation = "조건 충족 시 매입 가능합니다. 예외 조건을 확인하세요."
        else:
            verdict = ExclusionVerdict.PASSED
            summary = "✅ 1단계 통과: 매입제외 요건 해당 없음"
            recommendation = "2단계 서류 검증을 진행하세요."
        
        return ExclusionVerificationResult(
            verdict=verdict,
            total_rules_checked=len(check_results),
            excluded_count=len(excluded_rules),
            conditional_count=len(conditional_rules),
            passed_count=len(check_results) - len(excluded_rules) - len(conditional_rules),
            check_results=check_results,
            excluded_rules=excluded_rules,
            conditional_rules=conditional_rules,
            summary=summary,
            recommendation=recommendation
        )
    
    def _check_rule(self, rule: ExclusionRule, data: HousingExclusionData) -> ExclusionCheckResult:
        """개별 규칙 검증"""
        
        # 필드 값 추출
        field_value = self._get_field_value(rule.check_field, data)
        
        # 조건 평가
        is_violated = self._evaluate_condition(
            field_value, 
            rule.check_condition, 
            data
        )
        
        # 결과 생성
        if is_violated:
            # 예외 조건 확인
            exception_applied = False
            if rule.exception_condition:
                # 예외 조건은 수동 확인 필요로 표시
                exception_applied = False
                requires_manual = True
            else:
                requires_manual = False
            
            return ExclusionCheckResult(
                rule_id=rule.rule_id,
                rule_description=rule.description,
                is_excluded=True,
                severity=rule.severity,
                reason=f"제외 요건 해당: {rule.sub_category}",
                exception_applied=exception_applied,
                requires_manual_check=requires_manual or rule.severity == ExclusionSeverity.CONDITIONAL,
                evidence=f"{rule.check_field} = {field_value}"
            )
        else:
            return ExclusionCheckResult(
                rule_id=rule.rule_id,
                rule_description=rule.description,
                is_excluded=False,
                severity=rule.severity,
                reason="해당 없음",
                evidence=f"{rule.check_field} = {field_value}"
            )
    
    def _get_field_value(self, field_path: str, data: HousingExclusionData) -> Any:
        """필드 경로에서 값 추출"""
        # 중첩 필드 지원 (예: "land_use_plan.is_redevelopment_zone")
        parts = field_path.split(".")
        
        # 간단한 매핑 (실제로는 더 복잡한 데이터 구조 지원 필요)
        field_mapping = {
            "land_use_plan.is_redevelopment_zone": data.is_redevelopment_zone,
            "land_use_plan.is_maintenance_zone": data.is_maintenance_zone,
            "land_use_plan.is_public_housing_zone": data.is_public_housing_zone,
            "land_use_plan.is_housing_development_zone": data.is_housing_development_zone,
            "land_use_plan.is_small_housing_redevelopment_zone": data.is_small_housing_redevelopment_zone,
            "utilities.has_city_gas": data.has_city_gas,
            "utilities.has_water_sewage": data.has_water_sewage,
            "location.near_military_or_crematorium_500m": data.near_military_or_crematorium_500m,
            "location.near_harmful_facility_50m": data.near_harmful_facility_50m,
            "location.near_gas_station_25m": data.near_gas_station_25m,
            "location.near_entertainment_25m": data.near_entertainment_25m,
            "land.is_landlocked": data.is_landlocked,
            "land.has_access_road": data.has_access_road,
            "building.has_basement_units": data.has_basement_units,
            "building.meets_minimum_housing_standard": data.meets_minimum_housing_standard,
            "building.is_illegal_construction": data.is_illegal_construction,
            "registry.has_seizure": data.has_seizure,
            "registry.has_auction": data.has_auction,
            "building.exterior_material_compliant": data.exterior_material_compliant,
            "building.has_seismic_design": data.has_seismic_design,
            "building.has_individual_meters": data.has_individual_meters,
            "building.has_adequate_living_space": data.has_adequate_living_space,
            "building.has_elevator": data.has_elevator,
            "building.bathroom_pipes_embedded": data.bathroom_pipes_embedded,
            "building.is_unsold_apartment": data.is_unsold_apartment,
            "building.is_jeonse_fraud_housing": data.is_jeonse_fraud_housing,
            "ownership.has_unresolved_rights": data.has_unresolved_rights,
            "applicant.is_lh_employee_or_family": data.is_lh_employee_or_family,
            "applicant.has_fraud_sanction": data.has_fraud_sanction,
            "history.exclusion_count": data.exclusion_count,
        }
        
        return field_mapping.get(field_path)
    
    def _evaluate_condition(self, value: Any, condition: str, data: HousingExclusionData) -> bool:
        """조건 평가"""
        if value is None:
            # 값이 없으면 조건 미충족으로 간주 (안전 측)
            return False
        
        # 단순 조건 평가
        try:
            # 복합 조건 먼저 처리 (예: "== True and housing_type == '다자녀'")
            # "and"를 나중에 보면 "==" split 시 3개로 쪼개져 unpack 오류 발생
            if " and " in condition:
                # 복합 조건 (예: "== True and housing_type == '다자녀'")
                parts = condition.split(" and ")
                results = []
                for part in parts:
                    part = part.strip()
                    if "housing_type" in part:
                        # 주택 유형 조건
                        expected_type = part.split("==")[1].strip().strip("'\"")
                        results.append(data.housing_type == expected_type)
                    elif "permit_date" in part:
                        # 허가일 조건
                        if ">=" in part:
                            expected_date = part.split(">=")[1].strip().strip("'\"")
                            if data.permit_date:
                                results.append(data.permit_date >= expected_date)
                            else:
                                results.append(False)
                        elif "<" in part:
                            expected_date = part.split("<")[1].strip().strip("'\"")
                            if data.permit_date:
                                results.append(data.permit_date < expected_date)
                            else:
                                results.append(False)
                    else:
                        # 기본 조건
                        if "== True" in part:
                            results.append(value == True)
                        elif "== False" in part:
                            results.append(value == False)
                
                return all(results) if results else False
            
            # 단순 조건: "== True", "== False", ">= 2" 등
            if "==" in condition:
                op, expected = condition.split("==", 1)
                expected = expected.strip()
                if expected == "True":
                    return value is True
                elif expected == "False":
                    return value is False
                else:
                    return str(value).strip() == expected.strip().strip("'\"")
            if ">=" in condition:
                op, expected = condition.split(">=", 1)
                return float(value) >= float(expected.strip())
            if ">" in condition and "<" not in condition:
                op, expected = condition.split(">", 1)
                return float(value) > float(expected.strip())
            if "<" in condition:
                op, expected = condition.split("<", 1)
                return float(value) < float(expected.strip())
            return value == True
                
        except Exception as e:
            print(f"조건 평가 오류: {condition}, 값: {value}, 오류: {e}")
            return False
    
    def format_result(self, result: ExclusionVerificationResult) -> str:
        """결과 포맷팅"""
        lines = []
        
        lines.append("=" * 70)
        lines.append("【 1단계: 매입제외 요건 검증 결과 】")
        lines.append("=" * 70)
        lines.append("")
        
        # 최종 판정
        if result.verdict == ExclusionVerdict.EXCLUDED:
            lines.append("┌" + "─" * 68 + "┐")
            lines.append("│" + " " * 20 + "❌ 매입제외 대상" + " " * 20 + "│")
            lines.append("└" + "─" * 68 + "┘")
        elif result.verdict == ExclusionVerdict.CONDITIONAL:
            lines.append("┌" + "─" * 68 + "┐")
            lines.append("│" + " " * 18 + "⚠️ 조건부 검토 필요" + " " * 18 + "│")
            lines.append("└" + "─" * 68 + "┘")
        else:
            lines.append("┌" + "─" * 68 + "┐")
            lines.append("│" + " " * 18 + "✅ 1단계 통과" + " " * 22 + "│")
            lines.append("└" + "─" * 68 + "┘")
        
        lines.append("")
        lines.append(f"📊 검증 요약: 총 {result.total_rules_checked}개 규칙 검토")
        lines.append(f"   - 절대 제외: {result.excluded_count}건")
        lines.append(f"   - 조건부: {result.conditional_count}건")
        lines.append(f"   - 통과: {result.passed_count}건")
        lines.append("")
        
        # 제외 사유
        if result.excluded_rules:
            lines.append("-" * 70)
            lines.append("❌ 절대 제외 사유 (매입 불가)")
            lines.append("-" * 70)
            for idx, rule in enumerate(result.excluded_rules, 1):
                lines.append(f"  {idx}. [{rule.rule_id}] {rule.rule_description}")
                lines.append(f"     → {rule.reason}")
            lines.append("")
        
        # 조건부 사유
        if result.conditional_rules:
            lines.append("-" * 70)
            lines.append("⚠️ 조건부 사유 (예외 조건 확인 필요)")
            lines.append("-" * 70)
            for idx, rule in enumerate(result.conditional_rules, 1):
                lines.append(f"  {idx}. [{rule.rule_id}] {rule.rule_description}")
                lines.append(f"     → {rule.reason}")
                # 예외 조건 표시
                original_rule = self._find_rule(rule.rule_id)
                if original_rule and original_rule.exception_condition:
                    lines.append(f"     💡 예외: {original_rule.exception_condition}")
            lines.append("")
        
        # 권고사항
        lines.append("-" * 70)
        lines.append(f"📋 권고사항: {result.recommendation}")
        lines.append("-" * 70)
        
        return "\n".join(lines)
    
    def _find_rule(self, rule_id: str) -> Optional[ExclusionRule]:
        """규칙 ID로 규칙 찾기"""
        for rule in self.config.exclusion_rules:
            if rule.rule_id == rule_id:
                return rule
        return None


# =============================================================================
# 편의 함수
# =============================================================================

def quick_exclusion_check(housing_data: HousingExclusionData) -> tuple[ExclusionVerdict, str]:
    """
    빠른 매입제외 검증
    
    Returns:
        (판정, 요약 메시지)
    """
    engine = ExclusionVerificationEngine()
    result = engine.verify(housing_data)
    return result.verdict, result.summary
