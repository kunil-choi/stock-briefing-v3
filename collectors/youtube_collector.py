# collectors/youtube_collector.py
"""
YouTube 수집기 - v3
섹션 1: 방송사 + 개인유튜브 + 증권사유튜브 채널
섹션 2: 증권TV 채널 (전일 기준)
채널 재검사: YouTube Data API 기반 실제 콘텐츠 품질 검증
"""
import requests
import json
import os
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

# ── 주식/경제 관련 키워드 ──
STOCK_KEYWORDS = [
    "주식", "종목", "매수", "매도", "코스피", "코스닥", "상장", "실적",
    "반도체", "배터리", "2차전지", "바이오", "AI", "로봇", "방산", "원전",
    "ETF", "배당", "테마주", "급등", "목표가", "투자", "증시", "시황",
    "포트폴리오", "리밸런싱", "금리", "환율", "채권", "국채", "달러",
    "인플레이션", "경기", "FOMC", "연준", "GDP", "CPI", "고용",
    "엔비디아", "테슬라", "삼성전자", "SK하이닉스",
    "S&P", "나스닥", "다우", "미국장", "뉴욕증시",
    "상승", "하락", "전망", "분석", "추천", "리포트", "브리핑",
    "경제", "금융", "거시", "글로벌", "시장", "성공예감",
    "추천종목", "매매전략", "수익", "급락",
]

# ── 증권TV 전문가 프로그램 관련 키워드 ──
EXPERT_KEYWORDS = [
    "추천종목", "매매전략", "포트폴리오", "시황", "전문가",
    "투자전략", "종목분석", "주도주", "성장주", "가치주",
    "매수종목", "관심종목", "핵심종목", "대장주",
    "오늘의 증시", "장전", "장후", "시장분석",
]

# ── ✅ 재검사: 광고성·리딩방 의심 키워드 ──
AD_KEYWORDS = [
    "리딩방", "유료", "수익인증", "따라하면", "대박",
    "비공개", "카카오톡", "텔레그램", "가입", "구독료",
    "VIP", "프리미엄", "신청", "모집", "한정",
    "100% 수익", "검증된", "전설의", "비법",
]

# ── ✅ 재검사: 실질 정보 제공 키워드 ──
INFO_KEYWORDS = [
    "분석", "전망", "실적", "재무", "밸류에이션",
    "리포트", "리뷰", "점검", "이슈", "뉴스",
    "경제", "시황", "전략", "포트폴리오",
]


# ══════════════════════════════════════════════
# 공통 유틸
# ══════════════════════════════════════════════

def get_uploads_playlist_id(channel_id: str) -> str:
    """채널 ID (UC...) → 업로드 재생목록 ID (UU...)"""
    if channel_id.startswith("UC"):
        return "UU" + channel_id[2:]
    return None


def resolve_channel_id(channel_id_or_handle: str, api_key: str) -> str:
    """@handle 형식을 실제 채널 ID로 변환"""
    if channel_id_or_handle.startswith("UC") and len(channel_id_or_handle) == 24:
        return channel_id_or_handle
    handle = channel_id_or_handle.lstrip("@")
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
    return channel_id_or_handle


def get_recent_videos_via_playlist(
    channel_id: str,
    api_key: str,
    hours: int = 24,
    max_results: int = 15,
) -> list:
    """업로드 재생목록에서 최근 N시간 영상 가져오기"""
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
            print(f"    API 오류: {err.get('code')} - {err.get('message', '')}")
            return []

        items = data.get("items", [])
        recent_items = []
        for item in items:
            snippet = item.get("snippet", {})
            published_str = snippet.get("publishedAt", "")
            if not published_str:
                continue
            try:
                published_dt = datetime.fromisoformat(
                    published_str.replace("Z", "+00:00")
                )
                if published_dt >= cutoff:
                    video_id = snippet.get("resourceId", {}).get("videoId", "")
                    recent_items.append({
                        "id": {"videoId": video_id},
                        "snippet": {
                            "title":        snippet.get("title", ""),
                            "description":  snippet.get("description", ""),
                            "publishedAt":  published_str,
                            "channelId":    snippet.get("channelId", ""),
                            "channelTitle": snippet.get("channelTitle", ""),
                        },
                    })
                else:
                    break
            except Exception:
                continue

        print(f"    최근 {hours}시간 영상: {len(recent_items)}개 (전체 {len(items)}개 중)")
        return recent_items

    except Exception as e:
        print(f"    요청 오류: {e}")
        return []


