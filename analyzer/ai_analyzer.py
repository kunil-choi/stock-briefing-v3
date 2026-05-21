# analyzer/ai_analyzer.py
"""
AI 분석기 - v3
Claude API를 사용하여 수집된 데이터를 분석하고 HTML을 생성합니다.
섹션 1, 2, 3 분리 분석 + 종합 투자전략 도출
"""
import json
import re
import os
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from .api_client import call_claude_with_retry
from .html_generator import generate_html
from .naver_finance import load_stock_names, get_stock_price

KST = timezone(timedelta(hours=9))
CB = "\u0060\u0060\u0060"

SKIP_NAMES = {
    "삼성", "현대", "LG", "SK", "롯데", "한국", "대한", "국민",
    "신한", "우리", "하나", "기업", "산업", "전자", "화학",
    "건설", "증권", "보험", "카드", "캐피탈", "파이낸스",
    "글로벌", "인터내셔널", "코리아", "홀딩스",
}


def extract_mentions(all_data: list, stock_map: dict) -> dict:
    """수집된 데이터에서 종목 언급 횟수 추출"""
    type_map = {
        "뉴스": "뉴스",
        "경제방송": "경제방송",
        "개인유튜브": "개인유튜브",
        "유튜버": "개인유튜브",
        "유튜브": "개인유튜브",
        "증권사유튜브": "증권사유튜브",
        "증권사": "증권사유튜브",
        "증권TV": "증권TV",
        "애널리스트": "애널리스트",
    }

    mentions = defaultdict(lambda: defaultdict(list))

    for item in all_data:
        text = item.get("title", "") + " " + item.get("summary", "")
        src_type = type_map.get(item.get("source_type", ""), "기타")
        src_name = item.get("source_name", "")
        link = item.get("link", "")

        for name, code in stock_map.items():
            if len(name) < 3 or name in SKIP_NAMES:
                continue
            if name in text:
                mentions[name][src_type].append({
                    "source_name": src_name,
                    "source_type": src_type,
                    "detail": item.get("summary", "")[:300],
                    "source_url": link,
                    "title": item.get("title", ""),
                })

    return dict(mentions)


def analyze_and_generate_html(
    all_data: list,
    api_key: str,
    channels_data: dict = None,
    gh_repo: str = "",
) -> str:
    """
    전체 데이터를 분석하여 HTML 생성
    """
    import anthropic

    if not api_key:
        print("[AI 분석] API 키가 없습니다. 더미 데이터로 HTML 생성...")
        return _generate_fallback_html()

    client = anthropic.Anthropic(api_key=api_key)
    model = "claude-sonnet-4-5"

    now_kst = datetime.now(KST)

    # 종목 목록 로드
    print("  [종목목록] 로드 중...")
    stock_map = load_stock_names()

    # 데이터 섹션 분리
    section1_data = [d for d in all_data if d.get("section") == "section1" or d.get("source_type") in ["경제방송", "개인유튜브", "증권사유튜브", "뉴스"]]
    section2_data = [d for d in all_data if d.get("section") == "section2" or d.get("source_type") == "증권TV"]
    section3_data = [d for d in all_data if d.get("section") == "section3" or d.get("source_type") == "애널리스트"]

    print(f"  섹션1: {len(section1_data)}건, 섹션2: {len(section2_data)}건, 섹션3: {len(section3_data)}건")

    # === 시장 요약 생성 ===
    print("\n[시장 요약 생성 중...]")
    market_summary = _generate_market_summary(client, model, section1_data)

    # === 섹션 1 분석: 유튜브·미디어 채널 언급 종목 ===
    print("\n[섹션 1 분석 중...]")
    section1_stocks = _analyze_section1(client, model, section1_data, stock_map)

    # === 섹션 2 분석: 증권TV 전문가 추천 종목 ===
    print("\n[섹션 2 분석 중...]")
    section2_stocks = _analyze_section2(client, model, section2_data, stock_map)

    # === 섹션 3 분석: 애널리스트 리포트 ===
    print("\n[섹션 3 분석 중...]")
    section3_stocks = _analyze_section3(client, model, section3_data, stock_map)

    # === 종합 투자전략 생성 ===
    print("\n[종합 투자전략 생성 중...]")
    investment_strategy = _generate_investment_strategy(
        client, model, section1_stocks, section2_stocks, section3_stocks, market_summary
    )

    # === 주가 정보 보완 ===
    print("\n[주가 정보 조회 중...]")
    all_stocks = section1_stocks + section2_stocks + section3_stocks
    for stock in all_stocks:
        name = stock.get("name", "")
        code = stock.get("krx_code", "") or stock_map.get(name, "")
        if code:
            price_info = get_stock_price(code)
            if price_info.get("price"):
                stock["verified_price"] = price_info
                stock["krx_code"] = code

    # === HTML 생성 ===
    data = {
        "briefing_date": now_kst.strftime("%Y-%m-%d"),
        "market_summary": market_summary,
        "section1_stocks": section1_stocks,
        "section2_stocks": section2_stocks,
        "section3_stocks": section3_stocks,
        "investment_strategy": investment_strategy,
    }

    return generate_html(data, channels_data=channels_data, gh_repo=gh_repo)


