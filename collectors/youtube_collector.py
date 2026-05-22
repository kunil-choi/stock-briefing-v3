"""
유튜브 수집기 - v3
방송, 유튜버, 증권사 채널에서 주식 관련 영상 수집
"""
import os
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote  # ← requests.utils.quote 대신 표준 라이브러리 사용

from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi

from config import (
    YOUTUBE_API_KEY, SECURITIES_TV_CHANNELS, POPULAR_PANELISTS,
    BROADCAST_HOURS, YOUTUBER_HOURS, SECURITIES_TV_HOURS, load_channels
)

KST = timezone(timedelta(hours=9))

# 키워드 정의
STOCK_KEYWORDS = [
    "주식", "증시", "코스피", "코스닥", "나스닥", "S&P", "투자", "ETF", "펀드",
    "채권", "금리", "환율", "원달러", "경제", "재테크", "시황", "종목", "매수",
    "매도", "포트폴리오", "배당", "반도체", "2차전지", "AI", "인공지능"
]

EXPERT_KEYWORDS = [
    "전문가", "애널리스트", "펀드매니저", "대표", "소장", "연구원", "교수",
    "이사", "본부장", "팀장", "대담", "인터뷰", "특집"
]

AD_KEYWORDS = [
    "협찬", "광고", "프로모션", "이벤트", "할인", "모집", "신청", "등록"
]

INFO_KEYWORDS = [
    "강의", "교육", "기초", "입문", "초보", "배우기", "공부", "정리"
]


def get_youtube_client():
    """YouTube API 클라이언트 생성"""
    if not YOUTUBE_API_KEY:
        return None
    return build("youtube", "v3", developerKey=YOUTUBE_API_KEY)


def get_uploads_playlist_id(channel_id: str) -> str:
    """채널 ID(UCxxx)를 업로드 플레이리스트 ID(UUxxx)로 변환"""
    if channel_id.startswith("UC"):
        return "UU" + channel_id[2:]
    return channel_id


def resolve_channel_id(youtube, handle_or_id: str) -> str:
    """
    @handle 또는 채널 URL을 채널 ID(UCxxx)로 변환
    이미 UCxxx 형태면 그대로 반환
    """
    if handle_or_id.startswith("UC"):
        return handle_or_id

    # @handle 처리
    handle = handle_or_id.lstrip("@")
    try:
        response = youtube.channels().list(
            part="id",
            forHandle=handle
        ).execute()
        items = response.get("items", [])
        if items:
            return items[0]["id"]
    except Exception as e:
        print(f"  ⚠️ handle 변환 실패 ({handle_or_id}): {e}")
    return ""


def get_recent_videos_via_playlist(youtube, channel_id: str, hours: int = 24, max_results: int = 10) -> list:
    """
    채널의 업로드 플레이리스트에서 최근 영상 가져오기
    """
    playlist_id = get_uploads_playlist_id(channel_id)
    cutoff = datetime.now(KST) - timedelta(hours=hours)

    try:
        response = youtube.playlistItems().list(
            part="snippet",
            playlistId=playlist_id,
            maxResults=max_results
        ).execute()
    except Exception as e:
        print(f"  ⚠️ 플레이리스트 조회 실패 ({channel_id}): {e}")
        return []

    videos = []
    for item in response.get("items", []):
        snippet = item.get("snippet", {})
        published_str = snippet.get("publishedAt", "")
        try:
            published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
            published_kst = published.astimezone(KST)
            if published_kst >= cutoff:
                video_id = snippet.get("resourceId", {}).get("videoId", "")
                videos.append({
                    "video_id": video_id,
                    "title": snippet.get("title", ""),
                    "channel": snippet.get("channelTitle", ""),
                    "published": published_str,
                    "description": snippet.get("description", "")[:500],
                    "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
                    "url": f"https://www.youtube.com/watch?v={video_id}"
                })
        except Exception:
            continue

    return videos


def get_transcript(video_id: str, languages: list = None) -> str:
    """자막 가져오기"""
    if languages is None:
        languages = ["ko", "en"]
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
        text = " ".join([t["text"] for t in transcript_list[:100]])
        return text[:2000]
    except Exception:
        return ""


def is_stock_related(title: str, description: str = "") -> bool:
    """주식/경제 관련 영상인지 판단"""
    text = (title + " " + description).lower()
    return any(kw in text for kw in STOCK_KEYWORDS)


def is_expert_program(title: str, description: str = "") -> bool:
    """전문가 출연 프로그램인지 판단"""
    text = title + " " + description
    return any(kw in text for kw in EXPERT_KEYWORDS)


def has_popular_panelist(title: str, description: str = "") -> bool:
    """인기 패널리스트 등장 여부"""
    text = title + " " + description
    return any(p in text for p in POPULAR_PANELISTS)


def load_channels_safe() -> dict:
    """안전하게 channels.json 로드"""
    return load_channels()


