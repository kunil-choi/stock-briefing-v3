# collectors/youtube_collector.py
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    _TRANSCRIPT_AVAILABLE = True
except ImportError:
    _TRANSCRIPT_AVAILABLE = False

from config import (
    YOUTUBE_API_KEY,
    BROADCAST_HOURS,
    YOUTUBER_HOURS,
    SECURITIES_TV_HOURS,
    SECURITIES_TV_CHANNELS,
    POPULAR_PANELISTS,
)

KST = ZoneInfo("Asia/Seoul")

STOCK_KEYWORDS = [
    "주식", "종목", "투자", "매수", "매도", "코스피", "코스닥",
    "증권", "펀드", "ETF", "포트폴리오", "수익률", "배당",
    "반도체", "2차전지", "배터리", "바이오", "AI", "인공지능", "로봇",
    "삼성전자", "SK하이닉스", "카카오", "네이버", "현대차",
    "목표주가", "상향", "하향", "리포트", "실적", "시황", "전망",
    "상승", "하락", "브리핑", "분석", "추천", "경제", "금융",
]

EXPERT_KEYWORDS = [
    "전문가", "애널리스트", "증권사", "리서치", "투자의견",
    "목표가", "매수추천", "강력매수", "시황", "전망",
]

AD_KEYWORDS = [
    "광고", "협찬", "이벤트", "강의", "클래스", "수강", "모집", "세미나",
]


# ── 유틸리티 ───────────────────────────────────────────────────────────────────

def get_youtube_client(api_key: str = None):
    """YouTube API 클라이언트 생성"""
    if not api_key:
        api_key = os.environ.get("YOUTUBE_API_KEY", "")
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
    if channel_id.startswith("UC"):
        return "UU" + channel_id[2:]
    return channel_id


def resolve_channel_id(youtube, handle: str) -> str:
    """@handle → 채널ID 변환"""
    try:
        resp = youtube.channels().list(
            part="id",
            forHandle=handle.lstrip("@"),
        ).execute()
        items = resp.get("items", [])
        if items:
            return items[0]["id"]
    except Exception as e:
        print(f"  [채널ID 조회 실패] {handle}: {e}")
    return None


def get_recent_videos_via_playlist(youtube, channel_id: str, hours: int) -> list:
    """플레이리스트 API로 최근 N시간 영상 목록 반환"""
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
                snippet      = item.get("snippet", {})
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
                    or snippet.get("resourceId", {}).get("videoId", "")
                )
                if not video_id:
                    continue

                videos.append({
                    "video_id":     video_id,
                    "title":        snippet.get("title", ""),
                    "channel_id":   snippet.get("channelId", channel_id),
                    "channel_name": snippet.get("channelTitle", ""),
                    "published_at": pub_dt.strftime("%Y-%m-%d %H:%M"),
                    "thumbnail":    snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
                })

            if found_old:
                break
            next_page_token = resp.get("nextPageToken")
            if not next_page_token:
                break
            time.sleep(0.2)

    except HttpError as e:
        code = e.resp.status if hasattr(e, "resp") else 0
        if "playlistNotFound" in str(e) or code == 404:
            print(f"  [플레이리스트 없음] {channel_id}")
        else:
            print(f"  [플레이리스트 오류] {channel_id}: {e}")
    except Exception as e:
        print(f"  [영상 조회 오류] {channel_id}: {e}")

    return videos


def get_transcript(video_id: str, max_chars: int = 2000) -> str:
    """영상 자막 추출"""
    if not _TRANSCRIPT_AVAILABLE:
        return ""
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        try:
            t = transcript_list.find_transcript(["ko"])
        except Exception:
            try:
                t = transcript_list.find_generated_transcript(["ko"])
            except Exception:
                return ""
        entries = t.fetch()
        texts = []
        for e in entries:
            if hasattr(e, "text"):
                texts.append(e.text)
            elif isinstance(e, dict):
                texts.append(e.get("text", ""))
        return " ".join(texts)[:max_chars]
    except Exception:
        return ""


def is_stock_related(title: str, transcript: str = "") -> bool:
    combined = (title + " " + transcript).lower()
    return any(kw in combined for kw in STOCK_KEYWORDS)


def is_expert_program(title: str, transcript: str = "") -> bool:
    combined = title + " " + transcript
    return any(kw in combined for kw in EXPERT_KEYWORDS)


def has_popular_panelist(title: str, transcript: str = "") -> bool:
    combined = title + " " + transcript
    return any(name in combined for name in POPULAR_PANELISTS)


def is_ad_content(title: str) -> bool:
    return any(kw in title for kw in AD_KEYWORDS)