def _generate_market_summary(client, model: str, data: list) -> str:
    """시장 요약 생성"""
    # 뉴스 데이터 우선 활용
    news_items = [d for d in data if d.get("source_type") == "뉴스"]
    other_items = [d for d in data if d.get("source_type") != "뉴스"][:10]

    news_text = "\n".join([
        f"[{d.get('source_name', '')}] {d.get('title', '')}: {d.get('summary', '')[:200]}"
        for d in news_items[:20]
    ])

    other_text = "\n".join([
        f"[{d.get('source_name', '')}] {d.get('title', '')}"
        for d in other_items[:10]
    ])

    prompt = f"""다음은 오늘 수집된 주요 경제 뉴스와 미디어 콘텐츠입니다.

=== 주요 경제신문 뉴스 ===
{news_text}

=== 기타 미디어 ===
{other_text}

위 내용을 바탕으로 오늘 한국 주식시장의 시장 요약을 작성하세요.

요구사항:
1. 3~4개의 핵심 이슈를 각각 한 단락으로 서술
2. 각 단락은 "이슈 제목: 내용" 형식으로 작성
3. 매일경제, 한국경제, 서울경제, 이데일리, 머니투데이 등 신문사 기사를 주요 근거로 활용
4. 시장에 영향을 미치는 거시경제, 국내외 이슈, 주요 종목 동향을 포함
5. 각 단락 끝에 주요 참고 기사 출처를 간략히 표시 (예: [출처: 매일경제])
6. 한국어로 작성, 총 500~700자

JSON이 아닌 순수 텍스트로 작성하세요."""

    try:
        response = call_claude_with_retry(
            client, model, 1000, "당신은 한국 경제 전문 분석가입니다.", [{"role": "user", "content": prompt}]
        )
        return response.strip()
    except Exception as e:
        print(f"  [시장 요약 오류] {e}")
        return "오늘의 시장 요약을 생성하는 중 오류가 발생했습니다."


def _analyze_section1(client, model: str, data: list, stock_map: dict) -> list:
    """섹션 1: 유튜브·미디어 채널 언급 종목 분석"""
    if not data:
        return []

    data_text = "\n".join([
        f"[{d.get('source_type', '')}][{d.get('source_name', '')}] {d.get('title', '')}: {d.get('summary', '')[:300]}"
        for d in data[:40]
    ])

    stock_list = list(stock_map.keys())[:200]
    stock_list_text = ", ".join(stock_list)

    prompt = f"""다음은 유튜브 및 미디어 채널에서 수집된 콘텐츠입니다.

{data_text}

참고 종목 목록 (KOSPI/KOSDAQ 상장 종목):
{stock_list_text}

위 콘텐츠에서 한국 주식 종목이 언급된 경우를 분석하여 JSON 배열을 반환하세요.

각 종목 객체 형식:
{{
  "name": "종목명",
  "krx_code": "종목코드(6자리)",
  "signal": "긍정|부정|중립",
  "description": "기업 개요와 현재 주목받는 이유 (3~5문장)",
  "price_trend": "최근 2주간 주가 흐름 분석 (데이터 기반 서술)",
  "price_display": "가격 표시 문자열 (예: 181,000원 ▲10,700 (+5.58%) | 2주 변동: +12.3%)",
  "catalyst": "상승 촉매 (구체적 서술)",
  "risk": "리스크 (구체적 서술)",
  "reasons": [
    {{
      "source_type": "채널 유형 ([뉴스]/[경제방송]/[개인유튜브]/[증권사])",
      "source_name": "채널명",
      "detail": "핵심 언급 요약",
      "source_url": "원본 링크"
    }}
  ]
}}

중요 규칙:
- 언급 횟수는 표시하지 않음 (signal 배지만 사용)
- 각 채널별 언급 내용은 reasons 배열에 상세히 기록
- 최소 3개, 최대 8개 종목 추출
- 반드시 JSON 배열만 반환 (```json 포함)"""

    try:
        response = call_claude_with_retry(
            client, model, 4000, "당신은 한국 주식시장 분석 전문가입니다.", [{"role": "user", "content": prompt}]
        )
        return _parse_stocks_json(response)
    except Exception as e:
        print(f"  [섹션 1 오류] {e}")
        return []


