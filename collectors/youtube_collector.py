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

# 증권사 채널 전용 추가 필터: 순수 분석/전략 콘텐츠 키워드
SECURITIES_ANALYSIS_KEYWORDS = [
    "분석", "리포트", "전망", "시황", "전략", "목표주가", "추천종목",
    "포트폴리오", "섹터", "실적", "투자의견", "매수", "매도", "중립",
    "강력매수", "상향", "하향", "신규", "커버리지",
]

AD_KEYWORDS = [
    "광고", "협찬", "이벤트", "강의", "클래스", "수강", "모집", "세미나",
    "할인", "프로모션", "가입", "혜택", "신청",
]


# ── 유틸리티 ───────────────────────────────────────────────────────────────────

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
            # FetchedTranscriptSnippet 객체 또는 dict 모두 처리
            if hasattr(e, "text"):
                texts.append(str(e.text))
            elif isinstance(e, dict):
                texts.append(e.get("text", ""))
            else:
                # 기타 객체: 문자열 변환 시도
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
    """증권사 채널 전용: 순수 분석/전략 콘텐츠 여부 판별."""
    combined = (title + " " + transcript).lower()
    return any(kw in combined for kw in SECURITIES_ANALYSIS_KEYWORDS)


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
    channels.json 카테고리 값을 list[{"id":..., "name":...}] 형태로 통일.
    id가 빈 문자열인 항목(unconfirmed)은 제외.
    """
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


# ── 섹션 1 수집 ────────────────────────────────────────────────────────────────

def collect_section1_youtube(youtube, channels: dict) -> list:
    """
    섹션1: 방송사·유튜버·증권사 채널 영상 수집 (모두 24시간).

    채널 분류:
      broadcast  → "경제방송"  (한국경제TV·SBS Biz 등 방송국 유튜브)  24h
      youtuber   → "유튜버"    (슈카월드·삼프로TV 등 개인/독립 채널)   24h
      securities → "증권사"    (삼성증권·키움증권 등 증권사 공식 채널) 24h
                               is_securities_analysis() 추가 필터 적용

    섹션2(SECURITIES_TV_HOURS=48h)와 다른 점:
      섹션2는 경제방송TV 다시보기 채널로 업로드 지연을 감안해 48시간 수집.
      섹션1은 3개 카테고리 모두 24시간.
    """
    all_items  = []
    # BUG-H1 수정: securities 카테고리를 SECURITIES_HOURS(24h)로 명시
    # (YOUTUBER_HOURS 와 동일값이지만 의미를 명확히 분리)
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

                # BUG-NEW-2 최적화: 제목으로 먼저 주식 관련 여부 판단
                # 제목이 충분하면 자막은 summary 보강용으로만 취득
                if is_stock_related(title):
                    transcript = get_transcript(v["video_id"])
                    stock_ok   = True
                else:
                    transcript = get_transcript(v["video_id"])
                    stock_ok   = is_stock_related(title, transcript)

                if not stock_ok:
                    continue

                # 증권사 채널 추가 필터: 분석/전략 콘텐츠 여부 확인
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


# ── 섹션 2 수집 ────────────────────────────────────────────────────────────────

def collect_section2_securities_tv(youtube) -> list:
    """
    섹션2: config.py의 SECURITIES_TV_CHANNELS (경제방송 TV 다시보기 채널) 수집.

    수집 기간: SECURITIES_TV_HOURS = 48시간
    → 오전 종목추천 프로그램이 유튜브에 올라오는 시간이 늦어
      전날 방송분까지 포함하기 위해 이틀치(48h) 수집.

    필터: 전문가 출연 프로그램 또는 인기 패널리스트 포함 영상만 수집.

    BUG-M4 수정: 제목으로 먼저 판단, 통과하면 자막은 summary 보강용으로만 취득.
                 제목 불충분 시에만 자막으로 재판단하여 API 쿼터 절약.
    """
    all_items = []
    ch_list   = _normalize_channel_list(SECURITIES_TV_CHANNELS)
    print(f"  [섹션2] 경제방송TV {len(ch_list)}개 채널 ({SECURITIES_TV_HOURS}h, 전날 방송분 포함)")

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

            # 제목으로 먼저 판단: 통과 시 자막은 summary 보강용만
            title_pass = is_expert_program(title) or has_popular_panelist(title)
            if title_pass:
                transcript = get_transcript(v["video_id"])
            else:
                # 제목 불충분 → 자막으로 재판단
                transcript = get_transcript(v["video_id"])
                if not (is_expert_program(title, transcript) or has_popular_panelist(title, transcript)):
                    continue

            all_items.append({
                "source_type": "경제방송TV",
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
