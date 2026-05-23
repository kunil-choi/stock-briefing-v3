# analyzer/ai_analyzer.py
import json
import os
import re
from datetime import datetime, timedelta, timezone

from .api_client     import call_claude_with_retry
from .validation     import validate_stocks
from .html_generator import generate_html

KST = timezone(timedelta(hours=9))
CB  = "```"


# ── 종목 목록 로드 (V2 그대로) ────────────────────────────────────────────────

def load_stock_names() -> dict:
    import requests
    cache_path = "data/stock_names_cache.json"
    today      = datetime.now(KST).strftime("%Y-%m-%d")

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("date") == today and len(cache.get("stocks", {})) > 0:
                print(f"  [종목목록] 캐시 사용 ({len(cache['stocks'])}개)")
                return cache["stocks"]
        except Exception:
            pass

    print("  [종목목록] KRX 종목 목록 로드 중...")
    stock_map = {}
    headers   = {"User-Agent": "Mozilla/5.0", "Referer": "http://data.krx.co.kr/"}

    for market_id, market_name in [("STK", "코스피"), ("KSQ", "코스닥")]:
        try:
            url    = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
            params = {
                "bld":          "dbms/MDC/STAT/standard/MDCSTAT01901",
                "mktId":        market_id,
                "share":        "1",
                "money":        "1",
                "csvxls_isNo":  "false",
            }
            res   = requests.post(url, data=params, headers=headers, timeout=15)
            items = res.json().get("OutBlock_1", [])
            for item in items:
                name = item.get("ISU_ABBRV", "").strip()
                code = item.get("ISU_SRT_CD", "").strip()
                if name and code:
                    stock_map[name] = code
            print(f"  [{market_name}] {len(items)}개 로드")
        except Exception as e:
            print(f"  [{market_name}] KRX 오류: {e}")

    if not stock_map:
        print("  [종목목록] KRX 실패 → 폴백 사용")
        stock_map = {
            "삼성전자": "005930", "SK하이닉스": "000660", "LG에너지솔루션": "373220",
            "삼성바이오로직스": "207940", "현대차": "005380", "기아": "000270",
            "셀트리온": "068270", "POSCO홀딩스": "005490", "KB금융": "105560",
            "신한지주": "055550", "하나금융지주": "086790", "우리금융지주": "316140",
            "LG화학": "051910", "삼성SDI": "006400", "현대모비스": "012330",
            "카카오": "035720", "NAVER": "035420", "LG전자": "066570",
            "삼성물산": "028260", "SK텔레콤": "017670", "KT": "030200",
            "한화에어로스페이스": "012450", "HD현대중공업": "329180",
            "HD한국조선해양": "009540", "에코프로": "086520", "에코프로비엠": "247540",
            "알테오젠": "196170", "삼성전기": "009150", "HLB": "028300",
            "유한양행": "000100", "한미약품": "128940", "크래프톤": "259960",
            "엔씨소프트": "036570", "카카오게임즈": "293490", "HMM": "011200",
            "대한항공": "003490", "한화오션": "042660", "HD현대일렉트릭": "267260",
            "효성중공업": "298040", "LS일렉트릭": "010120", "두산로보틱스": "454910",
            "레인보우로보틱스": "277810", "리가켐바이오": "141080",
        }

    os.makedirs("data", exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"date": today, "stocks": stock_map}, f, ensure_ascii=False)
    print(f"  [종목목록] 총 {len(stock_map)}개 로드 완료")
    return stock_map


# ── 종목 언급 추출 ────────────────────────────────────────────────────────────

