# collectors/youtube_collector.py
"""
YouTube 수집기 - v3
섹션 1: 방송사 + 개인유튜버 + 증권사유튜브 채널 (24시간 기준)
섹션 2: 증권TV 전문가 채널 (48시간 기준)
채널 재검사: YouTube Data API 기반 실제 콘텐츠 품질 검증
"""
import os
import json
import requests
from datetime import datetime, timezone, timedelta

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    YouTubeTranscriptApi = None

from config import (
    YOUTUBE_API_KEY,
    POPULAR_PANELISTS,
    YOUTUBER_HOURS,
    BROADCAST_HOURS,
    SECURITIES_TV_HOURS,
    SECURITIES_TV_CHANNELS,
)

API_KEY = YOUTUBE_API_KEY
KST = timezone(timedelta(hours=9))

# ══════════════════════════════════════════════════════════════
#  키워드 목록
# ══════════════════════════════════════════════════════════════

STOCK_KEYWORDS = [
    "주식", "종목", "매수", "매도", "코스피", "코스닥", "상장", "실적",
    "반도체", "배터리", "2차전지", "바이오", "AI", "로봇", "방산", "원전",
    "ETF", "배당", "테마주", "급등", "목표가", "투자", "증시", "시황",
    "포트폴리오", "리밸런싱", "금리", "환율", "채권", "국채", "달러",
    "인플레이션", "경기", "FOMC", "연준", "GDP", "CPI",
    "엔비디아", "테슬라", "삼성전자", "SK하이닉스",
    "S&P", "나스닥", "다우", "미국장", "뉴욕증시",
    "상승", "하락", "전망", "분석", "추천", "리포트", "브리핑",
    "경제", "금융", "거시", "글로벌", "시장", "성공예감",
    "추천종목", "매매전략", "수익", "급락",
]

EXPERT_KEYWORDS = [
    "추천종목", "매매전략", "포트폴리오", "시황", "전문가",
    "투자전략", "종목분석", "주도주", "성장주", "가치주",
    "매수종목", "관심종목", "핵심종목", "대장주",
    "오늘의 증시", "장전", "장후", "시장분석",
]

# 채널 재검사용 키워드
AD_KEYWORDS = [
    "리딩방", "유료", "수익인증", "따라하면", "대박", "비공개",
    "카카오톡", "텔레그램", "가입", "구독료", "VIP", "프리미엄",
    "신청", "모집", "한정", "100% 수익", "검증된", "비법",
]

INFO_KEYWORDS = [
    "분석", "전망", "실적", "재무", "밸류에이션", "리포트",
    "리뷰", "점검", "이슈", "뉴스", "경제", "시황", "전략",
]


# ══════════════════════════════════════════════════════════════
#  YouTube API 유틸리티
# ══════════════════════════════════════════════════════════════

def get_uploads_playlist_id(channel_id: str) -> str:
    """채널 ID (UC...) → 업로드 재생목록 ID (UU...)"""
    if channel_id and channel_id.startswith("UC"):
        return "UU" + channel_id[2:]
    return None


def resolve_channel_id(channel_id_or_handle: str, api_key: str) -> str:
    """@handle 또는 채널 URL의 handle을 실제 채널 ID로 변환"""
    cid = channel_id_or_handle.strip()

    # 이미 UC...24자 형식이면 그대로 반환
    if cid.startswith("UC") and len(cid) == 24:
        return cid

    handle = cid.lstrip("@")
    url = "https://www.googleapis.com/youtube/v3/channels"
    params = {"part": "id", "forHandle": handle, "key": api_key}
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("items"):
            resolved = data["items"][0]["id"]
            print(f"  [ID변환] @{handle} → {resolved}")
            return resolved
    except Exception as e:
        print(f"  [ID변환 실패] @{handle}: {e}")
    return cid


