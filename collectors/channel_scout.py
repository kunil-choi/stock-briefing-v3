# collectors/channel_scout.py
"""
월 1회 신규 채널 탐색 스크립트
YouTube Data API search.list로 주식 관련 인기 채널을 검색하고
channels.json에 없는 채널을 pending_channels.json에 추가
"""
import os
import json
from datetime import datetime, timezone, timedelta

KST              = timezone(timedelta(hours=9))
PENDING_CHANNELS = "data/pending_channels.json"

# 검색 키워드 (주식/경제 관련)
SEARCH_KEYWORDS = [
    "한국주식 분석",
    "주식투자 전략",
    "증시 시황",
    "종목 추천 주식",
    "주식 전문가",
]

# 최소 구독자 기준 (API로 확인)
MIN_SUBSCRIBERS = 10000


def _load_json(path: str) -> list:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_json(path: str, data) -> None:
    os.makedirs("data", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_channel_stats(youtube, channel_id: str) -> dict:
    """채널 구독자수, 영상수 조회"""
    try:
        resp = youtube.channels().list(
            part="statistics,snippet",
            id=channel_id
        ).execute()
        items = resp.get("items", [])
        if not items:
            return {}
        stats   = items[0].get("statistics", {})
        snippet = items[0].get("snippet", {})
        return {
            "subscribers":   int(stats.get("subscriberCount", 0)),
            "video_count":   int(stats.get("videoCount", 0)),
            "channel_title": snippet.get("title", ""),
            "description":   snippet.get("description", "")[:200],
        }
    except Exception as e:
        print(f"  [채널통계] {channel_id} 조회 실패: {e}")
        return {}


def run():
    from config import YOUTUBE_API_KEY, load_channels
    from collectors.youtube_collector import get_youtube_client

    youtube = get_youtube_client(YOUTUBE_API_KEY)
    if not youtube:
        print("[채널탐색] YouTube 클라이언트 없음 → 종료")
        return

    now_kst   = datetime.now(KST)
    today_str = now_kst.strftime("%Y-%m-%d")
    print(f"=== 신규 채널 탐색 시작: {today_str} ===")

    # 기존 등록 채널 ID 집합
    channels_data    = load_channels()
    known_ids        = set()
    known_names      = set()
    for cat in ["broadcast", "youtuber", "securities"]:
        for ch in channels_data.get(cat, []):
            if isinstance(ch, dict):
                known_ids.add(ch.get("id", ""))
                known_names.add(ch.get("name", ""))

    # 기존 pending 데이터
    existing = {item["channel_id"]: item
                for item in _load_json(PENDING_CHANNELS)
                if item.get("channel_id")}

    found_new = 0
    used_quota = 0

    for keyword in SEARCH_KEYWORDS:
        print(f"  검색: '{keyword}'")
        try:
            resp = youtube.search().list(
                part="snippet",
                q=keyword,
                type="channel",
                order="relevance",
                regionCode="KR",
                relevanceLanguage="ko",
                maxResults=10,
            ).execute()
            used_quota += 100
        except Exception as e:
            print(f"  검색 실패: {e}")
            continue

        for item in resp.get("items", []):
            ch_id   = item["id"].get("channelId", "")
            snippet = item.get("snippet", {})
            ch_name = snippet.get("channelTitle", "").strip()

            if not ch_id or not ch_name:
                continue
            if ch_id in known_ids or ch_name in known_names:
                continue
            if ch_id in existing:
                # 발견 횟수만 업데이트
                existing[ch_id]["count"] += 1
                existing[ch_id]["last_seen"] = today_str
                continue

            # 구독자수 확인
            stats = _get_channel_stats(youtube, ch_id)
            used_quota += 1  # channels.list는 1유닛
            subs = stats.get("subscribers", 0)

            if subs < MIN_SUBSCRIBERS:
                print(f"    스킵: {ch_name} (구독자 {subs:,}명 미달)")
                continue

            existing[ch_id] = {
                "channel_name":    ch_name,
                "channel_id":      ch_id,
                "count":           1,
                "subscribers":     subs,
                "video_count":     stats.get("video_count", 0),
                "first_seen":      today_str,
                "last_seen":       today_str,
                "expert_appeared": [],
                "sample_videos":   [],
                "found_by":        f"검색: {keyword}",
                "status":          "pending",
            }
            found_new += 1
            print(f"    ✅ 신규 후보: {ch_name} (구독자 {subs:,}명)")

    _save_json(PENDING_CHANNELS, list(existing.values()))
    print(f"\n=== 탐색 완료: 신규 {found_new}개 추가, quota 사용 {used_quota}유닛 ===")


if __name__ == "__main__":
    run()
