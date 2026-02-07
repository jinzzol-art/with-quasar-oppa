"""
시험성적서 정밀 검증 모듈 (규칙 30)
=====================================

핵심 요구사항:
1. 시험성적서는 반드시 "열방출시험 + 가스유해성 시험" 두 가지 항목이 모두 있어야 함
2. 열전도율 시험은 무조건 제외 - 열전도율만 있는 시험성적서는 무효
3. 외벽 마감재가 석재일 경우에는 시험성적서 없어도 됨 (납품확인서만 필요)
4. 각 자재별로 시험성적서와 납품확인서가 있어야 함

검증 대상 자재 (준공도면에서 추출):
- 외벽 마감재료
- 외벽 단열재료
- 필로티 마감재료 (필로티 구조인 경우)
- 필로티 단열재료 (필로티 구조인 경우)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from enum import Enum
import re


class TestType(str, Enum):
    """시험 유형"""
    HEAT_RELEASE = "열방출시험"
    GAS_TOXICITY = "가스유해성시험"
    THERMAL_CONDUCTIVITY = "열전도율시험"  # 제외 대상
    UNKNOWN = "기타시험"


class ValidationStatus(str, Enum):
    """검증 상태"""
    VALID = "유효"
    INVALID_MISSING_HEAT = "무효_열방출없음"
    INVALID_MISSING_GAS = "무효_가스유해성없음"
    INVALID_MISSING_BOTH = "무효_둘다없음"
    INVALID_THERMAL_ONLY = "무효_열전도율만있음"
    NOT_SUBMITTED = "미제출"
    STONE_EXCEPTION = "석재예외"


@dataclass
class TestCertificateInfo:
    """개별 시험성적서 정보"""
    file_name: str = ""
    material_name: str = ""  # 대상 자재명
    
    # 시험 항목 포함 여부
    has_heat_release: bool = False
    has_gas_toxicity: bool = False
    has_thermal_conductivity: bool = False
    
    # 감지된 시험 항목 원문
    detected_tests: List[str] = field(default_factory=list)
    
    # 검증 결과
    is_valid: bool = False
    validation_status: ValidationStatus = ValidationStatus.NOT_SUBMITTED
    validation_message: str = ""
    
    def validate(self) -> None:
        """열방출 + 가스유해성 조합 검증"""
        if self.has_heat_release and self.has_gas_toxicity:
            self.is_valid = True
            self.validation_status = ValidationStatus.VALID
            self.validation_message = "유효: 열방출시험 + 가스유해성시험 조합 충족"
        elif self.has_thermal_conductivity and not self.has_heat_release and not self.has_gas_toxicity:
            self.is_valid = False
            self.validation_status = ValidationStatus.INVALID_THERMAL_ONLY
            self.validation_message = "무효: 열전도율 시험만 있음 (열방출+가스유해성 필요)"
        elif not self.has_heat_release and not self.has_gas_toxicity:
            self.is_valid = False
            self.validation_status = ValidationStatus.INVALID_MISSING_BOTH
            self.validation_message = "무효: 열방출시험, 가스유해성시험 둘 다 없음"
        elif not self.has_heat_release:
            self.is_valid = False
            self.validation_status = ValidationStatus.INVALID_MISSING_HEAT
            self.validation_message = "무효: 열방출시험 없음 (가스유해성만 있음)"
        else:  # not self.has_gas_toxicity
            self.is_valid = False
            self.validation_status = ValidationStatus.INVALID_MISSING_GAS
            self.validation_message = "무효: 가스유해성시험 없음 (열방출만 있음)"


@dataclass
class MaterialTestStatus:
    """자재별 시험성적서/납품확인서 상태"""
    material_type: str  # 외벽마감재료, 외벽단열재료, 필로티마감재료, 필로티단열재료
    material_name: str  # 실제 자재명 (예: 폴리우레탄폼, 압출법보온판)
    
    # 시험성적서 상태
    test_cert_exists: bool = False
    test_cert_info: Optional[TestCertificateInfo] = None
    test_cert_valid: bool = False
    
    # 납품확인서 상태
    delivery_conf_exists: bool = False
    
    # 석재 예외 여부 (외벽 마감재가 석재면 시험성적서 생략 가능)
    is_stone_exception: bool = False
    
    # 최종 검증 결과
    needs_supplement: bool = True  # 보완 필요 여부
    supplement_reasons: List[str] = field(default_factory=list)


@dataclass
class TestCertificateValidationResult:
    """시험성적서 전체 검증 결과"""
    
    # 검증 대상 자재 목록
    required_materials: List[MaterialTestStatus] = field(default_factory=list)
    
    # 제출된 시험성적서 목록
    submitted_test_certs: List[TestCertificateInfo] = field(default_factory=list)
    
    # 전체 유효 시험성적서 존재 여부 (열방출+가스유해성 조합)
    has_any_valid_test_cert: bool = False
    
    # 납품확인서 제출 여부
    delivery_conf_submitted: bool = False
    
    # 보완 필요 항목
    supplement_items: List[str] = field(default_factory=list)
    
    # 전체 검증 통과 여부
    is_passed: bool = False
    
    # 검증 요약
    summary: str = ""


class TestCertificateValidator:
    """시험성적서 정밀 검증기"""
    
    # 열방출시험 키워드 (대소문자 무시)
    HEAT_RELEASE_KEYWORDS = [
        "열방출", "열방출량", "총열방출량", "총열방출율", "열방출률", "열방출율",
        "thr", "total heat release", "heat release rate", "hrr",
        "열량방출", "열에너지", "발열량", "발열율",
        "cone calorimeter", "콘칼로리미터",
        "ks f iso 5660", "5660", "iso 5660",  # 열방출시험 표준
        "준불연", "불연", "난연"  # 이 키워드만으로는 부족하지만 참고
    ]
    
    # 가스유해성시험 키워드 (대소문자 무시)
    GAS_TOXICITY_KEYWORDS = [
        "가스유해성", "가스유해", "가스독성", "연소가스유해성", "연소가스",
        "gas toxicity", "gas toxic", "toxicity test", "toxic gas",
        "유해가스", "유독가스", "연기독성", "연기유해성",
        "ks f 2271", "2271",  # 가스유해성시험 표준
        "마우스", "mouse", "동물시험"  # 가스유해성 시험 특징
    ]
    
    # 열전도율시험 키워드 (제외 대상) - 대소문자 무시
    THERMAL_CONDUCTIVITY_KEYWORDS = [
        "열전도율", "열전도", "열전달", "열전도계수",
        "thermal conductivity", "heat conductivity", "k-value", "k값",
        "ks l iso 8302", "8302", "iso 8302",
        "ks l 9016", "9016",
        "단열성능", "단열시험"  # 단열 관련은 열전도율일 가능성 높음
    ]
    
    # 석재 키워드
    STONE_KEYWORDS = [
        "석재", "화강석", "대리석", "현무암", "사암", "석회암",
        "granite", "marble", "stone", "basalt",
        "타일", "테라코타", "세라믹", "도자기", "자기질"  # 불연재료
    ]
    
    def __init__(self):
        self.debug_mode = True
    
    def detect_test_types(self, text: str, detected_tests: List[str] = None) -> Tuple[bool, bool, bool]:
        """
        텍스트에서 시험 유형 감지
        
        Returns:
            (has_heat_release, has_gas_toxicity, has_thermal_conductivity)
        """
        text_lower = text.lower()
        all_text = text_lower
        
        # detected_tests 리스트도 함께 분석
        if detected_tests:
            all_text += " " + " ".join([t.lower() for t in detected_tests])
        
        has_heat = self._check_keywords(all_text, self.HEAT_RELEASE_KEYWORDS)
        has_gas = self._check_keywords(all_text, self.GAS_TOXICITY_KEYWORDS)
        has_thermal = self._check_keywords(all_text, self.THERMAL_CONDUCTIVITY_KEYWORDS)
        
        if self.debug_mode:
            print(f"  [시험유형감지] 열방출: {has_heat}, 가스유해성: {has_gas}, 열전도율: {has_thermal}")
        
        return has_heat, has_gas, has_thermal
    
    def _check_keywords(self, text: str, keywords: List[str]) -> bool:
        """키워드 포함 여부 확인"""
        text_lower = text.lower()
        for kw in keywords:
            if kw.lower() in text_lower:
                return True
        return False
    
    def is_stone_material(self, material_name: str) -> bool:
        """석재 여부 확인"""
        if not material_name:
            return False
        material_lower = material_name.lower()
        return self._check_keywords(material_lower, self.STONE_KEYWORDS)
    
    def validate_single_certificate(
        self, 
        file_name: str,
        raw_text: str,
        detected_tests: List[str] = None,
        material_name: str = None,
        ai_analysis: Dict = None
    ) -> TestCertificateInfo:
        """
        단일 시험성적서 검증
        
        Args:
            file_name: 파일명
            raw_text: 시험성적서 원문 텍스트
            detected_tests: AI가 감지한 시험 항목 목록
            material_name: 대상 자재명
            ai_analysis: AI 분석 결과 (has_heat_release_test 등)
        
        Returns:
            TestCertificateInfo: 검증 결과
        """
        cert = TestCertificateInfo(
            file_name=file_name,
            material_name=material_name or "",
            detected_tests=detected_tests or []
        )
        
        # 1단계: AI 분석 결과 적용 (있으면)
        if ai_analysis:
            cert.has_heat_release = ai_analysis.get("has_heat_release_test", False) is True
            cert.has_gas_toxicity = ai_analysis.get("has_gas_toxicity_test", False) is True
            cert.has_thermal_conductivity = ai_analysis.get("has_thermal_conductivity_test", False) is True
        
        # 2단계: 텍스트 기반 추가 검증 (AI 분석이 없거나 보완용)
        text_has_heat, text_has_gas, text_has_thermal = self.detect_test_types(
            raw_text, 
            detected_tests
        )
        
        # AI와 텍스트 분석 결과 병합 (OR 조건 - 하나라도 감지되면 있는 것으로)
        cert.has_heat_release = cert.has_heat_release or text_has_heat
        cert.has_gas_toxicity = cert.has_gas_toxicity or text_has_gas
        cert.has_thermal_conductivity = cert.has_thermal_conductivity or text_has_thermal
        
        # 3단계: 최종 검증
        cert.validate()
        
        if self.debug_mode:
            print(f"  [시험성적서검증] {file_name}")
            print(f"    - 자재: {cert.material_name or '미지정'}")
            print(f"    - 열방출: {cert.has_heat_release}")
            print(f"    - 가스유해성: {cert.has_gas_toxicity}")
            print(f"    - 열전도율: {cert.has_thermal_conductivity}")
            print(f"    - 판정: {cert.validation_message}")
        
        return cert
    
    def validate_all(
        self,
        as_built_materials: Dict[str, str],  # {material_type: material_name}
        test_certs: List[Dict],  # 제출된 시험성적서 AI 분석 결과들
        delivery_confs: List[Dict],  # 제출된 납품확인서 AI 분석 결과들
        has_piloti: bool = False
    ) -> TestCertificateValidationResult:
        """
        전체 시험성적서/납품확인서 검증
        
        Args:
            as_built_materials: 준공도면에서 추출된 자재 목록
                예: {
                    "exterior_finish": "석재",
                    "exterior_insulation": "압출법보온판",
                    "piloti_finish": "폴리우레탄폼",
                    "piloti_insulation": "폴리우레탄폼"
                }
            test_certs: 제출된 시험성적서 분석 결과 목록
            delivery_confs: 제출된 납품확인서 분석 결과 목록
            has_piloti: 필로티 구조 여부
        
        Returns:
            TestCertificateValidationResult: 전체 검증 결과
        """
        result = TestCertificateValidationResult()
        
        # 1. 필요한 자재 목록 구성
        material_type_labels = {
            "exterior_finish": "외벽마감재료",
            "exterior_insulation": "외벽단열재료",
            "piloti_finish": "필로티마감재료",
            "piloti_insulation": "필로티단열재료"
        }
        
        for mat_type, mat_name in as_built_materials.items():
            if not mat_name or not mat_name.strip():
                continue
            
            # 필로티 자재는 필로티 구조일 때만 필수
            if mat_type.startswith("piloti") and not has_piloti:
                continue
            
            status = MaterialTestStatus(
                material_type=material_type_labels.get(mat_type, mat_type),
                material_name=mat_name.strip()
            )
            
            # 석재 예외 확인 (외벽 마감재가 석재면 시험성적서 생략 가능)
            if mat_type == "exterior_finish" and self.is_stone_material(mat_name):
                status.is_stone_exception = True
                if self.debug_mode:
                    print(f"  [석재예외] {mat_name} - 시험성적서 생략 가능")
            
            result.required_materials.append(status)
        
        # 2. 제출된 시험성적서 검증
        valid_test_certs = []
        for cert_data in test_certs:
            cert = self.validate_single_certificate(
                file_name=cert_data.get("file_name", "시험성적서"),
                raw_text=cert_data.get("raw_text", ""),
                detected_tests=cert_data.get("detected_tests", []),
                material_name=cert_data.get("material_name"),
                ai_analysis=cert_data
            )
            result.submitted_test_certs.append(cert)
            
            if cert.is_valid:
                valid_test_certs.append(cert)
                result.has_any_valid_test_cert = True
        
        # 3. 납품확인서 제출 여부
        result.delivery_conf_submitted = len(delivery_confs) > 0
        
        # 4. 자재별 검증 수행
        for mat_status in result.required_materials:
            mat_status.supplement_reasons = []
            
            # 4-1. 시험성적서 검증
            if mat_status.is_stone_exception:
                # 석재 예외: 시험성적서 불필요
                mat_status.test_cert_valid = True
                mat_status.test_cert_exists = True  # 예외 처리로 간주
            else:
                # 시험성적서 필수
                if not result.submitted_test_certs:
                    mat_status.test_cert_exists = False
                    mat_status.supplement_reasons.append(
                        f"{mat_status.material_type}({mat_status.material_name}) 시험성적서 미제출"
                    )
                elif not result.has_any_valid_test_cert:
                    mat_status.test_cert_exists = True
                    mat_status.test_cert_valid = False
                    
                    # 구체적인 미비 사유 작성
                    if result.submitted_test_certs:
                        cert = result.submitted_test_certs[0]  # 첫 번째 시험성적서 기준
                        if cert.validation_status == ValidationStatus.INVALID_THERMAL_ONLY:
                            mat_status.supplement_reasons.append(
                                f"{mat_status.material_type}({mat_status.material_name}) 시험성적서 무효 "
                                f"(열전도율 시험만 있음 - 열방출+가스유해성 필요)"
                            )
                        elif cert.validation_status == ValidationStatus.INVALID_MISSING_HEAT:
                            mat_status.supplement_reasons.append(
                                f"{mat_status.material_type}({mat_status.material_name}) 시험성적서 무효 "
                                f"(열방출시험 없음)"
                            )
                        elif cert.validation_status == ValidationStatus.INVALID_MISSING_GAS:
                            mat_status.supplement_reasons.append(
                                f"{mat_status.material_type}({mat_status.material_name}) 시험성적서 무효 "
                                f"(가스유해성시험 없음)"
                            )
                        else:
                            mat_status.supplement_reasons.append(
                                f"{mat_status.material_type}({mat_status.material_name}) 시험성적서 무효 "
                                f"(열방출+가스유해성 조합 필요)"
                            )
                else:
                    mat_status.test_cert_exists = True
                    mat_status.test_cert_valid = True
            
            # 4-2. 납품확인서 검증 (모든 자재 필수 - 석재 포함)
            if not result.delivery_conf_submitted:
                mat_status.delivery_conf_exists = False
                mat_status.supplement_reasons.append(
                    f"{mat_status.material_type}({mat_status.material_name}) 납품확인서 미제출"
                )
            else:
                mat_status.delivery_conf_exists = True
            
            # 4-3. 보완 필요 여부 판정
            mat_status.needs_supplement = len(mat_status.supplement_reasons) > 0
            
            # 결과에 보완 항목 추가
            result.supplement_items.extend(mat_status.supplement_reasons)
        
        # 5. 자재 정보 없는 경우 처리
        if not result.required_materials:
            # 준공도면에서 자재를 추출하지 못한 경우에도 시험성적서/납품확인서는 필요
            if not result.submitted_test_certs:
                result.supplement_items.append("시험성적서 미제출 (준공도면 자재 미확인)")
            elif not result.has_any_valid_test_cert:
                cert = result.submitted_test_certs[0] if result.submitted_test_certs else None
                if cert:
                    if cert.validation_status == ValidationStatus.INVALID_THERMAL_ONLY:
                        result.supplement_items.append(
                            "시험성적서 무효 (열전도율 시험만 있음 - 열방출+가스유해성 필요)"
                        )
                    else:
                        result.supplement_items.append(
                            "시험성적서 무효 (열방출+가스유해성 조합 필요)"
                        )
            
            if not result.delivery_conf_submitted:
                result.supplement_items.append("납품확인서 미제출 (준공도면 자재 미확인)")
        
        # 6. 최종 판정
        result.is_passed = len(result.supplement_items) == 0
        
        # 7. 요약 생성
        if result.is_passed:
            result.summary = "시험성적서/납품확인서 검증 통과"
        else:
            # 중복 제거
            unique_items = list(dict.fromkeys(result.supplement_items))
            result.supplement_items = unique_items
            result.summary = f"보완 필요: {'; '.join(unique_items)}"
        
        return result


def analyze_test_certificate_text(text: str) -> Dict:
    """
    시험성적서 텍스트 분석 (Gemini/Claude 프롬프트용 참고 함수)
    
    AI 분석 전 사전 검증 또는 AI 결과 보완용
    """
    validator = TestCertificateValidator()
    has_heat, has_gas, has_thermal = validator.detect_test_types(text)
    
    return {
        "has_heat_release_test": has_heat,
        "has_gas_toxicity_test": has_gas,
        "has_thermal_conductivity_test": has_thermal,
        "is_valid": has_heat and has_gas,
        "analysis_note": (
            "유효 (열방출+가스유해성 조합 충족)" if has_heat and has_gas
            else "무효 (열전도율만 있음)" if has_thermal and not has_heat and not has_gas
            else "무효 (열방출+가스유해성 조합 미충족)"
        )
    }


# 테스트 코드
if __name__ == "__main__":
    print("=" * 70)
    print("시험성적서 정밀 검증 테스트")
    print("=" * 70)
    
    validator = TestCertificateValidator()
    
    # 테스트 케이스 1: 유효한 시험성적서 (열방출 + 가스유해성)
    print("\n[테스트 1] 유효한 시험성적서")
    cert1 = validator.validate_single_certificate(
        file_name="시험성적서_폴리우레탄.pdf",
        raw_text="KS F ISO 5660 열방출시험 총열방출량 8 MJ/㎡, KS F 2271 가스유해성시험 통과",
        detected_tests=["열방출시험", "가스유해성시험"],
        material_name="폴리우레탄폼"
    )
    print(f"  결과: {cert1.validation_status.value}")
    
    # 테스트 케이스 2: 무효 - 열전도율만 있음
    print("\n[테스트 2] 무효 - 열전도율만 있음")
    cert2 = validator.validate_single_certificate(
        file_name="시험성적서_단열재.pdf",
        raw_text="KS L 9016 열전도율 시험 thermal conductivity 0.034 W/mK",
        detected_tests=["열전도율시험"],
        material_name="압출법보온판"
    )
    print(f"  결과: {cert2.validation_status.value}")
    
    # 테스트 케이스 3: 무효 - 열방출만 있고 가스유해성 없음
    print("\n[테스트 3] 무효 - 열방출만 있음")
    cert3 = validator.validate_single_certificate(
        file_name="시험성적서_불완전.pdf",
        raw_text="열방출시험 THR 5.2 MJ/㎡",
        detected_tests=["열방출시험"],
        material_name="글라스울"
    )
    print(f"  결과: {cert3.validation_status.value}")
    
    # 테스트 케이스 4: 전체 검증 (석재 예외 포함)
    print("\n[테스트 4] 전체 검증 - 석재 예외 적용")
    result = validator.validate_all(
        as_built_materials={
            "exterior_finish": "화강석",  # 석재 → 시험성적서 생략 가능
            "exterior_insulation": "압출법보온판",
            "piloti_finish": "폴리우레탄폼",
            "piloti_insulation": "폴리우레탄폼"
        },
        test_certs=[
            {
                "file_name": "시험성적서.pdf",
                "has_heat_release_test": True,
                "has_gas_toxicity_test": True,
                "detected_tests": ["열방출시험", "가스유해성시험"],
                "material_name": "폴리우레탄폼"
            }
        ],
        delivery_confs=[
            {"file_name": "납품확인서.pdf"}
        ],
        has_piloti=True
    )
    print(f"  전체 통과: {result.is_passed}")
    print(f"  요약: {result.summary}")
    if result.supplement_items:
        print("  보완 항목:")
        for item in result.supplement_items:
            print(f"    - {item}")
    
    # 테스트 케이스 5: 열전도율만 있는 시험성적서로 전체 검증
    print("\n[테스트 5] 열전도율만 있는 시험성적서")
    result2 = validator.validate_all(
        as_built_materials={
            "exterior_finish": "알루미늄복합패널",
            "exterior_insulation": "비드법보온판"
        },
        test_certs=[
            {
                "file_name": "시험성적서_열전도율.pdf",
                "has_heat_release_test": False,
                "has_gas_toxicity_test": False,
                "has_thermal_conductivity_test": True,
                "detected_tests": ["열전도율시험"],
                "material_name": "비드법보온판"
            }
        ],
        delivery_confs=[],
        has_piloti=False
    )
    print(f"  전체 통과: {result2.is_passed}")
    print(f"  요약: {result2.summary}")
    if result2.supplement_items:
        print("  보완 항목:")
        for item in result2.supplement_items:
            print(f"    - {item}")
    
    print("\n" + "=" * 70)
    print("테스트 완료")
    print("=" * 70)
