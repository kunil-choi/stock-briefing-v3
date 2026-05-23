# collectors/youtube_collector.py - v3
"""
유튜브 수집기 - v3
섹션1: 방송/유튜버/증권사 공식 유튜브 채널 (24시간)
섹션2: 증권TV 전문가 출연 채널 (48시간)
"""

import os
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import YouTubeTranscriptApi

from config import (
    YOUTUBE_API_KEY,
    BROADCAST_HOURS,
    YOUTUBER_HOURS,
    SECURITIES_TV_HOURS,
    SECURITIES_TV_CHANNELS,
    POPULAR_PANELISTS,
)

KST = ZoneInfo("Asia/Seoul")

SOURCE_TYPE_MAP = {
    "broadcast":  "방송",
    "youtuber":   "유튜버",
    "securities": "증권",
}

# ── 키워드 정의 ──────────────────────────────────────────────────────────────

STOCK_KEYWORDS = [
    "주식", "종목", "투자", "매수", "매도", "코스피", "코스닥",
    "증권", "펀드", "ETF", "포트폴리오", "수익률", "배당",
    "반도체", "2차전지", "바이오", "AI", "인공지능", "로봇",
    "삼성전자", "SK하이닉스", "카카오", "네이버", "현대차",
    "목표주가", "상향", "하향", "리포트", "실적",
]

EXPERT_KEYWORDS = [
    "전문가", "애널리스트", "증권사", "리서치", "투자의견",
    "목표가", "매수추천", "강력매수", "시황", "전망",
]

AD_KEYWORDS = [
    "광고", "협찬", "이벤트", "구독", "좋아요", "알림설정",
    "강의", "클래스", "수강", "모집", "세미나",
]

INFO_KEYWORDS = [
    "뉴스", "속보", "브리핑", "시황", "마감", "개장",
    "분석", "전망", "예측", "리뷰",
]


# ══════════════════════════════════════════════════════════════
#  유틸리티 함수
# ══════════════════════════════════════════════════════════════

def get_youtube_client(api_key: str = None):
    """YouTube API 클라이언트 생성. api_key 미전달 시 환경변수 사용."""
    if not api_key:
        api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        print("  [YouTube] API 키 없음")
        return None
    try:
        client = build("youtube", "v3", developerKey=api_key)
        print("  [YouTube] 클라이언트 생성 완료")
        return client
    except Exception as e:
        print(f"  [YouTube] 클라이언트 생성 실패: {e}")
        return None


def get_uploads_playlist_id(channel_id: str) -> str:
    """채널 ID(UC...)를 업로드 플레이리스트 ID(UU...)로 변환"""
    if channel_id.startswith("UC"):
        return "UU" + channel_id[2:]
    return channel_id


def resolve_channel_id(youtube, handle: str) -> str:
    """@handle 형식을 실제 채널 ID로 변환"""
    try:
        resp = youtube.search().list(
            part="snippet",
            q=handle,
            type="channel",
            maxResults=1,
        ).execute()
        items = resp.get("items", [])
        if items:
            return items[0]["snippet"]["channelId"]
    except Exception as e:
        print(f"  [채널ID 조회 실패] {handle}: {e}")
    return None


def get_recent_videos_via_playlist(youtube, channel_id: str, hours: int) -> list:
    """플레이리스트 API로 최근 hours 시간 이내 영상 목록 반환"""
    playlist_id = get_uploads_playlist_id(channel_id)
    cutoff = datetime.now(KST) - timedelta(hours=hours)
    videos = []

    try:
        next_page_token = None
        while True:
            resp = youtube.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=20,
                pageToken=next_page_token,
            ).execute()

            items = resp.get("items", [])
            if not items:
                break

            found_old = False
            for item in items:
                snippet = item.get("snippet", {})
                published_at = snippet.get("publishedAt", "")
                if not published_at:
                    continue

                pub_dt = datetime.fromisoformat(
                    published_at.replace("Z", "+00:00")
                ).astimezone(KST)

                if pub_dt < cutoff:
                    found_old = True
                    break

                video_id = (
                    item.get("contentDetails", {}).get("videoId")
                    or snippet.get("resourceId", {}).get("videoId")
                )
                if not video_id:
                    continue

                videos.append({
                    "video_id":    video_id,
                    "title":       snippet.get("title", ""),
                    "channel_id":  snippet.get("channelId", channel_id),
                    "channel_name": snippet.get("channelTitle", ""),
                    "published_at": pub_dt.strftime("%Y-%m-%d %H:%M"),
                    "thumbnail":   (
                        snippet.get("thumbnails", {})
                               .get("medium", {})
                               .get("url", "")
                    ),
                })

            if found_old:
                break
            next_page_token = resp.get("nextPageToken")
            if not next_page_token:
                break
            time.sleep(0.2)

    except HttpError as e:
        if "playlistNotFound" in str(e) or e.resp.status == 404:
            print(f"  [플레이리스트 없음] {channel_id}")
        else:
            print(f"  [플레이리스트 오류] {channel_id}: {e}")
    except Exception as e:
        print(f"  [영상 조회 오류] {channel_id}: {e}")

    return videos


