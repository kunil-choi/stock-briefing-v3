# collectors/gemini_youtube_analyzer.py
"""
Gemini를 활용한 유튜브 영상 직접 분석 모듈

역할:
  - youtube_collector.py가 수집한 YouTube URL을 받아
    Gemini 1.5 Pro로 영상을 직접 시청·분석
  - 발언자 / 타임스탬프 / 실제 발언 원문 / 종목명 / 감성 추출
  - transcript(자막) 기반 분석을 병행하여 API 비용 절감

수정 이력:
- GEMINI-YT-1 : 최초 작성 — 영상 직접 분석 + transcript 폴백
- GEMINI-YT-2 : 배치 처리 추가 — 영상 수가 많을 때 병렬 처리 방지용 순차 처리
- GEMINI-YT-3 : 비용 제어 — 조회수/길이 기준으로 분석 대상 선별
"""

import json
import re
import time
from typing import Optional

# ── Gemini SDK 임포트 ─────────────────────────────────────────────────────────
try:
    import google.generativeai as genai
    _GEMINI_AVAILABLE = True
except ImportError:
    _GEMINI_AVAILABLE = False
    print("[GeminiYT] google-generativeai 미설치 → 영상 분석 비활성화")


# ── 분석 대상 선별 기준 ───────────────────────────────────────────────────────
# 비용 제어: 모든 영상을 분석하지 않고 아래 기준으로 우선순위 선별
_MAX_VIDEOS_PER_RUN   = 20    # 1회 실행당 최대 분석 영상 수
_MIN_TRANSCRIPT_CHARS = 200   # transcript가 이 길이 이상일 때만 분석 (너무 짧으면 의미 없음)
_ANALYSIS_SLEEP_SEC   = 1.0   # 영상 간 API 호출 간격 (rate limit 방지)

# ── 프롬프트 템플릿 ───────────────────────────────────────────────────────────
_PROMPT_VIDEO = """
이 유튜브 영상을 분석하여 주식 종목 언급을 모두 추출하세요.

추출 기준:
- 발언자가 특정 종목을 언급하며 투자 의견, 전망, 리스크를 말한 경우만 포함
- 단순히 종목명만 스쳐 지나가는 언급은 제외
- 추측으로 내용을 채우지 말 것. 영상에서 명확히 들리지 않으면 confidence를 "낮음"으로 표시

JSON 형식으로만 응답하세요:
{
  "video_summary": "영상 전체 주제 1~2문장",
  "main_speaker": "주요 발언자 이름 또는 역할 (알 수 없으면 빈 문자열)",
  "mentions": [
    {
      "stock_name": "종목명 (한국어 정식 명칭)",
      "timestamp": "MM:SS (알 수 없으면 빈 문자열)",
      "speaker": "발언자 이름 또는 역할",
      "statement": "실제 발언 내용 — 요약 금지, 원문에 최대한 가깝게 작성",
      "sentiment": "긍정|중립|부정 중 택1",
      "confidence": "높음|보통|낮음"
    }
  ]
}
"""

_PROMPT_TRANSCRIPT = """
아래는 유튜브 영상의 자막(transcript)입니다.
주식 종목 언급을 모두 추출하세요.

추출 기준:
- 특정 종목에 대한 투자 의견, 전망, 리스크 언급만 포함
- 단순 종목명 나열은 제외
- 자막에 없는 내용은 절대 추가하지 말 것

JSON 형식으로만 응답하세요:
{
  "video_summary": "영상 전체 주제 1~2문장",
  "main_speaker": "주요 발언자 이름 (자막에서 확인된 경우만, 아니면 빈 문자열)",
  "mentions": [
    {
      "stock_name": "종목명",
      "timestamp": "",
      "speaker": "발언자 이름 (확인된 경우만)",
      "statement": "자막 원문에서 해당 발언 발췌",
      "sentiment": "긍정|중립|부정 중 택1",
      "confidence": "높음|보통|낮음"
    }
  ]
}

[자막 원문]
{transcript}
"""


