#!/usr/bin/env python3
"""
법인 자동 감지 테스트
실행: python test_corporation_detection.py
"""

import sys
from datetime import date

def test_corporation_auto_detection():
    """법인 여부 자동 감지 테스트"""
    from core.enhanced_validation_engine import EnhancedValidator
    from core.data_models import PublicHousingReviewResult, CorporateDocuments, DocumentBase
    
    print("=" * 70)
    print("[TEST] 법인 자동 감지 테스트")
    print("=" * 70)
    
    announcement_date = "2025-07-04"
    engine = EnhancedValidator(announcement_date)
    
    # ========================================
    # 테스트 1: 법인 서류 있음 (사업자등록증만)
    # ========================================
    print("\n[테스트 1] 법인 사업자등록증만 있는 경우")
    print("-" * 70)
    
    result = PublicHousingReviewResult(review_date="2025-07-04")
    result.housing_sale_application.exists = True
    result.corporate_documents.business_registration.exists = True  # 법인 서류!
    
    print(f"검증 전 is_corporation: {result.corporate_documents.is_corporation}")
    
    engine.validate(result)
    
    print(f"검증 후 is_corporation: {result.corporate_documents.is_corporation}")
    
    # 개인 인감증명서 오류가 없어야 함
    individual_seal_errors = [
        item for item in result.supplementary_documents
        if "소유자 인감증명서" in item.document_name or "본인발급용 인감증명서" in item.document_name
    ]
    
    # 개인 정보 오류가 없어야 함
    individual_info_errors = [
        item for item in result.supplementary_documents
        if "소유자 정보 미기재" in item.reason
    ]
    
    if result.corporate_documents.is_corporation:
        print("[PASS] 법인으로 자동 감지됨")
    else:
        print("[FAIL] 법인 감지 실패")
    
    if not individual_seal_errors:
        print("[PASS] 개인 인감증명서 오류 없음")
    else:
        print(f"[FAIL] 개인 인감증명서 오류 발생: {individual_seal_errors[0].reason}")
    
    if not individual_info_errors:
        print("[PASS] 개인 정보 오류 없음")
    else:
        print(f"[FAIL] 개인 정보 오류 발생: {individual_info_errors[0].reason}")
    
    # ========================================
    # 테스트 2: 법인 서류 있음 (법인등기사항증명서만)
    # ========================================
    print("\n[테스트 2] 법인등기사항증명서만 있는 경우")
    print("-" * 70)
    
    result2 = PublicHousingReviewResult(review_date="2025-07-04")
    result2.housing_sale_application.exists = True
    result2.corporate_documents.corporate_registry.exists = True  # 법인 서류!
    
    print(f"검증 전 is_corporation: {result2.corporate_documents.is_corporation}")
    
    engine.validate(result2)
    
    print(f"검증 후 is_corporation: {result2.corporate_documents.is_corporation}")
    
    if result2.corporate_documents.is_corporation:
        print("[PASS] 법인으로 자동 감지됨")
    else:
        print("[FAIL] 법인 감지 실패")
    
    # ========================================
    # 테스트 3: 법인 서류 있음 (법인인감증명서만)
    # ========================================
    print("\n[테스트 3] 법인인감증명서만 있는 경우")
    print("-" * 70)
    
    result3 = PublicHousingReviewResult(review_date="2025-07-04")
    result3.housing_sale_application.exists = True
    result3.corporate_documents.corporate_seal_certificate.exists = True  # 법인 서류!
    
    print(f"검증 전 is_corporation: {result3.corporate_documents.is_corporation}")
    
    engine.validate(result3)
    
    print(f"검증 후 is_corporation: {result3.corporate_documents.is_corporation}")
    
    individual_seal_errors = [
        item for item in result3.supplementary_documents
        if "소유자 인감증명서" in item.document_name or "본인발급용 인감증명서" in item.document_name
    ]
    
    if result3.corporate_documents.is_corporation:
        print("[PASS] 법인으로 자동 감지됨")
    else:
        print("[FAIL] 법인 감지 실패")
    
    if not individual_seal_errors:
        print("[PASS] 개인 인감증명서 오류 없음 (법인인감증명서가 있으므로)")
    else:
        print(f"[FAIL] 개인 인감증명서 오류 발생: {individual_seal_errors[0].reason}")
    
    # ========================================
    # 테스트 4: 개인 (법인 서류 없음)
    # ========================================
    print("\n[테스트 4] 법인 서류 없음 (개인)")
    print("-" * 70)
    
    result4 = PublicHousingReviewResult(review_date="2025-07-04")
    result4.housing_sale_application.exists = True
    # 법인 서류 없음
    
    print(f"검증 전 is_corporation: {result4.corporate_documents.is_corporation}")
    
    engine.validate(result4)
    
    print(f"검증 후 is_corporation: {result4.corporate_documents.is_corporation}")
    
    if not result4.corporate_documents.is_corporation:
        print("[PASS] 개인으로 정확히 인식됨")
    else:
        print("[FAIL] 법인으로 잘못 인식됨")
    
    # 개인 인감증명서 오류가 있어야 함 (제출 안했으므로)
    individual_seal_errors = [
        item for item in result4.supplementary_documents
        if "소유자 인감증명서" in item.document_name or "본인발급용 인감증명서" in item.document_name
    ]
    
    if individual_seal_errors:
        print("[PASS] 개인 인감증명서 오류 정상 발생 (제출 안했으므로)")
    else:
        print("[FAIL] 개인 인감증명서 오류가 발생해야 하는데 없음")
    
    print("\n" + "=" * 70)
    print("[DONE] 법인 자동 감지 테스트 완료")
    print("=" * 70)


if __name__ == "__main__":
    try:
        test_corporation_auto_detection()
    except Exception as e:
        print(f"\n[ERROR] 테스트 중 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