def get_transcript(video_id: str, max_chars: int = 2000) -> str:
    """유튜브 영상 자막 추출"""
    if YouTubeTranscriptApi is None:
        return ""
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        try:
            transcript = transcript_list.find_transcript(["ko"])
        except Exception:
            try:
                transcript = transcript_list.find_generated_transcript(["ko"])
            except Exception:
                return ""

        entries = transcript.fetch()
        texts = []
        for e in entries:
            if hasattr(e, "text"):
                texts.append(e.text)
            elif isinstance(e, dict):
                texts.append(e.get("text", e.get("value", "")))

        return " ".join(texts)[:max_chars]
    except Exception:
        return ""


def is_stock_related(title: str, description: str = "") -> bool:
    """주식/경제 키워드 포함 여부 확인"""
    text = (title + " " + description).lower()
    return any(k.lower() in text for k in STOCK_KEYWORDS)


def is_expert_program(title: str, description: str = "") -> bool:
    """증권TV 전문가 프로그램 여부 확인"""
    text = (title + " " + description).lower()
    return any(k.lower() in text for k in EXPERT_KEYWORDS + STOCK_KEYWORDS)


def has_popular_panelist(title: str, description: str = "") -> list:
    """인기 패널 언급 여부 확인"""
    text = title + " " + description
    return [p for p in POPULAR_PANELISTS if p in text]


def load_channels_safe() -> dict:
    """channels.json 안전 로드"""
    try:
        from config import load_channels
        return load_channels()
    except Exception as e:
        print(f"  [channels.json 로드 실패] {e}")
        return {}


# ══════════════════════════════════════════════
# ✅ 수정 2/3 핵심: YouTube API 기반 채널 재검사
# ══════════════════════════════════════════════

def verify_channel(
    channel_id: str,
    channel_name: str,
    api_key: str,
    days: int = 30,
) -> dict:
    """
    단일 채널에 대해 YouTube Data API로 실제 콘텐츠 품질을 검증합니다.

    검증 기준:
    1. 최근 30일 업로드 영상 수 (0개 → inactive)
    2. 광고성·리딩방 의심 키워드 비율 (30% 이상 → warning)
    3. 실질 정보 제공 키워드 비율 (10% 미만 → warning)

    반환값:
    {
        "channel_id":     str,
        "channel_name":   str,
        "status":         "active" | "warning" | "inactive",
        "issue":          str,          # 경고 사유
        "video_count":    int,          # 최근 30일 영상 수
        "ad_ratio":       float,        # 광고성 키워드 비율 (0~1)
        "info_ratio":     float,        # 정보성 키워드 비율 (0~1)
        "last_verified":  str,          # 검사 날짜 (YYYY-MM-DD)
    }
    """
    result = {
        "channel_id":    channel_id,
        "channel_name":  channel_name,
        "status":        "active",
        "issue":         "",
        "video_count":   0,
        "ad_ratio":      0.0,
        "info_ratio":    0.0,
        "last_verified": datetime.now().strftime("%Y-%m-%d"),
    }

    if not api_key:
        result["issue"] = "YouTube API 키 없음"
        return result

    # ── Step 1: 최근 30일 영상 목록 수집 ──
    videos = get_recent_videos_via_playlist(
        channel_id, api_key, hours=days * 24, max_results=50
    )
    result["video_count"] = len(videos)

    if len(videos) == 0:
        result["status"] = "inactive"
        result["issue"]  = f"최근 {days}일 업로드 없음"
        return result

    # ── Step 2: 제목+설명 텍스트 수집 ──
    all_titles = []
    for v in videos:
        snippet = v.get("snippet", {})
        title   = snippet.get("title", "")
        desc    = snippet.get("description", "")[:200]
        all_titles.append(title + " " + desc)

    combined_text = " ".join(all_titles).lower()
    total_videos  = len(all_titles)

    # ── Step 3: 광고성 키워드 비율 계산 ──
    ad_hit_count = sum(
        1 for title_desc in all_titles
        if any(kw.lower() in title_desc.lower() for kw in AD_KEYWORDS)
    )
    ad_ratio = ad_hit_count / total_videos if total_videos > 0 else 0.0
    result["ad_ratio"] = round(ad_ratio, 3)

    # ── Step 4: 정보성 키워드 비율 계산 ──
    info_hit_count = sum(
        1 for title_desc in all_titles
        if any(kw.lower() in title_desc.lower() for kw in INFO_KEYWORDS)
    )
    info_ratio = info_hit_count / total_videos if total_videos > 0 else 0.0
    result["info_ratio"] = round(info_ratio, 3)

    # ── Step 5: 주식 관련 채널 여부 확인 ──
    stock_hit_count = sum(
        1 for title_desc in all_titles
        if any(kw.lower() in title_desc.lower() for kw in STOCK_KEYWORDS)
    )
    stock_ratio = stock_hit_count / total_videos if total_videos > 0 else 0.0

    # ── Step 6: 판정 ──
    issues = []

    if ad_ratio >= 0.30:
        issues.append(f"광고성·리딩방 의심 콘텐츠 비율 높음 ({ad_ratio*100:.0f}%)")

    if info_ratio < 0.10:
        issues.append(f"실질 정보 제공 비율 낮음 ({info_ratio*100:.0f}%)")

    if stock_ratio < 0.05:
        issues.append(f"주식·경제 관련 콘텐츠 비율 낮음 ({stock_ratio*100:.0f}%)")

    if issues:
        result["status"] = "warning"
        result["issue"]  = " / ".join(issues)

    return result


