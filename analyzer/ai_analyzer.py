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
- FIX-PROMPT-1: rules 문자열 끝 손상 복구 및 stock_plans 규칙 명시 (BUG-A2)
- FIX-APIKEY-1: call_claude_with_retry에 api_key 전달 누락 버그 수정
- FIX-PRICE-1 : 관심종목 현재가 조회 후 Claude 프롬프트에 포함, 가격 임의 생성 방지
"""

import json
import os
import re
import math
from datetime import datetime, timezone, timedelta
from typing import Optional

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


# ── FIX-FILTER-1: 단계별 관심종목 필터링 ─────────────────────────────────────

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
        if len(selected) >= target:
            break
        if len(data["channel_types"]) >= 2:
            selected.append((name, data))
            selected_names.add(name)
    print(f"[필터링] 1차(채널타입 2종↑): {len(selected)}개")

    # 2차: 채널타입 무관 총 4회 이상
    if len(selected) < target:
        for name, data in all_sorted:
            if len(selected) >= target:
                break
            if name in selected_names:
                continue
            if data["total_count"] >= 4:
                selected.append((name, data))
                selected_names.add(name)
        print(f"[필터링] 2차(전체 4회↑) 추가 후: {len(selected)}개")

    # 3차: 채널타입 무관 총 3회 이상
    if len(selected) < target:
        for name, data in all_sorted:
            if len(selected) >= target:
                break
            if name in selected_names:
                continue
            if data["total_count"] >= 3:
                selected.append((name, data))
                selected_names.add(name)
        print(f"[필터링] 3차(전체 3회↑) 추가 후: {len(selected)}개")

    print(f"[필터링] 최종 {len(selected)}개 선택")
    return selected


# ── 히든픽 후보 추출 ──────────────────────────────────────────────────────────

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


# ── Claude 프롬프트 생성 ──────────────────────────────────────────────────────

def build_analysis_prompt(filtered_mentions: list, hidden_candidates: list,
                          all_data: list, today_date: str,
                          now_kst: str, stock_prices: dict = None) -> str:

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
    headlines      = headlines[:60]
    headlines_text = "\n".join(headlines)

    top_stocks  = filtered_mentions[:15]
    stocks_info = []
    for rank, (name, data) in enumerate(top_stocks, 1):
        price_str = ""
        if stock_prices and name in stock_prices:
            price_str = f", 현재가:{stock_prices[name]:,}원"
        else:
            price_str = ", 현재가:미수집"
        line = (f"{rank}. {name} (코드:{data['code']}, "
                f"언급:{data['total_count']}회, "
                f"가중점수:{data['weighted_score']:.1f}, "
                f"채널유형:{','.join(data['channel_types'])}{price_str})")
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
        '각 단락 3~4문장. 400자 이상)",\n'
        '  "hot_sectors": [\n'
        '    {"name": "섹터이름", "reason": "이유 1~2단어"}\n'
        '  ],\n'
        '  "stocks": [\n'
        '    {\n'
        '      "rank": 1,\n'
        '      "name": "종목명",\n'
        '      "code": "종목코드",\n'
        '      "signal": "긍정|중립|부정 중 택1",\n'
        '      "summary": "종목 핵심 요약 2~3문장",\n'
        '      "catalyst": "상승 촉매 2~3문장",\n'
        '      "risk": "주요 리스크 1~2문장",\n'
        '      "channel_mentions": [\n'
        '        {\n'
        '          "source_type": "뉴스|경제방송|경제방송TV|유튜브|애널리스트 중 택1",\n'
        '          "source_name": "채널명",\n'
        '          "content": "언급 내용 1~2문장",\n'
        '          "url": "URL 없으면 빈 문자열"\n'
        '        }\n'
        '      ],\n'
        '      "channel_counts": {},\n'
        '      "total_count": 0,\n'
        '      "weighted_score": 0.0,\n'
        '      "overlap_count": 0,\n'
        '      "reasons": []\n'
        '    }\n'
        '  ],\n'
        '  "hidden_picks": [\n'
        '    {\n'
        '      "rank": 1,\n'
        '      "name": "종목명",\n'
        '      "code": "종목코드",\n'
        '      "signal": "긍정",\n'
        '      "summary": "종목 핵심 요약 2~3문장",\n'
        '      "catalyst": "상승 촉매 2~3문장",\n'
        '      "risk": "주요 리스크 1문장",\n'
        '      "channel_type": "애널리스트|경제방송TV|경제방송 중 택1",\n'
        '      "channel_name": "채널명",\n'
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
        '    "core_scenario": "핵심 시나리오 1문장",\n'
        '    "allocation": [\n'
        '      {"sector": "섹터명", "weight_pct": 30, "note": "편입 근거 1문장"}\n'
        '    ],\n'
        '    "stock_plans": [\n'
        '      {\n'
        '        "name": "종목명",\n'
        '        "trigger": "매수 트리거 조건",\n'
        '        "initial_weight_pct": 10,\n'
        '        "target_price": "1차 목표: +8~10% / 2차 목표: 조건부 추가 보유",\n'
        '        "stop_loss": "52주 고점 대비 -12~15%"\n'
        '      }\n'
        '    ],\n'
        '    "cash_policy": {\n'
        '      "current_pct": 15,\n'
        '      "deploy_trigger": "현금 투입 조건",\n'
        '      "raise_trigger": "현금 비중 확대 조건"\n'
        '    },\n'
        '    "risk_scenarios": [\n'
        '      {\n'
        '        "scenario": "리스크 시나리오명",\n'
        '        "probability": "높음|보통|낮음",\n'
        '        "impact": "포트폴리오 영향",\n'
        '        "response": "대응 방안"\n'
        '      }\n'
        '    ],\n'
        '    "theme_correlation": "테마 간 상관관계 및 섹터 로테이션 방향"\n'
        '  }\n'
        '}'
    )

    rules = (
        "[작성 규칙]\n"
        "1. stocks: 유의미한 관심종목만 선별, 최대 10개\n"
        "2. signal: 긍정|중립|부정 중 택1 (긍정=매수 우호적, 중립=관망, 부정=매도 우호적)\n"
        "3. summary / catalyst / risk: 빈 문자열 없이 내용 기입\n"
        "4. channel_mentions: 실제 언급된 채널/기사 내용 최대 4개\n"
        "   ※ channel_mentions와 reasons에 동일 출처를 중복 기재하지 말 것\n"
        "   ※ reasons는 빈 배열 []로 두고 channel_mentions만 채울 것\n"
        "5. hidden_picks: 반드시 [히든픽 후보] 목록에서만 선택, 없으면 []\n"
        "6. market_summary: 5단락, \\n\\n 구분, 각 단락 3~4문장, 400자 이상\n"
        "7. ai_strategy: 반드시 위 JSON 구조 그대로 출력 (문자열 아닌 객체)\n"
        "   - allocation 비중 합 + cash_policy.current_pct = 100%\n"
        "   - risk_scenarios: 최소 2개\n"
        "   - stock_plans: 관심종목 + 중소형 수혜주 1~2개 포함 권장\n"
        "8. 모든 URL은 출처 데이터에 있는 것만 사용, 없으면 빈 문자열\n"
        "9. JSON 외 설명문·마크다운 코드블록 없이 순수 JSON만 출력\n"
        "10. stock_plans의 target_price·stop_loss는 반드시 현재가 기준 비율(%)로 표현할 것\n"
        "    현재가가 제공된 종목은 실제 가격도 병기 가능\n"
        "    현재가가 '미수집'인 종목은 구체적인 가격 숫자를 절대 임의로 만들지 말 것\n"
    )

    return (
        f"오늘 날짜: {today_date} ({now_kst} KST)\n\n"
        f"[최근 주요 헤드라인]\n{headlines_text}\n\n"
        f"[관심종목 후보 (가중점수 순)]\n{stocks_text}\n\n"
        f"[히든픽 후보]\n{hidden_text}\n\n"
        f"위 데이터를 바탕으로 아래 JSON 형식으로 오늘의 AI 주식 브리핑을 작성하세요.\n\n"
        f"{rules}\n\n"
        f"[출력 JSON 구조]\n{CB}json\n{prompt_json_structure}\n{CB}"
    )


# ── JSON 파싱 헬퍼 ────────────────────────────────────────────────────────────

def _try_parse_json(text: str) -> Optional[dict]:
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ── ai_strategy dict → HTML 문자열 변환 ──────────────────────────────────────

def _format_ai_strategy(strategy: dict) -> str:
    if not isinstance(strategy, dict):
        return str(strategy)

    lines = []

    core = strategy.get("core_scenario", "")
    if core:
        lines.append(f"■ 핵심 시나리오\n{core}")

    allocation = strategy.get("allocation", [])
    if allocation:
        alloc_lines = ["■ 포트폴리오 배분"]
        for a in allocation:
            sector = a.get("sector", "")
            pct    = a.get("weight_pct", "")
            note   = a.get("note", "")
            alloc_lines.append(f"• {sector} {pct}% — {note}")
        lines.append("\n".join(alloc_lines))

    stock_plans = strategy.get("stock_plans", [])
    if stock_plans:
        plan_lines = ["■ 종목별 매매 계획"]
        for p in stock_plans:
            name    = p.get("name", "")
            trigger = p.get("trigger", "")
            weight  = p.get("initial_weight_pct", "")
            target  = p.get("target_price", "")
            stop    = p.get("stop_loss", "")
            plan_lines.append(
                f"• {name} [{weight}%] 진입: {trigger} / 목표: {target} / 손절: {stop}"
            )
        lines.append("\n".join(plan_lines))

    cash = strategy.get("cash_policy", {})
    if cash:
        c_pct    = cash.get("current_pct", "")
        c_deploy = cash.get("deploy_trigger", "")
        c_raise  = cash.get("raise_trigger", "")
        lines.append(
            f"■ 현금 정책\n"
            f"• 현재 현금 비중: {c_pct}%\n"
            f"• 투입 조건: {c_deploy}\n"
            f"• 확대 조건: {c_raise}"
        )

    risk_scenarios = strategy.get("risk_scenarios", [])
    if risk_scenarios:
        risk_lines = ["■ 리스크 시나리오"]
        for r in risk_scenarios:
            scenario = r.get("scenario", "")
            prob     = r.get("probability", "")
            impact   = r.get("impact", "")
            response = r.get("response", "")
            risk_lines.append(
                f"• [{prob}] {scenario} → 영향: {impact} / 대응: {response}"
            )
        lines.append("\n".join(risk_lines))

    theme = strategy.get("theme_correlation", "")
    if theme:
        lines.append(f"■ 테마 상관관계\n{theme}")

    return "\n\n".join(lines)


# ── URL 복원 헬퍼 ─────────────────────────────────────────────────────────────

def _restore_source_url(item: dict, all_data: list) -> None:
    for field in ("channel_mentions", "reasons"):
        entries = item.get(field, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            url_key = "url" if field == "channel_mentions" else "source_url"
            if entry.get(url_key):
                continue
            src_name = entry.get("source_name", "")
            if not src_name:
                continue
            for d in all_data:
                if d.get("source_name") == src_name:
                    link = d.get("link") or d.get("url", "")
                    if link:
                        entry[url_key] = link
                        break


# ── fallback HTML ─────────────────────────────────────────────────────────────

def _fallback_html(error_msg: str, briefing_date: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 주식 브리핑 — {briefing_date}</title>
<style>
body {{ background:#0d1117; color:#e6edf3;
       font-family:'Malgun Gothic',sans-serif;
       display:flex; align-items:center; justify-content:center;
       min-height:100vh; margin:0; }}
.box {{ background:#161b22; border:1px solid #30363d;
        border-radius:12px; padding:2rem; max-width:500px; text-align:center; }}
h2 {{ color:#ff6b6b; margin-bottom:1rem; }}
p  {{ color:#8b949e; font-size:.9rem; }}
</style>
</head>
<body>
<div class="box">
  <h2>⚠️ 브리핑 생성 실패</h2>
  <p>{briefing_date}</p>
  <p style="margin-top:1rem;">{error_msg}</p>
</div>
</body>
</html>"""


