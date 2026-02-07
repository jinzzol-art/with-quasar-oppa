"""
전용 추출기 디버그 - OwnerInfoExtractor 직접 테스트
"""
from PIL import Image

# 사용자가 보내준 이미지 경로
IMAGE_PATH = r"C:\Users\jinzz\.cursor\projects\c-Users-jinzz-OneDrive-Desktop-AI-Docu-Verifier\assets\c__Users_jinzz_AppData_Roaming_Cursor_User_workspaceStorage_fa7c7d5a04b8730bfd2af4f9b107567e_images_image-0814ca70-bef7-47e1-9d07-f6da1679f7d6.png"

def test_extractor_with_image():
    """OwnerInfoExtractor를 이미지로 직접 테스트"""
    print("=" * 70)
    print("[DEBUG] OwnerInfoExtractor 직접 테스트")
    print("=" * 70)
    
    from core.owner_info_extractor import OwnerInfoExtractor
    
    # 이미지 로드
    image = Image.open(IMAGE_PATH)
    if image.mode != 'RGB':
        image = image.convert('RGB')
    print(f"이미지 크기: {image.size}")
    
    # 추출기 생성
    extractor = OwnerInfoExtractor(provider="gemini")
    
    # 각 단계 직접 테스트
    print("\n>>> [테스트 1] 이름 + 법인 여부 추출")
    name_result = extractor._extract_name_and_corporation([image])
    print(f"결과: {name_result}")
    
    print("\n>>> [테스트 2] 생년월일 + 주소 추출")
    addr_result = extractor._extract_birth_and_address([image])
    print(f"결과: {addr_result}")
    
    print("\n>>> [테스트 3] 연락처 + 이메일 추출")
    contact_result = extractor._extract_contact_info([image])
    print(f"결과: {contact_result}")
    
    print("\n" + "=" * 70)
    print("[최종 결과]")
    print(f"  이름: {name_result.get('name') if name_result else '[실패]'}")
    print(f"  법인: {name_result.get('is_corporation') if name_result else '[실패]'}")
    print(f"  생년월일: {addr_result.get('birth_date') if addr_result else '[실패]'}")
    print(f"  주소: {addr_result.get('address') if addr_result else '[실패]'}")
    print(f"  연락처: {contact_result.get('phone') if contact_result else '[실패]'}")
    print(f"  이메일: {contact_result.get('email') if contact_result else '[실패]'}")
    print("=" * 70)

if __name__ == "__main__":
    test_extractor_with_image()
