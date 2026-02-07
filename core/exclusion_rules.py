"""
공공임대 기존주택 매입심사 - 공고문 기반 매입제외 규칙 시스템

핵심 원칙:
1. 공고문 = 헌법 (최우선 적용)
2. 매입제외 요건 해당 시 → 즉시 제외 (서류 검토 불필요)
3. 공고문 변경 시 → 규칙 DB 자동 갱신
4. 지역본부별 → 별도 공고문 적용 가능
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Any


# =============================================================================
# 매입제외 카테고리 정의
# =============================================================================

class ExclusionCategory(str, Enum):
    """매입제외 요건 대분류"""
    LOCATION = "지리적_요건"           # ① 주택 지리적 여건
    HOUSING_CONDITION = "주택_요건"    # ② 주택여건(구조, 면적 등)
    OTHER = "기타_요건"                # ③ 기타사항


class ExclusionSeverity(str, Enum):
    """제외 심각도"""
    ABSOLUTE = "절대_제외"      # 무조건 제외 (예외 없음)
    CONDITIONAL = "조건부"      # 조건 충족 시 신청 가능
    WARNING = "주의"            # 확인 필요


# =============================================================================
# 매입제외 규칙 데이터 모델
# =============================================================================

@dataclass
class ExclusionRule:
    """매입제외 규칙 단위"""
    rule_id: str                              # 규칙 고유 ID (예: LOC_001)
    category: ExclusionCategory               # 대분류
    sub_category: str                         # 소분류 (예: 재정비촉진지구)
    description: str                          # 제외 요건 설명
    severity: ExclusionSeverity               # 심각도
    
    # 검증 조건
    check_field: str                          # 검증 대상 필드명
    check_condition: str                      # 검증 조건 (예: "== True", "in ['재정비촉진지구']")
    
    # 예외 조건 (조건부인 경우)
    exception_condition: Optional[str] = None # 예외 조건 설명
    exception_documents: list[str] = field(default_factory=list)  # 필요 서류
    
    # 메타 정보
    legal_basis: Optional[str] = None         # 법적 근거
    reference_page: Optional[int] = None      # 공고문 페이지
    
    # 활성화 여부 (지역본부별 비활성화 가능)
    is_active: bool = True


@dataclass
class ExclusionCheckResult:
    """매입제외 검증 결과"""
    rule_id: str
    rule_description: str
    is_excluded: bool                         # 제외 여부
    severity: ExclusionSeverity
    reason: str                               # 제외/통과 사유
    exception_applied: bool = False           # 예외 적용 여부
    requires_manual_check: bool = False       # 수동 확인 필요
    evidence: Optional[str] = None            # 근거 자료


@dataclass
class AnnouncementConfig:
    """공고문 설정"""
    announcement_id: str                      # 공고 ID
    title: str                                # 공고명
    region: str                               # 지역본부
    announcement_date: str                    # 공고일
    application_start: str                    # 신청 시작일
    application_end: str                      # 신청 마감일
    
    # 매입대상 기준
    min_units: int = 15                       # 최소 호수
    max_exclusive_area: float = 85.0          # 최대 전용면적
    
    # 건령 기준
    min_construction_start: str = "2009-01-01"   # 최소 착공일
    min_approval_date: str = "2015-01-01"        # 최소 사용승인일
    officetel_min_approval: str = "2010-01-01"   # 오피스텔 최소 사용승인일
    
    # 유형별 면적 기준
    area_by_type: dict = field(default_factory=lambda: {
        "일반": {"min": 20, "max": 85},
        "청년": {"min": 16, "max": 60},
        "기숙사형": {"min": 16, "max": 60},
        "신혼신생아1": {"min": 36, "max": 85},
        "신혼신생아2": {"min": 36, "max": 85},
        "다자녀": {"min": 46, "max": 85},
    })
    
    # 매입제외 규칙 목록
    exclusion_rules: list[ExclusionRule] = field(default_factory=list)
    
    # 메타
    created_at: str = ""
    updated_at: str = ""
    source_file: str = ""


# =============================================================================
# 2025년 경기남부 공고문 기본 규칙 (4~7페이지 기준)
# =============================================================================

def get_default_exclusion_rules_2025_gyeonggi_south() -> list[ExclusionRule]:
    """2025년 경기남부 공고문 기준 매입제외 규칙"""
    
    rules = []
    
    # =========================================================================
    # ① 지리적 요건 (LOCATION)
    # =========================================================================
    
    # LOC_001: 재정비촉진지구
    rules.append(ExclusionRule(
        rule_id="LOC_001",
        category=ExclusionCategory.LOCATION,
        sub_category="재정비촉진지구",
        description="「도시재정비 촉진을 위한 특별법」에 의한 재정비촉진지구 내 주택",
        severity=ExclusionSeverity.CONDITIONAL,
        check_field="land_use_plan.is_redevelopment_zone",
        check_condition="== True",
        exception_condition="해제절차 진행 중이거나 존치관리구역으로 지정된 경우",
        exception_documents=["지구해제 합의서", "존치관리구역 지정 확인서"],
        legal_basis="도시재정비 촉진을 위한 특별법",
        reference_page=4
    ))
    
    # LOC_002: 정비구역
    rules.append(ExclusionRule(
        rule_id="LOC_002",
        category=ExclusionCategory.LOCATION,
        sub_category="정비구역",
        description="「도시 및 주거환경정비법」에 의한 정비구역 내 주택",
        severity=ExclusionSeverity.CONDITIONAL,
        check_field="land_use_plan.is_maintenance_zone",
        check_condition="== True",
        exception_condition="현지개량방식의 주거환경개선사업계획이 확정/준공된 경우",
        exception_documents=["주거환경개선사업 확정/준공 확인서"],
        legal_basis="도시 및 주거환경정비법",
        reference_page=4
    ))
    
    # LOC_003: 공공주택지구
    rules.append(ExclusionRule(
        rule_id="LOC_003",
        category=ExclusionCategory.LOCATION,
        sub_category="공공주택지구",
        description="「공공주택 특별법」에 의한 공공주택지구 및 도심공공주택복합지구(후보지 포함) 내 주택",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="land_use_plan.is_public_housing_zone",
        check_condition="== True",
        legal_basis="공공주택 특별법",
        reference_page=4
    ))
    
    # LOC_004: 택지개발예정지구
    rules.append(ExclusionRule(
        rule_id="LOC_004",
        category=ExclusionCategory.LOCATION,
        sub_category="택지개발예정지구",
        description="「택지개발촉진법」에 의한 택지개발예정지구 내 주택",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="land_use_plan.is_housing_development_zone",
        check_condition="== True",
        legal_basis="택지개발촉진법",
        reference_page=4
    ))
    
    # LOC_005: 빈집 및 소규모주택 정비구역
    rules.append(ExclusionRule(
        rule_id="LOC_005",
        category=ExclusionCategory.LOCATION,
        sub_category="소규모주택정비사업구역",
        description="「빈집 및 소규모주택 정비에 관한 특례법」에 따른 사업시행구역 내 주택",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="land_use_plan.is_small_housing_redevelopment_zone",
        check_condition="== True",
        legal_basis="빈집 및 소규모주택 정비에 관한 특례법",
        reference_page=4
    ))
    
    # LOC_006: 도시가스 미설치
    rules.append(ExclusionRule(
        rule_id="LOC_006",
        category=ExclusionCategory.LOCATION,
        sub_category="도시가스_미설치",
        description="도시가스 미설치 지역의 주택",
        severity=ExclusionSeverity.CONDITIONAL,
        check_field="utilities.has_city_gas",
        check_condition="== False",
        exception_condition="농어촌 등 지역여건상 설치 불가능 지역이거나 매도자가 설치하여 문제없는 경우",
        reference_page=4
    ))
    
    # LOC_007: 상하수도 미설치
    rules.append(ExclusionRule(
        rule_id="LOC_007",
        category=ExclusionCategory.LOCATION,
        sub_category="상하수도_미설치",
        description="상하수도 미설치 지역의 주택",
        severity=ExclusionSeverity.CONDITIONAL,
        check_field="utilities.has_water_sewage",
        check_condition="== False",
        exception_condition="농어촌 등 지역여건상 설치 불가능 지역이거나 매도자가 설치하여 문제없는 경우",
        reference_page=4
    ))
    
    # LOC_008: 군부대 사격장/화장장 500m 이내
    rules.append(ExclusionRule(
        rule_id="LOC_008",
        category=ExclusionCategory.LOCATION,
        sub_category="군부대_화장장_인접",
        description="직선거리 500m 이내 군부대 사격장·화장장이 있는 지역의 주택",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="location.near_military_or_crematorium_500m",
        check_condition="== True",
        reference_page=5
    ))
    
    # LOC_009: 유해시설 50m 이내
    rules.append(ExclusionRule(
        rule_id="LOC_009",
        category=ExclusionCategory.LOCATION,
        sub_category="유해시설_50m",
        description="직선거리 50m 이내「주택건설기준 등에 관한 규정」제9조의2 제1항 제1호 및 제2호 시설이 있는 주택",
        severity=ExclusionSeverity.CONDITIONAL,
        check_field="location.near_harmful_facility_50m",
        check_condition="== True",
        exception_condition="주거용 오피스텔은 해당 기준 미적용",
        reference_page=5
    ))
    
    # LOC_010: 주유소 등 25m 이내
    rules.append(ExclusionRule(
        rule_id="LOC_010",
        category=ExclusionCategory.LOCATION,
        sub_category="주유소_25m",
        description="직선거리 25m 이내 주유소·석유판매취급소·자동차용 천연가스충전소가 있는 주택",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="location.near_gas_station_25m",
        check_condition="== True",
        reference_page=5
    ))
    
    # LOC_011: 숙박/위락시설 25m 이내 (다자녀 유형)
    rules.append(ExclusionRule(
        rule_id="LOC_011",
        category=ExclusionCategory.LOCATION,
        sub_category="숙박위락시설_25m_다자녀",
        description="직선거리 25m 이내 일반숙박시설·위락시설이 있는 다자녀 유형 주택",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="location.near_entertainment_25m",
        check_condition="== True and housing_type == '다자녀'",
        reference_page=5
    ))
    
    # LOC_012: 맹지
    rules.append(ExclusionRule(
        rule_id="LOC_012",
        category=ExclusionCategory.LOCATION,
        sub_category="맹지",
        description="맹지 상태 또는 타인 소유의 시설물에 의해 부속토지가 점유된 주택",
        severity=ExclusionSeverity.CONDITIONAL,
        check_field="land.is_landlocked",
        check_condition="== True",
        exception_condition="점유해소 가능한 객관적 증빙자료 제출 시 조건부 신청 가능",
        exception_documents=["점유해소 증빙자료"],
        reference_page=5
    ))
    
    # LOC_013: 진입도로 미확보
    rules.append(ExclusionRule(
        rule_id="LOC_013",
        category=ExclusionCategory.LOCATION,
        sub_category="진입도로_미확보",
        description="주택 진입도로가 미확보된 주택 (출입로가 사도인 경우 등)",
        severity=ExclusionSeverity.CONDITIONAL,
        check_field="land.has_access_road",
        check_condition="== False",
        exception_condition="진입도로가 사도인 경우 LH에 무상귀속 또는 지자체에 기부채납 조건, 또는 지역권 설정 계약 및 등기된 경우",
        exception_documents=["무상귀속 확인서", "기부채납 확인서", "지역권 설정계약서"],
        reference_page=5
    ))
    
    # =========================================================================
    # ② 주택 요건 (HOUSING_CONDITION)
    # =========================================================================
    
    # HSG_001: 지하/반지하 세대
    rules.append(ExclusionRule(
        rule_id="HSG_001",
        category=ExclusionCategory.HOUSING_CONDITION,
        sub_category="지하세대",
        description="지하(반지하 포함) 세대가 있는 주택",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="building.has_basement_units",
        check_condition="== True",
        reference_page=6
    ))
    
    # HSG_002: 최저주거기준 미달
    rules.append(ExclusionRule(
        rule_id="HSG_002",
        category=ExclusionCategory.HOUSING_CONDITION,
        sub_category="최저주거기준_미달",
        description="국토교통부 공고「최저주거기준」에 미달하는 주택",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="building.meets_minimum_housing_standard",
        check_condition="== False",
        legal_basis="국토교통부 최저주거기준",
        reference_page=6
    ))
    
    # HSG_003: 불법건축물
    rules.append(ExclusionRule(
        rule_id="HSG_003",
        category=ExclusionCategory.HOUSING_CONDITION,
        sub_category="불법건축물",
        description="불법 건축물 및 법률상 제한사유(건축법 위반, 압류 및 가압류, 경매개시 등)가 있는 주택",
        severity=ExclusionSeverity.CONDITIONAL,
        check_field="building.is_illegal_construction",
        check_condition="== True",
        exception_condition="불법건축물을 치유하여 구조상 문제가 없는 경우, 법률상 제한사유 해소 가능한 경우 조건부 신청 가능",
        reference_page=6
    ))
    
    # HSG_004: 압류/가압류
    rules.append(ExclusionRule(
        rule_id="HSG_004",
        category=ExclusionCategory.HOUSING_CONDITION,
        sub_category="압류_가압류",
        description="압류 및 가압류가 있는 주택",
        severity=ExclusionSeverity.CONDITIONAL,
        check_field="registry.has_seizure",
        check_condition="== True",
        exception_condition="법률상 제한사유 해소 가능한 경우 조건부 신청 가능",
        reference_page=6
    ))
    
    # HSG_005: 경매개시
    rules.append(ExclusionRule(
        rule_id="HSG_005",
        category=ExclusionCategory.HOUSING_CONDITION,
        sub_category="경매개시",
        description="경매개시 결정이 있는 주택",
        severity=ExclusionSeverity.CONDITIONAL,
        check_field="registry.has_auction",
        check_condition="== True",
        exception_condition="법률상 제한사유 해소 가능한 경우 조건부 신청 가능",
        reference_page=6
    ))
    
    # HSG_006: 외벽 마감재 부적합 (2019.11.7 이후 허가)
    rules.append(ExclusionRule(
        rule_id="HSG_006",
        category=ExclusionCategory.HOUSING_CONDITION,
        sub_category="외벽마감재_부적합_신규",
        description="건축허가일이 2019.11.7일 이후인 경우 외벽 마감재료가 준불연재 또는 불연재 성능을 만족하지 않는 주택",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="building.exterior_material_compliant",
        check_condition="== False and permit_date >= '2019-11-07'",
        exception_documents=["외벽 단열재 시험성적서", "납품확인서", "시공사진"],
        reference_page=6
    ))
    
    # HSG_007: 외벽 마감재 부적합 (2019.11.6 이전 허가)
    rules.append(ExclusionRule(
        rule_id="HSG_007",
        category=ExclusionCategory.HOUSING_CONDITION,
        sub_category="외벽마감재_부적합_기존",
        description="건축허가일이 2019.11.6일 이전인 경우 외벽 단열재 또는 마감재가 준불연재 성능 이상을 만족하지 않는 주택",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="building.exterior_material_compliant",
        check_condition="== False and permit_date < '2019-11-07'",
        exception_documents=["외벽 단열재/마감재 시험성적서"],
        reference_page=6
    ))
    
    # HSG_008: 내진설계 미반영
    rules.append(ExclusionRule(
        rule_id="HSG_008",
        category=ExclusionCategory.HOUSING_CONDITION,
        sub_category="내진설계_미반영",
        description="관련법령(건축법 등)에 따른 내진설계가 반영되지 않은 주택",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="building.has_seismic_design",
        check_condition="== False",
        exception_condition="내진설계 기준은 건축허가 시점의 적용기준을 따름",
        legal_basis="건축법",
        reference_page=6
    ))
    
    # HSG_009: 계량기 미설치
    rules.append(ExclusionRule(
        rule_id="HSG_009",
        category=ExclusionCategory.HOUSING_CONDITION,
        sub_category="계량기_미설치",
        description="세대별 전기 및 수도계량기가 설치되지 않은 주택",
        severity=ExclusionSeverity.CONDITIONAL,
        check_field="building.has_individual_meters",
        check_condition="== False",
        exception_condition="계량기 추가설치가 가능한 경우 조건부 신청 가능",
        reference_page=6
    ))
    
    # HSG_010: 주거공간 미확보
    rules.append(ExclusionRule(
        rule_id="HSG_010",
        category=ExclusionCategory.HOUSING_CONDITION,
        sub_category="주거공간_미확보",
        description="세대 내 보일러실 및 세탁기, 냉장고 및 조리공간 확보가 어려운 주택",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="building.has_adequate_living_space",
        check_condition="== False",
        reference_page=6
    ))
    
    # HSG_011: 승강기 미설치
    rules.append(ExclusionRule(
        rule_id="HSG_011",
        category=ExclusionCategory.HOUSING_CONDITION,
        sub_category="승강기_미설치",
        description="승강기 미설치 주택",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="building.has_elevator",
        check_condition="== False",
        reference_page=6
    ))
    
    # HSG_012: 욕실 배관 매립
    rules.append(ExclusionRule(
        rule_id="HSG_012",
        category=ExclusionCategory.HOUSING_CONDITION,
        sub_category="욕실배관_매립",
        description="욕실 천장 오·배수관이 콘크리트에 매립된 주택",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="building.bathroom_pipes_embedded",
        check_condition="== True",
        reference_page=6
    ))
    
    # =========================================================================
    # ③ 기타 요건 (OTHER)
    # =========================================================================
    
    # OTH_001: 권리관계 미해소
    rules.append(ExclusionRule(
        rule_id="OTH_001",
        category=ExclusionCategory.OTHER,
        sub_category="권리관계_미해소",
        description="주택의 잔여지분이 존재하거나, 부동산 권리관계가 해소되지 않은 등 법률적 또는 사실적 분쟁이 있는 주택",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="ownership.has_unresolved_rights",
        check_condition="== True",
        reference_page=6
    ))
    
    # OTH_002: LH 직원/가족
    rules.append(ExclusionRule(
        rule_id="OTH_002",
        category=ExclusionCategory.OTHER,
        sub_category="LH직원_가족",
        description="매도신청인 본인 및 직계 존·비속, 배우자 및 배우자의 직계 존·비속이 前·現 공사 직원인 경우",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="applicant.is_lh_employee_or_family",
        check_condition="== True",
        exception_condition="퇴직직원의 경우 퇴직일로부터 5년간 적용",
        reference_page=6
    ))
    
    # OTH_003: 부정행위 제재자
    rules.append(ExclusionRule(
        rule_id="OTH_003",
        category=ExclusionCategory.OTHER,
        sub_category="부정행위_제재자",
        description="청탁 등 부정한 행위로 공사로부터 제재를 받은 행위자가 소유(중개)하는 주택",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="applicant.has_fraud_sanction",
        check_condition="== True",
        reference_page=7
    ))
    
    # OTH_004: 2회 이상 제외 주택
    rules.append(ExclusionRule(
        rule_id="OTH_004",
        category=ExclusionCategory.OTHER,
        sub_category="재신청_제한",
        description="공사에서 2회 이상 매입대상 제외한 주택으로 매입 제외 사유를 해소하지 않고 재신청한 주택",
        severity=ExclusionSeverity.CONDITIONAL,
        check_field="history.exclusion_count",
        check_condition=">= 2",
        exception_condition="매입 제외 사유를 해소한 경우 신청 가능",
        reference_page=7
    ))
    
    # OTH_005: 미분양 아파트
    rules.append(ExclusionRule(
        rule_id="OTH_005",
        category=ExclusionCategory.OTHER,
        sub_category="미분양_아파트",
        description="미분양 아파트에 해당하는 주택",
        severity=ExclusionSeverity.CONDITIONAL,
        check_field="building.is_unsold_apartment",
        check_condition="== True",
        exception_condition="도시형생활주택 아파트는 매입 가능",
        reference_page=7
    ))
    
    # OTH_006: 전세사기피해 주택
    rules.append(ExclusionRule(
        rule_id="OTH_006",
        category=ExclusionCategory.OTHER,
        sub_category="전세사기피해_주택",
        description="「전세사기 피해자 지원 및 주거안정에 관한 특별법」에 따른 전세사기피해 주택",
        severity=ExclusionSeverity.ABSOLUTE,
        check_field="building.is_jeonse_fraud_housing",
        check_condition="== True",
        legal_basis="전세사기 피해자 지원 및 주거안정에 관한 특별법",
        reference_page=7
    ))
    
    return rules


# =============================================================================
# 공고문 설정 저장/로드
# =============================================================================

class AnnouncementConfigManager:
    """공고문 설정 관리자"""
    
    CONFIG_DIR = Path("announcement_configs")
    
    def __init__(self):
        self.CONFIG_DIR.mkdir(exist_ok=True)
        self.current_config: Optional[AnnouncementConfig] = None
    
    def create_default_config(self, region: str = "경기남부") -> AnnouncementConfig:
        """기본 공고문 설정 생성"""
        config = AnnouncementConfig(
            announcement_id=f"2025_{region}_001",
            title=f"2025년도 {region}지역 기존주택 매입 공고",
            region=region,
            announcement_date="2025-07-04",
            application_start="2025-07-07",
            application_end="2025-09-30",
            min_units=15,
            max_exclusive_area=85.0,
            min_construction_start="2009-01-01",
            min_approval_date="2015-01-01",
            officetel_min_approval="2010-01-01",
            exclusion_rules=get_default_exclusion_rules_2025_gyeonggi_south(),
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            source_file="2025년경기남부지역기존주택매입공고문.pdf"
        )
        return config
    
    def save_config(self, config: AnnouncementConfig) -> Path:
        """설정 저장"""
        config.updated_at = datetime.now().isoformat()
        file_path = self.CONFIG_DIR / f"{config.announcement_id}.json"
        
        # ExclusionRule을 dict로 변환
        config_dict = {
            "announcement_id": config.announcement_id,
            "title": config.title,
            "region": config.region,
            "announcement_date": config.announcement_date,
            "application_start": config.application_start,
            "application_end": config.application_end,
            "min_units": config.min_units,
            "max_exclusive_area": config.max_exclusive_area,
            "min_construction_start": config.min_construction_start,
            "min_approval_date": config.min_approval_date,
            "officetel_min_approval": config.officetel_min_approval,
            "area_by_type": config.area_by_type,
            "exclusion_rules": [asdict(r) for r in config.exclusion_rules],
            "created_at": config.created_at,
            "updated_at": config.updated_at,
            "source_file": config.source_file,
        }
        
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(config_dict, f, ensure_ascii=False, indent=2)
        
        return file_path
    
    def load_config(self, announcement_id: str) -> Optional[AnnouncementConfig]:
        """설정 로드"""
        file_path = self.CONFIG_DIR / f"{announcement_id}.json"
        
        if not file_path.exists():
            return None
        
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # ExclusionRule 복원
        rules = []
        for r in data.get("exclusion_rules", []):
            r["category"] = ExclusionCategory(r["category"])
            r["severity"] = ExclusionSeverity(r["severity"])
            rules.append(ExclusionRule(**r))
        
        config = AnnouncementConfig(
            announcement_id=data["announcement_id"],
            title=data["title"],
            region=data["region"],
            announcement_date=data["announcement_date"],
            application_start=data["application_start"],
            application_end=data["application_end"],
            min_units=data.get("min_units", 15),
            max_exclusive_area=data.get("max_exclusive_area", 85.0),
            min_construction_start=data.get("min_construction_start", "2009-01-01"),
            min_approval_date=data.get("min_approval_date", "2015-01-01"),
            officetel_min_approval=data.get("officetel_min_approval", "2010-01-01"),
            area_by_type=data.get("area_by_type", {}),
            exclusion_rules=rules,
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            source_file=data.get("source_file", ""),
        )
        
        return config
    
    def list_configs(self) -> list[str]:
        """저장된 설정 목록"""
        return [f.stem for f in self.CONFIG_DIR.glob("*.json")]
    
    def set_current(self, config: AnnouncementConfig):
        """현재 적용 설정 지정"""
        self.current_config = config
    
    def get_current(self) -> Optional[AnnouncementConfig]:
        """현재 적용 설정 반환"""
        return self.current_config