def verify_all_channels(channels_data: dict, api_key: str) -> dict:
    """
    channels.json 의 모든 채널을 일괄 재검사합니다.

    반환값:
    {
        "broadcast":  { "채널명": verify_result, ... },
        "youtuber":   { ... },
        "securities": { ... },
        "summary": {
            "total": int, "active": int,
            "warning": int, "inactive": int,
        }
    }
    """
    print("\n=== 채널 전체 재검사 시작 ===")
    results = {
        "broadcast":  {},
        "youtuber":   {},
        "securities": {},
    }
    summary = {"total": 0, "active": 0, "warning": 0, "inactive": 0}

    for category in ["broadcast", "youtuber", "securities"]:
        channels = channels_data.get(category, {})
        print(f"\n[{category}] {len(channels)}개 채널 검사 중...")

        for name, info in channels.items():
            channel_id = info.get("id", "")
            if not channel_id:
                print(f"  [{name}] 채널 ID 없음, 건너뜀")
                continue

            print(f"  [{name}] 검사 중...")
            res = verify_channel(channel_id, name, api_key, days=30)
            results[category][name] = res

            # 원본 channels_data 상태 업데이트
            channels_data[category][name]["status"]        = res["status"]
            channels_data[category][name]["last_verified"] = res["last_verified"]

            summary["total"] += 1
            summary[res["status"]] = summary.get(res["status"], 0) + 1

            status_icon = "✅" if res["status"] == "active" else (
                "⚠️" if res["status"] == "warning" else "❌"
            )
            print(
                f"    {status_icon} {res['status']} | "
                f"영상 {res['video_count']}개 | "
                f"광고성 {res['ad_ratio']*100:.0f}% | "
                f"정보성 {res['info_ratio']*100:.0f}%"
                + (f" | {res['issue']}" if res["issue"] else "")
            )

    results["summary"] = summary
    print(
        f"\n=== 재검사 완료: 전체 {summary['total']}개 | "
        f"정상 {summary['active']}개 | "
        f"경고 {summary['warning']}개 | "
        f"비활성 {summary['inactive']}개 ==="
    )

    # ── 재검사 결과를 data/ 에 저장 (관리자 페이지 표시용) ──
    _save_verify_result(results)

    return results


def _save_verify_result(results: dict):
    """재검사 결과를 data/verify_result.json 에 저장"""
    os.makedirs("data", exist_ok=True)
    save_path = "data/verify_result.json"
    try:
        with open(save_path, "w", encoding="utf-8") as f:
            # summary 외 verify_result 객체는 직렬화 가능한 형태로 저장
            serializable = {
                k: v for k, v in results.items()
            }
            json.dump(serializable, f, ensure_ascii=False, indent=2)
        print(f"  [재검사] 결과 저장: {save_path}")
    except Exception as e:
        print(f"  [재검사] 결과 저장 실패: {e}")


# ══════════════════════════════════════════════
# 섹션 1: 유튜브·미디어 채널 수집
# ══════════════════════════════════════════════

