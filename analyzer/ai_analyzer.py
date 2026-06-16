# analyzer/ai_analyzer.py
"""
AI 주식 브리핑 분석 엔진

수정 이력:
- BUG-CR-1    : 상대 임포트로 전환
- BUG-HIDDEN  : 히든픽 로직 정비
- BUG-CACHE   : stock_map 캐시 키 우선순위 정비
- BUG-KEY-1   : ai_strategy 키 통일
- V2-PROMPT   : Claude 프롬프트를 V2 수준으로 개선
- V2-SYNC     : channel_mentions → reasons 동기화 블록 추가
- FIX-ANA-2   : generate_market_summary 데드코드 제거
- FIX-API-1   : Claude API 호출 실패 시 fallback HTML 반환
- FIX-STRAT   : ai_strategy 구조화 JSON 객체 전환
- FIX-MAX-1   : 관심종목 최대 10개로 확대
- FIX-STRAT-2 : ai_strategy dict → HTML 렌더링 전 문자열 변환 버그 수정
- FIX-FILTER-1: 관심종목 단계별 선정 로직 (1차: 채널타입 2종↑, 2차: 전체 4회↑, 3차: 전체 3회↑)
- FIX-SIG-4   : 프롬프트 signal 지시를 긍정/중립/부정으로 통일 (BUG-A1)
- FIX-PROMPT-1: rules 문자열 끝 손상 복구 (BUG-A2)
- FIX-PRE-1   : _is_premarket() 주말(토·일) 처리 추가 (BUG-A3)
"""

import json
import os
import re
import math
from datetime import datetime, timezone, timedelta

from .api_client import call_claude_with_retry

KST               = timezone(timedelta(hours=9))
STOCK_CACHE_FILE  = "data/stock_names_cache.json"
OUTPUT_FILE       = "data/briefing_data.json"
CB                = "```"

_SKIP_NAMES = {
    "삼성", "현대", "LG", "SK", "롯데", "한화", "포스코", "GS", "CJ",
    "KT", "LS", "DB", "OCI", "KG", "SG", "TG", "NH", "KB",
    "AI", "IT", "EV", "US", "EU", "UN", "M", "A", "S", "K",
    "전자", "화학", "건설", "증권", "은행", "보험", "자동차", "철강",
    "에너지", "바이오", "게임", "반도체", "배터리", "인터넷", "소프트웨어",
    "기업", "그룹", "홀딩스", "코리아", "코퍼레이션",
    "금리", "환율", "달러", "원화", "코스피", "코스닥", "나스닥",
    "매수", "매도", "상승", "하락", "급등", "급락",
    "시장", "투자", "주식", "펀드", "ETF", "채권", "선물", "옵션",
    "경제", "금융", "부동산", "인플레이션", "디플레이션",
    "중국", "미국", "유럽", "일본", "한국",
}
_MIN_NAME_LEN        = 2
_HIGH_QUALITY_TYPES  = {"애널리스트", "경제방송TV", "경제방송"}


# ── 유효성 검사 헬퍼 ──────────────────────────────────────────────────────────

def _is_valid_stock_name(name: str) -> bool:
    if len(name) < _MIN_NAME_LEN:
        return False
    if name in _SKIP_NAMES:
        return False
    if re.match(r'^[A-Z]{2,3}$', name):
        return False
    return True


# ── 채널 가중치 계산 ──────────────────────────────────────────────────────────

def _channel_weight(subscribers: int) -> float:
    if not subscribers or subscribers <= 0:
        return 0.5
    base = math.log10(max(subscribers, 10000)) - math.log10(100000)
    return min(1.0 + max(0.0, base), 3.0)


def _build_channel_weight_map(channels_data: dict) -> dict:
    weight_map = {}
    if not channels_data:
        return weight_map
    for section in ["broadcast", "youtuber", "securities"]:
        for ch in channels_data.get(section, []):
            name = ch.get("name", "")
            subs = ch.get("subscribers", 0)
            if name:
                weight_map[name] = _channel_weight(subs)
    return weight_map


# ── 종목 이름 로드 ────────────────────────────────────────────────────────────

