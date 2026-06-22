# collectors/youtube_precollect.py
"""
유튜브 사전 수집 스크립트
- 브리핑 실행 전에 미리 유튜브를 수집·분석해서 youtube_mentions.json에 누적
- 이미 분석된 video_url은 건너뛰고 신규 영상만 처리
- 실행: python collectors/youtube_precollect.py
"""
import os
import json
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
MENTIONS_FILE = "data/youtube_mentions.json"


def load_existing_mentions() -> tuple[list, set]:
    """기존 youtube_mentions.json 로드, 이미 분석된 video_url 집합 반환"""
    if not os.path.exists(MENTIONS_FILE):
        return [], set()
    try:
        with open(MENTIONS_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
        analyzed_urls = {m.get("video_url", "") for m in existing}
        print(f"[사전수집] 기존 데이터: {len(existing)}건, 분석된 영상: {len(analyzed_urls)}개")
        return existing, analyzed_urls
    except Exception as e:
        print(f"[사전수집] 기존 데이터 로드 실패: {e}")
        return [], set()


def save_mentions(mentions: list):
    """youtube_mentions.json 저장"""
    os.makedirs("data", exist_ok=True)
    with open(MENTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(mentions, f, ensure_ascii=False, indent=2)
    print(f"[사전수집] 저장 완료: {len(mentions)}건 → {MENTIONS_FILE}")


def run():
    from config import YOUTUBE_API_KEY, GEMINI_API_KEY, load_channels
    from collectors.youtube_collector import (
        get_youtube_client,
        collect_section1_youtube,
        collect_panelist_youtube,
    )
    from collectors.gemini_youtube_analyzer import (
        analyze_youtube_items,
        expand_gemini_mentions,
    )

    now_kst = datetime.now(KST)
    print(f"=== 유튜브 사전 수집 시작: {now_kst.strftime('%Y-%m-%d %H:%M KST')} ===")

    # 기존 데이터 로드
    existing_mentions, analyzed_urls = load_existing_mentions()

    # 오늘 날짜 기준으로 어제 데이터는 제거 (하루치만 유지)
    today_str = now_kst.strftime("%Y-%m-%d")
    yesterday_str = (now_kst - timedelta(days=1)).strftime("%Y-%m-%d")
    existing_mentions = [
        m for m in existing_mentions
        if m.get("date", "") in (today_str, yesterday_str)
    ]

    # 유튜브 수집
    channels = load_channels()
    youtube = get_youtube_client(YOUTUBE_API_KEY)
    if not youtube:
        print("[사전수집] YouTube 클라이언트 없음 → 종료")
        return

    yt_data      = collect_section1_youtube(youtube, channels)
    panelist_data = collect_panelist_youtube(youtube)
    youtube_raw  = yt_data + panelist_data

    # 이미 분석된 영상 제외
    new_items = [
        item for item in youtube_raw
        if item.get("link", "") not in analyzed_urls
    ]
    print(f"[사전수집] 신규 영상: {len(new_items)}개 (전체 {len(youtube_raw)}개 중)")

    if not new_items:
        print("[사전수집] 신규 영상 없음 → 종료")
        return

    # Gemini 분석
    if GEMINI_API_KEY:
        enriched = analyze_youtube_items(new_items, GEMINI_API_KEY)
        expanded = expand_gemini_mentions(enriched)
    else:
        print("[사전수집] GEMINI_API_KEY 없음 → 분석 스킵")
        return

    # 새로 추출된 mentions만 기존에 추가
    new_mention_urls = {item.get("link", "") for item in new_items}

    # expand_gemini_mentions가 저장한 파일 읽기
    if os.path.exists(MENTIONS_FILE):
        with open(MENTIONS_FILE, "r", encoding="utf-8") as f:
            all_mentions = json.load(f)
        print(f"[사전수집] 최종 누적: {len(all_mentions)}건")
    
    elapsed = int((datetime.now(KST) - now_kst).total_seconds())
    print(f"=== 사전 수집 완료 (소요: {elapsed}초) ===")


if __name__ == "__main__":
    run()
