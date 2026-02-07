"""
공공임대 기존주택 매입심사 - 2단계 통합 검증 시스템

검증 흐름:
┌─────────────────────────────────────────────────────┐
│  [1단계] 매입제외 요건 검증 (공고문 = 헌법)          │
│                                                     │
│  재정비촉진지구? 정비구역? 지하세대? 승강기?         │
│  내진설계? 압류? LH직원가족? ...                     │
│                                                     │
│  → 제외 대상? ──────────────> ❌ 즉시 반려           │
│         │                                           │
│         ↓ 통과                                      │
│                                                     │
│  [2단계] 서류 검증 (34개 요구 조건)                  │
│                                                     │
│  신청서, 위임장, 인감, 건축물대장, 등기부등본 ...     │
│                                                     │
│  → 보완 필요? ──────────────> ⚠️ 보완서류 목록       │
│         │                                           │
│         ↓ 모두 정상                                  │
│                                                     │
│  ✅ 심사 진행 가능                                   │
└─────────────────────────────────────────────────────┘
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from enum import Enum

from core.exclusion_rules import AnnouncementConfig, AnnouncementConfigManager
from core.exclusion_engine import (
    ExclusionVerificationEngine,
    ExclusionVerdict,
    ExclusionVerificationResult,
    HousingExclusionData,
)
from core.enhanced_validation_engine import EnhancedValidator
from core.data_models import PublicHousingReviewResult


class FinalVerdict(str, Enum):
    """최종 심사 결과"""
    EXCLUDED = "매입제외"           # ❌ 1단계에서 제외
    CONDITIONAL = "조건부_검토"     # ⚠️ 1단계 조건부 + 2단계 필요
    SUPPLEMENTARY = "보완필요"      # ⚠️ 1단계 통과, 2단계 보완필요
    APPROVED = "심사가능"           # ✅ 모두 통과


@dataclass
class IntegratedVerificationResult:
    """통합 검증 결과"""
    
    # 최종 판정
    final_verdict: FinalVerdict
    
    # 1단계 결과
    stage1_result: ExclusionVerificationResult
    stage1_passed: bool
    
    # 2단계 결과 (1단계 통과 시에만)
    stage2_result: Optional[PublicHousingReviewResult]
    stage2_passed: bool
    
    # 요약
    summary: str
    recommendation: str
    
    # 타임스탬프
    review_date: str


class IntegratedVerificationSystem:
    """
    통합 검증 시스템
    
    1단계 (매입제외) → 2단계 (서류검증) 순서로 진행
    """
    
    def __init__(self, announcement_config: Optional[AnnouncementConfig] = None):
        # 공고문 설정
        self.config_manager = AnnouncementConfigManager()
        
        if announcement_config:
            self.config = announcement_config
        else:
            self.config = self.config_manager.create_default_config("경기남부")
        
        # 1단계 엔진
        self.exclusion_engine = ExclusionVerificationEngine(self.config)
        
        # 2단계 엔진
        self.document_validator = EnhancedValidator(self.config.announcement_date)
    
    def verify(
        self,
        housing_data: HousingExclusionData,
        document_result: Optional[PublicHousingReviewResult] = None,
        skip_stage2_if_excluded: bool = True
    ) -> IntegratedVerificationResult:
        """
        통합 검증 실행
        
        Args:
            housing_data: 매입제외 검증용 데이터
            document_result: 2단계 서류 검증용 데이터 (AI 분석 결과)
            skip_stage2_if_excluded: 1단계 제외 시 2단계 생략
        
        Returns:
            IntegratedVerificationResult
        """
        review_date = datetime.now().strftime("%Y-%m-%d")
        
        # ===== 1단계: 매입제외 요건 검증 =====
        stage1_result = self.exclusion_engine.verify(housing_data)
        stage1_passed = stage1_result.verdict == ExclusionVerdict.PASSED
        
        # 1단계에서 절대 제외된 경우
        if stage1_result.verdict == ExclusionVerdict.EXCLUDED:
            return IntegratedVerificationResult(
                final_verdict=FinalVerdict.EXCLUDED,
                stage1_result=stage1_result,
                stage1_passed=False,
                stage2_result=None,
                stage2_passed=False,
                summary="❌ 매입제외 대상입니다.",
                recommendation="매입제외 요건에 해당하여 매입이 불가합니다. 서류 검토를 진행하지 않습니다.",
                review_date=review_date
            )
        
        # 1단계 조건부인 경우
        if stage1_result.verdict == ExclusionVerdict.CONDITIONAL:
            # 2단계도 진행하되, 최종 판정은 조건부
            stage2_result = None
            stage2_passed = False
            
            if document_result and not skip_stage2_if_excluded:
                stage2_result = self.document_validator.validate(document_result, None)
                stage2_passed = stage2_result.is_review_complete
            
            return IntegratedVerificationResult(
                final_verdict=FinalVerdict.CONDITIONAL,
                stage1_result=stage1_result,
                stage1_passed=False,
                stage2_result=stage2_result,
                stage2_passed=stage2_passed,
                summary="⚠️ 조건부 검토 대상입니다.",
                recommendation="매입제외 예외 조건 충족 여부를 먼저 확인하세요.",
                review_date=review_date
            )
        
        # ===== 2단계: 서류 검증 (1단계 통과 시) =====
        if document_result:
            stage2_result = self.document_validator.validate(document_result, None)
            stage2_passed = stage2_result.is_review_complete
            
            if stage2_passed:
                return IntegratedVerificationResult(
                    final_verdict=FinalVerdict.APPROVED,
                    stage1_result=stage1_result,
                    stage1_passed=True,
                    stage2_result=stage2_result,
                    stage2_passed=True,
                    summary="✅ 심사 진행 가능합니다.",
                    recommendation="모든 검증을 통과했습니다. 심사를 진행하세요.",
                    review_date=review_date
                )
            else:
                return IntegratedVerificationResult(
                    final_verdict=FinalVerdict.SUPPLEMENTARY,
                    stage1_result=stage1_result,
                    stage1_passed=True,
                    stage2_result=stage2_result,
                    stage2_passed=False,
                    summary=f"⚠️ 보완서류 {stage2_result.supplementary_count}건 필요",
                    recommendation="보완서류 제출 후 재검토가 필요합니다.",
                    review_date=review_date
                )
        else:
            # 서류 데이터 없음 - 1단계만 통과
            return IntegratedVerificationResult(
                final_verdict=FinalVerdict.APPROVED,  # 임시 통과
                stage1_result=stage1_result,
                stage1_passed=True,
                stage2_result=None,
                stage2_passed=False,
                summary="✅ 1단계 통과, 2단계 서류 검증 대기",
                recommendation="서류 분석 후 2단계 검증을 진행하세요.",
                review_date=review_date
            )
    
    def format_result(self, result: IntegratedVerificationResult) -> str:
        """결과 포맷팅"""
        lines = []
        
        # 헤더
        lines.append("=" * 70)
        lines.append("공공임대 기존주택 매입심사 종합 검증 결과")
        lines.append("=" * 70)
        lines.append(f"검토일자: {result.review_date}")
        lines.append(f"적용 공고: {self.config.title}")
        lines.append("")
        
        # 최종 판정 박스
        verdict_display = {
            FinalVerdict.EXCLUDED: ("❌ 매입제외", "red"),
            FinalVerdict.CONDITIONAL: ("⚠️ 조건부 검토", "yellow"),
            FinalVerdict.SUPPLEMENTARY: ("⚠️ 보완필요", "yellow"),
            FinalVerdict.APPROVED: ("✅ 심사가능", "green"),
        }
        
        verdict_text, _ = verdict_display[result.final_verdict]
        
        lines.append("┌" + "─" * 68 + "┐")
        lines.append("│" + f"  최종 판정: {verdict_text}".ljust(67) + "│")
        lines.append("└" + "─" * 68 + "┘")
        lines.append("")
        
        # 1단계 결과 요약
        lines.append("-" * 70)
        lines.append("【 1단계: 매입제외 요건 검증 】")
        lines.append("-" * 70)
        
        if result.stage1_passed:
            lines.append("✅ 통과 - 매입제외 요건 해당 없음")
        else:
            lines.append(f"결과: {result.stage1_result.summary}")
            
            if result.stage1_result.excluded_rules:
                lines.append("")
                lines.append("❌ 제외 사유:")
                for rule in result.stage1_result.excluded_rules:
                    lines.append(f"   • [{rule.rule_id}] {rule.rule_description}")
            
            if result.stage1_result.conditional_rules:
                lines.append("")
                lines.append("⚠️ 조건부 사유:")
                for rule in result.stage1_result.conditional_rules:
                    lines.append(f"   • [{rule.rule_id}] {rule.rule_description}")
        
        lines.append("")
        
        # 2단계 결과 요약
        if result.stage2_result:
            lines.append("-" * 70)
            lines.append("【 2단계: 서류 검증 】")
            lines.append("-" * 70)
            
            if result.stage2_passed:
                lines.append("✅ 통과 - 모든 서류 정상")
            else:
                lines.append(f"⚠️ 보완필요: {result.stage2_result.supplementary_count}건")
                lines.append("")
                lines.append("보완서류 목록:")
                for doc in result.stage2_result.supplementary_documents[:10]:  # 최대 10건
                    lines.append(f"   • {doc.document_name}: {doc.reason}")
                
                if len(result.stage2_result.supplementary_documents) > 10:
                    lines.append(f"   ... 외 {len(result.stage2_result.supplementary_documents) - 10}건")
        elif result.final_verdict == FinalVerdict.EXCLUDED:
            lines.append("-" * 70)
            lines.append("【 2단계: 서류 검증 】")
            lines.append("-" * 70)
            lines.append("⏭️ 생략 - 1단계에서 매입제외 판정")
        
        lines.append("")
        
        # 권고사항
        lines.append("-" * 70)
        lines.append(f"📋 권고사항: {result.recommendation}")
        lines.append("-" * 70)
        
        return "\n".join(lines)


# =============================================================================
# AI 분석 결과를 매입제외 데이터로 변환
# =============================================================================

def convert_ai_result_to_exclusion_data(
    ai_result: PublicHousingReviewResult,
    housing_type: str = "일반"
) -> HousingExclusionData:
    """
    AI 분석 결과(PublicHousingReviewResult)를 
    매입제외 검증용 데이터(HousingExclusionData)로 변환
    """
    data = HousingExclusionData()
    
    # 토지이용계획 (지구·지역 지정여부는 매입제외에 반영하지 않음)
    # is_redevelopment_zone, is_maintenance_zone, is_public_housing_zone, is_housing_development_zone 는
    # 토지이용계획확인원에서 채우지 않고 기본값 False 유지
    
    # 건축물대장 표제부
    if ai_result.building_ledger_title:
        blt = ai_result.building_ledger_title
        data.has_seismic_design = blt.seismic_design
        data.has_elevator = blt.has_elevator
        # 매입제외: 지하 세대(거주용 호)만 해당. 일반 지하층(주차장·창고 등)은 제외 사유 아님.
        # AI가 지하층 유무와 혼동해 true를 반환하는 경우가 있어, 매입제외 판단에서는 사용하지 않고 항상 False 처리.
        data.has_basement_units = False
    
    # 등기부등본
    if ai_result.building_registry:
        reg = ai_result.building_registry
        data.has_seizure = reg.has_seizure
        data.has_auction = getattr(reg, 'has_auction', False)
    
    # 임대유형
    data.housing_type = housing_type
    
    # 면적
    if ai_result.building_ledger_exclusive and ai_result.building_ledger_exclusive.units:
        areas = [u.exclusive_area for u in ai_result.building_ledger_exclusive.units 
                 if u.exclusive_area]
        if areas:
            data.exclusive_area = areas[0]
        data.total_units = len(ai_result.building_ledger_exclusive.units)
    
    return data