def get_recent_videos_via_playlist(
    channel_id: str,
    api_key: str,
    hours: int = 24,
    max_results: int = 15,
) -> list:
    """업로드 재생목록에서 최근 N시간 영상 목록 반환"""
    playlist_id = get_uploads_playlist_id(channel_id)
    if not playlist_id:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    url = "https://www.googleapis.com/youtube/v3/playlistItems"
    params = {
        "part": "snippet",
        "playlistId": playlist_id,
        "maxResults": max_results,
        "key": api_key,
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()

        if "error" in data:
            err = data["error"]
            print(f"    API 오류: {err.get('code')} - {err.get('message','')[:80]}")
            return []

        items = data.get("items", [])
        recent = []
        for item in items:
            snippet = item.get("snippet", {})
            published_str = snippet.get("publishedAt", "")
            if not published_str:
                continue
            try:
                pub_dt = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
                if pub_dt >= cutoff:
                    video_id = snippet.get("resourceId", {}).get("videoId", "")
                    recent.append({
                        "id": {"videoId": video_id},
                        "snippet": {
                            "title":        snippet.get("title", ""),
                            "description":  snippet.get("description", "")[:300],
                            "publishedAt":  published_str,
                            "channelId":    snippet.get("channelId", ""),
                            "channelTitle": snippet.get("channelTitle", ""),
                        },
                    })
                else:
                    break
            except Exception:
                continue
        return recent

    except Exception as e:
        print(f"    요청 실패: {e}")
        return []


def get_transcript(video_id: str, max_chars: int = 1000) -> str:
    """YouTube 자막(한국어) 추출. 실패 시 빈 문자열 반환"""
    if not YouTubeTranscriptApi or not video_id:
        return ""
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(
            video_id, languages=["ko", "ko-KR"]
        )
        text = " ".join(t.get("text", "") for t in transcript_list)
        return text[:max_chars]
    except Exception:
        return ""


def is_stock_related(title: str, description: str) -> bool:
    """제목/설명에 주식 관련 키워드가 포함되어 있는지 확인"""
    text = (title + " " + description).lower()
    return any(kw in text for kw in STOCK_KEYWORDS)


def is_expert_program(title: str, description: str) -> bool:
    """증권TV 전문가 프로그램 관련 키워드 확인"""
    text = title + " " + description
    return any(kw in text for kw in EXPERT_KEYWORDS)


def has_popular_panelist(title: str, description: str) -> list:
    """인기 패널리스트 언급 여부 확인 후 이름 목록 반환"""
    text = title + " " + description
    return [p for p in POPULAR_PANELISTS if p in text]


def load_channels_safe() -> dict:
    """channels.json 안전 로드 (실패 시 빈 구조 반환)"""
    try:
        with open("channels.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "broadcast":  data.get("broadcast", {}),
            "youtuber":   data.get("youtuber", {}),
            "securities": data.get("securities", {}),
        }
    except Exception as e:
        print(f"  [channels.json 로드 실패] {e}")
        return {"broadcast": {}, "youtuber": {}, "securities": {}}


# ══════════════════════════════════════════════════════════════
#  채널 재검사 (YouTube API 기반)
# ══════════════════════════════════════════════════════════════

def verify_channel(channel_id: str, api_key: str, hours: int = 72) -> dict:
    """
    채널 최근 영상 품질 검사.
    반환: {"status": "active"|"warning"|"inactive", "score": int, "reason": str}
    """
    videos = get_recent_videos_via_playlist(channel_id, api_key, hours=hours, max_results=10)

    if not videos:
        return {
            "status": "inactive",
            "score":  0,
            "reason": f"최근 {hours}시간 영상 없음",
        }

    total        = len(videos)
    stock_count  = 0
    ad_count     = 0
    info_count   = 0

    for v in videos:
        sn    = v.get("snippet", {})
        title = sn.get("title", "")
        desc  = sn.get("description", "")
        text  = title + " " + desc

        if is_stock_related(title, desc):
            stock_count += 1
        if any(kw in text for kw in AD_KEYWORDS):
            ad_count += 1
        if any(kw in text for kw in INFO_KEYWORDS):
            info_count += 1

    stock_ratio = stock_count / total if total else 0
    ad_ratio    = ad_count    / total if total else 0
    info_ratio  = info_count  / total if total else 0

    # 점수 계산 (0~100)
    score = int(stock_ratio * 50 + info_ratio * 30 - ad_ratio * 40)
    score = max(0, min(100, score))

    if score >= 50:
        status = "active"
        reason = f"주식 관련 영상 {stock_count}/{total}개, 정보성 {info_count}/{total}개"
    elif score >= 20:
        status = "warning"
        reason = f"주식 관련 영상 적음 ({stock_count}/{total}개), 광고성 {ad_count}/{total}개"
    else:
        status = "inactive"
        reason = f"관련 영상 부족 또는 광고성 콘텐츠 다수"

    return {"status": status, "score": score, "reason": reason}


def verify_all_channels(api_key: str) -> dict:
    """
    channels.json 의 모든 채널을 재검사하고 결과를 반환 + 저장.
    반환: {"broadcast": {...}, "youtuber": {...}, "securities": {...}}
    """
    channels = load_channels_safe()
    results  = {"broadcast": {}, "youtuber": {}, "securities": {}}

    for category, ch_dict in channels.items():
        items = ch_dict if isinstance(ch_dict, dict) else {}
        for name, info in items.items():
            cid = info.get("id", "") if isinstance(info, dict) else ""
            if not cid:
                continue
            print(f"  [재검사] {name} ({cid})...")
            result = verify_channel(cid, api_key)
            results[category][name] = {
                **info,
                "verify_status": result["status"],
                "verify_score":  result["score"],
                "verify_reason": result["reason"],
                "verify_date":   datetime.now(KST).strftime("%Y-%m-%d"),
            }

    _save_verify_result(results)
    return results


def _save_verify_result(results: dict) -> None:
    """재검사 결과를 data/verify_result.json 에 저장"""
    os.makedirs("data", exist_ok=True)
    path = "data/verify_result.json"
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"  [재검사] 결과 저장: {path}")
    except Exception as e:
        print(f"  [재검사] 저장 실패: {e}")


