# collectors/candidate_collector.py
"""
패널리스트 후보 + 신규 채널 후보 수집 모듈
- 유튜브 수집/Gemini 분석 결과에서 미등록 이름·채널을 추출
- data/pending_names.json, data/pending_channels.json에 누적 저장
"""
import os
import re
import json
from datetime import datetime, timezone, timedelta

KST              = timezone(timedelta(hours=9))
PENDING_NAMES    = "data/pending_names.json"
PENDING_CHANNELS = "data/pending_channels.json"

# 이름으로 볼 수 없는 패턴 (필터링용)
_SKIP_WORDS = {
    "AI", "ETF", "HBM", "CPO", "GPU", "LG", "SK", "KB", "NH", "DB",
    "IT", "TV", "KBS", "SBS", "MBC", "YTN", "CEO", "IPO", "PER",
    "MSCI", "ADR", "NFT", "PDF",
    "삼성", "현대", "롯데", "포스코", "코스피", "코스닥", "나스닥",
    "미국", "중국", "일본", "한국", "서울", "경제", "주식", "투자",
    "증권", "은행", "펀드", "채권", "금리", "환율", "달러", "원화",
    "상승", "하락", "매수", "매도", "급등", "급락", "전망", "분석",
}

# 2~4글자 한국어 이름 패턴
_KR_NAME_PATTERN = re.compile(r"[가-힣]{2,4}")


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


def collect_pending_names(
    youtube_items: list,
    known_panelists: list,
    gemini_mentions: list = None,
) -> None:
    """
    유튜브 영상 제목/description/Gemini 결과에서
    미등록 이름 후보를 추출해 pending_names.json에 누적
    """
    known_set = set(known_panelists)
    existing  = {item["name"]: item for item in _load_json(PENDING_NAMES)}
    today_str = datetime.now(KST).strftime("%Y-%m-%d")

    # Gemini가 발견한 speaker 이름도 후보에 포함
    gemini_speakers = set()
    if gemini_mentions:
        for m in gemini_mentions:
            sp = m.get("speaker", "").strip()
            if sp:
                # "염승환 미래에셋자산운용 이사" → "염승환" 추출
                name_match = _KR_NAME_PATTERN.match(sp)
                if name_match:
                    gemini_speakers.add(name_match.group())

    # 영상 제목/description에서 이름 후보 추출
    for item in youtube_items:
        title = item.get("title", "") or ""
        desc  = item.get("description", "") or ""
        detected = item.get("_detected_names", []) or []

        # _detected_names (Gemini 스캔 결과) 우선 활용
        candidates = set(detected) | gemini_speakers

        # 제목에서 추가 추출 (대괄호 안 이름 패턴)
        bracket_names = re.findall(r"[（(]([가-힣]{2,4})[）)]", title + desc)
        candidates.update(bracket_names)

        for name in candidates:
            if name in known_set:
                continue
            if name in _SKIP_WORDS or len(name) < 2:
                continue
            if not _KR_NAME_PATTERN.fullmatch(name):
                continue

            if name not in existing:
                existing[name] = {
                    "name":        name,
                    "count":       0,
                    "first_seen":  today_str,
                    "last_seen":   today_str,
                    "sample_videos": [],
                    "status":      "pending",  # pending / approved / rejected
                }

            existing[name]["count"]     += 1
            existing[name]["last_seen"]  = today_str
            vid_title = item.get("title", "")[:60]
            if vid_title and vid_title not in existing[name]["sample_videos"]:
                existing[name]["sample_videos"] = (
                    existing[name]["sample_videos"][-4:] + [vid_title]
                )

    pending = [v for v in existing.values() if v["status"] == "pending"]
    all_data = list(existing.values())
    _save_json(PENDING_NAMES, all_data)
    print(f"[후보수집] 패널리스트 후보: {len(pending)}건 (누적)")


def collect_pending_channels(
    youtube_items: list,
    known_channel_ids: set,
    gemini_speakers_by_channel: dict = None,
) -> None:
    """
    수집된 영상의 채널 중 미등록 채널을 후보로 저장
    Gemini 심층분석에서 전문가가 출연한 것으로 확인된 채널 우선
    """
    existing  = {item["channel_id"]: item for item in _load_json(PENDING_CHANNELS)}
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    gemini_speakers_by_channel = gemini_speakers_by_channel or {}

    for item in youtube_items:
        # 유튜브 링크에서 채널 정보 추출
        channel_name = item.get("source_name", "").strip()
        # video link에서 channel_id 직접 얻기 어려우므로
        # _panelist 태그나 gemini_speaker가 있는 영상 채널을 우선 수집
        has_expert = bool(
            item.get("_detected_names") or
            item.get("gemini_speaker") or
            item.get("_panelist_in_title")
        )

        if not channel_name or not has_expert:
            continue

        # 채널명 기반으로 이미 등록된 채널인지 확인
        # (channel_id가 없을 수 있어 채널명으로 대체)
        ch_key = channel_name

        if ch_key in known_channel_ids:
            continue

        expert_names = (
            item.get("_detected_names", []) or
            ([item.get("gemini_speaker")] if item.get("gemini_speaker") else [])
        )

        if ch_key not in existing:
            existing[ch_key] = {
                "channel_name":    channel_name,
                "channel_id":      "",  # 관리자가 확인 후 입력 또는 자동 조회
                "count":           0,
                "first_seen":      today_str,
                "last_seen":       today_str,
                "expert_appeared": [],
                "sample_videos":   [],
                "status":          "pending",
            }

        existing[ch_key]["count"]    += 1
        existing[ch_key]["last_seen"] = today_str

        for name in expert_names:
            if name and name not in existing[ch_key]["expert_appeared"]:
                existing[ch_key]["expert_appeared"].append(name)

        vid_title = item.get("title", "")[:60]
        if vid_title and vid_title not in existing[ch_key]["sample_videos"]:
            existing[ch_key]["sample_videos"] = (
                existing[ch_key]["sample_videos"][-4:] + [vid_title]
            )

    pending = [v for v in existing.values() if v["status"] == "pending"]
    _save_json(PENDING_CHANNELS, list(existing.values()))
    print(f"[후보수집] 채널 후보: {len(pending)}건 (누적)")
