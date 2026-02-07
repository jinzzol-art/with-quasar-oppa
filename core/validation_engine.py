"""
공공임대 기존주택 매입심사 검증 엔진 (레거시).

실제 사용처: EnhancedValidator (enhanced_validation_engine.py).
본 모듈은 하위 호환용으로 유지. 신규 로직·규칙 수정은 EnhancedValidator에만 반영할 것.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from core.data_models import (
    DocumentStatus,
    PublicHousingReviewResult,
    SupplementaryDocument,
    ApplicantType,
    AgentType,
)
from core.verification_rules import RULES_LIST


@dataclass
class ValidationRule:
    """검증 규칙 정의"""
    rule_number: int
    rule_name: str
    description: str
    

class PublicHousingValidator:
    """
    공공임대 기존주택 매입심사 검증 엔진
    
    34개 검증 요구 조건을 순차적으로 적용하여 보완서류를 판정
    """
    
    # 검증 규칙 정의 (34개)
    RULES = [
        ValidationRule(num, name, desc) for num, name, desc in RULES_LIST
    ]
    
    def __init__(self, announcement_date: str, correction_date: Optional[str] = None):
        """
        Args:
            announcement_date: 기준 공고일 (YYYY-MM-DD)
            correction_date: 정정공고일 (있는 경우)
        """
        self.announcement_date = datetime.strptime(announcement_date, "%Y-%m-%d").date()
        self.correction_date = (
            datetime.strptime(correction_date, "%Y-%m-%d").date() 
            if correction_date else None
        )
        self.supplementary_docs: list[SupplementaryDocument] = []
    
    def _add_supplementary(self, doc_name: str, reason: str, rule_number: int):
        """보완서류 항목 추가"""
        self.supplementary_docs.append(SupplementaryDocument(
            document_name=doc_name,
            reason=reason,
            rule_number=rule_number
        ))
    
    def _check_date_validity(self, date_str: Optional[str]) -> bool:
        """날짜가 공고일 이후인지 확인"""
        if not date_str:
            return False
        try:
            doc_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            return doc_date >= self.announcement_date
        except ValueError:
            return False
    
    @staticmethod
    def _parse_approval_date_to_ymd(s: Optional[str]) -> Optional[tuple]:
        """사용승인일 문자열 → (년, 월, 일). 실패 시 None."""
        if not s or not isinstance(s, str):
            return None
        raw = s.strip()
        if not raw:
            return None
        for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y. %m. %d", "%y-%m-%d", "%y.%m.%d",
                    "%Y년 %m월 %d일", "%Y년%m월%d일"):
            try:
                d = datetime.strptime(raw[:24].strip(), fmt)
                return (d.year, d.month, d.day)
            except (ValueError, TypeError):
                continue
        m = re.match(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", raw)
        if m:
            try:
                y, mo, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 1 <= mo <= 12 and 1 <= day <= 31:
                    return (y, mo, day)
            except (ValueError, TypeError):
                pass
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 8:
            y, mo, day = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
            if 1 <= mo <= 12 and 1 <= day <= 31:
                return (y, mo, day)
        if len(digits) == 6:
            y, mo, day = int("20" + digits[:2]), int(digits[2:4]), int(digits[4:6])
            if 1 <= mo <= 12 and 1 <= day <= 31:
                return (y, mo, day)
        if len(digits) == 7:
            y = int(digits[:4])
            if digits[4] == "0":
                mo, day = int(digits[4:6]), int(digits[6])
            else:
                mo, day = int(digits[4]), int(digits[5:7])
            if 1 <= mo <= 12 and 1 <= day <= 31:
                return (y, mo, day)
        return None
    
    def validate(self, result: PublicHousingReviewResult) -> PublicHousingReviewResult:
        """
        34개 검증 요구 조건 적용
        
        Args:
            result: Gemini 분석 결과
            
        Returns:
            검증 완료된 결과 (보완서류 목록 포함)
        """
        self.supplementary_docs = []
        
        # === 규칙 1: 주택매도 신청서 존재 ===
        if not result.housing_sale_application.exists:
            self._add_supplementary("주택매도 신청서", "서류 미제출", 1)
        
        # === 규칙 2: 작성일자 유효성 ===
        if result.housing_sale_application.exists:
            if not self._check_date_validity(result.housing_sale_application.issue_date):
                self._add_supplementary(
                    "주택매도 신청서", 
                    f"작성일자가 공고일({self.announcement_date}) 이전", 
                    2
                )
        
        # === 규칙 3: 소유자 정보 완비 ===
        if result.housing_sale_application.exists:
            owner = result.housing_sale_application.owner_info
            if not owner.is_complete:
                missing = []
                if not owner.name: missing.append("성명")
                if not owner.birth_date: missing.append("생년월일")
                if not owner.address: missing.append("현거주지 주소")
                if not owner.phone: missing.append("휴대전화번호")
                if not owner.email: missing.append("이메일주소")
                self._add_supplementary(
                    "주택매도 신청서", 
                    f"소유자 정보 미기재: {', '.join(missing)}", 
                    3
                )
        
        # === 규칙 4: 인감 검증 (45% 이상) ===
        seal = result.housing_sale_application.seal_verification
        if result.housing_sale_application.exists and not seal.is_valid:
            if seal.match_rate is not None and seal.match_rate < 45:
                self._add_supplementary(
                    "주택매도 신청서", 
                    f"인감 일치율 부족 ({seal.match_rate}% < 45%)", 
                    4
                )
            elif not seal.certificate_exists:
                self._add_supplementary("본인발급용 인감증명서", "서류 미제출", 4)
        
        # === 규칙 5: 대리인 신분증 ===
        agent = result.housing_sale_application.agent_info
        if agent.exists and not agent.id_card_match:
            self._add_supplementary(
                "대리인신분증사본", 
                "대리인 이름 불일치 또는 미제출", 
                5
            )
        
        # === 규칙 6: 대지면적 일치 ===
        if not result.housing_sale_application.land_area_match:
            self._add_supplementary(
                "대지면적 불일치", 
                "주택매도신청서, 토지대장, 토지이용계획확인서 간 대지면적 불일치", 
                6
            )
        
        # === 규칙 7: 사용승인일 (주택매도 신청서 vs 건축물대장 표제부) ===
        # 이미 일치로 판정된 경우 재비교하지 않음
        already_matched = getattr(result.housing_sale_application, "approval_date_match", None)
        
        if already_matched is True:
            # 이미 일치로 판정됨 - 보완서류 추가 안함
            pass
        elif already_matched is False:
            # 명시적으로 불일치로 판정된 경우만 보완서류 추가
            self._add_supplementary("주택매도 신청서", "건물사용승인일이 건축물대장 표제부와 불일치", 7)
        else:
            # 아직 판정되지 않은 경우: 직접 비교
            app_ymd = self._parse_approval_date_to_ymd(getattr(result.housing_sale_application, "approval_date", None) or "")
            title_ymd = self._parse_approval_date_to_ymd(getattr(result.building_ledger_title, "approval_date", None) or "")
            if app_ymd is not None and title_ymd is not None:
                if app_ymd == title_ymd:
                    result.housing_sale_application.approval_date_match = True
                elif app_ymd[:2] == title_ymd[:2]:
                    # 연월만 같으면 일치로 간주
                    result.housing_sale_application.approval_date_match = True
                else:
                    self._add_supplementary("주택매도 신청서", "건물사용승인일이 건축물대장 표제부와 불일치", 7)
            else:
                # 한쪽이라도 파싱 실패 시: 일치로 간주
                result.housing_sale_application.approval_date_match = True
        
        # === 규칙 8: 전용면적 일치 (호별) ===
        if result.rental_status.mismatched_units:
            for unit in result.rental_status.mismatched_units:
                self._add_supplementary(
                    f"매도신청주택 임대현황 ({unit}호)", 
                    "전용면적이 건축물대장 전유부와 불일치", 
                    8
                )
        
        # === 규칙 9: 위임장 존재 (대리접수 시) ===
        if agent.exists and not result.power_of_attorney.exists:
            self._add_supplementary("위임장", "대리접수이나 위임장 미제출", 9)
        
        # === 규칙 10: 위임장 내용 ===
        poa = result.power_of_attorney
        if poa.exists and not poa.land_area_match:
            self._add_supplementary("위임장", "소재지 또는 대지면적 오류", 10)
        
        # === 규칙 11: 위임장 인적사항 ===
        if poa.exists:
            issues = []
            if not poa.delegator.personal_info_complete:
                issues.append("위임자 인적사항 불완전")
            if not poa.delegator.seal_valid:
                issues.append("위임자 인감 미날인/불일치")
            if not poa.delegatee.personal_info_complete:
                issues.append("수임자 인적사항 불완전")
            if not poa.delegatee.seal_valid:
                issues.append("수임자 인감 미날인/불일치")
            if not poa.is_after_announcement:
                issues.append(f"작성일이 공고일({self.announcement_date}) 이전")
            if issues:
                self._add_supplementary("위임장", "; ".join(issues), 11)
        
        # === 규칙 12: 소유자 인감증명서 ===
        if not result.owner_identity.seal_certificate.exists:
            self._add_supplementary("소유자 인감증명서", "서류 미제출", 12)
        
        # === 규칙 13, 14: 소유자 신분증 ===
        if not result.owner_identity.all_ids_submitted:
            self._add_supplementary(
                "소유자 신분증 사본", 
                f"소유자 {result.owner_identity.owner_count}명 중 일부 미제출", 
                14 if result.owner_identity.owner_count > 1 else 13
            )
        
        # === 규칙 15: 법인 필수서류 ===
        corp = result.corporate_documents
        if corp.is_corporation:
            if not corp.business_registration.exists:
                self._add_supplementary("법인용 사업자등록증", "서류 미제출", 15)
            if not corp.corporate_seal_certificate.exists:
                self._add_supplementary("법인용 인감증명서", "서류 미제출", 15)
            if not corp.corporate_registry.exists:
                self._add_supplementary("법인 등기사항전부증명서", "서류 미제출", 15)
            if not corp.all_executive_ids_submitted:
                self._add_supplementary(
                    "법인 임원 신분증", 
                    f"등기 임원 {corp.executive_count}명 중 일부 미제출", 
                    15
                )
        
        # === 규칙 16: 개인정보 동의서 ===
        consent = result.consent_form
        if not consent.exists:
            self._add_supplementary("개인정보 수집 이용 및 제공 동의서", "서류 미제출", 16)
        elif consent.exists:
            issues = []
            if not consent.owner_signed:
                issues.append("소유자 미작성")
            if not consent.owner_seal_valid:
                issues.append("소유자 인감 불일치")
            if not consent.owner_date_valid:
                issues.append("소유자 작성일자 오류")
            if agent.exists:
                if not consent.agent_signed:
                    issues.append("대리인 미작성")
                if not consent.agent_seal_valid:
                    issues.append("대리인 인감 불일치")
            if issues:
                self._add_supplementary(
                    "개인정보 수집 이용 및 제공 동의서", 
                    "; ".join(issues), 
                    16
                )
        
        # === 규칙 17: 법인 연간계약건수 동의서 ===
        if corp.is_corporation:
            if not corp.contract_limit_consent.exists:
                self._add_supplementary(
                    "연간 계약건수 상한 여부 검증용 개인정보 수집 이용동의서", 
                    "서류 미제출", 
                    17
                )
            elif not corp.all_executives_signed:
                self._add_supplementary(
                    "연간 계약건수 상한 여부 검증용 개인정보 수집 이용동의서", 
                    "일부 임원 자필서명 누락", 
                    17
                )
        
        # === 규칙 18: 공인중개사 서류 ===
        realtor = result.realtor_documents
        if realtor.is_realtor_agent:
            if not realtor.office_registration.exists:
                self._add_supplementary("중개사무소 등록증", "서류 미제출", 18)
            if not realtor.business_registration.exists:
                self._add_supplementary("중개사 사업자등록증", "서류 미제출", 18)
            if not realtor.seal_match_with_application:
                self._add_supplementary(
                    "중개사무소 등록증", 
                    "주택매도신청서와 인감 불일치", 
                    18
                )
        
        # === 규칙 19: 청렴서약서 ===
        pledge = result.integrity_pledge
        if not pledge.exists:
            self._add_supplementary("청렴서약서", "서류 미제출", 19)
        else:
            issues = []
            if not pledge.owner_submitted:
                issues.append("소유자 미작성")
            if not pledge.owner_seal_valid:
                issues.append("소유자 인감 불일치")
            if not pledge.owner_id_number_valid:
                issues.append("소유자 주민번호/사업자번호 오류")
            if not pledge.corporation_id_type_correct:
                issues.append("법인인데 주민등록번호 기재 (사업자등록번호 필요)")
            if agent.exists and not pledge.agent_submitted:
                issues.append("대리인 미작성")
            if realtor.is_realtor_agent and not pledge.realtor_submitted:
                issues.append("중개사 미작성")
            if issues:
                self._add_supplementary("청렴서약서", "; ".join(issues), 19)
        
        # === 규칙 20: 공사직원여부 확인서 ===
        lh_conf = result.lh_employee_confirmation
        if not lh_conf.exists:
            self._add_supplementary("공사직원여부 확인서", "서류 미제출", 20)
        elif not all([lh_conf.owner_name_match, lh_conf.seal_valid, lh_conf.date_valid]):
            issues = []
            if not lh_conf.owner_name_match:
                issues.append("소유자 이름 불일치")
            if not lh_conf.seal_valid:
                issues.append("인감 불일치")
            if not lh_conf.date_valid:
                issues.append("작성일자 오류")
            self._add_supplementary("공사직원여부 확인서", "; ".join(issues), 20)
        
        # === 규칙 21: 건축물대장 표제부 ===
        bld_summary = result.building_ledger_summary
        bld_title = result.building_ledger_title
        if bld_summary.required and not bld_summary.exists:
            self._add_supplementary("건축물대장 총괄표제부", "여러 동 건물이나 총괄표제부 미제출", 21)
        if not bld_title.exists:
            self._add_supplementary("건축물대장 표제부", "서류 미제출", 21)
        
        # === 규칙 22: 건축물대장 전유부 전용면적 ===
        bld_excl = result.building_ledger_exclusive
        if bld_excl.invalid_area_units:
            for unit in bld_excl.invalid_area_units:
                self._add_supplementary(
                    f"건축물대장 전유부 ({unit}호)", 
                    "전용면적이 16㎡ 미만 또는 85㎡ 초과", 
                    22
                )
        
        # === 규칙 23: 건축물현황도 ===
        layout = result.building_layout_plan
        if not layout.exists:
            self._add_supplementary("건축물현황도", "서류 미제출", 23)
        else:
            issues = []
            if not layout.has_site_plan:
                issues.append("배치도 누락")
            if not layout.has_all_floor_plans:
                issues.append(f"층별 평면도 누락: {', '.join(layout.missing_floors)}")
            if not layout.has_unit_plans:
                issues.append(f"호별 평면도 누락: {', '.join(layout.missing_units)}")
            if not layout.is_government_issued:
                issues.append("지자체 발급분이 아님 (건축사무소 도면)")
            if issues:
                self._add_supplementary("건축물현황도", "; ".join(issues), 23)
        
        # === 규칙 24: 토지대장 ===
        land = result.land_ledger
        if not land.exists:
            self._add_supplementary("토지대장", "서류 미제출", 24)
        else:
            issues = []
            if not land.is_after_announcement:
                issues.append(f"발급일이 공고일({self.announcement_date}) 이전")
            if not land.land_area_match:
                issues.append("대지면적 불일치")
            if not land.all_parcels_submitted:
                issues.append(f"필지 누락: {', '.join(land.missing_parcels)}")
            if issues:
                self._add_supplementary("토지대장", "; ".join(issues), 24)
        
        # === 규칙 25: 토지이용계획확인원 (필지 누락, 지구·지역 해당 시 보완서류) ===
        land_use = result.land_use_plan
        if not land_use.exists:
            self._add_supplementary("토지이용계획확인원", "서류 미제출", 25)
        else:
            if not land_use.all_parcels_submitted:
                self._add_supplementary(
                    "토지이용계획확인원", 
                    f"필지 누락: {', '.join(land_use.missing_parcels)}", 
                    25
                )
            zones = []
            if getattr(land_use, "is_redevelopment_zone", False):
                zones.append("재정비촉진지구")
            if getattr(land_use, "is_maintenance_zone", False):
                zones.append("정비구역")
            if getattr(land_use, "is_public_housing_zone", False):
                zones.append("공공주택지구")
            if getattr(land_use, "is_housing_development_zone", False):
                zones.append("택지개발지구")
            if zones:
                self._add_supplementary("토지이용계획확인원", f"제외 대상 구역 해당: {', '.join(zones)}", 25)
        
        # === 규칙 26: 토지 등기부등본 ===
        land_reg = result.land_registry
        if not land_reg.exists:
            self._add_supplementary("토지 등기부등본", "서류 미제출", 26)
        elif not land_reg.all_parcels_submitted:
            self._add_supplementary(
                "토지 등기부등본", 
                f"필지 누락: {', '.join(land_reg.missing_parcels)}", 
                26
            )
        
        # === 규칙 27: 건물 등기부등본 ===
        bld_reg = result.building_registry
        if not bld_reg.exists:
            self._add_supplementary("건물 등기부등본", "서류 미제출", 27)
        elif not bld_reg.all_units_submitted:
            self._add_supplementary(
                "건물 등기부등본", 
                f"호수 누락: {', '.join(bld_reg.missing_units)}", 
                27
            )
        
        # === 규칙 28: 신탁 서류 ===
        trust = result.trust_documents
        if trust.trust_required:
            if not trust.trust_contract.exists:
                self._add_supplementary("신탁원부계약서", "신탁 건물이나 서류 미제출", 28)
            if not trust.sale_authority_confirmation.exists:
                self._add_supplementary("신탁물건 매매 권한 확인서", "서류 미제출", 28)
            elif not trust.all_parties_signed or not trust.all_seals_valid:
                self._add_supplementary(
                    "신탁물건 매매 권한 확인서", 
                    "일부 관계인 서명/인감 누락", 
                    28
                )
        
        # === 최종 결과 집계 ===
        result.supplementary_documents = self.supplementary_docs
        result.supplementary_count = len(self.supplementary_docs)
        result.is_review_complete = (len(self.supplementary_docs) == 0)
        
        # 요약 생성
        if result.is_review_complete:
            result.review_summary = "모든 서류가 정상적으로 확인되었습니다. 심사 진행 가능합니다."
        else:
            result.review_summary = (
                f"총 {result.supplementary_count}건의 보완서류가 필요합니다. "
                f"상세 내역을 확인하고 보완 요청하세요."
            )
        
        return result