# ══════════════════════════════════════════════════════════════
#  섹션 1: 유튜브·미디어 채널 수집
# ══════════════════════════════════════════════════════════════

def collect_section1_youtube() -> list:
    """
    섹션 1용 데이터 수집.
    방송사(BROADCAST_HOURS) + 개인유튜버(YOUTUBER_HOURS) + 증권사유튜브(YOUTUBER_HOURS)
    주식 관련 영상만 필터링하여 반환.
    """
    if not API_KEY:
        print("  [섹션1] YouTube API 키 없음")
        return []

    channels = load_channels_safe()
    results  = []

    channel_groups = [
        ("broadcast",  channels.get("broadcast",  {}), BROADCAST_HOURS,  "경제방송"),
        ("youtuber",   channels.get("youtuber",   {}), YOUTUBER_HOURS,   "개인유튜브"),
        ("securities", channels.get("securities", {}), YOUTUBER_HOURS,   "증권사유튜브"),
    ]

    for group_key, ch_dict, hours, source_type in channel_groups:
        items = ch_dict if isinstance(ch_dict, dict) else {}
        for ch_name, ch_info in items.items():
            if isinstance(ch_info, dict):
                cid    = ch_info.get("id", "")
                status = ch_info.get("status", "active")
            else:
                cid    = str(ch_info)
                status = "active"

            if not cid or status == "inactive":
                continue

            # handle → channel ID 변환
            resolved_id = resolve_channel_id(cid, API_KEY)

            print(f"  [{source_type}] {ch_name} 수집 중...")
            videos = get_recent_videos_via_playlist(
                resolved_id, API_KEY, hours=hours, max_results=15
            )

            for v in videos:
                sn    = v.get("snippet", {})
                title = sn.get("title", "")
                desc  = sn.get("description", "")
                vid   = v.get("id", {}).get("videoId", "")

                if not is_stock_related(title, desc):
                    continue

                # 자막 추출 (실패해도 계속 진행)
                transcript = get_transcript(vid, max_chars=800)
                summary    = transcript if transcript else desc[:400]

                panelists = has_popular_panelist(title, desc)

                results.append({
                    "source_type":    source_type,
                    "source_name":    ch_name,
                    "title":          title,
                    "summary":        summary,
                    "link":           f"https://www.youtube.com/watch?v={vid}",
                    "published":      sn.get("publishedAt", ""),
                    "section":        "section1",
                    "panelists":      panelists,
                    "has_transcript": bool(transcript),
                })

            print(f"    → {len([v for v in videos if is_stock_related(v.get('snippet',{}).get('title',''), v.get('snippet',{}).get('description',''))])}건 수집")

    print(f"  [섹션1 합계] {len(results)}건")
    return results


# ══════════════════════════════════════════════════════════════
#  섹션 2: 증권TV 전문가 채널 수집
# ══════════════════════════════════════════════════════════════

def collect_section2_securities_tv() -> list:
    """
    섹션 2용 데이터 수집.
    config.py 의 SECURITIES_TV_CHANNELS 목록 기반.
    전문가 프로그램 키워드 필터링 적용 (48시간 기준).
    """
    if not API_KEY:
        print("  [섹션2] YouTube API 키 없음")
        return []

    results = []

    for ch in SECURITIES_TV_CHANNELS:
        ch_id    = ch.get("id", "")
        ch_name  = ch.get("name", ch_id)
        hours    = SECURITIES_TV_HOURS

        if not ch_id:
            continue

        resolved_id = resolve_channel_id(ch_id, API_KEY)
        print(f"  [증권TV] {ch_name} 수집 중...")

        videos = get_recent_videos_via_playlist(
            resolved_id, API_KEY, hours=hours, max_results=20
        )

        for v in videos:
            sn    = v.get("snippet", {})
            title = sn.get("title", "")
            desc  = sn.get("description", "")
            vid   = v.get("id", {}).get("videoId", "")

            # 전문가 프로그램 키워드 또는 주식 관련 키워드 확인
            if not (is_expert_program(title, desc) or is_stock_related(title, desc)):
                continue

            transcript = get_transcript(vid, max_chars=800)
            summary    = transcript if transcript else desc[:400]

            # 패널리스트(전문가) 이름 추출
            panelists = has_popular_panelist(title, desc + " " + summary)

            results.append({
                "source_type":    "증권TV",
                "source_name":    ch_name,
                "title":          title,
                "summary":        summary,
                "link":           f"https://www.youtube.com/watch?v={vid}",
                "published":      sn.get("publishedAt", ""),
                "section":        "section2",
                "expert_name":    ", ".join(panelists) if panelists else "",
                "has_transcript": bool(transcript),
            })

        print(f"    → {len(results)}건 누적")

    print(f"  [섹션2 합계] {len(results)}건")
    return results