def _analyze_section2(client, model: str, data: list, stock_map: dict) -> list:
    """섹션 2: 증권TV 전문가 출연 추천 종목 분석"""
    if not data:
        return []

    data_text = "\n".join([
        f"[{d.get('source_name', '')}][전문가: {d.get('expert_name', '미확인')}] {d.get('title', '')}: {d.get('summary', '')[:300]}"
        for d in data[:30]
    ])

    stock_list = list(stock_map.keys())[:200]
    stock_list_text = ", ".join(stock_list)

    prompt = f"""다음은 증권TV 전문가 출연 프로그램에서 수집된 콘텐츠입니다.
(전일 방송 기준)

{data_text}

참고 종목 목록:
{stock_list_text}

위 증권TV 콘텐츠에서 전문가가 추천하거나 분석한 종목을 추출하여 JSON 배열로 반환하세요.

각 종목 객체 형식:
{{
  "name": "종목명",
  "krx_code": "종목코드(6자리)",
  "signal": "긍정|부정|중립",
  "description": "기업 개요와 전문가가 주목한 이유 (3~5문장)",
  "price_trend": "최근 2주간 주가 흐름 분석",
  "price_display": "가격 표시 문자열",
  "catalyst": "전문가가 언급한 상승 촉매",
  "risk": "전문가가 언급한 리스크",
  "reasons": [
    {{
      "source_type": "증권TV",
      "source_name": "채널명 (전문가명/코너명 포함)",
      "detail": "전문가 언급 핵심 내용",
      "source_url": "원본 링크"
    }}
  ]
}}

중요:
- source_type은 반드시 "증권TV"로 표시
- 전문가 이름 또는 코너명이 확인되면 source_name에 포함
- 최소 2개, 최대 6개 종목 추출
- 반드시 JSON 배열만 반환 (```json 포함)"""

    try:
        response = call_claude_with_retry(
            client, model, 3000, "당신은 한국 주식시장 분석 전문가입니다.", [{"role": "user", "content": prompt}]
        )
        stocks = _parse_stocks_json(response)
        # 섹션 구분 표시
        for s in stocks:
            s["section"] = "section2"
        return stocks
    except Exception as e:
        print(f"  [섹션 2 오류] {e}")
        return []


def _analyze_section3(client, model: str, data: list, stock_map: dict) -> list:
    """섹션 3: 애널리스트 리포트 분석"""
    if not data:
        return []

    # 카테고리별 분리
    simultaneous = [d for d in data if d.get("analyst_category") == "simultaneous"]
    new_coverage = [d for d in data if d.get("analyst_category") == "new_coverage"]
    first_mention = [d for d in data if d.get("analyst_category") == "first_in_6months"]

    all_reports_text = "\n".join([
        f"[{d.get('analyst_category', '')}][{d.get('source_name', '')}] {d.get('stock_name', '')} - {d.get('report_title', '')} (담당: {d.get('analyst', '')})"
        for d in data[:30]
    ])

    prompt = f"""다음은 오늘 발행된 증권사 애널리스트 리포트입니다.

{all_reports_text}

카테고리 정보:
- simultaneous: 복수 증권사 동시 언급 (24시간 내)
- new_coverage: 신규 커버리지 개시
- first_in_6months: 단일 증권사 첫 언급

위 리포트를 분석하여 JSON 배열로 반환하세요.

각 종목 객체 형식:
{{
  "name": "종목명",
  "krx_code": "종목코드(6자리)",
  "analyst_category": "simultaneous|new_coverage|first_in_6months",
  "signal": "긍정|부정|중립",
  "description": "기업 개요와 애널리스트가 주목한 이유 (3~5문장)",
  "price_trend": "최근 2주간 주가 흐름 분석",
  "price_display": "가격 표시 문자열",
  "catalyst": "리포트에서 언급된 상승 촉매",
  "risk": "리포트에서 언급된 리스크",
  "broker": "증권사명",
  "analyst_name": "담당 애널리스트명",
  "target_price": "목표주가",
  "opinion": "투자의견 (BUY/HOLD/SELL 등)",
  "reasons": [
    {{
      "source_type": "애널리스트",
      "source_name": "증권사명 (담당 애널리스트)",
      "detail": "리포트 핵심 내용 요약",
      "source_url": ""
    }}
  ]
}}

반드시 JSON 배열만 반환 (```json 포함)"""

    try:
        response = call_claude_with_retry(
            client, model, 3000, "당신은 한국 주식시장 분석 전문가입니다.", [{"role": "user", "content": prompt}]
        )
        stocks = _parse_stocks_json(response)
        for s in stocks:
            s["section"] = "section3"
            # 원본 데이터에서 카테고리 정보 보완
            stock_name = s.get("name", "")
            for d in data:
                if d.get("stock_name", "") == stock_name:
                    if not s.get("analyst_category"):
                        s["analyst_category"] = d.get("analyst_category", "")
                    if not s.get("broker"):
                        s["broker"] = d.get("source_name", "")
                    if not s.get("analyst_name"):
                        s["analyst_name"] = d.get("analyst", "")
                    break
        return stocks
    except Exception as e:
        print(f"  [섹션 3 오류] {e}")
        return []