def get_transcript(video_id: str, max_chars: int = 2000) -> str:
    """영상 자막 최대 max_chars 글자 반환"""
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(
            video_id, languages=["ko", "en"]
        )
        text = " ".join(t["text"] for t in transcript_list)
        return text[:max_chars]
    except Exception:
        return ""


def is_stock_related(title: str, transcript: str = "") -> bool:
    """제목·자막에 주식 관련 키워드 포함 여부 확인"""
    combined = (title + " " + transcript).lower()
    return any(kw in combined for kw in STOCK_KEYWORDS)


def is_expert_program(title: str, transcript: str = "") -> bool:
    """전문가 출연 프로그램 여부 확인"""
    combined = title + " " + transcript
    return any(kw in combined for kw in EXPERT_KEYWORDS)


def has_popular_panelist(title: str, transcript: str = "") -> bool:
    """인기 패널리스트 출연 여부 확인"""
    combined = title + " " + transcript
    return any(name in combined for name in POPULAR_PANELISTS)


def is_ad_content(title: str) -> bool:
    """광고·홍보성 콘텐츠 여부 확인"""
    return any(kw in title for kw in AD_KEYWORDS)


def load_channels_safe(channels: dict) -> dict:
    """channels dict 안전 로드 — 키 보장"""
    return {
        "broadcast":  channels.get("broadcast", []),
        "youtuber":   channels.get("youtuber", []),
        "securities": channels.get("securities", []),
    }


# ══════════════════════════════════════════════════════════════
#  채널 검증
# ══════════════════════════════════════════════════════════════

def verify_channel(youtube, channel_info: dict) -> dict:
    """채널의 최근 10개 영상 조회수 기반 검증"""
    channel_id   = channel_info.get("id", "")
    channel_name = channel_info.get("name", channel_id)

    result = {
        "channel_id":   channel_id,
        "channel_name": channel_name,
        "verified":     False,
        "avg_views":    0,
        "max_views":    0,
        "reason":       "",
    }

    try:
        playlist_id = get_uploads_playlist_id(channel_id)
        resp = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=playlist_id,
            maxResults=10,
        ).execute()

        video_ids = [
            item["contentDetails"]["videoId"]
            for item in resp.get("items", [])
        ]
        if not video_ids:
            result["reason"] = "영상 없음"
            return result

        stats_resp = youtube.videos().list(
            part="statistics",
            id=",".join(video_ids),
        ).execute()

        view_counts = []
        for item in stats_resp.get("items", []):
            vc = int(item.get("statistics", {}).get("viewCount", 0))
            view_counts.append(vc)

        if view_counts:
            avg = sum(view_counts) // len(view_counts)
            mx  = max(view_counts)
            result["avg_views"] = avg
            result["max_views"] = mx
            result["verified"]  = avg >= 1000
            result["reason"]    = (
                f"평균 조회수 {avg:,} (기준 1,000 이상)"
                if avg >= 1000
                else f"평균 조회수 {avg:,} — 기준 미달"
            )

    except Exception as e:
        result["reason"] = f"검증 오류: {e}"

    return result