def extract_mentions(all_data: list, stock_map: dict) -> dict:
    type_map = {
        "뉴스": "뉴스", "경제방송": "경제방송",
        "유튜버": "유튜브", "유튜브": "유튜브",
        "방송": "경제방송", "증권": "경제방송",
        "애널리스트": "애널리스트",
    }
    skip_names = {
        "삼성", "현대", "LG", "SK", "롯데", "한국", "대한", "국민",
        "신한", "우리", "하나", "기업", "산업", "전자", "화학",
        "건설", "증권", "보험", "카드", "캐피탈", "파이낸스",
        "글로벌", "인터내셔널", "코리아", "홀딩스",
    }
    mentions = {}
    for item in all_data:
        raw_type    = item.get("source_type", "기타")
        source_type = type_map.get(raw_type, raw_type)
        source_name = item.get("source_name", "")
        link        = item.get("link", "") or item.get("url", "")
        content_id  = link if link else (source_name + "|" + item.get("title", ""))
        full_text   = " ".join([
            item.get("title",   ""),
            item.get("summary", ""),
            item.get("content", ""),
        ])
        for stock_name, code in stock_map.items():
            if len(stock_name) < 2 or stock_name in skip_names:
                continue
            if stock_name not in full_text:
                continue
            if stock_name not in mentions:
                mentions[stock_name] = {
                    "code": code, "뉴스": [], "경제방송": [],
                    "유튜브": [], "애널리스트": [], "total": 0,
                }
            ch_key  = source_type if source_type in ["뉴스", "경제방송", "유튜브", "애널리스트"] else "뉴스"
            already = any(m.get("content_id") == content_id for m in mentions[stock_name][ch_key])
            if already:
                continue
            idx     = full_text.find(stock_name)
            context = full_text[max(0, idx - 50): idx + 150].strip()
            mentions[stock_name][ch_key].append({
                "source_name": source_name,
                "text":        context,
                "link":        link,
                "content_id":  content_id,
            })
            mentions[stock_name]["total"] += 1
    return mentions


def filter_mentions(mentions: dict, min_channel_types: int = 2) -> dict:
    filtered = {}
    for name, data in mentions.items():
        channel_types = sum(
            1 for t in ["뉴스", "경제방송", "유튜브", "애널리스트"] if len(data[t]) > 0
        )
        if channel_types >= min_channel_types:
            filtered[name] = data
    filtered = dict(sorted(filtered.items(), key=lambda x: x[1]["total"], reverse=True))
    print(f"  [필터] {len(filtered)}개 종목 선별 (2개 이상 채널)")
    return filtered


# ── 5단락 시장 요약 생성 ──────────────────────────────────────────────────────

def _fmt_pct(val) -> str:
    if val is None:
        return "N/A"
    try:
        return f"{float(val):+.2f}%"
    except Exception:
        return "N/A"


