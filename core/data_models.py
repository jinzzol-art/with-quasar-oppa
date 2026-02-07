"""
공공임대 기존주택 매입심사 검증 시스템 - 데이터 모델 v3.2

수정사항:
1. 검토일자(오늘) vs 발급일자/작성일자(서류기재) 분리
2. 인감 일치율 기준 45% 이상
3. 소유자 완비: 성명·생년월일·주소·휴대전화·이메일주소 모두 필수
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional, List

from pydantic import BaseModel, Field


# =============================================================================
# 열거형 정의
# =============================================================================

class DocumentStatus(str, Enum):
    """서류 상태"""
    VALID = "정상"
    MISSING = "미제출"
    INCOMPLETE = "보완필요"
    INVALID = "무효"


class ApplicantType(str, Enum):
    """신청자 유형"""
    INDIVIDUAL = "개인"
    CORPORATION = "법인"


class AgentType(str, Enum):
    """대리인 유형"""
    NONE = "없음"
    INDIVIDUAL = "개인대리인"
    REALTOR = "공인중개사"


# =============================================================================
# 기본 서류 정보 모델
# =============================================================================

class DocumentBase(BaseModel):
    """모든 서류의 기본 정보"""
    exists: bool = Field(default=False, description="서류 존재 여부")
    issue_date: Optional[str] = Field(default=None, description="서류상 발급일/작성일 (YYYY-MM-DD)")
    status: DocumentStatus = Field(default=DocumentStatus.MISSING, description="서류 상태")
    issues: list[str] = Field(default_factory=list, description="발견된 문제점 목록")


# =============================================================================
# 1. 주택매도 신청서 관련
# =============================================================================

class OwnerInfo(BaseModel):
    """소유자 정보"""
    name: Optional[str] = Field(default=None, description="성명")
    birth_date: Optional[str] = Field(default=None, description="생년월일")
    address: Optional[str] = Field(default=None, description="현거주지 주소")
    phone: Optional[str] = Field(default=None, description="휴대전화번호")
    email: Optional[str] = Field(default=None, description="이메일주소")
    is_complete: bool = Field(default=False, description="모든 정보 기재 여부")


class AgentInfo(BaseModel):
    """대리인 정보"""
    exists: bool = Field(default=False, description="대리인 기재 여부")
    name: Optional[str] = Field(default=None, description="대리인 성명")
    agent_type: AgentType = Field(default=AgentType.NONE, description="대리인 유형")
    id_card_match: bool = Field(default=False, description="신분증 이름 일치 여부")


class SealVerification(BaseModel):
    """인감 검증 정보 - 기준 45% 이상"""
    seal_exists: bool = Field(default=False, description="인감 날인 여부")
    certificate_exists: bool = Field(default=False, description="인감증명서 존재 여부")
    match_rate: Optional[float] = Field(default=None, description="인감 일치율 (%)")
    is_valid: bool = Field(default=False, description="유효성 (45% 이상)")


class HousingSaleApplication(DocumentBase):
    """주택매도 신청서"""
    # 작성일 (서류에 기재된 날짜)
    written_date: Optional[str] = Field(default=None, description="서류상 작성일자")
    is_after_announcement: bool = Field(default=False, description="공고일 이후 작성 여부")
    announcement_date: Optional[str] = Field(default=None, description="적용된 공고일")
    
    # 소유자 정보
    owner_info: OwnerInfo = Field(default_factory=OwnerInfo, description="소유자 정보")
    
    # 인감 검증
    seal_verification: SealVerification = Field(default_factory=SealVerification, description="인감 검증")
    
    # 대리인 정보
    agent_info: AgentInfo = Field(default_factory=AgentInfo, description="대리인 정보")
    
    # 대지면적
    land_area: Optional[float] = Field(default=None, description="대지면적 (㎡)")
    land_area_match: bool = Field(default=False, description="대지면적 일치 여부")
    
    # 사용승인일
    approval_date: Optional[str] = Field(default=None, description="건물사용승인일")
    approval_date_match: bool = Field(default=False, description="건축물대장과 일치 여부")


# =============================================================================
# 2. 매도신청주택 임대현황
# =============================================================================

class UnitInfo(BaseModel):
    """호별 정보"""
    unit_number: str = Field(description="호수")
    exclusive_area: Optional[float] = Field(default=None, description="전용면적 (㎡)")
    area_match: bool = Field(default=False, description="건축물대장 전유부와 일치 여부")
    status: DocumentStatus = Field(default=DocumentStatus.VALID, description="상태")


class RentalStatus(DocumentBase):
    """매도신청주택 임대현황"""
    units: list[UnitInfo] = Field(default_factory=list, description="호별 정보 목록")
    mismatched_units: list[str] = Field(default_factory=list, description="불일치 호수 목록")


# =============================================================================
# 3. 위임장 관련
# =============================================================================

class DelegationInfo(BaseModel):
    """위임자/수임자 정보"""
    name: Optional[str] = Field(default=None, description="성명")
    personal_info_complete: bool = Field(default=False, description="인적사항 완비 여부")
    seal_valid: bool = Field(default=False, description="인감도장 유효 여부")


class PowerOfAttorney(DocumentBase):
    """위임장"""
    written_date: Optional[str] = Field(default=None, description="서류상 작성일자")
    location: Optional[str] = Field(default=None, description="소재지")
    land_area: Optional[float] = Field(default=None, description="대지면적 (㎡)")
    land_area_match: bool = Field(default=False, description="대지면적 정확성")
    
    delegator: DelegationInfo = Field(default_factory=DelegationInfo, description="위임자 정보")
    delegatee: DelegationInfo = Field(default_factory=DelegationInfo, description="수임자 정보")
    
    is_after_announcement: bool = Field(default=False, description="공고일 이후 작성 여부")


# =============================================================================
# 4. 신분증 및 인감증명서
# =============================================================================

class IdentityDocument(DocumentBase):
    """신분증 사본"""
    document_type: Optional[str] = Field(default=None, description="신분증 종류")
    name_on_document: Optional[str] = Field(default=None, description="신분증상 이름")
    name_match: bool = Field(default=False, description="소유자 이름 일치 여부")


class OwnerIdentityDocuments(BaseModel):
    """소유자 신분증 및 인감증명서"""
    seal_certificate: DocumentBase = Field(default_factory=DocumentBase, description="인감증명서")
    seal_certificate_issue_date: Optional[str] = Field(default=None, description="인감증명서 발급일")
    
    identity_documents: list[IdentityDocument] = Field(default_factory=list, description="신분증 목록")
    
    owner_count: int = Field(default=1, description="소유자 수")
    all_ids_submitted: bool = Field(default=False, description="모든 소유자 신분증 제출 여부")


# =============================================================================
# 5. 법인 관련 서류
# =============================================================================

class CorporateDocuments(BaseModel):
    """법인 관련 서류"""
    is_corporation: bool = Field(default=False, description="법인 여부")
    
    business_registration: DocumentBase = Field(default_factory=DocumentBase, description="법인용 사업자등록증")
    corporate_seal_certificate: DocumentBase = Field(default_factory=DocumentBase, description="법인용 인감증명서")
    corporate_registry: DocumentBase = Field(default_factory=DocumentBase, description="법인 등기사항전부증명서")
    
    executive_ids: list[IdentityDocument] = Field(default_factory=list, description="임원 신분증 목록")
    executive_count: int = Field(default=0, description="등기 임원 수")
    all_executive_ids_submitted: bool = Field(default=False, description="모든 임원 신분증 제출 여부")
    
    contract_limit_consent: DocumentBase = Field(default_factory=DocumentBase, description="연간 계약건수 동의서")
    all_executives_signed: bool = Field(default=False, description="모든 임원 자필서명 여부")


# =============================================================================
# 6. 동의서 및 서약서
# =============================================================================

class ConsentForm(DocumentBase):
    """개인정보 수집 이용 및 제공 동의서"""
    owner_written_date: Optional[str] = Field(default=None, description="소유자 작성일자")
    owner_signed: bool = Field(default=True, description="소유자 작성 여부 (문서 있으면 기본 True)")
    owner_seal_valid: bool = Field(default=True, description="소유자 인감도장 일치 (문서 있으면 기본 True)")
    owner_date_valid: bool = Field(default=True, description="소유자 작성일자 유효 (기본 True)")
    
    agent_written_date: Optional[str] = Field(default=None, description="대리인 작성일자")
    agent_signed: bool = Field(default=True, description="대리인 작성 여부 (대리인란 있으면 기본 True)")
    agent_seal_valid: bool = Field(default=True, description="대리인 인감도장 일치 (대리인란 있으면 기본 True)")
    agent_date_valid: bool = Field(default=True, description="대리인 작성일자 유효 (기본 True)")


class IntegrityPledge(DocumentBase):
    """청렴서약서"""
    owner_written_date: Optional[str] = Field(default=None, description="소유자 작성일자")
    owner_submitted: bool = Field(default=True, description="소유자 작성 여부 (문서 있으면 기본 True)")
    owner_seal_valid: bool = Field(default=True, description="소유자 인감 유효 (문서 있으면 기본 True)")
    owner_id_number_valid: bool = Field(default=True, description="주민번호/사업자번호 정확성 (기본 True)")
    
    agent_submitted: bool = Field(default=True, description="대리인 작성 여부 (대리인란 있으면 기본 True)")
    agent_seal_valid: bool = Field(default=True, description="대리인 인감 유효 (대리인란 있으면 기본 True)")
    
    realtor_submitted: bool = Field(default=True, description="중개사 작성 여부 (중개사란 있으면 기본 True)")
    realtor_seal_valid: bool = Field(default=True, description="중개사 인감 유효 (중개사란 있으면 기본 True)")
    
    corporation_id_type_correct: bool = Field(default=True, description="법인 사업자등록번호 기재 여부")


class LHEmployeeConfirmation(DocumentBase):
    """공사직원여부 확인서"""
    written_date: Optional[str] = Field(default=None, description="작성일자")
    owner_name_match: bool = Field(default=True, description="소유자 이름 일치 (기본 True, 문서 있으면 유효)")
    seal_valid: bool = Field(default=True, description="인감도장 유효 (기본 True, 문서 있으면 유효)")
    date_valid: bool = Field(default=True, description="작성일자 유효 (기본 True, 공고일 이전이면 False)")


# =============================================================================
# 7. 공인중개사 관련
# =============================================================================

class RealtorDocuments(BaseModel):
    """공인중개사 관련 서류"""
    is_realtor_agent: bool = Field(default=False, description="대리인이 공인중개사 여부")
    
    office_registration: DocumentBase = Field(default_factory=DocumentBase, description="중개사무소 등록증")
    business_registration: DocumentBase = Field(default_factory=DocumentBase, description="중개사 사업자등록증")
    
    seal_match_with_application: bool = Field(default=False, description="인감 일치 여부")


# =============================================================================
# 8. 건축물대장
# =============================================================================

class BuildingLedgerSummary(DocumentBase):
    """건축물대장 총괄표제부. 한 필지에서 2개 이상 동이 있을 때 받는 서류. 내진설계·사용승인일 등은 표제부에서만 검토함."""
    required: bool = Field(default=False, description="총괄표제부 필요 여부 (2개 이상 동일 때 True)")
    building_count: int = Field(default=1, description="동 수")


class BuildingLedgerTitle(DocumentBase):
    """건축물대장 표제부. 한 필지에 한 동일 때 받는 서류. 내진설계 적용 여부·사용승인일 등은 이 표제부 데이터로만 검토함."""
    approval_date: Optional[str] = Field(default=None, description="사용승인일")
    seismic_design: Optional[bool] = Field(default=None, description="내진설계 적용 여부 (표제부에서만 추출·검토)")
    
    outdoor_parking: Optional[int] = Field(default=None, description="옥외 주차장 대수")
    indoor_parking: Optional[int] = Field(default=None, description="옥내 주차장 대수")
    mechanical_parking: Optional[int] = Field(default=None, description="기계식 주차장 대수")
    
    has_basement: Optional[bool] = Field(default=None, description="지하층 유무 (주차장·창고만 있어도 true)")
    basement_floors: Optional[int] = Field(default=None, description="지하층 수")
    has_basement_units: Optional[bool] = Field(default=None, description="지하 세대 존재 여부. 지하층에 거주용 호(세대)가 있을 때만 true. 지하층은 있지만 주차장·창고만 있으면 false.")
    has_elevator: Optional[bool] = Field(default=None, description="승강기 설치 여부")
    elevator_count: Optional[int] = Field(default=None, description="승강기 대수")
    has_worker_living_facility: Optional[bool] = Field(default=None, description="근로자생활시설(근생) 여부")
    has_piloti: Optional[bool] = Field(default=None, description="필로티 구조 여부")


class ExclusiveUnit(BaseModel):
    """전유부 호별 정보"""
    unit_number: str = Field(description="호수")
    exclusive_area: float = Field(description="전용면적 (㎡)")
    area_valid: bool = Field(default=False, description="면적 기준 충족 (16~85㎡)")
    status: DocumentStatus = Field(default=DocumentStatus.VALID, description="상태")


class BuildingLedgerExclusive(DocumentBase):
    """건축물대장 전유부"""
    units: list[ExclusiveUnit] = Field(default_factory=list, description="전유부 호별 정보")
    invalid_area_units: list[str] = Field(default_factory=list, description="면적 기준 미충족 호수")
    min_exclusive_area: Optional[float] = Field(default=None, description="전유부 최소 전용면적 (㎡)")
    max_exclusive_area: Optional[float] = Field(default=None, description="전유부 최대 전용면적 (㎡)")
    min_area_unit_numbers: list[str] = Field(default_factory=list, description="최소 면적 해당 호수")
    max_area_unit_numbers: list[str] = Field(default_factory=list, description="최대 면적 해당 호수")


class BuildingLayoutPlan(DocumentBase):
    """건축물현황도"""
    has_site_plan: bool = Field(default=False, description="배치도 존재 여부")
    has_all_floor_plans: bool = Field(default=False, description="모든 층별 평면도 존재 여부")
    has_unit_plans: bool = Field(default=False, description="호별 평면도 존재 여부")
    is_government_issued: bool = Field(default=False, description="지자체 발급분 여부")
    
    missing_floors: list[str] = Field(default_factory=list, description="누락된 층")
    missing_units: list[str] = Field(default_factory=list, description="누락된 호")


# =============================================================================
# 9. 토지 관련 서류
# =============================================================================

class LandLedger(DocumentBase):
    """토지대장"""
    land_area: Optional[float] = Field(default=None, description="대지면적 (㎡)")
    land_area_match: bool = Field(default=False, description="대지면적 일치 여부")
    is_after_announcement: bool = Field(default=False, description="공고일 이후 발급 여부")
    land_category: Optional[str] = Field(default=None, description="지목")
    use_restrictions: list[str] = Field(default_factory=list, description="용도·행위제한 등")
    
    total_parcels: int = Field(default=0, description="총 필지 수")
    submitted_parcels: int = Field(default=0, description="제출된 필지 수")
    all_parcels_submitted: bool = Field(default=False, description="모든 필지 제출 여부")
    missing_parcels: list[str] = Field(default_factory=list, description="누락된 필지")


class LandUsePlan(DocumentBase):
    """토지이용계획확인원"""
    land_area: Optional[float] = Field(default=None, description="대지면적 (㎡)")
    land_area_match: bool = Field(default=False, description="대지면적 일치 여부")
    
    total_parcels: int = Field(default=0, description="총 필지 수")
    submitted_parcels: int = Field(default=0, description="제출된 필지 수")
    all_parcels_submitted: bool = Field(default=False, description="모든 필지 제출 여부")
    missing_parcels: list[str] = Field(default_factory=list, description="누락된 필지")
    
    is_redevelopment_zone: bool = Field(default=False, description="재정비촉진지구 여부")
    is_maintenance_zone: bool = Field(default=False, description="정비구역 여부")
    is_public_housing_zone: bool = Field(default=False, description="공공주택지구 여부")
    is_housing_development_zone: bool = Field(default=False, description="택지개발지구 여부")
    has_exclusion_zone: bool = Field(default=False, description="제외 대상 구역 해당 여부")
    
    land_use_regulations: list[str] = Field(default_factory=list, description="토지이용규제 해당 사항")


class LandRegistry(DocumentBase):
    """토지 등기부등본"""
    land_area: Optional[float] = Field(default=None, description="대지면적 (㎡)")
    
    total_parcels: int = Field(default=0, description="총 필지 수")
    submitted_parcels: int = Field(default=0, description="제출된 필지 수")
    all_parcels_submitted: bool = Field(default=False, description="모든 필지 제출 여부")
    missing_parcels: list[str] = Field(default_factory=list, description="누락된 필지")


# =============================================================================
# 10. 건물 등기부등본
# =============================================================================

class BuildingRegistry(DocumentBase):
    """건물 등기부등본"""
    total_units: int = Field(default=0, description="총 호수")
    submitted_units: int = Field(default=0, description="제출된 호수")
    all_units_submitted: bool = Field(default=False, description="모든 호 제출 여부")
    missing_units: list[str] = Field(default_factory=list, description="누락된 호")
    
    has_mortgage: bool = Field(default=False, description="근저당 설정 여부")
    mortgage_details: list[str] = Field(default_factory=list, description="근저당 상세 내역")
    
    has_seizure: bool = Field(default=False, description="압류 여부")
    seizure_details: list[str] = Field(default_factory=list, description="압류 상세 내역")
    
    has_trust: bool = Field(default=False, description="신탁 여부")
    trust_details: list[str] = Field(default_factory=list, description="신탁 상세 내역")
    is_private_rental_stated: Optional[bool] = Field(default=None, description="민간임대용 명시 여부")


# =============================================================================
# 11-2. 준공도면·시험성적서·납품확인서 (규칙 29, 30)
# =============================================================================

class AsBuiltDrawing(DocumentBase):
    """준공도면. 규칙 29: 외벽마감·외벽단열·필로티 마감·단열 자재명 추출"""
    materials_extracted: bool = Field(default=False, description="자재명 추출 여부")
    exterior_finish_material: Optional[str] = Field(default=None, description="외벽 마감재료")
    exterior_insulation_material: Optional[str] = Field(default=None, description="외벽 단열재료")
    piloti_finish_material: Optional[str] = Field(default=None, description="필로티 마감재료")
    piloti_insulation_material: Optional[str] = Field(default=None, description="필로티 단열재료")


class TestCertificateDelivery(DocumentBase):
    """시험성적서·납품확인서. 규칙 30: 열방출+가스유해성 시험 조합 필수, 열전도율 제외"""
    has_heat_release_test: bool = Field(default=False, description="열방출시험 항목 여부")
    has_gas_toxicity_test: bool = Field(default=False, description="가스유해성 시험 항목 여부")
    has_thermal_conductivity_test: bool = Field(default=False, description="열전도율 시험 여부 (이것만 있으면 무효)")
    has_delivery_confirmation: bool = Field(default=False, description="납품확인서 여부")
    stone_exterior_exception: bool = Field(default=False, description="외벽 마감재 석재 시 시험성적서 생략 가능")
    materials_with_test_cert: List[str] = Field(default_factory=list, description="시험성적서(열방출+가스유해성) 있는 자재명 목록")
    materials_with_delivery_conf: List[str] = Field(default_factory=list, description="납품확인서 있는 자재명 목록")
    detected_tests: List[str] = Field(default_factory=list, description="시험성적서에서 감지된 모든 시험 항목명")
    # ★ 파일 존재 여부 (실제 파일이 제출되었는지)
    test_cert_file_exists: bool = Field(default=False, description="시험성적서 파일 실제 제출 여부")
    delivery_conf_file_exists: bool = Field(default=False, description="납품확인서 파일 실제 제출 여부")


# =============================================================================
# 11. 신탁 관련 서류
# =============================================================================

class TrustDocuments(BaseModel):
    """신탁 관련 서류"""
    trust_required: bool = Field(default=False, description="신탁 서류 필요 여부")
    
    trust_contract: DocumentBase = Field(default_factory=DocumentBase, description="신탁원부계약서")
    sale_authority_confirmation: DocumentBase = Field(default_factory=DocumentBase, description="신탁물건 매매 권한 확인서")
    
    all_parties_signed: bool = Field(default=False, description="모든 관계인 서명 여부")
    all_seals_valid: bool = Field(default=False, description="모든 인감도장 유효 여부")


# =============================================================================
# 보완서류 및 최종 결과
# =============================================================================

class SupplementaryDocument(BaseModel):
    """보완서류 항목"""
    document_name: str = Field(description="서류명")
    reason: str = Field(description="보완 사유")
    rule_number: int = Field(description="관련 규칙 번호")


class DocumentDateInfo(BaseModel):
    """서류별 발급일/작성일 정보"""
    document_name: str = Field(description="서류명")
    date_type: str = Field(description="날짜 종류 (발급일/작성일)")
    date_value: Optional[str] = Field(default=None, description="날짜 (YYYY-MM-DD)")
    is_valid: bool = Field(default=False, description="공고일 이후 여부")


# 문서 필드 목록 (PublicHousingReviewResult 병합 시 사용, 클래스 내부에 두면 Pydantic ModelPrivateAttr 됨)
PUBLIC_HOUSING_DOC_FIELDS = [
    "housing_sale_application", "rental_status", "power_of_attorney", "owner_identity",
    "corporate_documents", "consent_form", "integrity_pledge", "lh_employee_confirmation",
    "realtor_documents", "building_ledger_summary", "building_ledger_title",
    "building_ledger_exclusive", "building_layout_plan", "land_ledger", "land_use_plan",
    "land_registry", "building_registry", "trust_documents",
    "as_built_drawing", "test_certificate_delivery",
]


class PublicHousingReviewResult(BaseModel):
    """
    공공임대 기존주택 매입심사 최종 검증 결과
    """
    
    # === 검토 메타 정보 ===
    review_date: str = Field(description="검토일자 (오늘 날짜)")
    property_address: Optional[str] = Field(default=None, description="물건 소재지")
    parcel_number: Optional[str] = Field(default=None, description="지번 (건물 단위 그룹핑용)")
    applicant_type: ApplicantType = Field(default=ApplicantType.INDIVIDUAL, description="신청자 유형")
    applicant_type_display: Optional[str] = Field(default=None, description="비개인(법인·건설 등)일 때 문서에서 인식한 소유자/신청자 명칭")
    agent_type: AgentType = Field(default=AgentType.NONE, description="대리인 유형")
    
    # 공고 정보
    announcement_date: Optional[str] = Field(default=None, description="적용 공고일")
    correction_announcement_date: Optional[str] = Field(default=None, description="정정공고일")
    
    # === 서류별 발급일/작성일 목록 ===
    document_dates: list[DocumentDateInfo] = Field(
        default_factory=list, 
        description="각 서류의 발급일/작성일 목록"
    )
    
    # === 각 서류별 검증 결과 ===
    housing_sale_application: HousingSaleApplication = Field(default_factory=HousingSaleApplication)
    rental_status: RentalStatus = Field(default_factory=RentalStatus)
    power_of_attorney: PowerOfAttorney = Field(default_factory=PowerOfAttorney)
    owner_identity: OwnerIdentityDocuments = Field(default_factory=OwnerIdentityDocuments)
    corporate_documents: CorporateDocuments = Field(default_factory=CorporateDocuments)
    consent_form: ConsentForm = Field(default_factory=ConsentForm)
    integrity_pledge: IntegrityPledge = Field(default_factory=IntegrityPledge)
    lh_employee_confirmation: LHEmployeeConfirmation = Field(default_factory=LHEmployeeConfirmation)
    realtor_documents: RealtorDocuments = Field(default_factory=RealtorDocuments)
    building_ledger_summary: BuildingLedgerSummary = Field(default_factory=BuildingLedgerSummary)
    building_ledger_title: BuildingLedgerTitle = Field(default_factory=BuildingLedgerTitle)
    building_ledger_exclusive: BuildingLedgerExclusive = Field(default_factory=BuildingLedgerExclusive)
    building_layout_plan: BuildingLayoutPlan = Field(default_factory=BuildingLayoutPlan)
    land_ledger: LandLedger = Field(default_factory=LandLedger)
    land_use_plan: LandUsePlan = Field(default_factory=LandUsePlan)
    land_registry: LandRegistry = Field(default_factory=LandRegistry)
    building_registry: BuildingRegistry = Field(default_factory=BuildingRegistry)
    trust_documents: TrustDocuments = Field(default_factory=TrustDocuments)
    
    # 규칙 29, 30
    as_built_drawing: AsBuiltDrawing = Field(default_factory=AsBuiltDrawing)
    test_certificate_delivery: TestCertificateDelivery = Field(default_factory=TestCertificateDelivery)
    
    # === 최종 결과 ===
    supplementary_documents: list[SupplementaryDocument] = Field(default_factory=list)
    total_documents_checked: int = Field(default=0, description="검토된 총 서류 수")
    valid_documents_count: int = Field(default=0, description="정상 서류 수")
    supplementary_count: int = Field(default=0, description="보완필요 서류 수")
    
    is_review_complete: bool = Field(default=False, description="심사 완료 가능 여부")
    review_summary: str = Field(default="", description="검토 요약")

    @classmethod
    def merge_results(cls, results: List["PublicHousingReviewResult"], review_date: str, announcement_date: Optional[str] = None) -> "PublicHousingReviewResult":
        """여러 파일의 분석 결과를 하나로 병합 (같은 건물·지번일 때 검증 1회용)"""
        if not results:
            return cls(review_date=review_date, announcement_date=announcement_date)
        first = results[0]
        merged = first.model_copy(deep=True)
        merged.review_date = review_date
        merged.announcement_date = announcement_date or first.announcement_date
        merged.property_address = next((r.property_address for r in results if r.property_address), None)
        merged.parcel_number = next((r.parcel_number for r in results if r.parcel_number), None)
        for name in PUBLIC_HOUSING_DOC_FIELDS:
            doc = getattr(merged, name)
            if not hasattr(doc, "exists"):
                continue
            
            # ★★★ 특별 처리: housing_sale_application은 소유자 정보가 채워진 것 우선 ★★★
            if name == "housing_sale_application":
                best_result = None
                best_score = -1
                for r in results:
                    other = getattr(r, name)
                    if not getattr(other, "exists", False):
                        continue
                    # 소유자 정보 채워진 정도 계산
                    owner = getattr(other, "owner_info", None)
                    if owner:
                        score = sum([
                            bool(owner.name and str(owner.name).strip()),
                            bool(owner.birth_date and str(owner.birth_date).strip()),
                            bool(owner.address and str(owner.address).strip()),
                            bool(owner.phone and str(owner.phone).strip()),
                            bool(owner.email and str(owner.email).strip()),
                        ])
                        if score > best_score:
                            best_score = score
                            best_result = other
                    elif best_result is None:
                        best_result = other
                if best_result is not None:
                    setattr(merged, name, best_result.model_copy(deep=True))
                continue
            
            # 일반 문서: exists=True인 첫 번째 결과 사용
            for r in results:
                other = getattr(r, name)
                if getattr(other, "exists", False):
                    setattr(merged, name, other.model_copy(deep=True))
                    break
        return merged