def verify_channel(youtube, channel_id: str, min_views: int = 10000) -> dict:
    """
    채널 검증: 최근 영상 10개의 평균 조회수가 min_views 이상인지 확인
    """
    if not channel_id or not channel_id.startswith("UC"):
        return {"verified": False, "reason": "유효하지 않은 채널 ID"}

    playlist_id = get_uploads_playlist_id(channel_id)
    try:
        pl_resp = youtube.playlistItems().list(
            part="snippet",
            playlistId=playlist_id,
            maxResults=10
        ).execute()
    except Exception as e:
        return {"verified": False, "reason": str(e)}

    video_ids = [
        item["snippet"]["resourceId"]["videoId"]
        for item in pl_resp.get("items", [])
        if item.get("snippet", {}).get("resourceId", {}).get("videoId")
    ]
    if not video_ids:
        return {"verified": False, "reason": "영상 없음"}

    try:
        stats_resp = youtube.videos().list(
            part="statistics",
            id=",".join(video_ids)
        ).execute()
    except Exception as e:
        return {"verified": False, "reason": str(e)}

    views = []
    for item in stats_resp.get("items", []):
        vc = item.get("statistics", {}).get("viewCount", "0")
        try:
            views.append(int(vc))
        except ValueError:
            pass

    if not views:
        return {"verified": False, "reason": "조회수 데이터 없음"}

    avg_views = sum(views) / len(views)
    max_views = max(views)
    verified = max_views >= min_views

    return {
        "verified": verified,
        "avg_views": int(avg_views),
        "max_views": max_views,
        "checked_count": len(views),
        "reason": f"최대조회수 {max_views:,}" if verified else f"최대조회수 {max_views:,} (기준 미달)"
    }


def verify_all_channels(youtube, min_views: int = 10000) -> dict:
    """모든 채널 검증 후 data/verify_result.json에 저장"""
    channels_data = load_channels_safe()
    results = {}

    for category in ["broadcast", "youtuber", "securities"]:
        for ch in channels_data.get(category, []):
            ch_id = ch.get("id", "")
            ch_name = ch.get("name", "")
            if not ch_id:
                results[ch_name] = {"verified": False, "reason": "채널 ID 없음"}
                continue
            print(f"  검증 중: {ch_name} ({ch_id})")
            result = verify_channel(youtube, ch_id, min_views)
            results[ch_name] = result
            time.sleep(0.1)  # API 레이트 리밋 방지

    os.makedirs("data", exist_ok=True)
    with open("data/verify_result.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"✅ 검증 완료: {len(results)}개 채널")
    return results


def collect_section1_youtube(youtube) -> list:
    """
    섹션1 유튜브 수집:
    broadcast, youtuber, securities 카테고리에서 주식 관련 영상 수집
    """
    if not youtube:
        print("  ⚠️ YouTube API 키 없음, 건너뜀")
        return []

    channels_data = load_channels_safe()
    all_videos = []

    categories = [
        ("broadcast", BROADCAST_HOURS),
        ("youtuber", YOUTUBER_HOURS),
        ("securities", SECURITIES_TV_HOURS),
    ]

    for category, hours in categories:
        ch_list = channels_data.get(category, [])
        print(f"\n  [{category}] {len(ch_list)}개 채널 처리 중...")
        cat_count = 0

        for ch in ch_list:
            ch_id = ch.get("id", "")
            ch_name = ch.get("name", "")
            ch_url = ch.get("url", "")

            # ID가 없으면 URL에서 handle 추출하여 resolve 시도
            if not ch_id and ch_url:
                handle = ch_url.split("@")[-1] if "@" in ch_url else ""
                if handle:
                    ch_id = resolve_channel_id(youtube, handle)

            if not ch_id:
                print(f"    ⚠️ {ch_name}: 채널 ID 없음, 건너뜀")
                continue

            try:
                videos = get_recent_videos_via_playlist(youtube, ch_id, hours=hours)
                stock_videos = [v for v in videos if is_stock_related(v["title"], v.get("description", ""))]
                for v in stock_videos:
                    v["category"] = category
                    v["channel_name"] = ch_name
                    v["source"] = "youtube_section1"
                all_videos.extend(stock_videos)
                cat_count += len(stock_videos)
                if stock_videos:
                    print(f"    ✅ {ch_name}: {len(stock_videos)}개 수집")
            except Exception as e:
                print(f"    ❌ {ch_name}: {e}")

        print(f"  [{category}] 소계: {cat_count}개")

    print(f"\n  섹션1 유튜브 총계: {len(all_videos)}개")
    return all_videos


def collect_section2_securities_tv(youtube) -> list:
    """
    섹션2 증권TV 수집:
    SECURITIES_TV_CHANNELS에서 전문가 출연 영상 수집
    """
    if not youtube:
        print("  ⚠️ YouTube API 키 없음, 건너뜀")
        return []

    all_videos = []
    print(f"\n  [섹션2 증권TV] {len(SECURITIES_TV_CHANNELS)}개 채널 처리 중...")

    for ch in SECURITIES_TV_CHANNELS:
        ch_id = ch.get("id", "")
        ch_name = ch.get("name", "")
        ch_url = ch.get("url", "")

        # ID가 없으면 URL에서 handle 추출하여 resolve 시도
        if not ch_id and ch_url:
            handle = ch_url.split("@")[-1] if "@" in ch_url else ""
            if handle:
                ch_id = resolve_channel_id(youtube, handle)

        if not ch_id:
            print(f"    ⚠️ {ch_name}: 채널 ID 없음, 건너뜀")
            continue

        try:
            videos = get_recent_videos_via_playlist(youtube, ch_id, hours=SECURITIES_TV_HOURS)

            # 전문가 출연 또는 주식 관련 필터
            filtered = [
                v for v in videos
                if is_expert_program(v["title"], v.get("description", ""))
                or has_popular_panelist(v["title"], v.get("description", ""))
                or is_stock_related(v["title"], v.get("description", ""))
            ]

            for v in filtered:
                v["category"] = "securities_tv"
                v["channel_name"] = ch_name
                v["source"] = "youtube_section2"

            all_videos.extend(filtered)  # ✅ 수정: 채널별 초기화 없이 누적
            print(f"    ✅ {ch_name}: {len(filtered)}개 수집")

        except Exception as e:
            print(f"    ❌ {ch_name}: {e}")

    print(f"\n  섹션2 증권TV 총계: {len(all_videos)}개")
    return all_videos
