# analyzer/api_client.py
"""
Claude API 클라이언트 - v3
anthropic SDK 대신 requests 직접 호출 (GitHub Actions 환경 Connection error 해결)
"""
import time
import json
import requests as req_lib

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"


def call_claude_with_retry(
    client,          # anthropic.Anthropic 인스턴스 또는 None (api_key 추출용)
    model: str,
    max_tokens: int,
    system: str,
    messages: list,
    max_retries: int = 3,
) -> str:
    """
    Claude API 직접 호출 (requests 사용, 재시도 로직 포함)
    anthropic SDK의 httpx 기반 연결 오류를 우회합니다.
    """
    # api_key 추출
    api_key = ""
    if client is not None:
        # anthropic.Anthropic 인스턴스에서 api_key 추출
        if hasattr(client, "api_key"):
            api_key = client.api_key
        elif hasattr(client, "_api_key"):
            api_key = client._api_key
        elif hasattr(client, "auth_token"):
            api_key = client.auth_token

    if not api_key:
        # 환경변수 fallback
        import os
        api_key = os.getenv("ANTHROPIC_API_KEY", "")

    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY가 설정되지 않았습니다.")

    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "content-type": "application/json",
    }

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }

    for attempt in range(max_retries):
        try:
            resp = req_lib.post(
                ANTHROPIC_API_URL,
                headers=headers,
                json=payload,
                timeout=120,
            )

            if resp.status_code == 200:
                data = resp.json()
                return data["content"][0]["text"]

            elif resp.status_code == 429:
                wait = 60 * (attempt + 1)
                print(f"  [API] Rate limit (429). {wait}초 대기 후 재시도...")
                time.sleep(wait)

            elif resp.status_code == 529:
                wait = 30 * (attempt + 1)
                print(f"  [API] 과부하 (529). {wait}초 대기...")
                time.sleep(wait)

            elif resp.status_code == 401:
                raise ValueError(f"API 키 인증 실패 (401): {resp.text[:200]}")

            else:
                print(f"  [API] HTTP {resp.status_code}: {resp.text[:300]}")
                if attempt < max_retries - 1:
                    time.sleep(15)
                else:
                    raise RuntimeError(f"API 오류 {resp.status_code}: {resp.text[:200]}")

        except req_lib.exceptions.ConnectionError as e:
            print(f"  [API] 연결 오류 (attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(20)
            else:
                raise

        except req_lib.exceptions.Timeout:
            print(f"  [API] 타임아웃 (attempt {attempt+1}/{max_retries})")
            if attempt < max_retries - 1:
                time.sleep(15)
            else:
                raise

        except (ValueError, RuntimeError):
            raise

        except Exception as e:
            print(f"  [API] 예외 (attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(10)
            else:
                raise

    raise RuntimeError(f"API 호출 실패 (최대 {max_retries}회 재시도)")
