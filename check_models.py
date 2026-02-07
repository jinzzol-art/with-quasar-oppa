
# check_models.py
import os
import google.generativeai as genai
from dotenv import load_dotenv

# 환경변수 로드
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

# REST 방식으로 설정 (선생님 환경에 맞춤)
genai.configure(api_key=api_key, transport='rest')

print(">>> 구글 서버에 사용 가능한 모델 목록을 요청합니다...")

try:
    # 모델 목록 조회
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f"- 발견된 모델: {m.name}")
            
except Exception as e:
    print(f"\n[에러 발생] 목록을 가져오지 못했습니다:\n{e}")
    