# ── 메인 워크플로우 ───────────────────────────────────────────────────────────

def analyze_and_generate_html(
    all_data: list,
    channels_data: dict = None,
    gh_repo: str = "",
    gh_token: str = "",
    market_overview: dict = None,
) -> str:
    from .html_generator import generate_html
    from .naver_finance import fetch_naver_stock_price
    from config import ANTHROPIC_API_KEY

    now_kst       = datetime.now(KST)
    today_date    = now_kst.strftime("%Y년 %m월 %d일")
    now_kst_str   = now_kst.strftime("%H:%M")
    briefing_date = today_date

    # ── 1. 종목명 로드 ────────────────────────────────────────────────────────
    stock_map = load_stock_names()

    # ── 2. 언급 추출 ──────────────────────────────────────────────────────────
    mentions = extract_mentions(all_data, stock_map, channels_data)

    if not mentions:
        print("[분석] 언급 종목 없음 → fallback")
        return _fallback_html("수집된 종목 언급이 없습니다.", briefing_date)

    # ── 3. 관심종목 필터링 ────────────────────────────────────────────────────
    filtered = filter_mentions(mentions)
    filtered_names = {name for name, _ in filtered}

    # ── 3-1. 관심종목 현재가 조회 (FIX-PRICE-1) ──────────────────────────────
    stock_prices = {}
    for name, data in filtered:
        code = data.get("code", "")
        if code:
            price_info = fetch_naver_stock_price(name, code_override=code)
            if price_info and price_info.get("price"):
                stock_prices[name] = price_info["price"]
    print(f"[주가조회] {len(stock_prices)}/{len(filtered)}개 종목 주가 수집 완료")

    # ── 4. 히든픽 후보 ────────────────────────────────────────────────────────
    hidden_candidates = extract_hidden_picks(mentions, filtered_names)

    # ── 5. Claude 프롬프트 생성 및 API 호출 ──────────────────────────────────
    prompt = build_analysis_prompt(
        filtered, hidden_candidates, all_data, today_date, now_kst_str,
        stock_prices=stock_prices
    )

    print(f"[Claude] 프롬프트 길이: {len(prompt)}자")

    try:
        response_text = call_claude_with_retry(prompt, api_key=ANTHROPIC_API_KEY)
    except Exception as e:
        print(f"[Claude] API 호출 실패: {e}")
        return _fallback_html(f"Claude API 오류: {e}", briefing_date)

    # ── 6. JSON 파싱 ──────────────────────────────────────────────────────────
    result = _try_parse_json(response_text)
    if not result:
        print("[Claude] JSON 파싱 실패 → fallback")
        return _fallback_html("AI 응답 파싱 실패. 잠시 후 다시 시도하세요.", briefing_date)

    # ── 7. ai_strategy dict → 문자열 변환 (FIX-STRAT-2) ─────────────────────
    ai_strat = result.get("ai_strategy")
    if isinstance(ai_strat, dict):
        result["ai_strategy"] = _format_ai_strategy(ai_strat)

    # ── 8. channel_counts / overlap_count 재계산 (V2-SYNC) ───────────────────
    mention_lookup = dict(filtered)
    for stock in result.get("stocks", []):
        name = stock.get("name", "")
        if name in mention_lookup:
            data = mention_lookup[name]
            cc   = {}
            for ch_type, items in data["channels"].items():
                cc[ch_type] = len(items)
            stock["channel_counts"]  = cc
            stock["total_count"]     = data["total_count"]
            stock["weighted_score"]  = round(data["weighted_score"], 2)
            stock["overlap_count"]   = len(data["channel_types"])
            stock["reasons"]         = []

    # ── 9. 히든픽 weighted_score 동기화 ──────────────────────────────────────
    hidden_lookup = {p["name"]: p for p in hidden_candidates}
    for hp in result.get("hidden_picks", []):
        name = hp.get("name", "")
        if name in hidden_lookup:
            hp["weighted_score"] = hidden_lookup[name]["weighted_score"]
            hp["channel_type"]   = hidden_lookup[name]["channel_type"]

    # ── 10. URL 복원 ──────────────────────────────────────────────────────────
    for stock in result.get("stocks", []):
        _restore_source_url(stock, all_data)
    for hp in result.get("hidden_picks", []):
        _restore_source_url(hp, all_data)

    # ── 11. 결과 저장 ─────────────────────────────────────────────────────────
    os.makedirs("data", exist_ok=True)
    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[저장] {OUTPUT_FILE} 저장 완료")
    except Exception as e:
        print(f"[저장] 실패: {e}")

    # ── 12. HTML 생성 ─────────────────────────────────────────────────────────
    return generate_html(
        result, channels_data, gh_repo, gh_token, market_overview, all_data
    )