def generate_market_summary(market_overview: dict, all_data: list,
                             api_key: str, today_date: str) -> str:
    nf = market_overview.get("night_futures", {})
    us = market_overview.get("us_market",    {})
    kr = market_overview.get("korea_market", {})

    nf_price = nf.get("price", "N/A")
    nf_pct   = _fmt_pct(nf.get("change_pct"))
    # BUG-7 수정: direction 을 한국어 설명으로 변환
    nf_dir_raw = nf.get("direction", "neutral")
    nf_dir = "상승(콜)" if nf_dir_raw == "call" else ("하락(풋)" if nf_dir_raw == "put" else "보합/중립")

    sp_price = us.get("sp500",  {}).get("price", "N/A")
    sp_pct   = _fmt_pct(us.get("sp500",  {}).get("change_pct"))
    nq_price = us.get("nasdaq", {}).get("price", "N/A")
    nq_pct   = _fmt_pct(us.get("nasdaq", {}).get("change_pct"))
    dw_price = us.get("dow",    {}).get("price", "N/A")
    dw_pct   = _fmt_pct(us.get("dow",    {}).get("change_pct"))
    fx_price = us.get("usd_krw",{}).get("price", "N/A")
    fx_pct   = _fmt_pct(us.get("usd_krw",{}).get("change_pct"))

    kp_price = kr.get("kospi",  {}).get("price", "N/A")
    kp_pct   = _fmt_pct(kr.get("kospi",  {}).get("change_pct"))
    kd_price = kr.get("kosdaq", {}).get("price", "N/A")
    kd_pct   = _fmt_pct(kr.get("kosdaq", {}).get("change_pct"))

    headlines = "\n".join(
        f"- {d.get('title','')}"
        for d in all_data if d.get("source_type") == "뉴스"
    )[:2000]

    prompt = f"""당신은 한국 주식시장 전문 애널리스트입니다.
아래 시장 데이터와 뉴스 헤드라인을 바탕으로 오늘 아침 브리핑용 시장 요약 5단락을 작성하세요.

[야간선물] 코스피200 야간선물: {nf_price} ({nf_pct}) → 방향: {nf_dir}
[미국증시] S&P500: {sp_price}({sp_pct}), 나스닥: {nq_price}({nq_pct}), 다우: {dw_price}({dw_pct}), 달러/원: {fx_price}({fx_pct})
[한국증시] 코스피: {kp_price}({kp_pct}), 코스닥: {kd_price}({kd_pct})
[뉴스헤드라인]
{headlines}

작성 규칙:
1. 아래 5개 소제목을 순서대로 그대로 사용하고, 각 단락은 "소제목: 내용" 형식으로 작성하세요.
2. 각 단락은 200~300자로 작성하세요.
3. 위 수치를 직접 인용하세요.
4. 단락 사이는 빈 줄 하나로 구분하세요.
5. 마크다운 기호(**, ##, ```)를 사용하지 마세요.
6. 순수 텍스트 5단락만 출력하세요.

소제목(순서 고정):
1) 야간선물 시장 동향
2) 미국 증시 마감 요약
3) 전일 국내 증시 흐름
4) 오늘 국내 증시 예상 흐름
5) 주요 섹터 포커스"""

    result = call_claude_with_retry(api_key, prompt, max_tokens=2000)
    if result and len(result.strip()) > 100:
        result = re.sub(r'\*{1,3}', '', result)
        result = re.sub(r'^#{1,4}\s*', '', result, flags=re.MULTILINE)
        result = result.replace('```', '')
        return result.strip()

    # 폴백 (BUG-7 수정: nf_dir 중복 없이 자연스럽게)
    return (
        f"야간선물 시장 동향: 코스피200 야간선물은 {nf_price}포인트({nf_pct})로 마감하여 "
        f"{nf_dir} 신호를 나타내고 있습니다. 외국인 및 기관 포지션 동향과 함께 "
        f"장 시작 전 주목이 필요합니다.\n\n"
        f"미국 증시 마감 요약: S&P500 {sp_price}({sp_pct}), "
        f"나스닥 {nq_price}({nq_pct}), 다우 {dw_price}({dw_pct})로 마감했습니다. "
        f"달러/원 환율은 {fx_price}원({fx_pct})으로 마감했습니다.\n\n"
        f"전일 국내 증시 흐름: 코스피 {kp_price}({kp_pct}), "
        f"코스닥 {kd_price}({kd_pct})로 마감했습니다. "
        f"전반적인 수급 흐름은 미국 증시 영향을 받았습니다.\n\n"
        f"오늘 국내 증시 예상 흐름: 야간선물 방향({nf_dir})과 미국 증시 흐름을 "
        f"고려할 때 장 초반 {nf_dir} 흐름으로 출발이 예상됩니다. "
        f"환율 변동 및 외국인 수급에 주목하세요.\n\n"
        f"주요 섹터 포커스: 반도체, 방산, 바이오 섹터에 주목이 필요합니다. "
        f"글로벌 AI 수요 및 지정학적 이슈가 시장 흐름을 좌우할 수 있습니다."
    )


# ── Claude 종목 분석 프롬프트 ─────────────────────────────────────────────────