def collect_section1_youtube() -> list:
    """
    섹션 1용 YouTube 수집:
    - 방송사 채널 (broadcast)
    - 개인 유튜브 채널 (youtuber)
    - 증권사 유튜브 채널 (securities)
    active 상태인 채널만 수집합니다.
    """
    print("\n=== 섹션 1: 유튜브·미디어 채널 수집 ===")

    if not API_KEY:
        print("  YouTube API 키가 설정되지 않았습니다.")
        return []

    results = []
    channels_data = load_channels_safe()

    channel_groups = {
        "방송사":      channels_data.get("broadcast",  {}),
        "개인유튜브":  channels_data.get("youtuber",   {}),
        "증권사유튜브": channels_data.get("securities", {}),
    }

    for group_type, channels in channel_groups.items():
        for name, info in channels.items():
            if isinstance(info, dict):
                channel_id = info.get("id", "")
                # ✅ inactive 채널은 수집 제외
                if info.get("status") == "inactive":
                    print(f"\n[{group_type}] {name} → 비활성 채널, 건너뜀")
                    continue
            else:
                channel_id = info

            if not channel_id:
                continue

            print(f"\n[{group_type}] {name} ({channel_id})")

            if not channel_id.startswith("UC"):
                channel_id = resolve_channel_id(channel_id, API_KEY)

            hours = BROADCAST_HOURS if group_type == "방송사" else YOUTUBER_HOURS
            videos = get_recent_videos_via_playlist(
                channel_id, API_KEY, hours=hours, max_results=10
            )

            collected = 0
            for item in videos:
                snippet  = item.get("snippet", {})
                title    = snippet.get("title", "")
                desc     = snippet.get("description", "")
                video_id = item.get("id", {}).get("videoId", "")

                if not is_stock_related(title, desc):
                    continue

                transcript = get_transcript(video_id, max_chars=1500)
                summary    = transcript if transcript else desc[:500]
                panelists  = has_popular_panelist(title, desc)

                source_label = name
                if panelists:
                    source_label = f"{name} (패널: {', '.join(panelists)})"

                if group_type == "방송사":
                    channel_type_tag = "경제방송"
                elif group_type == "증권사유튜브":
                    channel_type_tag = "증권사"
                else:
                    channel_type_tag = "개인유튜브"

                results.append({
                    "source_type":    channel_type_tag,
                    "source_name":    source_label,
                    "channel_group":  group_type,
                    "title":          title,
                    "summary":        summary,
                    "link":           f"https://www.youtube.com/watch?v={video_id}",
                    "published":      snippet.get("publishedAt", ""),
                    "panelists":      panelists,
                    "section":        "section1",
                })
                collected += 1

            print(f"  → {collected}개 수집")

    print(f"\n[섹션 1 유튜브 합계] {len(results)}건")
    return results


# ══════════════════════════════════════════════
# 섹션 2: 증권TV 전문가 채널 수집
# ══════════════════════════════════════════════

def collect_section2_securities_tv() -> list:
    """
    섹션 2용 YouTube 수집:
    - 증권TV 전문가 출연 프로그램 (전일 기준)
    - config.py SECURITIES_TV_CHANNELS 기준
    - active 상태인 채널만 수집
    """
    print("\n=== 섹션 2: 증권TV 전문가 채널 수집 ===")

    if not API_KEY:
        print("  YouTube API 키가 설정되지 않았습니다.")
        return []

    results = []

    for name, info in SECURITIES_TV_CHANNELS.items():
        channel_id = info.get("id", "")
        if not channel_id:
            continue

        print(f"\n[증권TV] {name} ({channel_id})")

        videos = get_recent_videos_via_playlist(
            channel_id, API_KEY,
            hours=SECURITIES_TV_HOURS,
            max_results=20,
        )

        collected = 0
        for item in videos:
            snippet  = item.get("snippet", {})
            title    = snippet.get("title", "")
            desc     = snippet.get("description", "")
            video_id = item.get("id", {}).get("videoId", "")

            if not is_expert_program(title, desc):
                continue

            transcript = get_transcript(video_id, max_chars=2000)
            summary    = transcript if transcript else desc[:600]
            panelists  = has_popular_panelist(title, desc)

            expert_label = panelists[0] if panelists else ""

            results.append({
                "source_type":  "증권TV",
                "source_name":  name,
                "expert_name":  expert_label,
                "title":        title,
                "summary":      summary,
                "link":         f"https://www.youtube.com/watch?v={video_id}",
                "published":    snippet.get("publishedAt", ""),
                "panelists":    panelists,
                "section":      "section2",
            })
            collected += 1

        print(f"  → {collected}개 수집")

    print(f"\n[섹션 2 증권TV 합계] {len(results)}건")
    return results