def _normalize_channel_list(raw) -> list:
    """
    channels.json 카테고리 값을 list[{"id": ..., "name": ...}] 형태로 통일.
    dict / list 양쪽 모두 처리.
    """
    if isinstance(raw, list):
        result = []
        for item in raw:
            if isinstance(item, dict):
                result.append({
                    "id":   item.get("id", ""),
                    "name": item.get("name", item.get("id", "")),
                })
            elif isinstance(item, str):
                result.append({"id": item, "name": item})
        return result
    elif isinstance(raw, dict):
        result = []
        for name, val in raw.items():
            if isinstance(val, dict):
                result.append({"id": val.get("id", ""), "name": name})
            elif isinstance(val, str):
                result.append({"id": val, "name": name})
        return result
    return []


# ── 섹션 1 수집 ────────────────────────────────────────────────────────────────

def collect_section1_youtube(youtube, channels: dict) -> list:
    """
    섹션1: 방송·유튜버·증권 채널 영상 수집.
    BUG-8 수정: 제목 통과 시 자막 중복 수집 제거.
    """
    all_items = []
    categories = [
        ("broadcast", BROADCAST_HOURS, "방송"),
        ("youtuber",  YOUTUBER_HOURS,  "유튜버"),
        ("top50",     YOUTUBER_HOURS,  "유튜버"),
    ]

    for cat_key, hours, source_type in categories:
        raw     = channels.get(cat_key, {})
        ch_list = _normalize_channel_list(raw)
        if not ch_list:
            continue

        print(f"  [섹션1-{cat_key}] {len(ch_list)}개 채널 ({hours}h)")
        collected = 0

        for ch in ch_list:
            channel_id   = ch.get("id", "")
            channel_name = ch.get("name", channel_id)

            if not channel_id:
                continue

            if channel_id.startswith("@"):
                resolved = resolve_channel_id(youtube, channel_id)
                if not resolved:
                    print(f"    [스킵] {channel_name} — 채널ID 조회 실패")
                    continue
                channel_id = resolved

            videos = get_recent_videos_via_playlist(youtube, channel_id, hours)

            for v in videos:
                title = v.get("title", "")
                if is_ad_content(title):
                    continue

                # BUG-8 수정: 제목이 주식 관련이면 자막 추가 수집, 아니면 자막으로 재확인
                if is_stock_related(title):
                    transcript = get_transcript(v["video_id"])
                else:
                    transcript = get_transcript(v["video_id"])
                    if not is_stock_related(title, transcript):
                        continue

                all_items.append({
                    "source_type": source_type,
                    "source_name": channel_name,
                    "title":       title,
                    "summary":     transcript[:500] if transcript else title,
                    "link":        f"https://www.youtube.com/watch?v={v['video_id']}",
                    "published":   v.get("published_at", ""),
                })
                collected += 1

            time.sleep(0.2)

        print(f"    → {collected}건 수집")

    print(f"  [섹션1] 총 {len(all_items)}건")
    return all_items


# ── 섹션 2 수집 ────────────────────────────────────────────────────────────────

def collect_section2_securities_tv(youtube) -> list:
    """
    섹션2: 증권TV 전문가 출연 채널 수집 (SECURITIES_TV_HOURS=48h).
    SECURITIES_TV_CHANNELS dict를 _normalize_channel_list로 안전하게 순회.
    """
    all_items = []
    ch_list = _normalize_channel_list(SECURITIES_TV_CHANNELS)
    print(f"  [섹션2] 증권TV {len(ch_list)}개 채널 ({SECURITIES_TV_HOURS}h)")

    for ch in ch_list:
        channel_id   = ch.get("id", "")
        channel_name = ch.get("name", channel_id)

        if not channel_id:
            print(f"    [스킵] {channel_name} — 채널ID 없음")
            continue

        videos    = get_recent_videos_via_playlist(youtube, channel_id, SECURITIES_TV_HOURS)
        collected = 0

        for v in videos:
            title = v.get("title", "")
            if is_ad_content(title):
                continue

            transcript = get_transcript(v["video_id"])

            if not (is_expert_program(title, transcript) or has_popular_panelist(title, transcript)):
                continue

            all_items.append({
                "source_type": "경제방송",
                "source_name": channel_name,
                "title":       title,
                "summary":     transcript[:500] if transcript else title,
                "link":        f"https://www.youtube.com/watch?v={v['video_id']}",
                "published":   v.get("published_at", ""),
            })
            collected += 1

        print(f"    {channel_name}: {collected}건")
        time.sleep(0.3)

    print(f"  [섹션2] 총 {len(all_items)}건")
    return all_items