def build_analysis_prompt(filtered_mentions: dict, all_data: list,
                           today_date: str, now_kst: str) -> str:
    stock_contexts = ""
    for rank, (name, data) in enumerate(filtered_mentions.items(), 1):
        if rank > 15:
            break
        stock_contexts += f"\n\n### [{rank}] {name} (총 {data['total']}회 언급)\n"
        for ch_type in ["뉴스", "경제방송", "유튜브", "애널리스트"]:
            items = data[ch_type]
            if not items:
                continue
            stock_contexts += f"\n**{ch_type} ({len(items)}회):**\n"
            for item in items[:5]:
                link_str = item['link'] if item['link'] else "링크없음"
                stock_contexts += (
                    f"- [{item['source_name']}] (링크: {link_str})\n"
                    f"  {item['text']}\n"
                )

    news_headlines = "\n".join(
        f"- {d.get('title','')}"
        for d in all_data if d.get("source_type") == "뉴스"
    )

    prompt = (
        f"당신은 한국 주식시장 전문 애널리스트입니다.\n"
        f"기준 시각: {now_kst}\n\n"
        f"아래는 오늘 4개 채널(뉴스/경제방송/유튜브/애널리스트리포트)에서 "
        f"2개 이상 채널에 공통 언급된 종목들과 관련 발언 원문입니다.\n\n"
        f"**분석 지침:**\n"
        f"1. 각 발언에서 해당 종목에 대한 평가를 긍정/중립/부정으로 판단하세요.\n"
        f"2. signal은 긍정/부정/중립 발언 횟수를 합산해 다수결로 결정하세요.\n"
        f"3. channel_counts는 각 채널별 실제 콘텐츠 수를 그대로 기재하세요.\n"
        f"4. total_count는 모든 채널 콘텐츠 수의 합계입니다.\n"
        f"5. overlap_count는 언급된 채널 종류의 수입니다.\n"
        f"6. reasons의 source_url은 반드시 위 발언 데이터의 '링크' 값을 그대로 사용하세요. "
        f"링크가 '링크없음'이면 빈 문자열(\"\")로 기재하세요.\n"
        f"7. reasons detail은 채널별 실제 발언 내용을 구체적으로 요약하세요.\n"
        f"8. hidden_picks는 한 채널에서만 언급됐지만 투자 가치가 높은 긍정적 종목 최대 3개.\n"
        f"9. description 200자, price_trend/catalyst/risk 각 150자, reasons detail 각 100자.\n"
        f"10. market_summary는 빈 문자열로 두세요 (별도 생성됩니다).\n"
        f"11. investment_strategy는 구체적 투자 전략 400자.\n"
        f"12. 모호한 표현('특정 종목', '이 종목') 사용 금지.\n\n"
        f"## 오늘 뉴스 헤드라인:\n{news_headlines}\n\n"
        f"## 종목별 채널 발언 원문:\n{stock_contexts}\n\n"
        f"JSON만 출력하세요:\n\n"
        + CB + "json\n"
        "{\n"
        f'  "briefing_date": "{today_date}",\n'
        '  "market_summary": "",\n'
        '  "hot_sectors": ["섹터1", "섹터2", "섹터3"],\n'
        '  "stocks": [\n'
        "    {\n"
        '      "rank": 1, "name": "종목명", "signal": "긍정",\n'
        '      "description": "기업 소개 200자",\n'
        '      "price_trend": "주가흐름 150자",\n'
        '      "catalyst": "상승촉매 150자",\n'
        '      "risk": "리스크 150자",\n'
        '      "total_count": 5,\n'
        '      "channel_counts": {"뉴스":1,"경제방송":1,"유튜브":3,"애널리스트":0},\n'
        '      "source_types": ["뉴스","경제방송","유튜브"],\n'
        '      "overlap_count": 3,\n'
        '      "reasons": [\n'
        '        {"source_type":"뉴스","source_name":"출처명","source_url":"https://...","detail":"발언 내용 100자"}\n'
        "      ]\n"
        "    }\n"
        "  ],\n"
        '  "hidden_picks": [\n'
        "    {\n"
        '      "rank": 1, "name": "종목명", "signal": "긍정",\n'
        '      "description": "기업 소개 300자",\n'
        '      "catalyst": "주목 이유 150자",\n'
        '      "risk": "리스크 150자",\n'
        '      "reasons": [\n'
        '        {"source_type":"유튜브","source_name":"채널명","source_url":"https://...","detail":"발언 내용 100자"}\n'
        "      ]\n"
        "    }\n"
        "  ],\n"
        '  "investment_strategy": "구체적 투자 전략 400자"\n'
        "}\n"
        + CB
    )
    return prompt