def _generate_investment_strategy(
    client, model: str,
    section1_stocks: list,
    section2_stocks: list,
    section3_stocks: list,
    market_summary: str
) -> str:
    """종합 투자전략 생성 (섹션 1~3 교차 분석)"""

    s1_names = [s.get("name", "") for s in section1_stocks]
    s2_names = [s.get("name", "") for s in section2_stocks]
    s3_names = [s.get("name", "") for s in section3_stocks]

    # 교차 종목 발견
    cross_s1_s3 = set(s1_names) & set(s3_names)
    cross_s2_s3 = set(s2_names) & set(s3_names)
    cross_all = set(s1_names) & set(s2_names) & set(s3_names)

    cross_info = ""
    if cross_all:
        cross_info += f"3개 섹션 모두 언급: {', '.join(cross_all)}\n"
    if cross_s1_s3:
        cross_info += f"섹션1+섹션3 교차: {', '.join(cross_s1_s3)}\n"
    if cross_s2_s3:
        cross_info += f"섹션2+섹션3 교차: {', '.join(cross_s2_s3)}\n"

    prompt = f"""다음은 오늘의 주식 브리핑 데이터입니다.

시장 요약:
{market_summary[:300]}

섹션 1 (유튜브·미디어 채널 언급 종목): {', '.join(s1_names)}
- 긍정 종목: {', '.join(s.get('name', '') for s in section1_stocks if s.get('signal') == '긍정')}

섹션 2 (증권TV 전문가 추천 종목): {', '.join(s2_names)}
- 전일 방송 기준

섹션 3 (애널리스트 리포트):
- 복수 증권사 동시 언급: {', '.join(s.get('name', '') for s in section3_stocks if s.get('analyst_category') == 'simultaneous')}
- 신규 커버리지: {', '.join(s.get('name', '') for s in section3_stocks if s.get('analyst_category') == 'new_coverage')}

교차 분석:
{cross_info if cross_info else '교차 종목 없음'}

위 데이터를 종합적으로 교차 분석하여 오늘의 투자전략을 서술하세요.

요구사항:
1. 섹션 1, 2, 3을 통합한 교차 분석 근거를 명시
2. 교차 언급 종목은 특히 상세히 서술
3. 구체적인 매매 전략 (분할 매수, 비중 조절 등) 포함
4. 500~700자 분량의 한국어 서술
5. 순수 텍스트로 작성 (JSON 불필요)
6. 마지막에 주요 리스크 요인 2~3가지 포함"""

    try:
        response = call_claude_with_retry(
            client, model, 1500,
            "당신은 한국 주식시장 투자전략 전문가입니다.",
            [{"role": "user", "content": prompt}]
        )
        return response.strip()
    except Exception as e:
        print(f"  [투자전략 오류] {e}")
        return "오늘의 투자전략을 생성하는 중 오류가 발생했습니다."


def _parse_stocks_json(response: str) -> list:
    """Claude 응답에서 JSON 파싱"""
    # ```json ... ``` 블록 추출
    pattern = r"```json\s*([\s\S]*?)\s*```"
    match = re.search(pattern, response)
    if match:
        json_str = match.group(1)
    else:
        # JSON 배열 직접 추출 시도
        array_match = re.search(r"\[[\s\S]*\]", response)
        if array_match:
            json_str = array_match.group(0)
        else:
            print("  [JSON 파싱] JSON 배열을 찾을 수 없음")
            return []

    try:
        stocks = json.loads(json_str)
        if isinstance(stocks, list):
            return stocks
        return []
    except json.JSONDecodeError as e:
        print(f"  [JSON 파싱 오류] {e}")
        return []


def _generate_fallback_html() -> str:
    """API 키 없을 때 기본 HTML 생성"""
    from .html_generator import generate_html
    data = {
        "briefing_date": datetime.now(KST).strftime("%Y-%m-%d"),
        "market_summary": "API 키가 설정되지 않아 시장 요약을 생성할 수 없습니다.\n\n환경변수 설정: ANTHROPIC_API_KEY, YOUTUBE_API_KEY",
        "section1_stocks": [],
        "section2_stocks": [],
        "section3_stocks": [],
        "investment_strategy": "API 키를 설정하면 AI 투자전략이 자동 생성됩니다.",
    }
    return generate_html(data)
