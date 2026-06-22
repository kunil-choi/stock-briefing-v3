# collectors/gemini_youtube_analyzer.py
"""
Gemini를 활용한 유튜브 영상 직접 분석 모듈

역할:
  - youtube_collector.py가 수집한 YouTube URL을 받아
    Gemini로 영상을 직접 시청·분석
  - 발언자 / 타임스탬프 / 실제 발언 원문 / 종목명 / 감성 추출
  - transcript(자막) 기반 분석을 병행하여 API 비용 절감

수정 이력:
- GEMINI-YT-1  : 최초 작성 — 영상 직접 분석 + transcript 폴백
- GEMINI-YT-2  : 배치 처리 추가 — 순차 처리
- GEMINI-YT-3  : 비용 제어 — 조회수/길이 기준으로 분석 대상 선별
- GEMINI-YT-4  : Content 구조 오류 수정
                 {"video_url": url} → parts 리스트 구조로 변경
                 YouTube URL은 file_data가 아닌 직접 url 방식 사용
                 (※ GEMINI-YT-5에서 이 판단이 잘못됐던 것으로 확인 — 되돌림)
- GEMINI-YT-5  : 전면 재작성.
                 1) google-generativeai(legacy) SDK는 2025-11-30 EOL,
                    저장소도 archived 상태 → google-genai(신규 통합 SDK)로 교체.
                 2) gemini-1.5-pro 모델은 이미 완전히 shutdown(404) →
                    현재 서비스 중인 모델로 교체. 모델명은 GEMINI_MODEL
                    상수로 분리해 다음 모델 교체 시 한 곳만 고치면 되도록 함.
                 3) GEMINI-YT-4의 "URL을 문자열로 직접 전달" 방식은 실제로는
                    Gemini가 영상으로 인식하지 못하는 잘못된 구조였음 →
                    공식 문서대로 types.Part(file_data=types.FileData(...))
                    구조로 복원.
"""

import json
import re
import time
from typing import Optional

# ── Gemini SDK 임포트 (GEMINI-YT-5: 신규 통합 SDK google-genai) ──────────────
try:
    from google import genai
    from google.genai import types
    _GEMINI_AVAILABLE = True
except ImportError:
    _GEMINI_AVAILABLE = False
    print("[GeminiYT] google-genai 미설치 → 영상 분석 비활성화")

# GEMINI-YT-5: 모델명을 상수로 분리.
# Gemini는 모델을 자주 셧다운하므로(예: gemini-1.5-pro, gemini-2.0-flash 등
# 이미 shutdown) 다음에 또 막히면 이 한 줄만 바꾸면 되도록 구성.
# 2026-06 기준 안정 서비스 중인 모델. 추후 ai.google.dev/gemini-api/docs/models
# 의 deprecation 페이지에서 현재 상태 확인 권장.
GEMINI_MODEL = "gemini-2.5-flash"


# ── 분석 대상 선별 기준 ───────────────────────────────────────────────────────
_MAX_VIDEOS_PER_RUN   = 20
_MIN_TRANSCRIPT_CHARS = 200
_ANALYSIS_SLEEP_SEC   = 1.5   # rate limit 방지 (1.0 → 1.5로 여유 확보)

# ── 프롬프트 템플릿 ───────────────────────────────────────────────────────────
_PROMPT_VIDEO = """
이 유튜브 영상을 분석하여 주식 종목 언급을 추출하세요.
방송 제작용 데이터로 사용되므로, 정확성이 최우선입니다.

[분석 기준]
- 출연자가 특정 종목에 대해 투자 의견/전망/리스크를 명확히 언급한 경우만 포함
- 단순 종목명 언급, 지나가는 언급은 제외
- 영상에서 확인되지 않은 내용은 절대 추가 금지

[발언자 확인 방법]
- 화면 하단 자막(이름/소속 표시)을 최우선으로 확인
- 목소리와 화면 출연자를 매칭하여 누가 말했는지 특정
- 발언자를 특정할 수 없으면 speaker를 빈 문자열로 둘 것

JSON 형식으로만 응답하세요:
{
  "video_summary": "영상 전체 주제 1~2문장",
  "main_speaker": "주요 발언자 이름과 소속/직책 (예: 염승환 LS증권 이사)",
  "speakers": ["출연자1 이름/소속", "출연자2 이름/소속"],
  "mentions": [
    {
      "stock_name": "종목명 (한국어 정식 명칭)",
      "timestamp": "MM:SS (영상에서 확인된 경우만, 모르면 빈 문자열)",
      "speaker": "발언자 이름과 소속/직책 (화면 자막 기준, 모르면 빈 문자열)",
      "fact": "해당 종목에 대한 핵심 팩트 1문장 — 구체적 수치/전망 포함 (예: 목표주가 10만원 제시, 3분기 영업이익 15조 전망)",
      "quote": "발언자의 실제 발언을 최대한 원문 그대로 1~2문장 (예: 지금 삼성전자 안 사면 평생 후회합니다)",
      "sentiment": "긍정|중립|부정 중 택1",
      "confidence": "높음|보통|낮음"
    }
  ]
}
"""

