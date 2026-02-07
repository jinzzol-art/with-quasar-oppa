"""Pydantic 모델 속성 할당 테스트"""
from core.data_models import PublicHousingReviewResult, OwnerInfo

# 결과 객체 생성
result = PublicHousingReviewResult(review_date="2025-02-04")

# 소유자 정보 참조
owner = result.housing_sale_application.owner_info

print(f"[전] owner.name: {owner.name}")
print(f"[전] result 내부: {result.housing_sale_application.owner_info.name}")

# 속성 할당
owner.name = "테스트이름"

print(f"[후] owner.name: {owner.name}")
print(f"[후] result 내부: {result.housing_sale_application.owner_info.name}")

# 동일 객체인지 확인
print(f"\n동일 객체? {owner is result.housing_sale_application.owner_info}")