def verify_all_channels(youtube, channels: dict) -> dict:
    """모든 채널 검증 후 data/verify_result.json 저장"""
    channels = load_channels_safe(channels)
    results  = {"broadcast": [], "youtuber": [], "securities": []}

    for category, ch_list in channels.items():
        print(f"  [검증] {category} 채널 {len(ch_list)}개")
        for ch in ch_list:
            res = verify_channel(youtube, ch)
            results[category].append(res)
            status = "✅" if res["verified"] else "❌"
            print(f"    {status} {res['channel_name']}: {res['reason']}")
            time.sleep(0.3)

    os.makedirs("data", exist_ok=True)
    with open("data/verify_result.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("  [검증] data/verify_result.json 저장 완료")
    return results


# ══════════════════════════════════════════════════════════════
#  섹션 1 수집
# ══════════════════════════════════════════════════════════════

def collect_section1_youtube(youtube, channels: dict) -> list:
    """
    섹션1: 방송·유튜버·증권사 공식 채널 영상 수집
    - broadcast:  BROADCAST_HOURS (24h)
    - youtuber:   YOUTUBER_HOURS  (24h)
    - securities: YOUTUBER_HOURS  (24h) ← SECURITIES_TV_HOURS(48h) 아님
    """
    channels = load_channels_safe(channels)
    all_items = []

    # ★ securities는 의도적으로 YOUTUBER_HOURS(24h) 사용
    categories = [
        ("broadcast",  BROADCAST_HOURS),
        ("youtuber",   YOUTUBER_HOURS),
        ("securities", YOUTUBER_HOURS),
    ]

    for category, hours in categories:
        ch_list = channels.get(category, [])
        print(f"  [섹션1] {category} 채널 {len(ch_list)}개 ({hours}h)")
        source_type = SOURCE_TYPE_MAP.get(category, category)
        collected = 0

        for ch in ch_list:
            channel_id   = ch.get("id", "")
            channel_name = ch.get("name", channel_id)

            if not channel_id:
                continue

            # @handle 처리
            if channel_id.startswith("@"):
                resolved = resolve_channel_id(youtube, channel_id)
                if not resolved:
                    print(f"    [스킵] {channel_name} — 채널ID 조회 실패")
                    continue
                channel_id = resolved

            videos = get_recent_videos_via_playlist(youtube, channel_id, hours)

            for v in videos:
                title = v.get("title", "")

                # 광고 콘텐츠 제외
                if is_ad_content(title):
                    continue

                # 주식 관련 여부 확인 (자막은 선택적으로 수집)
                transcript = ""
                if is_stock_related(title):
                    transcript = get_transcript(v["video_id"])
                else:
                    # 자막으로 재확인
                    transcript = get_transcript(v["video_id"])
                    if not is_stock_related(title, transcript):
                        continue

                item = {
                    "source_type":  source_type,
                    "source_name":  channel_name,
                    "category":     category,
                    "section":      "section1",
                    "video_id":     v["video_id"],
                    "title":        title,
                    "summary":      transcript[:500] if transcript else title,
                    "published_at": v.get("published_at", ""),
                    "thumbnail":    v.get("thumbnail", ""),
                    "url":          f"https://www.youtube.com/watch?v={v['video_id']}",
                }
                all_items.append(item)
                collected += 1

            time.sleep(0.2)

        print(f"    → {collected}건 수집")

    print(f"  [섹션1] 총 {len(all_items)}건")
    return all_items


# ══════════════════════════════════════════════════════════════
#  섹션 2 수집
# ══════════════════════════════════════════════════════════════

def collect_section2_securities_tv(youtube) -> list:
    """
    섹션2: 증권TV 전문가 출연 채널 영상 수집
    - SECURITIES_TV_HOURS (48h) 사용 — 전일 방송 캡처 목적
    """
    all_items = []
    print(f"  [섹션2] 증권TV 채널 {len(SECURITIES_TV_CHANNELS)}개 ({SECURITIES_TV_HOURS}h)")

    for ch in SECURITIES_TV_CHANNELS:
        channel_id   = ch.get("id", "")
        channel_name = ch.get("name", channel_id)

        if not channel_id:
            print(f"    [스킵] {channel_name} — 채널ID 없음")
            continue

        videos = get_recent_videos_via_playlist(youtube, channel_id, SECURITIES_TV_HOURS)
        collected = 0

        for v in videos:
            title = v.get("title", "")

            if is_ad_content(title):
                continue

            transcript = get_transcript(v["video_id"])

            # 전문가 출연 또는 인기 패널리스트 포함 여부
            if not (
                is_expert_program(title, transcript)
                or has_popular_panelist(title, transcript)
            ):
                continue

            item = {
                "source_type":  "증권TV",
                "source_name":  channel_name,
                "category":     "securities_tv",
                "section":      "section2",
                "video_id":     v["video_id"],
                "title":        title,
                "summary":      transcript[:500] if transcript else title,
                "published_at": v.get("published_at", ""),
                "thumbnail":    v.get("thumbnail", ""),
                "url":          f"https://www.youtube.com/watch?v={v['video_id']}",
            }
            all_items.append(item)
            collected += 1

        print(f"    {channel_name}: {collected}건")
        time.sleep(0.3)

    print(f"  [섹션2] 총 {len(all_items)}건")
    return all_items