def load_stock_names() -> dict:
    today_kst = datetime.now(KST).strftime("%Y-%m-%d")

    if os.path.exists(STOCK_CACHE_FILE):
        try:
            with open(STOCK_CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            cached_map = cache.get("stock_map") or cache.get("stocks", {})
            if cache.get("date") == today_kst and cached_map:
                print(f"[종목캐시] {len(cached_map)}개 로드 (캐시)")
                return cached_map
        except Exception:
            pass

    stock_map = {}
    try:
        import requests
        for market_id in ["STK", "KSQ"]:
            url = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
            payload = {
                "bld":        "dbms/MDC/STAT/standard/MDCSTAT01901",
                "mktId":       market_id,
                "share":       "1",
                "csvxls_isNo": "false",
            }
            headers = {"Referer": "http://data.krx.co.kr/"}
            resp = requests.post(url, data=payload, headers=headers, timeout=10)
            data = resp.json()
            for item in data.get("OutBlock_1", []):
                name = item.get("ISU_ABBRV", "").strip()
                code = item.get("ISU_SRT_CD", "").strip()
                if name and code:
                    stock_map[name] = code
        if stock_map:
            os.makedirs("data", exist_ok=True)
            with open(STOCK_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {"date": today_kst, "stock_map": stock_map, "stocks": stock_map},
                    f, ensure_ascii=False,
                )
            print(f"[종목로드] KRX에서 {len(stock_map)}개 로드")
            return stock_map
    except Exception as e:
        print(f"[종목로드] KRX 요청 실패: {e}, fallback 사용")

    stock_map = {
        "삼성전자": "005930", "SK하이닉스": "000660", "LG에너지솔루션": "373220",
        "삼성바이오로직스": "207940", "현대차": "005380", "NAVER": "035420",
        "카카오": "035720", "셀트리온": "068270", "삼성SDI": "006400",
        "LG전자": "051910", "KB금융": "105560", "신한지주": "055550",
        "하나금융지주": "086790", "현대모비스": "012330", "LG화학": "066570",
        "삼성물산": "028260", "SK텔레콤": "017670", "롯데케미칼": "011170",
        "CJ제일제당": "097950", "한화솔루션": "009830", "삼성생명": "032830",
        "SK이노베이션": "096770", "KT": "030200", "한화에어로스페이스": "012450",
        "HMM": "011200", "현대글로비스": "015760", "삼성증권": "032830",
        "SK바이오팜": "017670", "삼성전기": "009150",
        "LG디스플레이": "034220", "현대제철": "004020",
        "HD현대": "329180", "두산에너빌리티": "034020",
    }
    print(f"[종목로드] fallback {len(stock_map)}개 사용")
    return stock_map


# ── 언급 추출 ─────────────────────────────────────────────────────────────────

def extract_mentions(all_data: list, stock_map: dict,
                     channels_data: dict = None) -> dict:
    weight_map = _build_channel_weight_map(channels_data) if channels_data else {}

    type_map = {
        "뉴스":        "뉴스",
        "경제방송":    "경제방송",
        "경제방송TV":  "경제방송TV",
        "유튜브":      "유튜브",
        "증권사":      "유튜브",
        "애널리스트":  "애널리스트",
    }
    default_weights = {
        "뉴스": 1.5, "경제방송": 1.8, "경제방송TV": 1.8,
        "애널리스트": 2.5, "유튜브": 1.0,
    }

    mentions = {}

    for item in all_data:
        raw_type  = item.get("source_type", "유튜브")
        ch_type   = type_map.get(raw_type, "유튜브")
        src_name  = item.get("source_name", "")
        title     = item.get("title", "")
        summary   = item.get("summary", "") or item.get("content", "")
        link      = item.get("link", "") or item.get("url", "")
        text      = f"{title} {summary}"
        weight    = weight_map.get(src_name, default_weights.get(ch_type, 1.0))

        for name, code in stock_map.items():
            if not _is_valid_stock_name(name):
                continue
            if name not in text:
                continue

            content_id = f"{src_name}_{link}_{name}"

            if name not in mentions:
                mentions[name] = {
                    "code":           code,
                    "total_count":    0,
                    "weighted_score": 0.0,
                    "channel_types":  set(),
                    "channels":       {},
                }

            entry = mentions[name]
            existing_ids = [
                m["content_id"]
                for ch_items in entry["channels"].values()
                for m in ch_items
            ]
            if content_id in existing_ids:
                continue

            entry["channel_types"].add(ch_type)
            if ch_type not in entry["channels"]:
                entry["channels"][ch_type] = []

            idx     = text.find(name)
            snippet = text[max(0, idx - 50): idx + 150].strip()

            entry["channels"][ch_type].append({
                "source_name":  src_name,
                "snippet":      snippet,
                "link":         link,
                "content_id":   content_id,
                "weight":       round(weight, 2),
            })
            entry["total_count"]    += 1
            entry["weighted_score"] += weight

    for name in mentions:
        mentions[name]["channel_types"] = list(mentions[name]["channel_types"])

    print(f"[언급추출] {len(mentions)}개 종목 발견")
    return mentions


# ── FIX-FILTER-1: 단계별 관심종목 필터링 ────────────────────────────────────
# 1차: 서로 다른 채널타입 2종 이상 교차언급
# 2차: 채널타입 무관 총 4회 이상 언급 (1차 미달 시 추가)
# 3차: 채널타입 무관 총 3회 이상 언급 (2차 후도 10개 미달 시 추가)
# 각 단계에서 이미 선정된 종목은 제외, 합산 10개 초과 시 중단

def filter_mentions(mentions: dict, target: int = 10) -> list:
    all_sorted = sorted(
        mentions.items(),
        key=lambda x: x[1]["weighted_score"],
        reverse=True,
    )

    selected       = []
    selected_names = set()

    # 1차: 서로 다른 채널타입 2종 이상
    for name, data in all_sorted:
        if
