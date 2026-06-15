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
    "증권", "ETF", "수익률 전망", "실적발표", "어닝", "리포트",
    "급등", "급락", "섹터", "포트폴리오", "코멘트",
    "삼성전자", "SK하이닉스", "LG에너지솔루션", "현대차",
    "금리인상", "환율", "달러", "AI반도체", "배터리",
]

EXPERT_KEYWORDS = [
    "매수전략", "애널리스트", "증권사", "리포트", "전망",
    "목표주가", "투자의견", "매수추천", "시장분석",
    "섹터분석", "포트폴리오", "리스크", "코멘트",
]

SECURITIES_ANALYSIS_KEYWORDS = [
    "분석", "리포트", "전망", "시황", "코멘트",
    "목표주가", "투자의견", "매수추천", "종목분석",
    "수익률 전망", "섹터분석", "포트폴리오", "리스크",
    "매수전략", "수익률", "실적발표", "이슈", "스탁",
    "신규 커버리지",
]

AD_KEYWORDS = [
    "광고비", "협찬", "홍보영상", "신청하기", "무료강의",
    "유료과정", "강의모집", "수강생", "연락처", "카카오링크",
]

# ── 패널리스트 검색 전용 설정 ──────────────────────────────────────────
# 이름 검색 시 함께 붙이는 주식/경제 맥락 키워드
_PANELIST_SEARCH_SUFFIXES = ["주식", "경제", "투자", "증시", "전망"]
# 검색 결과에서 수집할 최대 영상 수 (이름당)
_PANELIST_MAX_RESULTS = 10
# 검색 대상 기간 (시간)
_PANELIST_HOURS = 48


def get_youtube_client(api_key: str = None):
    if not api_key:
        api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        print("  [YouTube] API 키 없음")
        return None
    try:
        client = build("youtube", "v3", developerKey=api_key)
        print("  [YouTube] 클라이언트 초기화 성공")
        return client
    except Exception as e:
        print(f"  [YouTube] 클라이언트 초기화 실패: {e}")
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
        print(f"  [일반 오류 발생] {channel_id}: {e}")

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


# ── 섹션1: 등록 채널 플레이리스트 수집 ─────────────────────────────────

def collect_section1_youtube(youtube, channels: dict) -> list:
    all_items  = []
    categories = [
        ("broadcast",  BROADCAST_HOURS,  "경제방송", False),
        ("youtuber",   YOUTUBER_HOURS,   "유튜브",   False),
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

        print(f"   → {collected}건 수집")

    print(f"  [섹션1] 총 {len(all_items)}건")
    return all_items


# ── 섹션2: 패널리스트 이름 검색 수집 (신규) ────────────────────────────

def collect_panelist_youtube(youtube) -> list:
    """
    POPULAR_PANELISTS 이름으로 YouTube를 검색해서,
    등록 채널 외부 영상까지 포함해 주식·경제 관련 콘텐츠를 수집한다.

    - 수집 기간: _PANELIST_HOURS (48h)
    - 이름당 검색 suffix 중 첫 매칭 결과 사용 (quota 절약)
    - source_type: "유튜브" (애널리스트/경제방송 채널 출신 인물이면 가중치 상향은
      ai_analyzer의 channel_weight 로직에서 자동 처리됨)
    - 중복 제거: video_id 기준
    """
    if not youtube:
        print("  [패널리스트 검색] YouTube 클라이언트 없음 → 스킵")
        return []

    cutoff    = datetime.now(KST) - timedelta(hours=_PANELIST_HOURS)
    all_items = []
    seen_ids  = set()

    print(f"  [패널리스트 검색] {len(POPULAR_PANELISTS)}명, 최근 {_PANELIST_HOURS}h")

    for name in POPULAR_PANELISTS:
        collected = 0

        for suffix in _PANELIST_SEARCH_SUFFIXES:
            query = f"{name} {suffix}"
            try:
                resp = youtube.search().list(
                    part="snippet",
                    q=query,
                    type="video",
                    order="date",
                    publishedAfter=cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    maxResults=_PANELIST_MAX_RESULTS,
                    relevanceLanguage="ko",
                    regionCode="KR",
                ).execute()
            except HttpError as e:
                print(f"    [검색 오류] {query}: {e}")
                break
            except Exception as e:
                print(f"    [검색 오류] {query}: {e}")
                break

            items = resp.get("items", [])
            if not items:
                continue

            for item in items:
                snippet  = item.get("snippet", {})
                video_id = item.get("id", {}).get("videoId", "")
                if not video_id or video_id in seen_ids:
                    continue

                title        = snippet.get("title", "").strip()
                channel_name = snippet.get("channelTitle", "").strip()
                published_at = snippet.get("publishedAt", "")

                if not title or is_ad_content(title):
                    continue

                # 제목에 이름이 없으면 자막까지 확인
                if name not in title:
                    transcript = get_transcript(video_id)
                    if name not in transcript:
                        continue
                    summary = transcript[:500]
                else:
                    transcript = get_transcript(video_id)
                    summary    = transcript[:500] if transcript else title

                # 주식/경제 관련성 최종 확인
                if not is_stock_related(title, transcript):
                    continue

                # 발행일 파싱
                try:
                    pub_dt = datetime.fromisoformat(
                        published_at.replace("Z", "+00:00")
                    ).astimezone(KST)
                    pub_str = pub_dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pub_str = published_at

                seen_ids.add(video_id)
                all_items.append({
                    "source_type": "유튜브",
                    "source_name": channel_name,
                    "title":       title,
                    "summary":     summary or title,
                    "link":        f"https://www.youtube.com/watch?v={video_id}",
                    "published":   pub_str,
                    # 패널리스트 이름을 메타로 저장해두면
                    # ai_analyzer의 extract_mentions에서 snippet에 이름이 잡힘
                    "_panelist":   name,
                })
                collected += 1

            # 첫 suffix에서 결과를 찾았으면 나머지 suffix는 생략 (quota 절약)
            if collected > 0:
                break

            time.sleep(0.1)

        print(f"    {name}: {collected}건")
        time.sleep(0.3)   # 검색 API 호출 간격

    print(f"  [패널리스트 검색] 총 {len(all_items)}건 (중복 제거 후)")
    return all_items