# ── 내부 유틸리티 ────────────────────────────────────────────────────────────

def _parse_gemini_response(text: str) -> Optional[dict]:
    """Gemini 응답에서 JSON 추출."""
    # 코드블록 안 JSON 우선 시도
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 코드블록 없이 순수 JSON
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _analyze_via_transcript(model, transcript: str, video_url: str) -> Optional[dict]:
    """transcript 텍스트로 Gemini 분석 (영상 직접 접근 실패 시 폴백)."""
    if not transcript or len(transcript) < _MIN_TRANSCRIPT_CHARS:
        return None
    prompt = _PROMPT_TRANSCRIPT.format(transcript=transcript[:4000])
    try:
        response = model.generate_content(prompt)
        return _parse_gemini_response(response.text)
    except Exception as e:
        print(f"    [GeminiYT] transcript 분석 실패 ({video_url}): {e}")
        return None


def _analyze_via_video_url(model, video_url: str) -> Optional[dict]:
    """YouTube URL을 Gemini에 직접 전달하여 영상 분석."""
    try:
        response = model.generate_content([
            {"video_url": video_url},
            _PROMPT_VIDEO,
        ])
        return _parse_gemini_response(response.text)
    except Exception as e:
        # 영상 접근 불가(비공개/지역 제한 등) 시 None 반환 → transcript로 폴백
        print(f"    [GeminiYT] 영상 직접 분석 실패 ({video_url}): {e}")
        return None


# ── 메인 분석 함수 ───────────────────────────────────────────────────────────

def analyze_youtube_items(
    youtube_items: list,
    api_key: str,
    max_videos: int = _MAX_VIDEOS_PER_RUN,
) -> list:
    """
    youtube_collector.py가 수집한 항목 리스트를 받아
    Gemini로 각 영상을 분석하고, mentions 정보를 enriched_items로 반환.

    반환 형식 — 기존 item에 아래 필드 추가:
      - gemini_summary    : 영상 전체 요약
      - gemini_speaker    : 주요 발언자
      - gemini_mentions   : [{stock_name, timestamp, speaker, statement, sentiment, confidence}]
      - gemini_analyzed   : True (분석 완료) / False (스킵 또는 실패)
    """
    if not _GEMINI_AVAILABLE:
        print("[GeminiYT] Gemini SDK 없음 → 전체 스킵")
        return youtube_items

    if not api_key:
        print("[GeminiYT] GEMINI_API_KEY 없음 → 전체 스킵")
        return youtube_items

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-pro")

    # 분석 대상 선별 — transcript가 있는 항목 우선, 최대 max_videos개
    candidates = []
    for item in youtube_items:
        transcript = item.get("summary", "") or ""
        # summary 필드에 transcript가 저장돼 있음 (youtube_collector 참고)
        has_transcript = len(transcript) >= _MIN_TRANSCRIPT_CHARS
        candidates.append((item, transcript, has_transcript))

    # transcript 있는 항목 우선 정렬
    candidates.sort(key=lambda x: x[2], reverse=True)
    to_analyze  = candidates[:max_videos]
    skip_count  = len(candidates) - len(to_analyze)

    print(f"[GeminiYT] 분석 대상: {len(to_analyze)}개 "
          f"(전체 {len(youtube_items)}개 중, {skip_count}개 스킵)")

    enriched = []
    analyzed_count = 0
    failed_count   = 0

    for item, transcript, has_transcript in to_analyze:
        video_url = item.get("link", "")
        title     = item.get("title", "")

        result = None

        # 1차 시도: YouTube URL 직접 분석 (Gemini 1.5 Pro 기능)
        if video_url:
            result = _analyze_via_video_url(model, video_url)

        # 2차 시도: transcript 폴백
        if result is None and has_transcript:
            result = _analyze_via_transcript(model, transcript, video_url)

        if result:
            item["gemini_summary"]  = result.get("video_summary", "")
            item["gemini_speaker"]  = result.get("main_speaker", "")
            item["gemini_mentions"] = result.get("mentions", [])
            item["gemini_analyzed"] = True
            analyzed_count += 1

            mention_count = len(item["gemini_mentions"])
            print(f"  ✅ [{title[:30]}] → 종목 언급 {mention_count}개 추출")
        else:
            item["gemini_summary"]  = ""
            item["gemini_speaker"]  = ""
            item["gemini_mentions"] = []
            item["gemini_analyzed"] = False
            failed_count += 1
            print(f"  ❌ [{title[:30]}] → 분석 실패")

        enriched.append(item)
        time.sleep(_ANALYSIS_SLEEP_SEC)

    # 분석 대상 외 항목은 gemini 필드 없이 그대로 추가
    analyzed_urls = {item.get("link") for item, _, _ in to_analyze}
    for item in youtube_items:
        if item.get("link") not in analyzed_urls:
            item["gemini_analyzed"] = False
            enriched.append(item)

    print(f"[GeminiYT] 완료 — 성공:{analyzed_count} / 실패:{failed_count} / "
          f"스킵:{skip_count}")
    return enriched


