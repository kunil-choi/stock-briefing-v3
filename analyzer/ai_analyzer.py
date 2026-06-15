# analyzer/ai_analyzer.py
"""
AI 주식 브리핑 분석 엔진

수정 이력:
- BUG-CR-1   : 상대 임포트로 전환
- BUG-HIDDEN : 히든픽 로직 정비
- BUG-CACHE  : stock_map 캐시 키 우선순위 정비
- BUG-KEY-1  : ai_strategy 키 통일
- V2-PROMPT  : Claude 프롬프트를 V2 수준으로 개선 (summary / catalyst / risk / channel_mentions 추가)
- V2-SYNC    : channel_mentions → reasons 동기화 블록 추가
- FIX-ANA-2  : generate_market_summary 데드코드 제거
- FIX-API-1  : Claude API 호출 실패 시 fallback HTML 반환 (try/except 추가)
- FIX-STRAT  : ai_strategy 구조화 JSON 객체 전환
- FIX-MAX-1  : 관심종목 최대 10개로 확대 (꼭 10개를 채울 필요 없이 유의미한 종목만 선택)
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


# ── 유효성 검사 헬퍼 ────────────────────────────────────────────────────────────

def _is_valid_stock_name(name: str) -> bool:
    if len(name) < _MIN_NAME_LEN:
        return False
    if name in _SKIP_NAMES:
        return False
    if re.match(r'^[A-Z]{2,3}$', name):
        return False
    return True


# ── 채널 가중치 계산 ────────────────────────────────────────────────────────────

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


# ── 종목 이름 로드 ──────────────────────────────────────────────────────────────

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


# ── 언급 추출 ───────────────────────────────────────────────────────────────────

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


def filter_mentions(mentions: dict, min_channel_types: int = 2) -> list:
    filtered = [
        (name, data) for name, data in mentions.items()
        if len(data["channel_types"]) >= min_channel_types
    ]
    filtered.sort(key=lambda x: x[1]["weighted_score"], reverse=True)
    print(f"[필터링] {len(filtered)}개 종목 선택 (채널 유형 ≥{min_channel_types})")
    return filtered


# ── 히든픽 후보 추출 ────────────────────────────────────────────────────────────

def extract_hidden_picks(mentions: dict, filtered_names: set,
                         max_picks: int = 3) -> list:
    candidates = []
    for name, data in mentions.items():
        if name in filtered_names:
            continue
        ch_types = set(data["channel_types"])
        if len(ch_types) != 1:
            continue
        sole_type = list(ch_types)[0]
        if sole_type not in _HIGH_QUALITY_TYPES:
            continue
        candidates.append({
            "name":           name,
            "code":           data["code"],
            "channel_type":   sole_type,
            "channels":       data["channels"],
            "weighted_score": round(data["weighted_score"], 2),
            "total_count":    data["total_count"],
        })
    candidates.sort(key=lambda x: x["weighted_score"], reverse=True)
    result = candidates[:max_picks]
    print(f"[히든픽] 후보 {len(candidates)}개 중 {len(result)}개 선택")
    return result


# ── Claude 프롬프트 생성 (V2 + ai_strategy 구조화) ─────────────────────────────

def build_analysis_prompt(filtered_mentions: list, hidden_candidates: list,
                          all_data: list, today_date: str,
                          now_kst: str) -> str:

    headlines = []
    for item in all_data[:150]:
        title   = (item.get("title") or "").strip()
        src     = item.get("source_name", "")
        stype   = item.get("source_type", "")
        stock   = item.get("stock_name", "")
        url     = item.get("link") or item.get("url", "")
        summary = (item.get("summary") or item.get("content") or "")[:120]
        if title:
            line = f"[{stype}/{src}] {title}"
            if stock:
                line += f" (종목: {stock})"
            if summary:
                line += f" → {summary}"
            if url:
                line += f" [URL: {url}]"
            headlines.append(line)
    headlines       = headlines[:60]
    headlines_text  = "\n".join(headlines)

    top_stocks  = filtered_mentions[:15]
    stocks_info = []
    for rank, (name, data) in enumerate(top_stocks, 1):
        line = (f"{rank}. {name} (코드:{data['code']}, "
                f"언급:{data['total_count']}회, "
                f"가중점수:{data['weighted_score']:.1f}, "
                f"채널유형:{','.join(data['channel_types'])})")
        stocks_info.append(line)
        for ch_type, items in data["channels"].items():
            for item in items[:5]:
                w_str   = f"[가중치:{item.get('weight', 1.0):.1f}]"
                link    = item.get("link", "")
                url_str = f" [URL: {link}]" if link else ""
                stocks_info.append(
                    f"   [{ch_type}]{w_str} {item['source_name']}: "
                    f"{item['snippet'][:200]}{url_str}"
                )

    hidden_info = []
    for i, pick in enumerate(hidden_candidates, 1):
        name    = pick["name"]
        ch_type = pick["channel_type"]
        line = (f"{i}. {name} (코드:{pick['code']}, "
                f"채널:{ch_type}, 가중점수:{pick['weighted_score']:.1f})")
        hidden_info.append(line)
        for item in pick["channels"].get(ch_type, [])[:3]:
            link    = item.get("link", "")
            url_str = f" [URL: {link}]" if link else ""
            hidden_info.append(
                f"   [{ch_type}] {item['source_name']}: "
                f"{item['snippet'][:200]}{url_str}"
            )

    stocks_text = "\n".join(stocks_info)
    hidden_text = "\n".join(hidden_info) if hidden_info else "해당 없음"

    prompt_json_structure = (
        '{\n'
        f'  "briefing_date": "{today_date}",\n'
        '  "market_summary": "시장 전체 분석 (5개 단락, \\n\\n 구분, '
        '각 단락 3~4문장. 시장요약/글로벌/국내 단락 3~4문장, 400자 이상'
        '(시장요약/글로벌요약/국내이슈/종목이슈/전망/코멘트 포함))",\n'
        '  "hot_sectors": [\n'
        '    {"name": "섹터이름", "reason": "이유 단어 1~2단어"}\n'
        '  ],\n'
        '  "stocks": [\n'
        '    {\n'
        '      "rank": 1,\n'
        '      "name": "종목명",\n'
        '      "code": "종목코드",\n'
        '      "signal": "강력매수|매수|관망|매도|중립 중 택1",\n'
        '      "summary": "종목 핵심 요약 2~3문장 (차트특성, 증권사 코멘트, 시장 내 위상)",\n'
        '      "catalyst": "상승 촉매 2~3문장 (핵심이벤트/실적발표/수급 분석 1~2문장)",\n'
        '      "risk": "주요 리스크 1~2문장",\n'
        '      "channel_mentions": [\n'
        '        {\n'
        '          "source_type": "뉴스|경제방송|경제방송TV|유튜브|애널리스트 중 택1",\n'
        '          "source_name": "채널명 실제 이름 기입",\n'
        '          "content": "이 채널/기사에서 종목에 대해 언급한 내용 1~2문장",\n'
        '          "url": "제공된 URL (없으면 빈 문자열)"\n'
        '        }\n'
        '      ],\n'
        '      "channel_counts": {},\n'
        '      "total_count": 0,\n'
        '      "weighted_score": 0.0,\n'
        '      "overlap_count": 0,\n'
        '      "reasons": [\n'
        '        {\n'
        '          "source_type": "채널유형",\n'
        '          "source_name": "출처명",\n'
        '          "detail": "언급 내용 요약 1문장",\n'
        '          "source_url": "URL 없으면 빈 문자열"\n'
        '        }\n'
        '      ]\n'
        '    }\n'
        '  ],\n'
        '  "hidden_picks": [\n'
        '    {\n'
        '      "rank": 1,\n'
        '      "name": "종목명",\n'
        '      "code": "종목코드",\n'
        '      "signal": "positive",\n'
        '      "summary": "종목 핵심 요약 2~3문장",\n'
        '      "catalyst": "상승 촉매 이유 2~3문장 (핵심 이벤트/수급 분석 1~2문장)",\n'
        '      "risk": "주요 리스크 1문장",\n'
        '      "channel_type": "애널리스트|경제방송TV|경제방송 중 택1",\n'
        '      "channel_name": "채널명 실제 이름 기입",\n'
        '      "reasons": [\n'
        '        {\n'
        '          "source_type": "채널유형",\n'
        '          "source_name": "출처명",\n'
        '          "detail": "언급 내용 요약",\n'
        '          "source_url": "URL 없으면 빈 문자열"\n'
        '        }\n'
        '      ]\n'
        '    }\n'
        '  ],\n'
        '  "ai_strategy": {\n'
        '    "core_scenario": "핵심 시나리오 1문장 (오늘 시장을 움직이는 핵심 변수 + 방향성)",\n'
        '    "allocation": [\n'
        '      {"sector": "섹터명", "weight_pct": 30, "note": "편입 근거 1문장"}\n'
        '    ],\n'
        '    "stock_plans": [\n'
        '      {\n'
        '        "name": "종목명",\n'
        '        "trigger": "매수 트리거 조건 (예: FOMC 비둘기파 확인 + 코스피 2일 연속 양봉)",\n'
        '        "initial_weight_pct": 10,\n'
        '        "target_price": "1차 목표: +8~10% / 2차 목표: 실적 가이던스 상향 시 추가 보유",\n'
        '        "stop_loss": "52주 고점 대비 -12~15% 또는 구체적 가격"\n'
        '      }\n'
        '    ],\n'
        '    "cash_policy": {\n'
        '      "current_pct": 15,\n'
        '      "deploy_trigger": "현금 투입 조건 (예: FOMC 비둘기파 확정 후 당일 종가 매수)",\n'
        '      "raise_trigger": "현금 비중 확대 조건 (예: 코스피 -3% 이상 급락 시 20%→30%)"\n'
        '    },\n'
        '    "risk_scenarios": [\n'
        '      {\n'
        '        "scenario": "리스크 시나리오명",\n'
        '        "probability": "높음|보통|낮음",\n'
        '        "impact": "포트폴리오 영향",\n'
        '        "response": "대응 방안"\n'
        '      }\n'
        '    ],\n'
        '    "theme_correlation": "테마 간 상관관계 및 섹터 로테이션 방향 (역상관 시나리오 포함)"\n'
        '  }\n'
        '}'
    )

    rules = (
        "[작성 규칙]\n"
        # ★ FIX-MAX-1: 5개 → 최대 10개, 꼭 채울 필요 없음
        "1. stocks: 유의미한 관심종목만 선별하되 최대 10개 이내로 출력 (무리해서 10개를 채울 필요 없음)\n"
        "2. signal: 언급된 방향성 기반 판단 — 강력매수|매수|관망|매도|중립 중 택1\n"
        "3. summary / catalyst / risk: 출처별 설명, 빈 문자열 없이 내용 기입\n"
        "4. channel_mentions: 실제 언급된 채널/기사에서 종목에 대해 언급한 내용 최대 4개\n"
        "5. hidden_picks: 반드시 [히든픽 후보] 목록에서만 선택, 없으면 빈 배열 []\n"
        "6. hidden_picks 후보에 없는 종목은 [] 반환\n"
        "7. market_summary: 5단락, \\n\\n 구분, 각 단락 3~4문장, 400자 이상\n"
        "8. ai_strategy: 반드시 위 JSON 구조(core_scenario/allocation/stock_plans/cash_policy/risk_scenarios/theme_correlation) 그대로 출력\n"
        "   - stock_plans: stocks 배열에 종목 + 비주류/중소형 수혜주 1~2개 추가\n"
        "   - stock_plans.trigger: '이벤트 조건 + 차트 조건' 형식 명확히\n"
        "   - stock_plans.target_price: '1차 +N% / 2차 조건부 추가 보유' 형식\n"
        "   - stock_plans.stop_loss: '52주 고점 대비 -12~15%' 혹은 절대 가격 명시\n"
        "   - allocation 비중 합계 = stock_plans 비중 합 + cash_policy.current_pct = 100%\n"
        "   - risk_scenarios: 최소 2개, 테마 역상관 시나리오 1개 포함\n"
        "   - theme_correlation: 동시 강세 불가 시나리오와 섹터 로테이션 방향 명시\n"
        "9. channel_counts / total_count / weighted_score / overlap_count: 빈 값은 0으로 출력\n"
        "10. reasons의 텍스트 항목은 반드시 \"detail\" 키 사용 (\"reason\" 키 허용)\n"
        "11. URL이 제공된 경우 반드시 source_url에 기입, 없으면 빈 문자열\n"
        "12. 지수 전체 종목 제외, 종목명/ETF/펀드 제외"
    )

    return (
        f"다음은 오늘({today_date}) 한국 주요 경제 채널 및 뉴스에서 수집한 정보입니다.\n"
        f"아래 데이터를 종합 분석해 투자 브리핑 JSON을 출력해 주세요.\n\n"
        f"[오늘 날짜] {today_date} ({now_kst} KST)\n\n"
        f"[수집된 뉴스 목록 - 채널별 수집 내용]\n{headlines_text}\n\n"
        f"[관심종목 후보 - 2개 이상 채널유형 언급, 가중점수 높은 순]\n{stocks_text}\n\n"
        f"[히든픽 후보 - 고품질 채널(애널리스트/경제방송TV/경제방송)에서만 언급된 종목]\n{hidden_text}\n\n"
        f"[출력 형식] 반드시 아래 JSON 형식에 맞춰 출력:\n"
        f"{CB}json\n{prompt_json_structure}\n{CB}\n\n"
        f"{rules}"
    )


# ── source_url 복원 ─────────────────────────────────────────────────────────────

def _restore_source_url(reason: dict, real_channel_data: list) -> dict:
    if reason.get("source_url"):
        return reason
    src_name = reason.get("source_name", "")
    for item in real_channel_data:
        if item.get("source_name") == src_name:
            url = item.get("link") or item.get("url", "")
            if url:
                reason["source_url"] = url
                return reason
    return reason


# ── JSON 파싱 ───────────────────────────────────────────────────────────────────

def _try_parse_json(text: str):
    if not text:
        return None
    match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    if match:
        candidate = match.group(1)
    else:
        start = text.find('{')
        end   = text.rfind('}')
        if start == -1 or end == -1:
            return None
        candidate = text[start:end + 1]

    candidate = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', candidate)
    candidate = re.sub(r',\s*([}\]])', r'\1', candidate)

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        lines   = [ln for ln in candidate.split('\n') if ln.strip()]
        cleaned = '\n'.join(lines)
        try:
            return json.loads(cleaned)
        except Exception:
            return None


# ── fallback HTML ───────────────────────────────────────────────────────────────

def _fallback_html(channels_data, gh_repo, market_overview,
                   all_data, briefing_date, message):
    from .html_generator import generate_html
    return generate_html(
        {
            "briefing_date":  briefing_date,
            "market_summary": message,
            "hot_sectors":    [],
            "stocks":         [],
            "hidden_picks":   [],
            "ai_strategy":    "",
        },
        channels_data, gh_repo, "", market_overview, all_data,
    )


# ── 메인 분석 워크플로우 ────────────────────────────────────────────────────────

def analyze_and_generate_html(
    all_data: list,
    api_key: str,
    channels_data: dict = None,
    gh_repo: str = "",
    market_overview: dict = None,
) -> str:
    print("=" * 60)
    print("[AI분석] 시작")
    now_kst     = datetime.now(KST)
    today_date  = now_kst.strftime("%Y-%m-%d")
    now_str     = now_kst.strftime("%H:%M")

    os.makedirs("data", exist_ok=True)

    # 1. 종목명 로드
    stock_map = load_stock_names()
    if not stock_map:
        print("[AI분석] 종목명 로드 실패")
        return _fallback_html(
            channels_data, gh_repo, market_overview,
            all_data, today_date, "종목명 데이터를 불러오지 못했습니다.",
        )

    # 2. 언급 추출
    mentions = extract_mentions(all_data, stock_map, channels_data)

    # 3. 관심종목 필터링
    filtered       = filter_mentions(mentions)
    filtered_names = {name for name, _ in filtered}

    # 4. 히든픽 후보 추출
    hidden_candidates = extract_hidden_picks(mentions, filtered_names)

    if not filtered and not hidden_candidates:
        print("[AI분석] 관심종목/히든픽 없음")
        return _fallback_html(
            channels_data, gh_repo, market_overview,
            all_data, today_date, "오늘 분석할 종목이 없습니다.",
        )

    # 5. Claude 프롬프트 생성
    prompt = build_analysis_prompt(
        filtered, hidden_candidates, all_data, today_date, now_str
    )
    print(f"[AI분석] Claude 호출 "
          f"(관심종목 {len(filtered)}개, 히든픽 후보 {len(hidden_candidates)}개)")

    # 6. Claude API 호출 (FIX-API-1)
    try:
        response = call_claude_with_retry(prompt, api_key, max_tokens=16000)
    except Exception as e:
        print(f"[AI분석] Claude API 호출 실패: {e}")
        return _fallback_html(
            channels_data, gh_repo, market_overview,
            all_data, today_date, "AI 분석 중 오류가 발생했습니다.",
        )

    # 7. JSON 파싱
    result = _try_parse_json(response)
    if not result:
        print("[AI분석] JSON 파싱 실패 → fallback HTML 반환")
        return _fallback_html(
            channels_data, gh_repo, market_overview,
            all_data, today_date, "AI 분석 결과를 파싱하지 못했습니다.",
        )

    print(f"[AI분석] 파싱 성공: "
          f"관심종목 {len(result.get('stocks', []))}개, "
          f"히든픽 {len(result.get('hidden_picks', []))}개")

    # 8. 실측 데이터로 channel_counts / total_count / weighted_score 보정
    mention_dict = dict(filtered)
    for stock in result.get("stocks", []):
        name = stock.get("name", "")
        if name in mention_dict:
            d = mention_dict[name]
            stock["channel_counts"]  = {k: len(v) for k, v in d["channels"].items()}
            stock["total_count"]     = d["total_count"]
            stock["weighted_score"]  = round(d["weighted_score"], 2)
            stock["overlap_count"]   = len(d["channel_types"])

    # 9. 히든픽 검증 및 보정
    hidden_dict = {p["name"]: p for p in hidden_candidates}
    for hp in result.get("hidden_picks", []):
        name = hp.get("name", "")
        if name in filtered_names:
            print(f"  [히든픽검증] {name} → 관심종목과 중복, 제거")
            hp["_remove"] = True
            continue
        if name in hidden_dict:
            d = hidden_dict[name]
            hp["channel_type"]   = d["channel_type"]
            hp["total_count"]    = d["total_count"]
            hp["weighted_score"] = d["weighted_score"]

    result["hidden_picks"] = [
        hp for hp in result.get("hidden_picks", [])
        if not hp.get("_remove")
    ]

    # 10. source_url 복원
    for stock in result.get("stocks", []):
        for reason in stock.get("reasons", []):
            _restore_source_url(reason, all_data)
    for pick in result.get("hidden_picks", []):
        for reason in pick.get("reasons", []):
            _restore_source_url(reason, all_data)

    # 11. channel_mentions → reasons 동기화 (V2-SYNC)
    for stock in result.get("stocks", []):
        cm_list = stock.get("channel_mentions", [])
        if cm_list and not stock.get("reasons"):
            stock["reasons"] = [
                {
                    "source_type": cm.get("source_type", ""),
                    "source_name": cm.get("source_name", ""),
                    "detail":      cm.get("content", ""),
                    "source_url":  cm.get("url", ""),
                }
                for cm in cm_list
            ]

    # 12. 결과 저장
    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[AI분석] {OUTPUT_FILE} 저장 완료")

    # 13. HTML 생성
    from .html_generator import generate_html
    return generate_html(
        result, channels_data, gh_repo, "", market_overview, all_data,
    )
