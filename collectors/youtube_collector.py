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
    SECURITIES_HOURS,
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

SECURITIES_ANALYSIS_KEYWORDS = [
    "분석", "리포트", "전망", "시황", "전략", "목표주가", "추천종목",
    "포트폴리오", "섹터", "실적", "투자의견", "매수", "매도", "중립",
    "강력매수", "상향", "하향", "신규", "커버리지",
]

AD_KEYWORDS = [
    "광고", "협찬", "이벤트", "강의", "클래스", "수강", "모집", "세미나",
    "할인", "프로모션", "가입", "혜택", "신청",
]


def get_youtube_client(api_key: str = None):
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
    playlist_id = get_uploads_playlist_id(channel_id)
    cutoff      = datetime.now(KST) - timedelta(hours=hours)
    videos      = []

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
        texts   = []
        for e in entries:
            if hasattr(e, "text"):
                texts.append(str(e.text))
            elif isinstance(e, dict):
                texts.append(e.get("text", ""))
            else:
                try:
                    texts.append(str(e))
                except Exception:
                    pass
        return " ".join(texts)[:max_chars]
    except Exception:
        return ""


def is_stock_related(title: str, transcript: str = "") -> bool:
    combined = (title + " " + transcript).lower()
    return any(kw in combined for kw in STOCK_KEYWORDS)


def is_securities_analysis(title: str, transcript: str = "") -> bool:
    combined = (title + " " + transcript).lower()
    return any(kw in combined for kw in SECURITIES_ANALYSIS_KEYWORDS)


def is_ad_content(title: str) -> bool:
    return any(kw in title for kw in AD_KEYWORDS)


def has_popular_panelist(title: str, transcript: str = "") -> bool:
    combined = title + " " + transcript
    return any(name in combined for name in POPULAR_PANELISTS)


def _normalize_channel_list(raw) -> list:
    result = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                ch_id   = item.get("id", "").strip()
                ch_name = item.get("name", ch_id)
                if ch_id:
                    result.append({"id": ch_id, "name": ch_name})
            elif isinstance(item, str) and item.strip():
                result.append({"id": item.strip(), "name": item.strip()})
    elif isinstance(raw, dict):
        for name, val in raw.items():
            if isinstance(val, dict):
                ch_id = val.get("id", "").strip()
                if ch_id:
                    result.append({"id": ch_id, "name": name})
            elif isinstance(val, str) and val.strip():
                result.append({"id": val.strip(), "name": name})
    return result


def collect_section1_youtube(youtube, channels: dict) -> list:
    all_items  = []
    categories = [
        ("broadcast",  BROADCAST_HOURS,  "경제방송", False),
        ("youtuber",   YOUTUBER_HOURS,   "유튜버",   False),
        ("securities", SECURITIES_HOURS, "증권사",   True),
    ]

    for cat_key, hours, source_type, securities_filter in categories:
        raw     = channels.get(cat_key, [])
        ch_list = _normalize_channel_list(raw)
        if not ch_list:
            print(f"  [섹션1-{cat_key}] 채널 없음 → 스킵")
            continue

        print(f"  [섹션1-{cat_key}] {len(ch_list)}개 채널 ({hours}h, type={source_type})")
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

                if is_stock_related(title):
                    transcript = get_transcript(v["video_id"])
                    stock_ok   = True
                else:
                    transcript = get_transcript(v["video_id"])
                    stock_ok   = is_stock_related(title, transcript)

                if not stock_ok:
                    continue

                if securities_filter and not is_securities_analysis(title, transcript):
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
