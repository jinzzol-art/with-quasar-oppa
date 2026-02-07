#!/usr/bin/env python3
"""
소유자 정보 전용 추출기 테스트
실행: python test_owner_extractor.py <PDF_파일_경로>
"""

import sys
import os

def test_owner_extractor(pdf_path: str):
    """소유자 정보 추출 테스트"""
    from core.owner_info_extractor import extract_owner_info_from_pdf
    
    print("=" * 70)
    print("[TEST] 소유자 정보 전용 추출기 테스트")
    print("=" * 70)
    print(f"PDF 파일: {pdf_path}")
    print("-" * 70)
    
    # 소유자 정보 추출
    result = extract_owner_info_from_pdf(pdf_path)
    
    print("\n[추출 결과]")
    print("-" * 70)
    print(f"  이름: {result.name or '[미추출]'}")
    print(f"  생년월일: {result.birth_date or '[미추출]'}")
    print(f"  주소: {result.address or '[미추출]'}")
    print(f"  연락처: {result.phone or '[미추출]'}")
    print(f"  이메일: {result.email or '[미추출]'}")
    print(f"  법인 여부: {result.is_corporation}")
    print(f"  인감 존재: {result.has_seal}")
    print(f"  신뢰도: {result.confidence:.0%}")
    
    # 결과 판정
    print("\n[판정]")
    print("-" * 70)
    
    filled_items = []
    missing_items = []
    
    if result.name:
        filled_items.append("성명")
    else:
        missing_items.append("성명")
    
    if result.birth_date:
        filled_items.append("생년월일")
    else:
        missing_items.append("생년월일")
    
    if result.address:
        filled_items.append("주소")
    else:
        missing_items.append("주소")
    
    if result.phone:
        filled_items.append("연락처")
    else:
        missing_items.append("연락처")
    
    if result.email:
        filled_items.append("이메일")
    else:
        missing_items.append("이메일")
    
    print(f"  추출 성공: {', '.join(filled_items) if filled_items else '없음'}")
    print(f"  추출 실패: {', '.join(missing_items) if missing_items else '없음'}")
    
    if len(filled_items) >= 3:
        print("\n[PASS] 소유자 정보 3개 이상 추출 성공")
    else:
        print(f"\n[FAIL] 소유자 정보 부족 ({len(filled_items)}/5)")
    
    print("=" * 70)
    
    return result


def test_seal_comparison(app_pdf: str, cert_pdf: str):
    """인감 유사도 비교 테스트"""
    from core.owner_info_extractor import compare_seal_similarity
    
    print("\n" + "=" * 70)
    print("[TEST] 인감 유사도 비교 테스트")
    print("=" * 70)
    print(f"주택매도신청서: {app_pdf}")
    print(f"인감증명서: {cert_pdf}")
    print("-" * 70)
    
    similarity, note = compare_seal_similarity(app_pdf, cert_pdf)
    
    print(f"\n[비교 결과]")
    print(f"  유사도: {similarity:.1f}%")
    print(f"  설명: {note}")
    
    if similarity >= 45:
        print(f"\n[PASS] 인감 유사도 기준 충족 (>= 45%)")
    else:
        print(f"\n[FAIL] 인감 유사도 미달 (< 45%)")
    
    print("=" * 70)
    
    return similarity


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법:")
        print("  python test_owner_extractor.py <PDF_파일_경로>")
        print("  python test_owner_extractor.py <주택매도신청서.pdf> <인감증명서.pdf>")
        print("\n예시:")
        print("  python test_owner_extractor.py document.pdf")
        print("  python test_owner_extractor.py application.pdf certificate.pdf")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    
    if not os.path.exists(pdf_path):
        print(f"[ERROR] 파일을 찾을 수 없습니다: {pdf_path}")
        sys.exit(1)
    
    try:
        # 소유자 정보 추출 테스트
        test_owner_extractor(pdf_path)
        
        # 인감 비교 테스트 (두 번째 인자가 있으면)
        if len(sys.argv) >= 3:
            cert_pdf = sys.argv[2]
            if os.path.exists(cert_pdf):
                test_seal_comparison(pdf_path, cert_pdf)
            else:
                print(f"[WARNING] 인감증명서 파일을 찾을 수 없습니다: {cert_pdf}")
    
    except Exception as e:
        print(f"\n[ERROR] 테스트 중 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