# ── URL 복원 ──────────────────────────────────────────────────────────────────

def _restore_source_url(reason, real_channel_data):
    ch    = reason.get("source_type", "")
    sname = reason.get("source_name", "")
    if reason.get("source_url"):
        return
    ch_key = {"뉴스": "뉴스", "경제방송": "경제방송",
               "유튜브": "유튜브", "애널리스트": "애널리스트"}.get(ch, "")
    if not ch_key or ch_key not in real_channel_data:
        return
    candidates = real_channel_data[ch_key]
    for m in candidates:
        if m.get("source_name") == sname and m.get("link"):
            reason["source_url"] = m["link"]
            return
    for m in candidates:
        rs = m.get("source_name", "")
        if (sname in rs or rs in sname) and m.get("link"):
            reason["source_url"] = m["link"]
            return
    for m in candidates:
        if m.get("link"):
            reason["source_url"] = m["link"]
            return


# ── JSON 파싱 강화 (BUG-10 수정) ─────────────────────────────────────────────

def _try_parse_json(text: str):
    """
    Claude 응답에서 JSON 추출을 단계적으로 시도.
    1단계: 원본 그대로 파싱
    2단계: 개행 제거 후 파싱
    3단계: 마크다운 코드블록 제거 후 파싱
    4단계: 제어문자 제거 후 파싱
    """
    # 1단계: JSON 블록 추출 후 원본 파싱
    match = re.search(r'```json\s*([\s\S]*?)```', text)
    if match:
        candidate = match.group(1).strip()
    else:
        # JSON 오브젝트 최대 범위 추출
        start = text.find('{')
        end   = text.rfind('}')
        if start == -1 or end == -1:
            return None
        candidate = text[start:end + 1]

    for attempt, cleaner in enumerate([
        lambda s: s,                                              # 원본
        lambda s: s.replace('\n', ' ').replace('\r', ''),        # 개행 제거
        lambda s: re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s),  # 제어문자 제거
        lambda s: re.sub(r',\s*([}\]])', r'\1', s),              # 후행 콤마 제거
    ], 1):
        try:
            cleaned = cleaner(candidate)
            result  = json.loads(cleaned)
            if attempt > 1:
                print(f"  [JSON] {attempt}단계 복구 성공")
            return result
        except json.JSONDecodeError:
            continue

    return None


# ── 메인 진입점 ───────────────────────────────────────────────────────────────