# ── gemini_mentions → all_data 확장 헬퍼 ─────────────────────────────────────

def expand_gemini_mentions(enriched_items: list) -> list:
    """
    gemini_mentions에서 추출된 발언을 별도 항목으로 확장하여
    ai_analyzer.py의 extract_mentions()가 읽을 수 있는 형태로 변환.

    기존 항목은 유지하고, 각 mention을 추가 항목으로 append.
    이렇게 하면 Claude 프롬프트에 "홍길동: 삼성전자 단기 조정 예상" 같은
    실제 발언이 snippet으로 포함됨.
    """
    expanded = list(enriched_items)  # 기존 항목 유지

    for item in enriched_items:
        mentions = item.get("gemini_mentions", [])
        if not mentions:
            continue

        base_url     = item.get("link", "")
        source_name  = item.get("source_name", "")
        source_type  = item.get("source_type", "유튜브")
        published    = item.get("published", "")
        speaker      = item.get("gemini_speaker", "")

        for mention in mentions:
            stock_name = mention.get("stock_name", "")
            statement  = mention.get("statement", "")
            timestamp  = mention.get("timestamp", "")
            m_speaker  = mention.get("speaker") or speaker
            sentiment  = mention.get("sentiment", "중립")
            confidence = mention.get("confidence", "보통")

            if not stock_name or not statement:
                continue

            # confidence 낮음은 포함하되 가중치 조정을 위해 플래그 표시
            timestamp_url = f"{base_url}&t={timestamp}" if timestamp else base_url

            # 발언 원문을 summary/content로 저장 → extract_mentions가 읽음
            summary = f"[{m_speaker}] {statement}" if m_speaker else statement
            if sentiment != "중립":
                summary += f" (감성:{sentiment})"

            expanded.append({
                "source_type":    source_type,
                "source_name":    source_name,
                "title":          f"{m_speaker or source_name}: {stock_name} 언급",
                "summary":        summary,
                "content":        statement,
                "link":           timestamp_url,
                "url":            timestamp_url,
                "published":      published,
                "stock_name":     stock_name,   # extract_mentions 직접 힌트
                "gemini_speaker": m_speaker,
                "gemini_sentiment": sentiment,
                "gemini_confidence": confidence,
                "_from_gemini":   True,         # 검수 시 출처 추적용
            })

    original_count  = len(enriched_items)
    expanded_count  = len(expanded) - original_count
    print(f"[GeminiYT] 발언 확장: {expanded_count}개 항목 추가 "
          f"(원본 {original_count}개 유지)")
    return expanded