_PROMPT_TRANSCRIPT = """
아래는 유튜브 영상의 자막(transcript)입니다.
주식 종목 언급을 추출하세요. 방송 제작용 데이터입니다.

[분석 기준]
- 특정 종목에 대한 투자 의견/전망/수치가 있는 언급만 포함
- 단순 종목명 나열 제외
- 자막에 없는 내용 절대 추가 금지

JSON 형식으로만 응답하세요:
{{
  "video_summary": "영상 전체 주제 1~2문장",
  "main_speaker": "주요 발언자 이름 (자막에서 확인된 경우만, 모르면 빈 문자열)",
  "speakers": [],
  "mentions": [
    {{
      "stock_name": "종목명",
      "timestamp": "",
      "speaker": "발언자 이름 (자막에서 확인된 경우만, 모르면 빈 문자열)",
      "fact": "해당 종목 핵심 팩트 1문장 — 구체적 수치/전망 포함",
      "quote": "자막 원문에서 발언자 말 그대로 1~2문장 발췌",
      "sentiment": "긍정|중립|부정 중 택1",
      "confidence": "높음|보통|낮음"
    }}
  ]
}}

[자막 원문]
{transcript}
"""


# ── 내부 유틸리티 ────────────────────────────────────────────────────────────

def _parse_gemini_response(text: str) -> Optional[dict]:
    """Gemini 응답에서 JSON 추출."""
    if not text:
        return None
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _analyze_via_transcript(client, transcript: str, video_url: str) -> Optional[dict]:
    """transcript 텍스트로 Gemini 분석 (영상 직접 접근 실패 시 폴백)."""
    if not transcript or len(transcript) < _MIN_TRANSCRIPT_CHARS:
        return None
    prompt = _PROMPT_TRANSCRIPT.format(transcript=transcript[:4000])
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        return _parse_gemini_response(response.text)
    except Exception as e:
        print(f"    [GeminiYT] transcript 분석 실패 ({video_url}): {e}")
        return None


