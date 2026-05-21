# analyzer/api_client.py
"""
Claude API 클라이언트 - v3
재시도 로직 포함
"""
import time
import anthropic


def call_claude_with_retry(
    client: anthropic.Anthropic,
    model: str,
    max_tokens: int,
    system: str,
    messages: list,
    max_retries: int = 3,
) -> str:
    """Claude API 호출 (재시도 로직 포함)"""
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
            )
            return response.content[0].text
        except anthropic.RateLimitError:
            wait = 60 * (attempt + 1)
            print(f"  [API] Rate limit. {wait}초 대기 후 재시도...")
            time.sleep(wait)
        except anthropic.APIStatusError as e:
            if e.status_code == 529:
                wait = 30 * (attempt + 1)
                print(f"  [API] 과부하 ({e.status_code}). {wait}초 대기...")
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            print(f"  [API] 오류: {e}")
            if attempt < max_retries - 1:
                time.sleep(10)
            else:
                raise

    raise RuntimeError(f"API 호출 실패 (최대 {max_retries}회 재시도)")
