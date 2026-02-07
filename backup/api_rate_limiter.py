"""
전역 API Rate Limiter — 싱글톤

모든 Vision API 호출(Gemini, Claude)이 이 limiter를 거쳐 동시 호출 수와
호출 간격을 제어합니다. 429 발생 시 글로벌 쿨다운으로 전체 쓰레드 일시정지.
"""
from __future__ import annotations

import threading
import time


class GlobalAPIRateLimiter:
    """
    전역 API Rate Limiter (싱글톤)

    - Semaphore(MAX_CONCURRENT_CALLS): 동시 API 호출 수 제한
    - MIN_INTERVAL: 호출 간 최소 간격 (초)
    - COOLDOWN: 429 발생 시 전체 쓰레드 일시정지 시간 (초)
    """

    MAX_CONCURRENT_CALLS = 3   # 동시 API 호출 최대 3개
    MIN_INTERVAL = 1.2         # API 호출 간 최소 1.2초 (~50 RPM)
    COOLDOWN = 30.0            # 429 발생 시 전체 30초 일시정지

    _instance: GlobalAPIRateLimiter | None = None
    _init_lock = threading.Lock()

    def __new__(cls) -> GlobalAPIRateLimiter:
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._semaphore = threading.Semaphore(cls.MAX_CONCURRENT_CALLS)
                    inst._interval_lock = threading.Lock()
                    inst._last_call_time = 0.0
                    inst._cooldown_until = 0.0
                    cls._instance = inst
        return cls._instance

    def acquire(self) -> None:
        """API 호출 전 호출 — 동시 호출 수·간격·쿨다운 대기"""
        self._semaphore.acquire()
        try:
            with self._interval_lock:
                # 글로벌 쿨다운 대기
                now = time.monotonic()
                if now < self._cooldown_until:
                    wait = self._cooldown_until - now
                    print(f"    [GlobalLimiter] 쿨다운 대기 {wait:.1f}초...")
                    time.sleep(wait)

                # 최소 간격 대기
                now = time.monotonic()
                elapsed = now - self._last_call_time
                if elapsed < self.MIN_INTERVAL:
                    time.sleep(self.MIN_INTERVAL - elapsed)

                self._last_call_time = time.monotonic()
        except Exception:
            self._semaphore.release()
            raise

    def release(self) -> None:
        """API 응답 수신 후 호출"""
        self._semaphore.release()

    def report_rate_limit(self) -> None:
        """429 발생 시 글로벌 쿨다운 설정 — 모든 쓰레드에 영향"""
        with self._interval_lock:
            self._cooldown_until = time.monotonic() + self.COOLDOWN
            print(f"    [GlobalLimiter] 429 감지 → 전체 {self.COOLDOWN}초 쿨다운 시작")


# 편의를 위한 모듈 레벨 싱글톤 접근
def get_global_limiter() -> GlobalAPIRateLimiter:
    return GlobalAPIRateLimiter()
