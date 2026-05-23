# analyzer/api_client.py
import os
import time
import requests as req_lib

ANTHROPIC_API_URL     = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"

# 모델 우선순위: claude-sonnet-4-5 → claude-3-7-sonnet → 3-5-sonnet → haiku 폴백
CLAUDE_MODELS = [
    "claude-sonnet-4-5",            # 1순위: 최신 claude-sonnet-4-5 (사용자 지정)
    "claude-3-7-sonnet-20250219",   # 2순위: claude-3-7
    "claude-3-5-sonnet-20241022",   # 3순위: 안정적 고성능
    "claude-3-5-haiku-20241022",    # 4순위: 경량 폴백
]


def call_claude_with_retry(api_key: str, prompt: str,
                            max_tokens: int = 16000,
                            max_retries: int = 3) -> str:
    """
    Claude API 직접 호출 (requests 사용).
    모델 순서대로 시도하고, 각 모델당 max_retries 회 재시도.
    """
    if not api_key:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("  [API] ANTHROPIC_API_KEY 없음")
        return ""

    headers = {
        "x-api-key":         api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "content-type":      "application/json",
    }

    for model in CLAUDE_MODELS:
        for attempt in range(max_retries):
            payload = {
                "model":      model,
                "max_tokens": max_tokens,
                "messages":   [{"role": "user", "content": prompt}],
            }
            try:
                print(f"  [API] 모델={model}, 시도={attempt+1}/{max_retries}")
                resp = req_lib.post(
                    ANTHROPIC_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=180,
                )

                if resp.status_code == 200:
                    data   = resp.json()
                    result = data["content"][0]["text"].strip()
                    print(f"  [API] 성공 (모델={model}, {len(result)}자)")
                    return result

                elif resp.status_code == 429:
                    wait = 60 * (attempt + 1)
                    print(f"  [API] 429 Rate limit → {wait}초 대기...")
                    time.sleep(wait)

                elif resp.status_code in (529, 503, 500):
                    wait = 30 * (attempt + 1)
                    print(f"  [API] {resp.status_code} 서버 과부하 → {wait}초 대기...")
                    time.sleep(wait)

                elif resp.status_code == 401:
                    print("  [API] 401 인증 실패. API 키를 확인하세요.")
                    return ""

                else:
                    print(f"  [API] HTTP {resp.status_code}: {resp.text[:200]}")
                    if attempt < max_retries - 1:
                        time.sleep(15)

            except req_lib.exceptions.ConnectionError as e:
                print(f"  [API] 연결 오류 ({attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(20)

            except req_lib.exceptions.Timeout:
                print(f"  [API] 타임아웃 ({attempt+1}/{max_retries})")
                if attempt < max_retries - 1:
                    time.sleep(15)

            except Exception as e:
                print(f"  [API] 예외 ({attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(10)

        print(f"  [API] 모델 {model} 전체 실패 → 다음 모델 시도")

    print("  [API] 모든 모델/재시도 실패")
    return ""
