#!/usr/bin/env python3
"""
시험성적서 검증 로직 테스트 스크립트 v2

★ 핵심 검증 규칙 (규칙 30) ★
1. 시험성적서는 반드시 "열방출시험 + 가스유해성시험" 두 가지 항목이 모두 있어야 유효
2. 열전도율 시험은 무조건 제외 - 열전도율만 있는 시험성적서는 무효
3. 외벽 마감재가 석재일 경우에는 시험성적서 없어도 됨 (납품확인서만 필요)

테스트 시나리오:
1. 유효 - 열방출 + 가스유해성 모두 있음
2. 무효 - 열전도율만 있음 (가장 중요한 필터링 케이스)
3. 무효 - 열방출만 있음
4. 무효 - 가스유해성만 있음
5. 유효 - 열방출 + 가스유해성 + 열전도율 (조합 충족)
6. 석재 예외 - 시험성적서 없어도 됨
"""

from dataclasses import dataclass, field
from typing import List
from enum import Enum


class ValidationStatus(str, Enum):
    VALID = "유효"
    INVALID_THERMAL_ONLY = "무효_열전도율만"
    INVALID_MISSING_HEAT = "무효_열방출없음"
    INVALID_MISSING_GAS = "무효_가스유해성없음"
    INVALID_MISSING_BOTH = "무효_둘다없음"
    NOT_SUBMITTED = "미제출"
    STONE_EXCEPTION = "석재예외"


@dataclass
class TestCertResult:
    """시험성적서 검증 결과"""
    has_heat_release: bool = False
    has_gas_toxicity: bool = False
    has_thermal_conductivity: bool = False
    detected_tests: List[str] = field(default_factory=list)
    status: ValidationStatus = ValidationStatus.NOT_SUBMITTED
    is_valid: bool = False
    message: str = ""
    
    def validate(self):
        """핵심 검증 로직"""
        # 1. 열방출 + 가스유해성 둘 다 있으면 유효
        if self.has_heat_release and self.has_gas_toxicity:
            self.is_valid = True
            self.status = ValidationStatus.VALID
            self.message = "✅ 유효: 열방출시험 + 가스유해성시험 조합 충족"
        
        # 2. 열전도율만 있으면 무효 (가장 중요한 필터링)
        elif self.has_thermal_conductivity and not self.has_heat_release and not self.has_gas_toxicity:
            self.is_valid = False
            self.status = ValidationStatus.INVALID_THERMAL_ONLY
            self.message = "❌ 무효: 열전도율 시험만 있음 (열방출+가스유해성 필요)"
        
        # 3. 열방출만 있고 가스유해성 없음
        elif self.has_heat_release and not self.has_gas_toxicity:
            self.is_valid = False
            self.status = ValidationStatus.INVALID_MISSING_GAS
            self.message = "❌ 무효: 가스유해성 시험 없음 (열방출만 있음)"
        
        # 4. 가스유해성만 있고 열방출 없음
        elif self.has_gas_toxicity and not self.has_heat_release:
            self.is_valid = False
            self.status = ValidationStatus.INVALID_MISSING_HEAT
            self.message = "❌ 무효: 열방출시험 없음 (가스유해성만 있음)"
        
        # 5. 둘 다 없음
        else:
            self.is_valid = False
            self.status = ValidationStatus.INVALID_MISSING_BOTH
            self.message = "❌ 무효: 열방출시험, 가스유해성 시험 둘 다 없음"


def detect_tests_from_text(detected_tests: List[str]) -> tuple:
    """detected_tests에서 시험 유형 감지"""
    detected_text = " ".join([t.lower() for t in detected_tests])
    
    # 열방출시험 키워드
    heat_keywords = ["열방출", "총열방출", "열방출률", "thr", "heat release", "hrr",
                     "발열량", "5660", "콘칼로리미터", "cone calorimeter"]
    has_heat = any(kw.lower() in detected_text for kw in heat_keywords)
    
    # 가스유해성시험 키워드
    gas_keywords = ["가스유해", "가스독성", "gas toxic", "연소가스", "유해가스",
                    "연기독성", "2271", "마우스", "mouse"]
    has_gas = any(kw.lower() in detected_text for kw in gas_keywords)
    
    # 열전도율시험 키워드 (제외 대상)
    thermal_keywords = ["열전도율", "열전도", "thermal conductivity", "k-value",
                        "단열성능", "단열시험", "8302", "9016"]
    has_thermal = any(kw.lower() in detected_text for kw in thermal_keywords)
    
    return has_heat, has_gas, has_thermal


def is_stone_material(material_name: str) -> bool:
    """석재 여부 확인"""
    stone_keywords = ["석재", "화강석", "대리석", "현무암", "사암", "석회암",
                      "granite", "marble", "stone", "타일", "테라코타"]
    return any(kw.lower() in material_name.lower() for kw in stone_keywords)