def analyze_and_generate_html(all_data, api_key, channels_data=None,
                               gh_repo="", market_overview=None):
    print("\n" + "=" * 60)
    print("[AI 분석] 시작")
    print("=" * 60)

    now_kst    = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    today_date = datetime.now(KST).strftime("%Y-%m-%d")
    gh_token   = os.environ.get("GH_TOKEN", "")

    # 1단계: 종목 추출
    print("\n[1단계] 종목명 추출...")
    stock_map = load_stock_names()

    if not stock_map:
        data = {
            "briefing_date": today_date,
            "market_summary": "종목 목록 로드 실패.",
            "hot_sectors": [], "stocks": [], "hidden_picks": [],
            "investment_strategy": "데이터 수집 완료, 종목 목록 로드 실패.",
        }
        if market_overview:
            data["market_summary"] = generate_market_summary(
                market_overview, all_data, api_key, today_date
            )
        return generate_html(data, channels_data, gh_repo, gh_token, market_overview=market_overview)

    mentions = extract_mentions(all_data, stock_map)
    print(f"  [추출] 언급 종목 총 {len(mentions)}개")
    filtered = filter_mentions(mentions, min_channel_types=2)

    if not filtered:
        data = {
            "briefing_date": today_date,
            "market_summary": "오늘 수집된 데이터에서 공통 언급 종목 없음.",
            "hot_sectors": [], "stocks": [], "hidden_picks": [],
            "investment_strategy": "분석할 종목이 없습니다.",
        }
        if market_overview:
            data["market_summary"] = generate_market_summary(
                market_overview, all_data, api_key, today_date
            )
        return generate_html(data, channels_data, gh_repo, gh_token, market_overview=market_overview)

    # 2단계: 5단락 시장 요약
    print("\n[2단계] 5단락 시장 요약 생성 중...")
    market_summary_text = ""
    if market_overview:
        market_summary_text = generate_market_summary(
            market_overview, all_data, api_key, today_date
        )
        print(f"  [시장요약] {len(market_summary_text)}자 생성")
    else:
        market_summary_text = "시장 데이터를 수집하지 못했습니다."

    # 3단계: 종목 심층 분석
    print(f"\n[3단계] Claude 종목 분석 ({len(filtered)}개 종목)...")
    prompt      = build_analysis_prompt(filtered, all_data, today_date, now_kst)
    result_text = call_claude_with_retry(api_key, prompt, max_tokens=16000)

    data = None
    if result_text:
        data = _try_parse_json(result_text)  # BUG-10 수정: 강화된 파싱
        if data:
            print(f"[3단계] 파싱 성공: 종목 {len(data.get('stocks', []))}개")
        else:
            print("[3단계] JSON 파싱 최종 실패")

    if not data:
        data = {
            "briefing_date": today_date, "market_summary": "",
            "hot_sectors": [], "stocks": [], "hidden_picks": [],
            "investment_strategy": "AI 분석에 실패했습니다.",
        }

    # 5단락 시장 요약 주입
    data["market_summary"] = market_summary_text

    # channel_counts / source_url 복원
    for stock in data.get("stocks", []):
        name = stock.get("name", "")
        if name in filtered:
            real = filtered[name]
            stock["channel_counts"] = {
                "뉴스":      len(real["뉴스"]),
                "경제방송":  len(real["경제방송"]),
                "유튜브":    len(real["유튜브"]),
                "애널리스트":len(real["애널리스트"]),
            }
            stock["total_count"] = real["total"]
            for reason in stock.get("reasons", []):
                _restore_source_url(reason, real)

    for stock in data.get("hidden_picks", []):
        hp_name = stock.get("name", "")
        for reason in stock.get("reasons", []):
            if reason.get("source_url"):
                continue
            ch_key = {
                "뉴스": "뉴스", "경제방송": "경제방송",
                "유튜브": "유튜브", "애널리스트": "애널리스트",
            }.get(reason.get("source_type", ""), "")
            if not ch_key:
                continue
            if hp_name in filtered:
                _restore_source_url(reason, filtered[hp_name])
                if reason.get("source_url"):
                    continue
            sname = reason.get("source_name", "")
            for stock_data in filtered.values():
                if ch_key not in stock_data:
                    continue
                for m in stock_data[ch_key]:
                    rs = m.get("source_name", "")
                    if (sname == rs or sname in rs or rs in sname) and m.get("link"):
                        reason["source_url"] = m["link"]
                        break
                if reason.get("source_url"):
                    break

    # 검증
    if data.get("stocks") or data.get("hidden_picks"):
        data = validate_stocks(data, api_key, all_data, stock_map)
    else:
        print("[검증] 종목 없음 → 스킵")

    # 저장
    os.makedirs("data", exist_ok=True)
    save_data = json.loads(json.dumps(data, ensure_ascii=False))
    for s in save_data.get("stocks", []):
        s.pop("chart_base64", None)
    for s in save_data.get("hidden_picks", []):
        s.pop("chart_base64", None)
    with open("data/briefing_data.json", "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    print("[저장] data/briefing_data.json 완료")

    return generate_html(data, channels_data, gh_repo, gh_token, market_overview=market_overview)
