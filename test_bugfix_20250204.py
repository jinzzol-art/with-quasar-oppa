#!/usr/bin/env python3
"""
버그 수정 검증 스크립트
실행: python test_bugfix_20250204.py
"""

import sys
from datetime import date

# 공고일 파싱 테스트
def test_announcement_date_parsing():
    """공고일 파싱 테스트"""
    from core.announcement_parser import AnnouncementPDFParser
    
    print("=" * 60)
    print("테스트 1: 공고일 파싱")
    print("=" * 60)
    
    parser = AnnouncementPDFParser()
    
    test_cases = [
        ("2025. 7. 4.\n", "2025-07-04", "문서 끝 형식"),
        ("공고일: 2025.7.4", "2025-07-04", "공고일 명시 형식"),
        ("2025년 7월 4일 공고\n기타 내용", "2025-07-04", "년월일 형식"),
    ]
    
    for text, expected, description in test_cases:
        result = parser._extract_date(text, "공고")
        status = "✅ PASS" if result == expected else "❌ FAIL"
        print(f"{status} {description}")
        print(f"   입력: {text.strip()}")
        print(f"   기대값: {expected}")
        print(f"   실제값: {result}")
        print()
    
    print()


# 법인/개인 검증 로직 테스트
def test_corporate_validation():
    """법인 검증 로직 테스트"""
    from core.enhanced_validation_engine import EnhancedValidationEngine
    from core.data_models import VerificationResult, CorporateDocuments, DocumentBase
    
    print("=" * 60)
    print("테스트 2: 법인/개인 구분 검증")
    print("=" * 60)
    
    # 공고일 설정
    announcement_date = date(2025, 7, 4)
    engine = EnhancedValidationEngine(announcement_date)
    
    # 테스트 케이스 1: 개인 소유자
    print("케이스 1: 개인 소유자")
    result_individual = VerificationResult()
    result_individual.housing_sale_application.exists = True
    result_individual.corporate_documents.is_corporation = False
    result_individual.owner_identity.seal_certificate.exists = False
    
    engine.validate(result_individual)
    
    has_individual_seal_error = any(
        "소유자 인감증명서" in item.document_name
        for item in result_individual.supplementary_documents
    )
    
    status = "✅ PASS" if has_individual_seal_error else "❌ FAIL"
    print(f"{status} 개인 인감증명서 미제출 오류 발생 (예상: True, 실제: {has_individual_seal_error})")
    print()
    
    # 테스트 케이스 2: 법인 소유자
    print("케이스 2: 법인 소유자")
    result_corporate = VerificationResult()
    result_corporate.housing_sale_application.exists = True
    result_corporate.corporate_documents.is_corporation = True
    result_corporate.corporate_documents.corporate_seal_certificate.exists = True
    result_corporate.corporate_documents.business_registration.exists = True
    result_corporate.corporate_documents.corporate_registry.exists = True
    
    engine.validate(result_corporate)
    
    has_individual_seal_error = any(
        "소유자 인감증명서" in item.document_name and "서류 미제출" in item.reason
        for item in result_corporate.supplementary_documents
    )
    has_corporate_seal_error = any(
        "법인용 인감증명서" in item.document_name and "서류 미제출" in item.reason
        for item in result_corporate.supplementary_documents
    )
    
    status_1 = "✅ PASS" if not has_individual_seal_error else "❌ FAIL"
    status_2 = "✅ PASS" if not has_corporate_seal_error else "❌ FAIL"
    
    print(f"{status_1} 개인 인감증명서 오류 없음 (예상: False, 실제: {has_individual_seal_error})")
    print(f"{status_2} 법인 인감증명서 오류 없음 (예상: False, 실제: {has_corporate_seal_error})")
    
    print()


# 날짜 유효성 검증 테스트
def test_date_validity():
    """작성일자 유효성 검증 테스트"""
    from core.enhanced_validation_engine import EnhancedValidationEngine
    
    print("=" * 60)
    print("테스트 3: 작성일자 유효성 검증")
    print("=" * 60)
    
    # 공고일: 2025-07-04
    announcement_date = date(2025, 7, 4)
    engine = EnhancedValidationEngine(announcement_date)
    
    test_cases = [
        ("2025-07-05", True, "공고일 이후"),
        ("2025-07-04", True, "공고일 당일"),
        ("2025-07-03", False, "공고일 이전"),
        ("2025-06-30", False, "공고일 이전"),
    ]
    
    for date_str, expected_valid, description in test_cases:
        is_valid, confidence = engine._check_date_validity(date_str)
        status = "✅ PASS" if is_valid == expected_valid else "❌ FAIL"
        print(f"{status} {description}")
        print(f"   날짜: {date_str}")
        print(f"   기대: {expected_valid}, 실제: {is_valid}, 신뢰도: {confidence}")
        print()
    
    print()


def main():
    """메인 테스트 실행"""
    print("\n" + "=" * 60)
    print("버그 수정 검증 시작")
    print("=" * 60 + "\n")
    
    try:
        test_announcement_date_parsing()
        test_corporate_validation()
        test_date_validity()
        
        print("=" * 60)
        print("✅ 모든 테스트 완료")
        print("=" * 60)
        print("\n주의: 이 테스트는 기본적인 로직만 검증합니다.")
        print("실제 PDF 파일로 전체 통합 테스트를 권장합니다.")
        
    except Exception as e:
        print(f"\n❌ 테스트 중 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