def run_test(test_name: str, detected_tests: List[str], material_name: str = None, 
             expected_valid: bool = None, expected_status: ValidationStatus = None):
    """테스트 실행"""
    print(f"\n{'='*60}")
    print(f"테스트: {test_name}")
    print(f"{'='*60}")
    print(f"감지된 시험: {detected_tests}")
    
    # 석재 예외 체크
    if material_name and is_stone_material(material_name):
        print(f"자재: {material_name} → 석재 예외 적용 (시험성적서 불필요)")
        result = TestCertResult(status=ValidationStatus.STONE_EXCEPTION, is_valid=True, 
                                 message="ℹ️ 석재 예외: 시험성적서 생략 가능")
    else:
        # 텍스트 분석
        has_heat, has_gas, has_thermal = detect_tests_from_text(detected_tests)
        
        result = TestCertResult(
            has_heat_release=has_heat,
            has_gas_toxicity=has_gas,
            has_thermal_conductivity=has_thermal,
            detected_tests=detected_tests
        )
        result.validate()
    
    print(f"\n결과:")
    print(f"  - 열방출시험: {'✅' if result.has_heat_release else '❌'}")
    print(f"  - 가스유해성시험: {'✅' if result.has_gas_toxicity else '❌'}")
    print(f"  - 열전도율시험: {'⚠️ (제외대상)' if result.has_thermal_conductivity else '❌'}")
    print(f"  - 판정: {result.message}")
    print(f"  - 상태: {result.status.value}")
    
    # 예상 결과 검증
    if expected_valid is not None:
        check = "✅ PASS" if result.is_valid == expected_valid else "❌ FAIL"
        print(f"\n검증: 유효성 {check} (예상: {expected_valid}, 실제: {result.is_valid})")
    
    if expected_status is not None:
        check = "✅ PASS" if result.status == expected_status else "❌ FAIL"
        print(f"검증: 상태 {check} (예상: {expected_status.value}, 실제: {result.status.value})")
    
    return result


def main():
    print("=" * 70)
    print("시험성적서 검증 로직 테스트 v2")
    print("=" * 70)
    print("\n★ 핵심 규칙: 열방출시험 + 가스유해성시험 조합 필수")
    print("★ 열전도율 시험만 있으면 무조건 무효")
    
    # 테스트 1: 유효 - 열방출 + 가스유해성
    run_test(
        "시나리오 1: 유효 (열방출 + 가스유해성)",
        ["열방출시험", "가스유해성시험"],
        expected_valid=True,
        expected_status=ValidationStatus.VALID
    )
    
    # 테스트 2: 무효 - 열전도율만 (가장 중요한 필터링)
    run_test(
        "시나리오 2: 무효 (열전도율만 - 핵심 필터링)",
        ["열전도율시험", "KS L 9016"],
        expected_valid=False,
        expected_status=ValidationStatus.INVALID_THERMAL_ONLY
    )
    
    # 테스트 3: 무효 - 열방출만
    run_test(
        "시나리오 3: 무효 (열방출만)",
        ["열방출시험", "THR", "총열방출량"],
        expected_valid=False,
        expected_status=ValidationStatus.INVALID_MISSING_GAS
    )
    
    # 테스트 4: 무효 - 가스유해성만
    run_test(
        "시나리오 4: 무효 (가스유해성만)",
        ["가스유해성시험", "KS F 2271"],
        expected_valid=False,
        expected_status=ValidationStatus.INVALID_MISSING_HEAT
    )
    
    # 테스트 5: 유효 - 열방출 + 가스유해성 + 열전도율 (조합 충족)
    run_test(
        "시나리오 5: 유효 (열방출 + 가스유해성 + 열전도율)",
        ["열방출시험", "가스유해성시험", "열전도율시험"],
        expected_valid=True,
        expected_status=ValidationStatus.VALID
    )
    
    # 테스트 6: 무효 - 아무것도 없음
    run_test(
        "시나리오 6: 무효 (시험 항목 없음)",
        ["기타시험", "인장강도"],
        expected_valid=False,
        expected_status=ValidationStatus.INVALID_MISSING_BOTH
    )
    
    # 테스트 7: 석재 예외
    run_test(
        "시나리오 7: 석재 예외 (시험성적서 불필요)",
        [],  # 시험성적서 없음
        material_name="화강석",
        expected_valid=True,
        expected_status=ValidationStatus.STONE_EXCEPTION
    )
    
    # 테스트 8: 영문 키워드
    run_test(
        "시나리오 8: 영문 키워드 (Total Heat Release + Gas Toxicity)",
        ["Total Heat Release", "Gas Toxicity Test"],
        expected_valid=True,
        expected_status=ValidationStatus.VALID
    )
    
    # 테스트 9: KS 표준 번호로 감지
    run_test(
        "시나리오 9: KS 표준 번호 (ISO 5660 + KS F 2271)",
        ["KS F ISO 5660", "KS F 2271"],
        expected_valid=True,
        expected_status=ValidationStatus.VALID
    )
    
    # 테스트 10: 열전도율만 (다양한 표현)
    run_test(
        "시나리오 10: 열전도율만 (다양한 표현)",
        ["Thermal Conductivity", "단열성능시험", "K-value"],
        expected_valid=False,
        expected_status=ValidationStatus.INVALID_THERMAL_ONLY
    )
    
    print("\n" + "=" * 70)
    print("테스트 완료!")
    print("=" * 70)


if __name__ == "__main__":
    main()
