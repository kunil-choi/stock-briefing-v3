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
            vid_url   = item.get("link", "")
            sample_titles = [
                (v.get("title") if isinstance(v, dict) else v)
                for v in existing[name]["sample_videos"]
            ]
            if vid_title and vid_title not in sample_titles:
                existing[name]["sample_videos"] = (
                    existing[name]["sample_videos"][-4:]
                    + [{"title": vid_title, "url": vid_url}]
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

    ★ channel_id는 관리자가 수동으로 찾아 입력할 필요 없이 자동으로 채운다.
    youtube_collector.py가 이미 각 영상 항목에 channel_id(YouTube search/
    playlist 응답의 snippet.channelId)를 담아 보내주므로, 여기서는 그 값을
    그대로 받아쓰기만 하면 된다(과거엔 이 필드를 읽지 않고 항상 빈 문자열로
    저장해 관리자가 매번 채널 페이지에서 직접 복사해야 했다).
    """
    # 예전에는 channel_id로 키를 만들면서 아래 루프는 channel_name으로 조회해
    # 항상 어긋나(channel_id가 비어있는 경우가 대부분) 같은 채널이 실행마다
    # 중복 추가되는 버그가 있었다 — 이제 일관되게 channel_name으로 키를 잡는다.
    existing  = {item["channel_name"]: item for item in _load_json(PENDING_CHANNELS)}
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    gemini_speakers_by_channel = gemini_speakers_by_channel or {}

    for item in youtube_items:
        # 유튜브 링크에서 채널 정보 추출
        channel_name = item.get("source_name", "").strip()
        channel_id   = item.get("channel_id", "").strip()
        # _panelist 태그나 gemini_speaker가 있는 영상 채널을 우선 수집
        has_expert = bool(
            item.get("_detected_names") or
            item.get("gemini_speaker") or
            item.get("_panelist_in_title")
        )

        if not channel_name or not has_expert:
            continue

        ch_key = channel_name

        if ch_key in known_channel_ids or channel_id in known_channel_ids:
            continue

        expert_names = (
            item.get("_detected_names", []) or
            ([item.get("gemini_speaker")] if item.get("gemini_speaker") else [])
        )

        if ch_key not in existing:
            existing[ch_key] = {
                "channel_name":    channel_name,
                "channel_id":      channel_id,  # 자동으로 채워짐
                "count":           0,
                "first_seen":      today_str,
                "last_seen":       today_str,
                "expert_appeared": [],
                "sample_videos":   [],
                "status":          "pending",
            }
        elif channel_id and not existing[ch_key].get("channel_id"):
            # 예전에(이 수정 이전에) channel_id 없이 만들어진 후보를 이번에
            # 알게 된 값으로 백필한다.
            existing[ch_key]["channel_id"] = channel_id

        existing[ch_key]["count"]    += 1
        existing[ch_key]["last_seen"] = today_str

        for name in expert_names:
            if name and name not in existing[ch_key]["expert_appeared"]:
                existing[ch_key]["expert_appeared"].append(name)

        vid_title = item.get("title", "")[:60]
        vid_url   = item.get("link", "")
        sample_titles = [
            (v.get("title") if isinstance(v, dict) else v)
            for v in existing[ch_key]["sample_videos"]
        ]
        if vid_title and vid_title not in sample_titles:
            existing[ch_key]["sample_videos"] = (
                existing[ch_key]["sample_videos"][-4:]
                + [{"title": vid_title, "url": vid_url}]
            )

    pending = [v for v in existing.values() if v["status"] == "pending"]
    _save_json(PENDING_CHANNELS, list(existing.values()))
    print(f"[후보수집] 채널 후보: {len(pending)}건 (누적)")
