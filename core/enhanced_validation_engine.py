"""
공공임대 기존주택 매입심사 - 고도화 검증 엔진 v3.2

수정사항:
- 인감 일치율 기준: 45% 이상
- 검토일자/발급일자 분리 처리
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
    DocumentDateInfo,
)


@dataclass
class EnhancedSupplementaryDocument:
    """강화된 보완서류 항목"""
    document_name: str
    reason: str
    rule_number: int
    confidence: str = "high"
    needs_manual_check: bool = False
    validation_details: Optional[str] = None


class EnhancedValidator:
    """
    고도화 검증 엔진 v3.2
    
    - 인감 일치율 기준: 45% 이상
    - 검토일자 자동 설정 (오늘)
    """
    
    # ★ 인감 일치율 기준: 45% 이상
    SEAL_MATCH_THRESHOLD = 45.0
    
    def __init__(self, announcement_date: str, correction_date: Optional[str] = None):
        self.announcement_date = datetime.strptime(announcement_date, "%Y-%m-%d").date()
        self.correction_date = (
            datetime.strptime(correction_date, "%Y-%m-%d").date() 
            if correction_date else None
        )
        self.supplementary_docs: list[EnhancedSupplementaryDocument] = []
        self.manual_check_items: list[dict] = []
    
    def _add_supplementary(self, doc_name: str, reason: str, rule_number: int,
                           confidence: str = "high", needs_manual_check: bool = False,
                           validation_details: Optional[str] = None):
        self.supplementary_docs.append(EnhancedSupplementaryDocument(
            document_name=doc_name,
            reason=reason,
            rule_number=rule_number,
            confidence=confidence,
            needs_manual_check=needs_manual_check,
            validation_details=validation_details
        ))
        
        if needs_manual_check:
            self.manual_check_items.append({
                "document": doc_name,
                "reason": reason,
                "rule": rule_number,
                "details": validation_details
            })
    
    def _check_date_validity(self, date_str: Optional[str]) -> tuple[bool, str]:
        """날짜가 공고일 이후인지 확인"""
        if not date_str:
            return False, "manual_check"
        
        try:
            # 다양한 날짜 형식 파싱
            for fmt in ["%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"]:
                try:
                    doc_date = datetime.strptime(date_str, fmt).date()
                    if doc_date >= self.announcement_date:
                        return True, "high"
                    else:
                        return False, "high"
                except ValueError:
                    continue
            return False, "low"
        except Exception:
            return False, "manual_check"
    
    @staticmethod
    def _parse_approval_date_to_ymd(s: Optional[str]) -> Optional[tuple]:
        """사용승인일 문자열 → (년, 월, 일). 파싱 실패 시 None. 비교는 이 튜플로만."""
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
    
    def validate(self, result: PublicHousingReviewResult, dual_report: Optional[dict] = None) -> PublicHousingReviewResult:
        """검증 실행"""
        self.supplementary_docs = []
        self.manual_check_items = []
        
        # 검토일자 설정 (오늘)
        result.review_date = datetime.now().strftime("%Y-%m-%d")
        
        # ============================================================
        # 🔥🔥🔥 법인 여부 자동 감지 (완전 강화!) 🔥🔥🔥
        # ============================================================
        corp = result.corporate_documents
        
        # 법인 키워드 목록 (한글 + 영문)
        CORP_KEYWORDS = [
            "건설", "법인", "주식회사", "(주)", "㈜", "유한회사", "합명회사", 
            "합자회사", "사단법인", "재단법인", "농협", "조합", "코퍼레이션",
            "개발", "산업", "부동산", "투자", "홀딩스", "그룹", "에셋", "종합",
            "엔지니어링", "건축", "토건", "주택", "디벨로퍼", "파트너스", "자산",
            "corporation", "corp", "inc", "ltd", "llc", "holdings", "company"
        ]
        
        # 방법 1: 법인 서류(사업자등록증, 법인등기, 법인인감증명서) 중 하나라도 있으면 법인
        if (corp.business_registration.exists or 
            corp.corporate_registry.exists or 
            corp.corporate_seal_certificate.exists):
            corp.is_corporation = True
            print(f"[법인 감지] 법인 서류 발견 - is_corporation=True 설정")
        
        # 방법 2: 소유자 이름에 법인 키워드가 있으면 법인
        owner_name = result.housing_sale_application.owner_info.name or ""
        if owner_name:
            owner_name_lower = owner_name.lower()
            for keyword in CORP_KEYWORDS:
                if keyword.lower() in owner_name_lower:
                    corp.is_corporation = True
                    print(f"[법인 감지] 소유자 이름에 '{keyword}' 포함: '{owner_name}' → is_corporation=True")
                    break
        
        # 방법 3: 법인인감증명서가 있으면 법인
        if corp.corporate_seal_certificate.exists:
            corp.is_corporation = True
            print(f"[법인 감지] 법인인감증명서 발견 - is_corporation=True 설정")
        
        # ★★★ 방법 4: 소유자 이름이 없어도, 다른 필드에서 법인 키워드 검색 ★★★
        # property_address, review_summary 등에서도 검색
        if not corp.is_corporation:
            # 물건 소재지에서 법인명 검색 (예: "XX건설 소유")
            prop_addr = result.property_address or ""
            review_summary = result.review_summary or ""
            applicant_display = result.applicant_type_display or ""
            
            search_texts = [prop_addr, review_summary, applicant_display]
            combined_text = " ".join(search_texts).lower()
            
            for keyword in CORP_KEYWORDS:
                if keyword.lower() in combined_text:
                    corp.is_corporation = True
                    print(f"[법인 감지] 기타 필드에서 '{keyword}' 발견 → is_corporation=True")
                    break
        
        # ★★★ 방법 5: 개인 생년월일이 없고, 소유자 이름이 2글자 초과면 법인 가능성 ★★★
        if not corp.is_corporation:
            owner = result.housing_sale_application.owner_info
            # 생년월일이 없고 (법인은 생년월일 없음)
            # 이름이 3글자 이상이면서 (법인명은 보통 긺)
            # 전화번호나 주소는 있으면 → 법인 가능성
            if owner_name and len(owner_name) >= 4 and not owner.birth_date:
                # 추가 체크: 이름 끝이 일반적인 법인 접미사인지
                corp_suffixes = ["건설", "개발", "산업", "부동산", "투자", "종합", "건축", "주택", "에셋"]
                for suffix in corp_suffixes:
                    if owner_name.endswith(suffix):
                        corp.is_corporation = True
                        print(f"[법인 감지] 이름 '{owner_name}'이 '{suffix}'로 끝남 → is_corporation=True")
                        break
        
        # 🔥 법인 감지 결과 로깅
        if corp.is_corporation:
            print(f"[법인 확정] ★★★ 법인 소유자로 처리됨 - 규칙3,4 개인정보 검증 제외 ★★★")
        else:
            print(f"[개인 확정] 개인 소유자로 처리됨 - 개인정보 검증 수행")
        
        # === 규칙 1: 주택매도 신청서 존재 ===
        if not result.housing_sale_application.exists:
            self._add_supplementary("주택매도 신청서", "서류 미제출", 1)
        
        # === 규칙 2: 작성일자 유효성 ===
        # 개선: 날짜가 추출되지 않은 경우에만 수동확인 필요로 처리 (있는데 없다고 하지 않음)
        if result.housing_sale_application.exists:
            written_date = result.housing_sale_application.written_date or result.housing_sale_application.issue_date
            if written_date and written_date.strip():
                # 날짜가 있으면 유효성 검사
                date_valid, confidence = self._check_date_validity(written_date)
                if not date_valid and confidence == "high":
                    # 명확히 공고일 이전인 경우만 오류
                    self._add_supplementary(
                        "주택매도 신청서",
                        f"작성일자가 공고일({self.announcement_date}) 이전",
                        2,
                        confidence,
                        needs_manual_check=False,
                        validation_details=f"서류상 작성일: {written_date}"
                    )
            else:
                # 날짜가 추출되지 않은 경우 수동확인
                self._add_supplementary(
                    "주택매도 신청서",
                    f"작성일자가 공고일({self.announcement_date}) 이전 또는 미확인",
                    2,
                    "manual_check",
                    needs_manual_check=True,
                    validation_details="작성일자 미추출 - 수동확인 필요"
                )
        
        # === 규칙 3: 소유자 정보 완비 ===
        # 개선: 법인일 경우 소유자 개인정보 검증 제외
        # 개인일 경우만 추출된 정보가 3개 이상인지 확인
        if result.housing_sale_application.exists:
            # 법인 여부 확인
            is_corporate = result.corporate_documents.is_corporation
            
            if not is_corporate:
                # 개인 소유자인 경우에만 개인정보 검증
                owner = result.housing_sale_application.owner_info
                extracted_count = sum([
                    bool(owner.name),
                    bool(owner.birth_date),
                    bool(owner.address),
                    bool(owner.phone),
                    bool(owner.email),
                ])
                
                if extracted_count == 0:
                    # 아무것도 추출되지 않음 - 수동확인 필요
                    self._add_supplementary(
                        "주택매도 신청서",
                        "소유자 정보 미기재: 성명·생년월일·주소·연락처·이메일 확인 필요",
                        3,
                        "manual_check",
                        needs_manual_check=True,
                        validation_details="소유자 정보가 전혀 추출되지 않음 - 스캔 품질 확인 또는 수동 입력 필요"
                    )
                elif extracted_count < 3:
                    # 일부만 추출됨 - 누락 항목 명시
                    missing = []
                    if not owner.name: missing.append("성명")
                    if not owner.birth_date: missing.append("생년월일")
                    if not owner.address: missing.append("주소")
                    if not owner.phone: missing.append("연락처")
                    if not owner.email: missing.append("이메일")
                    
                    if missing:
                        self._add_supplementary(
                            "주택매도 신청서",
                            f"소유자 정보 일부 미추출: {', '.join(missing)} [수동확인필요]",
                            3,
                            "medium",
                            needs_manual_check=True,
                            validation_details=f"추출된 정보: {extracted_count}/5개 - OCR 품질 문제 가능"
                        )
                # 3개 이상 추출되면 is_complete로 간주하고 오류 추가 안함
                else:
                    owner.is_complete = True
            # 법인인 경우 이 규칙을 건너뜀 (법인 서류로 검증)
        
        # === 규칙 4: 인감 검증 (45% 이상) ===
        # 개선: 법인일 경우 개인 인감증명서 검증 제외
        seal = result.housing_sale_application.seal_verification
        is_corporate = result.corporate_documents.is_corporation
        
        if result.housing_sale_application.exists and not is_corporate:
            # 개인 소유자인 경우에만 개인 인감 검증
            if seal.match_rate is not None:
                if seal.match_rate >= self.SEAL_MATCH_THRESHOLD:
                    seal.is_valid = True
                elif seal.match_rate >= 42:  # 42~45%: 경계선
                    self._add_supplementary(
                        "주택매도 신청서 인감",
                        f"인감 일치율 경계: {seal.match_rate:.1f}% (기준: {self.SEAL_MATCH_THRESHOLD}%)",
                        4,
                        "medium",
                        needs_manual_check=True,
                        validation_details="인감 일치율이 기준치 근처 - 육안 확인 권장"
                    )
                else:
                    self._add_supplementary(
                        "주택매도 신청서 인감",
                        f"인감 불일치: {seal.match_rate:.1f}% (기준: {self.SEAL_MATCH_THRESHOLD}%)",
                        4
                    )
            elif not seal.certificate_exists:
                self._add_supplementary("본인발급용 인감증명서", "서류 미제출", 4)
        # 법인인 경우 개인 인감 검증 건너뜀 (법인인감증명서는 규칙15에서 검증)
        
        # === 규칙 5: 대리인 신분증 ===
        agent = result.housing_sale_application.agent_info
        if agent.exists and not agent.id_card_match:
            self._add_supplementary("대리인신분증사본", "대리인 이름 불일치 또는 미제출", 5)
        
        # === 규칙 6: 대지면적 일치 (세 값이 모두 있고 실제로 다를 때만 불일치로 처리) ===
        la_app = getattr(result.housing_sale_application, "land_area", None)
        la_land = getattr(result.land_ledger, "land_area", None)
        la_plan = getattr(result.land_use_plan, "land_area", None)
        try:
            fa, fl, fp = float(la_app) if la_app is not None else None, float(la_land) if la_land is not None else None, float(la_plan) if la_plan is not None else None
        except (TypeError, ValueError):
            fa, fl, fp = la_app, la_land, la_plan
        vals = [v for v in (fa, fl, fp) if v is not None]
        if len(vals) >= 2:
            tol = 0.1
            if not all(abs(vals[0] - v) <= tol for v in vals) and not result.housing_sale_application.land_area_match:
                self._add_supplementary(
                    "대지면적 불일치",
                    "주택매도신청서, 토지대장, 토지이용계획확인서 간 대지면적 불일치",
                    6
                )
        
        # === 규칙 7: 사용승인일 (주택매도 신청서 vs 건축물대장 표제부) ===
        # 이미 unified_pdf_analyzer에서 일치로 판정된 경우 재비교하지 않음
        already_matched = getattr(result.housing_sale_application, "approval_date_match", None)
        
        if already_matched is True:
            # 이미 일치로 판정됨 - 보완서류 추가 안함
            print(f"    [규칙7] 사용승인일: 이미 일치로 판정됨 (재검사 생략)")
        elif already_matched is False:
            # 명시적으로 불일치로 판정된 경우만 보완서류 추가
            self._add_supplementary(
                "주택매도 신청서",
                "건물사용승인일이 건축물대장 표제부와 불일치",
                7
            )
            print(f"    [규칙7] 사용승인일: 명시적 불일치 판정")
        else:
            # 아직 판정되지 않은 경우: 직접 비교 (년,월,일 튜플로)
            app_ymd = self._parse_approval_date_to_ymd(
                getattr(result.housing_sale_application, "approval_date", None) or ""
            )
            title_ymd = self._parse_approval_date_to_ymd(
                getattr(result.building_ledger_title, "approval_date", None) or ""
            )
            print(f"    [규칙7] 사용승인일 비교: 신청서={app_ymd}, 표제부={title_ymd}")
            
            if app_ymd is not None and title_ymd is not None:
                # 둘 다 파싱 성공 시 비교
                if app_ymd == title_ymd:
                    result.housing_sale_application.approval_date_match = True
                    print(f"    [규칙7] → 완전 일치")
                elif app_ymd[:2] == title_ymd[:2]:
                    # 연월만 같으면 일치로 간주 (일자 오타 허용)
                    result.housing_sale_application.approval_date_match = True
                    print(f"    [규칙7] → 연월 일치 (일자 차이 허용)")
                else:
                    # 실제로 다른 날짜일 때만 불일치
                    self._add_supplementary(
                        "주택매도 신청서",
                        "건물사용승인일이 건축물대장 표제부와 불일치",
                        7
                    )
                    print(f"    [규칙7] → 불일치 (보완서류 추가)")
            else:
                # 한쪽이라도 파싱 실패 시: 일치로 간주 (오탐 방지)
                result.housing_sale_application.approval_date_match = True
                print(f"    [규칙7] → 날짜 미추출, 일치로 간주")
        
        # === 규칙 8: 전용면적 일치 ===
        if result.rental_status.mismatched_units:
            for unit in result.rental_status.mismatched_units:
                self._add_supplementary(
                    f"매도신청주택 임대현황 ({unit}호)",
                    "전용면적이 건축물대장 전유부와 불일치",
                    8
                )
        
        # === 규칙 9: 위임장 존재 ===
        if agent.exists and not result.power_of_attorney.exists:
            self._add_supplementary("위임장", "대리접수이나 위임장 미제출", 9)
        
        # === 규칙 10: 위임장 내용 (대지면적 값이 있을 때만 비교) ===
        poa = result.power_of_attorney
        if poa.exists and poa.land_area is not None and getattr(result.housing_sale_application, "land_area", None) is not None and not poa.land_area_match:
            self._add_supplementary("위임장", "소재지 또는 대지면적 오류", 10)
        
        # === 규칙 11: 위임장 인적사항 (추출된 항목만 검사, 있는 건 있는 것으로) ===
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
            if poa.written_date and not poa.is_after_announcement:
                issues.append(f"작성일이 공고일({self.announcement_date}) 이전")
            if issues:
                self._add_supplementary("위임장", "; ".join(issues), 11)
        
        # === 규칙 12~14: 신분증/인감증명서 ===
        # 개선: 법인인 경우 개인 인감증명서 검증 건너뜀
        corp = result.corporate_documents
        is_corporate = corp.is_corporation
        
        if not is_corporate:
            # 개인 소유자인 경우에만 개인 인감증명서 검증
            if not result.owner_identity.seal_certificate.exists:
                self._add_supplementary("소유자 인감증명서", "서류 미제출", 12)
            
            if not result.owner_identity.all_ids_submitted:
                self._add_supplementary(
                    "소유자 신분증 사본",
                    f"소유자 {result.owner_identity.owner_count}명 중 일부 미제출",
                    14 if result.owner_identity.owner_count > 1 else 13
                )
        
        # === 규칙 15, 17: 법인 관련 ===
        if corp.is_corporation:
            result.applicant_type = ApplicantType.CORPORATION
            if not result.applicant_type_display and result.housing_sale_application.owner_info.name:
                result.applicant_type_display = result.housing_sale_application.owner_info.name.strip()
            if not corp.business_registration.exists:
                self._add_supplementary("법인용 사업자등록증", "서류 미제출", 15)
            if not corp.corporate_seal_certificate.exists:
                self._add_supplementary("법인용 인감증명서", "서류 미제출", 15)
            if not corp.corporate_registry.exists:
                self._add_supplementary("법인 등기사항전부증명서", "서류 미제출", 15)
            if not corp.all_executive_ids_submitted:
                self._add_supplementary("법인 임원 신분증", f"등기 임원 {corp.executive_count}명 중 일부 미제출", 15)
            
            if not corp.contract_limit_consent.exists:
                self._add_supplementary("연간 계약건수 상한 검증용 동의서", "서류 미제출", 17)
            elif not corp.all_executives_signed:
                self._add_supplementary("연간 계약건수 상한 검증용 동의서", "일부 임원 자필서명 누락", 17)
        
        # === 규칙 16: 개인정보 동의서 ===
        consent = result.consent_form
        if not consent.exists:
            self._add_supplementary("개인정보 수집 이용 및 제공 동의서", "서류 미제출", 16)
        else:
            issues = []
            if not consent.owner_signed: issues.append("소유자 미작성")
            if not consent.owner_seal_valid: issues.append("소유자 인감 불일치")
            if not consent.owner_date_valid: issues.append("소유자 작성일자 오류")
            if agent.exists:
                if not consent.agent_signed: issues.append("대리인 미작성")
                if not consent.agent_seal_valid: issues.append("대리인 인감 불일치")
            if issues:
                self._add_supplementary("개인정보 수집 이용 및 제공 동의서", "; ".join(issues), 16)
        
        # === 규칙 18: 공인중개사 서류 ===
        realtor = result.realtor_documents
        if realtor.is_realtor_agent:
            if not realtor.office_registration.exists:
                self._add_supplementary("중개사무소 등록증", "서류 미제출", 18)
            if not realtor.business_registration.exists:
                self._add_supplementary("중개사 사업자등록증", "서류 미제출", 18)
            if not realtor.seal_match_with_application:
                self._add_supplementary("중개사무소 등록증", "주택매도신청서와 인감 불일치", 18)
        
        # === 규칙 19: 청렴서약서 ===
        pledge = result.integrity_pledge
        if not pledge.exists:
            self._add_supplementary("청렴서약서", "서류 미제출", 19)
        else:
            issues = []
            if not pledge.owner_submitted: issues.append("소유자 미작성")
            if not pledge.owner_seal_valid: issues.append("소유자 인감 불일치")
            if not pledge.owner_id_number_valid: issues.append("소유자 주민번호/사업자번호 오류")
            if not pledge.corporation_id_type_correct: issues.append("법인인데 주민등록번호 기재")
            if agent.exists and not pledge.agent_submitted: issues.append("대리인 미작성")
            if realtor.is_realtor_agent and not pledge.realtor_submitted: issues.append("중개사 미작성")
            if issues:
                self._add_supplementary("청렴서약서", "; ".join(issues), 19)
        
        # === 규칙 20: 공사직원여부 확인서 ===
        # 개선: 문서가 있으면 기본적으로 유효하게 처리. 명시적으로 false인 경우만 오류
        lh_conf = result.lh_employee_confirmation
        if not lh_conf.exists:
            self._add_supplementary("공사직원여부 확인서", "서류 미제출", 20)
        else:
            issues = []
            # owner_name_match: 신청서의 소유자 이름이 있고, 확인서의 이름이 명시적으로 다른 경우만 불일치
            app_owner_name = result.housing_sale_application.owner_info.name
            lh_owner_name = getattr(lh_conf, "_extracted_owner_name", None)  # 추출된 원본 이름
            if app_owner_name and lh_owner_name and lh_conf.owner_name_match is False:
                issues.append("소유자 이름 불일치")
            # 이름 비교 불가능한 경우는 일치로 간주 (기본값 True 유지)
            
            # seal_valid: 명시적으로 도장이 없다고 판단된 경우만 오류
            if lh_conf.seal_valid is False and hasattr(lh_conf, '_explicit_seal_check') and lh_conf._explicit_seal_check:
                issues.append("인감 불일치")
            
            # date_valid: 날짜가 추출되고 공고일 이전인 경우만 오류
            if lh_conf.written_date and lh_conf.date_valid is False:
                issues.append("작성일자 오류")
            elif not lh_conf.written_date:
                # 날짜 미추출 - 수동확인 권장 (오류는 아님)
                pass
            
            if issues:
                self._add_supplementary("공사직원여부 확인서", "; ".join(issues), 20)
        
        # === 규칙 21: 건축물대장 표제부 ===
        if result.building_ledger_summary.required and not result.building_ledger_summary.exists:
            self._add_supplementary("건축물대장 총괄표제부", "여러 동 건물이나 총괄표제부 미제출", 21)
        if not result.building_ledger_title.exists:
            self._add_supplementary("건축물대장 표제부", "서류 미제출", 21)
        
        # === 규칙 22: 전용면적 범위 ===
        if result.building_ledger_exclusive.invalid_area_units:
            for unit in result.building_ledger_exclusive.invalid_area_units:
                self._add_supplementary(f"건축물대장 전유부 ({unit}호)", "전용면적이 16㎡ 미만 또는 85㎡ 초과", 22)
        
        # === 규칙 23: 건축물현황도 (문서 있으면 배치/층별/호별/지자체는 있는 것으로 간주. 명시적 false만 누락 처리) ===
        layout = result.building_layout_plan
        if not layout.exists:
            self._add_supplementary("건축물현황도", "서류 미제출", 23)
        # 문서 있으면 has_* 는 기본 true로 적용되므로, 여기서 추가 보완서류는 하지 않음(있는데 누락이라고 하지 않음)
        
        # === 규칙 24: 토지대장 (필지 누락은 명시적 증거 있을 때만: missing_parcels 또는 total != submitted) ===
        land = result.land_ledger
        if not land.exists:
            self._add_supplementary("토지대장", "서류 미제출", 24)
        else:
            issues = []
            if not land.is_after_announcement and getattr(land, "issue_date", None):
                issues.append(f"발급일이 공고일({self.announcement_date}) 이전")
            missing_parcels = getattr(land, "missing_parcels", []) or []
            total_p = getattr(land, "total_parcels", 0) or 0
            submitted_p = getattr(land, "submitted_parcels", 0) or 0
            if not land.all_parcels_submitted and (missing_parcels or (total_p and submitted_p and total_p != submitted_p)):
                issues.append("필지 누락")
            if issues:
                self._add_supplementary("토지대장", "; ".join(issues), 24)
        
        # === 규칙 25: 토지이용계획확인원 (필지 누락, 지구·지역 해당 시 보완서류) ===
        land_use = result.land_use_plan
        if not land_use.exists:
            self._add_supplementary("토지이용계획확인원", "서류 미제출", 25)
        else:
            missing_p = getattr(land_use, "missing_parcels", []) or []
            tp = getattr(land_use, "total_parcels", 0) or 0
            sp = getattr(land_use, "submitted_parcels", 0) or 0
            if not land_use.all_parcels_submitted and (missing_p or (tp and sp and tp != sp)):
                self._add_supplementary("토지이용계획확인원", "필지 누락", 25)
            # 재정비촉진지구·정비구역·공공주택지구·택지개발지구 해당 시 보완서류
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
        
        # === 규칙 26: 토지 등기부등본 (필지 누락은 명시적 증거 있을 때만) ===
        if not result.land_registry.exists:
            self._add_supplementary("토지 등기부등본", "서류 미제출", 26)
        else:
            missing_pr = getattr(result.land_registry, "missing_parcels", []) or []
            tu = getattr(result.land_registry, "total_parcels", 0) or 0
            su = getattr(result.land_registry, "submitted_parcels", 0) or 0
            if not result.land_registry.all_parcels_submitted and (missing_pr or (tu and su and tu != su)):
                self._add_supplementary("토지 등기부등본", "필지 누락", 26)
        
        # === 규칙 27: 건물 등기부등본 (호수 누락은 명시적 증거 있을 때만) ===
        if not result.building_registry.exists:
            self._add_supplementary("건물 등기부등본", "서류 미제출", 27)
        else:
            missing_u = getattr(result.building_registry, "missing_units", []) or []
            tu_b = getattr(result.building_registry, "total_units", 0) or 0
            su_b = getattr(result.building_registry, "submitted_units", 0) or 0
            if not result.building_registry.all_units_submitted and (missing_u or (tu_b and su_b and tu_b != su_b)):
                self._add_supplementary("건물 등기부등본", "호수 누락", 27)
        
        # === 규칙 28: 신탁 서류 ===
        trust = result.trust_documents
        if trust.trust_required:
            if not trust.trust_contract.exists:
                self._add_supplementary("신탁원부계약서", "신탁 건물이나 서류 미제출", 28)
            if not trust.sale_authority_confirmation.exists:
                self._add_supplementary("신탁물건 매매 권한 확인서", "서류 미제출", 28)
            elif not trust.all_parties_signed or not trust.all_seals_valid:
                self._add_supplementary("신탁물건 매매 권한 확인서", "일부 관계인 서명/인감 누락", 28)
        
        # === 규칙 29: 준공도면 (추출된 자재와 미추출 항목을 구체적으로 표기) ===
        # 핵심: 필로티 구조가 아닌 건물은 필로티 자재 검사 생략
        as_built = result.as_built_drawing
        if not as_built.exists:
            self._add_supplementary("준공도면", "서류 미제출", 29)
        else:
            # 필로티 구조 여부 확인 (건축물대장 표제부에서)
            blt = result.building_ledger_title
            has_piloti = getattr(blt, "has_piloti", None)
            # 필로티 여부를 모르면 준공도면 데이터에서 추론
            if has_piloti is None:
                pil_f = getattr(as_built, "piloti_finish_material", None) or ""
                pil_i = getattr(as_built, "piloti_insulation_material", None) or ""
                # 필로티 자재가 추출되었으면 필로티 구조로 간주
                has_piloti = bool(pil_f.strip() or pil_i.strip())
            
            ext_f = getattr(as_built, "exterior_finish_material", None) or ""
            ext_i = getattr(as_built, "exterior_insulation_material", None) or ""
            pil_f = getattr(as_built, "piloti_finish_material", None) or ""
            pil_i = getattr(as_built, "piloti_insulation_material", None) or ""
            
            extracted = []
            if ext_f and ext_f.strip():
                extracted.append(f"외벽마감재료: {ext_f.strip()}")
            if ext_i and ext_i.strip():
                extracted.append(f"외벽단열재료: {ext_i.strip()}")
            if pil_f and pil_f.strip():
                extracted.append(f"필로티마감재료: {pil_f.strip()}")
            if pil_i and pil_i.strip():
                extracted.append(f"필로티단열재료: {pil_i.strip()}")
            
            missing = []
            # 외벽 자재는 항상 필수
            if not (ext_f and ext_f.strip()):
                missing.append("외벽마감재료")
            if not (ext_i and ext_i.strip()):
                missing.append("외벽단열재료")
            
            # 필로티 자재는 필로티 구조일 때만 필수
            if has_piloti:
                if not (pil_f and pil_f.strip()):
                    missing.append("필로티마감재료")
                if not (pil_i and pil_i.strip()):
                    missing.append("필로티단열재료")
            
            # 외벽 자재만 없는 경우도 문제로 처리
            # 하지만 이미 materials_extracted가 True면 AI가 추출을 시도한 것
            materials_extracted = getattr(as_built, "materials_extracted", False)
            
            if missing:
                # 도면이 있고 AI 추출을 시도했지만 일부만 추출된 경우
                if extracted:
                    # 추출된 것이 있으면 일부 미추출로 표시
                    msg = f"추출된 자재: {', '.join(extracted)} / 미추출: {', '.join(missing)}"
                    self._add_supplementary("준공도면", msg, 29)
                elif materials_extracted:
                    # AI 추출 시도했으나 전부 실패 — 수동 확인 필요
                    msg = f"자재명 미추출 — 도면에서 외벽마감·외벽단열 자재명을 추출해야 함"
                    self._add_supplementary("준공도면", msg, 29)
                else:
                    # AI 추출 시도 안함 — 도면 파일은 있으나 분석 안됨
                    # 이 경우는 문서 존재만으로 통과 (수동 확인 권장)
                    print(f"    [규칙29] 준공도면: 자재 추출 미시도, 문서 존재로 통과 (수동확인 권장)")
            else:
                # 모든 필수 자재 추출됨
                print(f"    [규칙29] 준공도면: 모든 필수 자재 추출됨")
        
        # === 규칙 30: 시험성적서·납품확인서 (외벽 및 필로티 자재별 철저 검증) ===
        # ★★★ 핵심 규칙 - 절대로 놓치면 안 됨 ★★★
        # 1. 시험성적서는 반드시 "열방출시험 + 가스유해성 시험" 둘 다 있어야 유효
        # 2. 열전도율 시험만 있으면 무효 → 무조건 보완서류
        # 3. 외벽 마감재가 석재면 시험성적서 생략 가능 (납품확인서는 필요)
        # 4. 각 자재별로 시험성적서와 납품확인서가 필요
        
        tcd = result.test_certificate_delivery
        as_built = result.as_built_drawing
        
        # ========================================
        # 1) 시험성적서 파일 존재 및 유효성 판정
        # ========================================
        test_cert_file_exists = getattr(tcd, "test_cert_file_exists", False) or tcd.exists
        delivery_conf_file_exists = getattr(tcd, "delivery_conf_file_exists", False) or tcd.has_delivery_confirmation
        
        # 열방출, 가스유해성, 열전도율 시험 여부
        has_heat = tcd.has_heat_release_test is True
        has_gas = tcd.has_gas_toxicity_test is True
        has_thermal = getattr(tcd, "has_thermal_conductivity_test", False) is True
        
        # detected_tests에서 추가 검증 (텍스트 기반 보완 검사)
        detected_tests = getattr(tcd, "detected_tests", []) or []
        detected_text = " ".join([str(t).lower() for t in detected_tests])
        
        # 열방출시험 키워드 확장 검색
        heat_keywords = ["열방출", "총열방출", "열방출률", "thr", "heat release", "hrr", 
                         "발열량", "5660", "콘칼로리미터", "cone calorimeter"]
        for kw in heat_keywords:
            if kw.lower() in detected_text:
                has_heat = True
                break
        
        # 가스유해성시험 키워드 확장 검색
        gas_keywords = ["가스유해", "가스독성", "gas toxic", "연소가스", "유해가스",
                        "연기독성", "2271", "마우스", "mouse"]
        for kw in gas_keywords:
            if kw.lower() in detected_text:
                has_gas = True
                break
        
        # 열전도율시험 키워드 확장 검색
        thermal_keywords = ["열전도율", "열전도", "thermal conductivity", "k-value",
                            "단열성능", "단열시험", "8302", "9016"]
        for kw in thermal_keywords:
            if kw.lower() in detected_text:
                has_thermal = True
                break
        
        # ★★★ 핵심 판정: 열방출+가스유해성 조합만 유효 ★★★
        has_valid_test_cert = test_cert_file_exists and has_heat and has_gas
        
        # ★★★ 열전도율만 있는지 확인 (가장 중요한 필터링) ★★★
        is_thermal_only = has_thermal and not has_heat and not has_gas
        
        print(f"    [규칙30 검증] 시험성적서 파일: {test_cert_file_exists}, 열방출: {has_heat}, 가스유해성: {has_gas}, 열전도율: {has_thermal}")
        print(f"    [규칙30 검증] 유효 시험성적서: {has_valid_test_cert}, 열전도율만: {is_thermal_only}")
        
        # ========================================
        # 2) 석재 예외 판정 (외벽 마감재가 석재면 시험성적서 생략 가능)
        # ========================================
        stone_keywords = ["석재", "화강석", "대리석", "현무암", "사암", "석회암",
                          "granite", "marble", "stone", "타일", "테라코타", 
                          "세라믹", "도자기", "자기질"]
        
        ext_finish = (getattr(as_built, "exterior_finish_material", None) or "").strip().lower()
        is_stone_finish = any(kw.lower() in ext_finish for kw in stone_keywords)
        
        # stone_exterior_exception 플래그 또는 자재명으로 석재 여부 판정
        stone_exception = tcd.stone_exterior_exception or is_stone_finish
        
        if stone_exception:
            print(f"    [규칙30 검증] ℹ️ 석재 예외 적용: 외벽 마감재({ext_finish or '미지정'}) - 시험성적서 생략 가능")
        
        # ========================================
        # 3) 준공도면에서 추출된 자재 목록 구성
        # ========================================
        required_materials = []
        
        # 외벽 자재 (필수)
        ext_finish_name = (getattr(as_built, "exterior_finish_material", None) or "").strip()
        ext_insul_name = (getattr(as_built, "exterior_insulation_material", None) or "").strip()
        if ext_finish_name:
            required_materials.append(("외벽마감재료", ext_finish_name, "exterior_finish", is_stone_finish))
        if ext_insul_name:
            required_materials.append(("외벽단열재료", ext_insul_name, "exterior_insul", False))
        
        # 필로티 자재 (필로티 구조인 경우만)
        pil_finish = (getattr(as_built, "piloti_finish_material", None) or "").strip()
        pil_insul = (getattr(as_built, "piloti_insulation_material", None) or "").strip()
        if pil_finish:
            required_materials.append(("필로티마감재료", pil_finish, "piloti_finish", False))
        if pil_insul:
            required_materials.append(("필로티단열재료", pil_insul, "piloti_insul", False))
        
        # ========================================
        # 4) 검증 수행
        # ========================================
        missing_items = []
        
        # 4-1) 열전도율만 있는 경우 → 최우선 경고
        if test_cert_file_exists and is_thermal_only:
            missing_items.append("⚠️ 시험성적서 무효: 열전도율 시험만 있음 (열방출+가스유해성 시험 조합 필수, 열전도율은 제외 대상)")
        
        # 4-2) 준공도면에서 자재가 추출되지 않은 경우
        if not required_materials:
            if not test_cert_file_exists:
                missing_items.append("준불연시험성적서 미제출 (준공도면 자재 미확인)")
            elif not has_valid_test_cert and not is_thermal_only:
                # 열전도율만 있는 경우는 이미 위에서 처리됨
                if not has_heat and not has_gas:
                    missing_items.append("준불연시험성적서 미비 (열방출시험+가스유해성 시험 없음)")
                elif not has_heat:
                    missing_items.append("준불연시험성적서 미비 (열방출시험 없음)")
                elif not has_gas:
                    missing_items.append("준불연시험성적서 미비 (가스유해성 시험 없음)")
            if not delivery_conf_file_exists:
                missing_items.append("납품확인서 미제출 (준공도면 자재 미확인)")
        else:
            # 4-3) 자재별로 검증
            for label, mat_name, mat_type, is_stone in required_materials:
                mat_desc = f"{label}({mat_name})"
                
                # 석재 예외: 시험성적서 불필요 (외벽 마감재만 해당)
                if is_stone and mat_type == "exterior_finish":
                    print(f"    [규칙30 검증] {mat_desc}: 석재 예외 적용 (시험성적서 생략)")
                    # 납품확인서는 여전히 필요
                    if not delivery_conf_file_exists:
                        missing_items.append(f"{mat_desc} 납품확인서 미제출 (석재도 납품확인서 필요)")
                    continue
                
                # 시험성적서 검증 (석재 아닌 모든 자재)
                if not test_cert_file_exists:
                    missing_items.append(f"{mat_desc} 준불연시험성적서 미제출")
                elif not has_valid_test_cert and not is_thermal_only:
                    # 열전도율만 있는 경우는 이미 위에서 처리됨
                    if not has_heat and not has_gas:
                        missing_items.append(f"{mat_desc} 준불연시험성적서 무효 (열방출+가스유해성 둘 다 없음)")
                    elif not has_heat:
                        missing_items.append(f"{mat_desc} 준불연시험성적서 무효 (열방출시험 없음, 가스유해성만)")
                    elif not has_gas:
                        missing_items.append(f"{mat_desc} 준불연시험성적서 무효 (가스유해성 시험 없음, 열방출만)")
                
                # 납품확인서 검증 (모든 자재 필수)
                if not delivery_conf_file_exists:
                    missing_items.append(f"{mat_desc} 납품확인서 미제출")
        
        # ========================================
        # 5) 결과 보고
        # ========================================
        if missing_items:
            # 중복 제거
            unique_missing = list(dict.fromkeys(missing_items))
            self._add_supplementary(
                "준불연시험성적서·납품확인서",
                "; ".join(unique_missing),
                30,
                needs_manual_check=False
            )
            print(f"    [규칙30 검증] 보완 필요: {len(unique_missing)}건")
        elif not tcd.exists and not delivery_conf_file_exists:
            # 아예 제출 안 된 경우
            self._add_supplementary("준불연시험성적서·납품확인서", "서류 미제출", 30)
            print(f"    [규칙30 검증] 서류 미제출")
        else:
            print(f"    [규칙30 검증] ✅ 통과")
        
        # === 규칙 31: 표제부 근생(근로자생활시설) 여부 ===
        blt = result.building_ledger_title
        if blt.exists and getattr(blt, "has_worker_living_facility", None) is None:
            self._add_supplementary("건축물대장 표제부", "근생(근로자생활시설) 여부 확인 필요", 31)
        
        # === 규칙 32: 전유부 최소·최대 면적 및 해당 호 ===
        excl = result.building_ledger_exclusive
        if excl.exists and excl.units:
            areas = [getattr(u, "exclusive_area", None) or getattr(u, "area", None) for u in excl.units]
            areas = [a for a in areas if a is not None]
            if areas:
                min_a, max_a = min(areas), max(areas)
                min_units = [getattr(u, "unit_number", "") or str(getattr(u, "unit", "")) for u in excl.units if (getattr(u, "exclusive_area", None) or getattr(u, "area", None)) == min_a]
                max_units = [getattr(u, "unit_number", "") or str(getattr(u, "unit", "")) for u in excl.units if (getattr(u, "exclusive_area", None) or getattr(u, "area", None)) == max_a]
                excl.min_exclusive_area = min_a
                excl.max_exclusive_area = max_a
                excl.min_area_unit_numbers = min_units or []
                excl.max_area_unit_numbers = max_units or []
            else:
                self._add_supplementary("건축물대장 전유부", "전유부 최소·최대 면적 및 해당 호 데이터 확인 필요", 32)
        elif excl.exists:
            self._add_supplementary("건축물대장 전유부", "전유부 최소·최대 면적 및 해당 호 데이터 확인 필요", 32)
        
        # === 규칙 33: 건물 등기부등본 민간임대용 명시 ===
        reg = result.building_registry
        if reg.exists and getattr(reg, "is_private_rental_stated", None) is None:
            self._add_supplementary("건물 등기부등본", "민간임대용 명시 여부 확인 필요", 33)
        
        # === 규칙 34: 토지 지목·용도·행위제한 ===
        land = result.land_ledger
        if land.exists and not getattr(land, "land_category", None) and not (getattr(land, "use_restrictions", None) or []):
            self._add_supplementary("토지대장", "지목·용도·행위제한 확인 필요", 34)
        
        # === 최종 결과 ===
        result.supplementary_documents = [
            SupplementaryDocument(
                document_name=doc.document_name,
                reason=doc.reason + (f" [수동확인필요]" if doc.needs_manual_check else ""),
                rule_number=doc.rule_number
            )
            for doc in self.supplementary_docs
        ]
        
        result.supplementary_count = len(self.supplementary_docs)
        result.is_review_complete = (len(self.supplementary_docs) == 0)
        
        manual_count = len(self.manual_check_items)
        if result.is_review_complete:
            result.review_summary = "✅ 모든 서류가 정상적으로 확인되었습니다."
        else:
            summary = f"총 {result.supplementary_count}건의 보완서류가 필요합니다."
            if manual_count > 0:
                summary += f" ({manual_count}건 수동확인 권장)"
            result.review_summary = summary
        
        return result
    
    def get_manual_check_report(self) -> str:
        if not self.manual_check_items:
            return "수동확인 필요 항목 없음"
        
        lines = [
            "",
            "=" * 50,
            "⚠️ 수동확인 필요 항목",
            "=" * 50,
        ]
        
        for idx, item in enumerate(self.manual_check_items, 1):
            lines.append(f"[{idx}] {item['document']}")
            lines.append(f"    사유: {item['reason']}")
            if item.get('details'):
                lines.append(f"    상세: {item['details']}")
        
        return "\n".join(lines)
