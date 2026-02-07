#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF 텍스트 기반 소유자 추출 테스트
실행: python test_text_extraction.py
"""

import sys

def test_text_extraction():
    """PDF 텍스트에서 소유자 정보 추출 테스트"""
    from core.unified_pdf_analyzer import UnifiedPDFAnalyzer
    
    print("=" * 70)
    print("[TEST] PDF 텍스트 기반 소유자 추출 테스트")
    print("=" * 70)
    
    analyzer = UnifiedPDFAnalyzer(provider="claude")
    
    # ========================================
    # 테스트 1: 법인 키워드 감지
    # ========================================
    print("\n[Test 1] 법인 키워드 감지")
    print("-" * 70)
    
    test_texts = [
        "소유자 성명: 주식회사 대한건설",
        "소유주 상호: (주)삼성개발",
        "성명: 한양건설 주식회사",
        "소유자: 홍길동",  # 개인
        "소유자 성명 (주)미래토건 서울시 강남구",
        "상호: 유한회사 삼성산업",
    ]
    
    for text in test_texts:
        is_corp = analyzer._detect_corporation_from_text(text)
        print(f"  '{text[:40]}...' -> 법인={is_corp}")
    
    # ========================================
    # 테스트 2: 소유자 이름 직접 추출
    # ========================================
    print("\n[Test 2] 소유자 이름 직접 추출")
    print("-" * 70)
    
    test_texts2 = [
        "주택매도신청서\n소유자 성명: 주식회사 대한건설\n생년월일: \n주소: 서울시",
        "소유주 상호: (주)삼성개발 생년월일 920101",
        "성명 한양건설 주식회사 주소 서울시 강남구",
        "소유자: 홍길동 생년월일 801215",
    ]
    
    for text in test_texts2:
        name = analyzer._extract_owner_name_from_text(text)
        print(f"  텍스트: '{text[:30]}...'")
        print(f"  -> 추출된 이름: '{name}'")
        print()
    
    # ========================================
    # 테스트 3: 법인명 직접 추출
    # ========================================
    print("\n[Test 3] 법인명 직접 추출")
    print("-" * 70)
    
    test_texts3 = [
        "주식회사 대한건설이 소유한 건물입니다.",
        "(주)삼성개발 명의로 등기된 토지",
        "한양건설 주식회사가 매도합니다.",
        "유한회사 미래산업 대표이사",
        "사단법인 한국주택협회",
        "개인 소유자 홍길동입니다.",  # 법인 아님
    ]
    
    for text in test_texts3:
        corp_name = analyzer._extract_corporation_name_from_text(text)
        print(f"  텍스트: '{text[:40]}...'")
        print(f"  -> 추출된 법인명: '{corp_name}'")
        print()
    
    # ========================================
    # 테스트 4: 이름에서 법인 여부 감지
    # ========================================
    print("\n[Test 4] 이름에서 법인 여부 감지")
    print("-" * 70)
    
    names = [
        "주식회사 대한건설",
        "(주)삼성개발",
        "한양건설",
        "미래산업",
        "홍길동",
        "김철수",
        "유한회사 삼성",
    ]
    
    for name in names:
        is_corp = analyzer._detect_corporation_from_name(name)
        print(f"  '{name}' -> 법인={is_corp}")
    
    print("\n" + "=" * 70)
    print("[TEST COMPLETE] PDF 텍스트 기반 소유자 추출 테스트 완료")
    print("=" * 70)


if __name__ == "__main__":
    try:
        test_text_extraction()
    except Exception as e:
        print(f"\n[ERROR] 테스트 중 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