def _analyze_via_video_url(client, video_url: str) -> Optional[dict]:
    """
    GEMINI-YT-5:
    YouTube URL은 file_data(FileData) 구조로 전달해야 Gemini가
    실제 영상으로 인식한다. 단순 문자열로 넘기면 텍스트로만 취급되어
    영상 내용을 전혀 보지 못한 채 항상 실패한다 (GEMINI-YT-4의 오판 수정).
    """
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=types.Content(parts=[
                types.Part(file_data=types.FileData(file_uri=video_url)),
                types.Part(text=_PROMPT_VIDEO),
            ]),
        )
        return _parse_gemini_response(response.text)
    except Exception as e:
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
        print("[GeminiYT] google-genai SDK 없음 → 전체 스킵")
        return youtube_items

    if not api_key:
        print("[GeminiYT] GEMINI_API_KEY 없음 → 전체 스킵")
        return youtube_items

    client = genai.Client(api_key=api_key)

    # 분석 대상 선별 — transcript가 있는 항목 우선, 최대 max_videos개
    candidates = []
    for item in youtube_items:
        transcript     = item.get("summary", "") or ""
        has_transcript = len(transcript) >= _MIN_TRANSCRIPT_CHARS
        candidates.append((item, transcript, has_transcript))

    # transcript 있는 항목 우선 정렬
    candidates.sort(key=lambda x: x[2], reverse=True)
    to_analyze = candidates[:max_videos]
    skip_count = len(candidates) - len(to_analyze)

    print(f"[GeminiYT] 분석 대상: {len(to_analyze)}개 "
          f"(전체 {len(youtube_items)}개 중, {skip_count}개 스킵, model={GEMINI_MODEL})")

    enriched       = []
    analyzed_count = 0
    failed_count   = 0

    for item, transcript, has_transcript in to_analyze:
        video_url = item.get("link", "")
        title     = item.get("title", "")

        result = None

        # GEMINI-YT-6: 영상 직접분석(file_data)은 영상 전체를 토큰화하므로
        # 자막 텍스트 대비 토큰 비용이 매우 커서, 다수 영상을 연속 처리하면
        # generate_content_paid_tier_input_token_count(분당 토큰) quota를
        # 빠르게 소진해 429 RESOURCE_EXHAUSTED가 양산됨.
        # 분석 대상은 이미 "자막 있는 영상 우선"으로 선별해두므로,
        # 자막이 있으면 자막을 먼저 쓰고, 없을 때만 비용이 큰 영상 직접분석을 쓴다.
        if has_transcript:
            result = _analyze_via_transcript(client, transcript, video_url)

        # 자막이 없거나 자막 분석이 실패한 경우에만 영상 직접분석 시도
        if result is None and video_url:
            result = _analyze_via_video_url(client, video_url)

        if result:
            item["gemini_summary"]   = result.get("video_summary", "")
            item["gemini_speaker"]   = result.get("main_speaker", "")
            item["gemini_speakers"]  = result.get("speakers", [])
            item["gemini_mentions"]  = result.get("mentions", [])
            item["gemini_analyzed"]  = True
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
    gemini_mentions에서 추출된 발언을 별도 항목으로 확장.

    변경사항:
    - fact/quote 필드 추가 (방송 제작용)
    - data/youtube_mentions.json 별도 저장 (외부 앱 연동용)
    - speakers 필드(출연자 목록) 저장
    """
    import os
    import json
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))

    expanded       = list(enriched_items)
    youtube_export = []  # 외부 앱 연동용 별도 저장 데이터

    for item in enriched_items:
        mentions = item.get("gemini_mentions", [])
        if not mentions:
            continue

        base_url    = item.get("link", "")
        source_name = item.get("source_name", "")
        source_type = item.get("source_type", "유튜브")
        published   = item.get("published", "")
        main_speaker = item.get("gemini_speaker", "")
        speakers    = item.get("gemini_speakers", [])
        video_summary = item.get("gemini_summary", "")

        for mention in mentions:
            stock_name = mention.get("stock_name", "")
            fact       = mention.get("fact", "")
            quote      = mention.get("quote", "")
            # 구버전 호환: statement가 있으면 fact/quote 폴백
            statement  = mention.get("statement", "")
            if not fact and statement:
                fact = statement
            timestamp  = mention.get("timestamp", "")
            m_speaker  = mention.get("speaker") or main_speaker
            sentiment  = mention.get("sentiment", "중립")
            confidence = mention.get("confidence", "보통")

            if not stock_name or (not fact and not quote):
                continue

            timestamp_url = f"{base_url}&t={timestamp}" if timestamp else base_url

            # 브리핑용 summary (기존 흐름 유지)
            summary_parts = []
            if m_speaker:
                summary_parts.append(f"[{m_speaker}]")
            if fact:
                summary_parts.append(fact)
            if quote:
                summary_parts.append(f'"{quote}"')
            summary = " ".join(summary_parts)
            if sentiment != "중립":
                summary += f" (감성:{sentiment})"

            expanded.append({
                "source_type":       source_type,
                "source_name":       source_name,
                "title":             f"{m_speaker or source_name}: {stock_name} 언급",
                "summary":           summary,
                "content":           fact or quote,
                "link":              timestamp_url,
                "url":               timestamp_url,
                "published":         published,
                "stock_name":        stock_name,
                "gemini_speaker":    m_speaker,
                "gemini_fact":       fact,
                "gemini_quote":      quote,
                "gemini_sentiment":  sentiment,
                "gemini_confidence": confidence,
                "_from_gemini":      True,
            })

            # 외부 앱 연동용 데이터 축적
            youtube_export.append({
                "date":          datetime.now(KST).strftime("%Y-%m-%d"),
                "channel":       source_name,
                "video_url":     base_url,
                "video_title":   item.get("title", ""),
                "video_summary": video_summary,
                "published":     published,
                "speakers":      speakers,
                "main_speaker":  main_speaker,
                "stock_name":    stock_name,
                "timestamp":     timestamp,
                "timestamp_url": timestamp_url,
                "speaker":       m_speaker,
                "fact":          fact,
                "quote":         quote,
                "sentiment":     sentiment,
                "confidence":    confidence,
            })

    # 외부 앱 연동용 JSON 저장
    if youtube_export:
        os.makedirs("data", exist_ok=True)
        export_path = "data/youtube_mentions.json"
        try:
            with open(export_path, "w", encoding="utf-8") as f:
                json.dump(youtube_export, f, ensure_ascii=False, indent=2)
            print(f"[GeminiYT] 방송제작용 데이터 저장: {export_path} ({len(youtube_export)}건)")
        except Exception as e:
            print(f"[GeminiYT] 방송제작용 데이터 저장 실패: {e}")

    original_count = len(enriched_items)
    expanded_count = len(expanded) - original_count
    print(f"[GeminiYT] 발언 확장: {expanded_count}개 항목 추가 "
          f"(원본 {original_count}개 유지)")
    return expanded
