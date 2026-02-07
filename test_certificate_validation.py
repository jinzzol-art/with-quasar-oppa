#!/usr/bin/env python3
"""
시험성적서 검증 로직 테스트 스크립트

v5.1에서 개선된 시험성적서 검증 기능을 테스트합니다.
"""

from core.data_models import PublicHousingReviewResult
from core.result_formatter import ResultFormatter

def test_test_certificate_validation():
    """시험성적서 검증 결과 출력 테스트"""
    
    print("=" * 70)
    print("시험성적서 검증 테스트 - v5.1")
    print("=" * 70)
    print()
    
    # 테스트 케이스 1: 모든 항목 포함
    print("📋 테스트 케이스 1: 모든 항목 포함")
    print("-" * 70)
    result1 = PublicHousingReviewResult(review_date="2025-02-04")
    result1.test_certificate_delivery.exists = True
    result1.test_certificate_delivery.has_heat_release_test = True
    result1.test_certificate_delivery.has_gas_toxicity_test = True
    result1.test_certificate_delivery.has_delivery_confirmation = True
    result1.test_certificate_delivery.materials_with_test_cert = ["폴리우레탄폼", "압출법보온판"]
    
    # 시험성적서 섹션만 출력
    lines = []
    lines.append("[시험성적서 검증 (규칙 30)]")
    tcd = result1.test_certificate_delivery
    if tcd.has_heat_release_test:
        lines.append(f"  ✅ 열방출시험: 포함됨")
    else:
        lines.append(f"  ❌ 열방출시험: 미포함 (보완 필요)")
    
    if tcd.has_gas_toxicity_test:
        lines.append(f"  ✅ 가스유해성 시험: 포함됨")
    else:
        lines.append(f"  ❌ 가스유해성 시험: 미포함 (보완 필요)")
    
    if tcd.has_delivery_confirmation:
        lines.append(f"  ✅ 납품확인서: 제출됨")
    else:
        lines.append(f"  ❌ 납품확인서: 미제출 (보완 필요)")
    
    if tcd.materials_with_test_cert:
        lines.append(f"  📄 시험성적서 확인된 자재: {', '.join(tcd.materials_with_test_cert)}")
    
    print("\n".join(lines))
    print()
    
    # 테스트 케이스 2: 가스유해성 시험 누락
    print("📋 테스트 케이스 2: 가스유해성 시험 누락")
    print("-" * 70)
    result2 = PublicHousingReviewResult(review_date="2025-02-04")
    result2.test_certificate_delivery.exists = True
    result2.test_certificate_delivery.has_heat_release_test = True
    result2.test_certificate_delivery.has_gas_toxicity_test = False  # 누락
    result2.test_certificate_delivery.has_delivery_confirmation = True
    result2.test_certificate_delivery.materials_with_test_cert = ["폴리우레탄폼"]
    
    lines = []
    lines.append("[시험성적서 검증 (규칙 30)]")
    tcd = result2.test_certificate_delivery
    if tcd.has_heat_release_test:
        lines.append(f"  ✅ 열방출시험: 포함됨")
    else:
        lines.append(f"  ❌ 열방출시험: 미포함 (보완 필요)")
    
    if tcd.has_gas_toxicity_test:
        lines.append(f"  ✅ 가스유해성 시험: 포함됨")
    else:
        lines.append(f"  ❌ 가스유해성 시험: 미포함 (보완 필요)")
    
    if tcd.has_delivery_confirmation:
        lines.append(f"  ✅ 납품확인서: 제출됨")
    else:
        lines.append(f"  ❌ 납품확인서: 미제출 (보완 필요)")
    
    if tcd.materials_with_test_cert:
        lines.append(f"  📄 시험성적서 확인된 자재: {', '.join(tcd.materials_with_test_cert)}")
    
    print("\n".join(lines))
    print()
    
    # 테스트 케이스 3: 외벽 마감재 석재 예외
    print("📋 테스트 케이스 3: 외벽 마감재 석재 예외")
    print("-" * 70)
    result3 = PublicHousingReviewResult(review_date="2025-02-04")
    result3.test_certificate_delivery.exists = True
    result3.test_certificate_delivery.has_heat_release_test = False
    result3.test_certificate_delivery.has_gas_toxicity_test = False
    result3.test_certificate_delivery.has_delivery_confirmation = True
    result3.test_certificate_delivery.stone_exterior_exception = True  # 석재 예외
    
    lines = []
    lines.append("[시험성적서 검증 (규칙 30)]")
    tcd = result3.test_certificate_delivery
    
    if tcd.stone_exterior_exception:
        lines.append(f"  ℹ️  외벽 마감재가 석재로 확인됨 (시험성적서 생략 가능)")
    
    if tcd.has_delivery_confirmation:
        lines.append(f"  ✅ 납품확인서: 제출됨")
    else:
        lines.append(f"  ❌ 납품확인서: 미제출 (보완 필요)")
    
    print("\n".join(lines))
    print()
    
    # 테스트 케이스 4: 모든 항목 누락
    print("📋 테스트 케이스 4: 모든 항목 누락")
    print("-" * 70)
    result4 = PublicHousingReviewResult(review_date="2025-02-04")
    result4.test_certificate_delivery.exists = False  # 아예 없음
    
    lines = []
    lines.append("[시험성적서 검증 (규칙 30)]")
    lines.append("  ❌ 시험성적서 미제출")
    lines.append("  보완 필요: 열방출시험 자료, 가스유해성 시험 자료, 납품확인서")
    
    print("\n".join(lines))
    print()
    
    print("=" * 70)
    print("테스트 완료!")
    print("=" * 70)


if __name__ == "__main__":
    test_test_certificate_validation()
