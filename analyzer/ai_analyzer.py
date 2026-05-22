# analyzer/ai_analyzer.py
"""
AI 분석기 - v3
Claude API를 사용하여 수집된 데이터를 분석하고 HTML을 생성합니다.
- 섹션 1 (유튜브/미디어), 섹션 2 (증권TV), 섹션 3 (애널리스트) 분리 분석
- 종합 투자전략 교차 도출
- get_stock_full_info() 로 실제 2주 주가 데이터 주입
"""
import json
import re
import os
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from .api_client import call_claude_with_retry
from .html_generator import generate_html
from .naver_finance import load_stock_names, get_stock_full_info

KST = timezone(timedelta(hours=9))

MODEL = "claude-sonnet-4-6"

SKIP_NAMES = {
    "삼성", "현대", "LG", "SK", "롯데", "한국", "대한", "국민",
    "신한", "우리", "하나", "기업", "산업", "전자", "화학",
    "건설", "증권", "보험", "카드", "캐피탈", "파이낸스",
    "글로벌", "인터내셔널", "코리아", "홀딩스",
}


# ══════════════════════════════════════════════════════════════
#  공개 함수
# ══════════════════════════════════════════════════════════════

def extract_mentions(all_data: list, stock_map: dict) -> dict:
    """수집된 데이터에서 종목 언급 횟수 추출 (집계용)"""
    type_map = {
        "뉴스": "뉴스", "경제방송": "경제방송",
        "개인유튜브": "개인유튜브", "유튜버": "개인유튜브", "유튜브": "개인유튜브",
        "증권사유튜브": "증권사유튜브", "증권사": "증권사유튜브",
        "증권TV": "증권TV", "애널리스트": "애널리스트",
    }
    mentions = defaultdict(lambda: defaultdict(list))

    for item in all_data:
        text     = item.get("title", "") + " " + item.get("summary", "")
        src_type = type_map.get(item.get("source_type", ""), "기타")
        src_name = item.get("source_name", "")
        link     = item.get("link", "")

        for name, code in stock_map.items():
            if len(name) < 3 or name in SKIP_NAMES:
                continue
            if name in text:
                mentions[name][src_type].append({
                    "source_name": src_name,
                    "source_type": src_type,
                    "detail":      item.get("summary", "")[:300],
                    "source_url":  link,
                    "title":       item.get("title", ""),
                })

    return dict(mentions)


def analyze_and_generate_html(
    all_data: list,
    api_key: str,
    channels_data: dict = None,
    gh_repo: str = "",
) -> str:
    """전체 데이터를 분석하여 최종 HTML 반환"""
    if not api_key:
        print("[AI 분석] API 키가 없습니다. 더미 HTML 생성...")
        return _generate_fallback_html()

    # API 키 정제 (GitHub Secrets 줄바꿈 방지)
    api_key = api_key.strip()
    os.environ["ANTHROPIC_API_KEY"] = api_key

    class _Client:
        def __init__(self, key):
            self.api_key = key

    client  = _Client(api_key)
    model   = MODEL
    now_kst = datetime.now(KST)

    # 종목 목록 로드
    print("  [종목목록] 로드 중...")
    stock_map = load_stock_names()

    # 섹션별 데이터 분류
    news_data = [d for d in all_data if d.get("source_type") == "뉴스"]

    section1_data = [
        d for d in all_data
        if d.get("section") == "section1"
        or (
            d.get("source_type") in ["경제방송", "개인유튜브", "증권사유튜브"]
            and d.get("section") not in ("section2", "section3")
        )
    ]
    section2_data = [
        d for d in all_data
        if d.get("section") == "section2" or d.get("source_type") == "증권TV"
    ]
    section3_data = [
        d for d in all_data
        if d.get("section") == "section3" or d.get("source_type") == "애널리스트"
    ]

    print(
        f"  뉴스: {len(news_data)}건 | "
        f"섹션1: {len(section1_data)}건 | "
        f"섹션2: {len(section2_data)}건 | "
        f"섹션3: {len(section3_data)}건"
    )

    # AI 분석
    print("\n[시장 요약 생성 중...]")
    market_summary = _generate_market_summary(
        client, model, news_data + section1_data[:10]
    )

    print("\n[섹션 1 분석 중...]")
    section1_stocks = _analyze_section1(client, model, section1_data, stock_map)

    print("\n[섹션 2 분석 중...]")
    section2_stocks = _analyze_section2(client, model, section2_data, stock_map)

    print("\n[섹션 3 분석 중...]")
    section3_stocks = _analyze_section3(client, model, section3_data, stock_map)

    print("\n[종합 투자전략 생성 중...]")
    investment_strategy = _generate_investment_strategy(
        client, model,
        section1_stocks, section2_stocks, section3_stocks,
        market_summary,
    )

    # 실제 2주 주가 데이터 주입
    print("\n[주가 정보 조회 중...]")
    _enrich_stocks_with_price(
        section1_stocks + section2_stocks + section3_stocks,
        stock_map,
    )

    # HTML 생성
    data = {
        "briefing_date":       now_kst.strftime("%Y-%m-%d"),
        "market_summary":      market_summary,
        "section1_stocks":     section1_stocks,
        "section2_stocks":     section2_stocks,
        "section3_stocks":     section3_stocks,
        "investment_strategy": investment_strategy,
    }

    return generate_html(data, channels_data=channels_data, gh_repo=gh_repo)


# ══════════════════════════════════════════════════════════════
#  주가 데이터 주입
# ══════════════════════════════════════════════════════════════

def _enrich_stocks_with_price(stocks: list, stock_map: dict) -> None:
    """
    종목 리스트에 실제 주가 + 2주 히스토리 정보를 주입.
    """
    for stock in stocks:
        name = stock.get("name", "")
        code = stock.get("krx_code", "").strip() or stock_map.get(name, "")
        if not code or code == "NONE":
            continue

        try:
            info = get_stock_full_info(code)
        except Exception as e:
            print(f"  [주가조회 실패] {name}({code}): {e}")
            continue

        if not info.get("price"):
            continue

        stock["krx_code"] = code

        stock["verified_price"] = {
            "code":       info.get("code", code),
            "price":      info.get("price", ""),
            "change":     info.get("change", ""),
            "change_pct": info.get("change_pct", ""),
        }

        if info.get("price_display"):
            stock["price_display"] = info["price_display"]

        history_summary = info.get("history_summary", "")
        period_change   = info.get("period_change", "")
        period_high     = info.get("period_high", "")
        period_low      = info.get("period_low", "")

        if history_summary:
            prefix = f"[실제 데이터] {history_summary}"
            if period_change:
                prefix += f" | 2주 등락: {period_change}"
            if period_high and period_low:
                prefix += f" | 2주 고가 {period_high}원 / 저가 {period_low}원"

            existing = stock.get("price_trend", "")
            stock["price_trend"] = prefix + ("\n" + existing if existing else "")

        print(
            f"  [주가] {name}({code}): "
            f"{info.get('price', '')}원 "
            f"{info.get('change', '')} ({info.get('change_pct', '')})"
        )


# ══════════════════════════════════════════════════════════════
#  AI 분석 헬퍼
# ══════════════════════════════════════════════════════════════

def _generate_market_summary(client, model: str, data: list) -> str:
    """뉴스 + 미디어 데이터로 시장 요약 생성"""
    news_items  = [d for d in data if d.get("source_type") == "뉴스"]
    other_items = [d for d in data if d.get("source_type") != "뉴스"][:10]

    news_text = "\n".join(
        f"[{d.get('source_name','')}] {d.get('title','')}: {d.get('summary','')[:200]}"
        for d in news_items[:20]
    )
    other_text = "\n".join(
        f"[{d.get('source_name','')}] {d.get('title','')}"
        for d in other_items[:10]
    )

    if not news_text and not other_text:
        return "오늘 수집된 뉴스 데이터가 없습니다. RSS 피드 연결 상태를 확인해 주세요."

    prompt = (
        "다음은 오늘 수집된 주요 경제 뉴스와 미디어 콘텐츠입니다.\n\n"
        "=== 주요 경제신문 뉴스 ===\n" + news_text + "\n\n"
        "=== 기타 미디어 ===\n" + other_text + "\n\n"
        "위 내용을 바탕으로 오늘 한국 주식시장의 시장 요약을 작성하세요.\n\n"
        "요구사항:\n"
        "1. 3~4개의 핵심 이슈를 각각 한 단락으로 서술\n"
        "2. 각 단락은 '이슈 제목: 내용' 형식으로 작성\n"
        "3. 매일경제, 한국경제, 서울경제, 이데일리, 머니투데이 등 신문사 기사를 주요 근거로 활용\n"
        "4. 시장에 영향을 미치는 거시경제, 국내외 이슈, 주요 종목 동향을 포함\n"
        "5. 각 단락 끝에 주요 참고 기사 출처를 간략히 표시 (예: [출처: 매일경제])\n"
        "6. 한국어로 작성, 총 500~700자\n\n"
        "JSON이 아닌 순수 텍스트로 작성하세요."
    )

    try:
        response = call_claude_with_retry(
            client, model, 1000,
            "당신은 한국 경제 전문 분석가입니다.",
            [{"role": "user", "content": prompt}],
        )
        return response.strip()
    except Exception as e:
        print(f"  [시장 요약 오류] {e}")
        return f"시장 요약 생성 중 오류가 발생했습니다: {str(e)[:100]}"


def _analyze_section1(client, model: str, data: list, stock_map: dict) -> list:
    """섹션 1: 유튜브·미디어 채널 언급 종목 분석"""
    if not data:
        print("  [섹션 1] 수집된 데이터 없음")
        return []

    data_text = "\n".join(
        f"[{d.get('source_type','')}][{d.get('source_name','')}] "
        f"{d.get('title','')}: {d.get('summary','')[:300]}"
        for d in data[:40]
    )
    stock_list_text = ", ".join(list(stock_map.keys())[:200])

    prompt = (
        "다음은 유튜브 및 미디어 채널에서 수집된 콘텐츠입니다.\n\n"
        + data_text + "\n\n"
        "참고 종목 목록 (KOSPI/KOSDAQ 상장 종목):\n"
        + stock_list_text + "\n\n"
        "위 콘텐츠에서 한국 주식 종목이 언급된 경우를 분석하여 JSON 배열을 반환하세요.\n\n"
        "각 종목 객체 형식:\n"
        "{\n"
        '  "name": "종목명",\n'
        '  "krx_code": "종목코드(6자리, 모를 경우 빈 문자열)",\n'
        '  "signal": "긍정|부정|중립",\n'
        '  "description": "기업 개요와 현재 주목받는 이유 (3~5문장)",\n'
        '  "price_trend": "최근 2주간 주가 흐름 분석",\n'
        '  "price_display": "가격 표시 문자열 (예: 181,000원 ▲10,700 (+5.58%))",\n'
        '  "catalyst": "상승 촉매 (구체적 서술)",\n'
        '  "risk": "리스크 (구체적 서술)",\n'
        '  "reasons": [\n'
        "    {\n"
        '      "source_type": "채널 유형 ([뉴스]/[경제방송]/[개인유튜브]/[증권사])",\n'
        '      "source_name": "채널명",\n'
        '      "detail": "핵심 언급 요약",\n'
        '      "source_url": "원본 링크 또는 빈 문자열"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "중요 규칙:\n"
        "- 최소 3개, 최대 8개 종목 추출\n"
        "- 반드시 JSON 배열만 반환, 코드블록(```json ... ```)으로 감싸기"
    )

    try:
        response = call_claude_with_retry(
            client, model, 4000,
            "당신은 한국 주식시장 분석 전문가입니다.",
            [{"role": "user", "content": prompt}],
        )
        return _parse_stocks_json(response)
    except Exception as e:
        print(f"  [섹션 1 오류] {e}")
        return []


def _analyze_section2(client, model: str, data: list, stock_map: dict) -> list:
    """섹션 2: 증권TV 전문가 추천 종목 분석"""
    if not data:
        print("  [섹션 2] 수집된 데이터 없음")
        return []

    data_text = "\n".join(
        f"[{d.get('source_name','')}][전문가: {d.get('expert_name','미확인')}] "
        f"{d.get('title','')}: {d.get('summary','')[:300]}"
        for d in data[:30]
    )
    stock_list_text = ", ".join(list(stock_map.keys())[:200])

    prompt = (
        "다음은 증권TV 전문가 출연 프로그램에서 수집된 콘텐츠입니다. (전일 방송 기준)\n\n"
        + data_text + "\n\n"
        "참고 종목 목록:\n" + stock_list_text + "\n\n"
        "위 증권TV 콘텐츠에서 전문가가 추천하거나 분석한 종목을 추출하여 JSON 배열로 반환하세요.\n\n"
        "각 종목 객체 형식:\n"
        "{\n"
        '  "name": "종목명",\n'
        '  "krx_code": "종목코드(6자리, 모를 경우 빈 문자열)",\n'
        '  "signal": "긍정|부정|중립",\n'
        '  "description": "기업 개요와 전문가가 주목한 이유 (3~5문장)",\n'
        '  "price_trend": "최근 2주간 주가 흐름 분석",\n'
        '  "price_display": "가격 표시 문자열",\n'
        '  "catalyst": "전문가가 언급한 상승 촉매",\n'
        '  "risk": "전문가가 언급한 리스크",\n'
        '  "reasons": [\n'
        "    {\n"
        '      "source_type": "증권TV",\n'
        '      "source_name": "채널명 (전문가명/코너명 포함)",\n'
        '      "detail": "전문가 언급 핵심 내용",\n'
        '      "source_url": "원본 링크 또는 빈 문자열"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "중요:\n"
        "- source_type은 반드시 '증권TV'로 표시\n"
        "- 최소 2개, 최대 6개 종목 추출\n"
        "- 반드시 JSON 배열만 반환, 코드블록(```json ... ```)으로 감싸기"
    )

    try:
        response = call_claude_with_retry(
            client, model, 3000,
            "당신은 한국 주식시장 분석 전문가입니다.",
            [{"role": "user", "content": prompt}],
        )
        stocks = _parse_stocks_json(response)
        for s in stocks:
            s["section"] = "section2"
        return stocks
    except Exception as e:
        print(f"  [섹션 2 오류] {e}")
        return []


def _analyze_section3(client, model: str, data: list, stock_map: dict) -> list:
    """섹션 3: 애널리스트 리포트 분석"""
    if not data:
        print("  [섹션 3] 수집된 데이터 없음")
        return []

    reports_text = "\n".join(
        f"[{d.get('analyst_category','unknown')}][{d.get('source_name','')}] "
        f"{d.get('stock_name','')} - {d.get('report_title','')} "
        f"(담당: {d.get('analyst','')})"
        + (f" | 목표가: {d.get('target_price','')}원" if d.get("target_price") else "")
        for d in data[:30]
    )

    prompt = (
        "다음은 오늘 발행된 증권사 애널리스트 리포트입니다.\n\n"
        + reports_text + "\n\n"
        "카테고리:\n"
        "- simultaneous: 복수 증권사 동시 언급 (24시간 내)\n"
        "- new_coverage: 신규 커버리지 개시\n"
        "- first_in_6months: 단일 증권사 6개월 내 첫 언급\n\n"
        "위 리포트를 분석하여 JSON 배열로 반환하세요.\n\n"
        "각 종목 객체 형식:\n"
        "{\n"
        '  "name": "종목명",\n'
        '  "krx_code": "종목코드(6자리, 모를 경우 빈 문자열)",\n'
        '  "analyst_category": "simultaneous|new_coverage|first_in_6months",\n'
        '  "signal": "긍정|부정|중립",\n'
        '  "description": "기업 개요와 애널리스트가 주목한 이유 (3~5문장)",\n'
        '  "price_trend": "최근 2주간 주가 흐름 분석",\n'
        '  "price_display": "가격 표시 문자열",\n'
        '  "catalyst": "리포트에서 언급된 상승 촉매",\n'
        '  "risk": "리포트에서 언급된 리스크",\n'
        '  "broker": "증권사명 (복수인 경우 / 로 구분)",\n'
        '  "analyst_name": "담당 애널리스트명",\n'
        '  "target_price": "목표주가 숫자만 (예: 85000), 없으면 빈 문자열",\n'
        '  "opinion": "BUY/HOLD/SELL 또는 매수/중립/매도, 없으면 빈 문자열",\n'
        '  "reasons": [\n'
        "    {\n"
        '      "source_type": "애널리스트",\n'
        '      "source_name": "증권사명 (담당 애널리스트)",\n'
        '      "detail": "리포트 핵심 내용 요약",\n'
        '      "source_url": ""\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "반드시 JSON 배열만 반환, 코드블록(```json ... ```)으로 감싸기"
    )

    try:
        response = call_claude_with_retry(
            client, model, 3000,
            "당신은 한국 주식시장 분석 전문가입니다.",
            [{"role": "user", "content": prompt}],
        )
        stocks = _parse_stocks_json(response)

        # Claude 응답에서 누락된 필드를 원본 데이터로 보완
        for s in stocks:
            s["section"] = "section3"
            stock_name = s.get("name", "")

            for d in data:
                if d.get("stock_name", "") == stock_name:
                    if not s.get("analyst_category"):
                        s["analyst_category"] = d.get("analyst_category", "")
                    # broker: Claude가 못 채운 경우 source_name으로 보완
                    if not s.get("broker"):
                        s["broker"] = d.get("source_name", "")
                    if not s.get("analyst_name"):
                        s["analyst_name"] = d.get("analyst", "")
                    # target_price: Claude가 빈 문자열로 반환한 경우 원본으로 보완
                    if not s.get("target_price"):
                        s["target_price"] = d.get("target_price", "")
                    # opinion: Claude가 빈 문자열로 반환한 경우 원본으로 보완
                    if not s.get("opinion"):
                        s["opinion"] = d.get("opinion", "")
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
    market_summary: str,
) -> str:
    """섹션 1~3 교차 분석 종합 투자전략 생성"""
    s1_names = [s.get("name", "") for s in section1_stocks]
    s2_names = [s.get("name", "") for s in section2_stocks]
    s3_names = [s.get("name", "") for s in section3_stocks]

    cross_all  = set(s1_names) & set(s2_names) & set(s3_names)
    cross_s1s3 = set(s1_names) & set(s3_names)
    cross_s2s3 = set(s2_names) & set(s3_names)

    cross_lines = ""
    if cross_all:
        cross_lines += "3개 섹션 모두 언급: " + ", ".join(cross_all) + "\n"
    if cross_s1s3 - cross_all:
        cross_lines += "섹션1+섹션3 교차: " + ", ".join(cross_s1s3 - cross_all) + "\n"
    if cross_s2s3 - cross_all:
        cross_lines += "섹션2+섹션3 교차: " + ", ".join(cross_s2s3 - cross_all) + "\n"

    if not s1_names and not s2_names and not s3_names:
        return "분석할 종목 데이터가 없습니다. 데이터 수집 상태를 확인해 주세요."

    s3_simul = ", ".join(
        s.get("name", "") for s in section3_stocks
        if s.get("analyst_category") == "simultaneous"
    ) or "없음"
    s3_new = ", ".join(
        s.get("name", "") for s in section3_stocks
        if s.get("analyst_category") == "new_coverage"
    ) or "없음"

    prompt = (
        "다음은 오늘의 주식 브리핑 데이터입니다.\n\n"
        "시장 요약:\n" + market_summary[:300] + "\n\n"
        "섹션 1 (유튜브·미디어 채널 언급 종목): " + (", ".join(s1_names) or "없음") + "\n"
        "- 긍정 종목: " + (
            ", ".join(s.get("name", "") for s in section1_stocks if s.get("signal") == "긍정")
            or "없음"
        ) + "\n\n"
        "섹션 2 (증권TV 전문가 추천): " + (", ".join(s2_names) or "없음") + "\n\n"
        "섹션 3 (애널리스트 리포트):\n"
        "- 복수 증권사 동시 언급: " + s3_simul + "\n"
        "- 신규 커버리지: " + s3_new + "\n\n"
        "교차 분석:\n" + (cross_lines if cross_lines else "교차 종목 없음") + "\n\n"
        "위 데이터를 종합 교차 분석하여 오늘의 투자전략을 작성하세요.\n\n"
        "요구사항:\n"
        "1. 섹션 1, 2, 3 통합 교차 분석 근거를 명시\n"
        "2. 교차 언급 종목은 특히 상세히 서술\n"
        "3. 구체적인 매매 전략 (분할 매수, 비중 조절 등) 포함\n"
        "4. 500~700자 분량의 한국어 서술\n"
        "5. 순수 텍스트로 작성 (JSON 불필요)\n"
        "6. 마지막에 주요 리스크 요인 2~3가지 포함"
    )

    try:
        response = call_claude_with_retry(
            client, model, 1500,
            "당신은 한국 주식시장 투자전략 전문가입니다.",
            [{"role": "user", "content": prompt}],
        )
        return response.strip()
    except Exception as e:
        print(f"  [투자전략 오류] {e}")
        return f"투자전략 생성 중 오류가 발생했습니다: {str(e)[:100]}"


# ══════════════════════════════════════════════════════════════
#  공통 유틸
# ══════════════════════════════════════════════════════════════

def _parse_stocks_json(response: str) -> list:
    """Claude 응답에서 JSON 배열 파싱"""
    m = re.search(r"```json\s*([\s\S]*?)\s*```", response)
    if m:
        json_str = m.group(1)
    else:
        m2 = re.search(r"\[[\s\S]*\]", response)
        if m2:
            json_str = m2.group(0)
        else:
            print(f"  [JSON 파싱] 배열 없음. 응답 앞부분: {response[:200]}")
            return []

    try:
        result = json.loads(json_str)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError as e:
        print(f"  [JSON 파싱 오류] {e}")
        return []


def _generate_fallback_html() -> str:
    """API 키 없을 때 기본 안내 HTML 생성"""
    data = {
        "briefing_date": datetime.now(KST).strftime("%Y-%m-%d"),
        "market_summary": (
            "API 키 미설정: 데이터를 불러올 수 없습니다.\n\n"
            "설정 방법: GitHub 저장소 Settings → Secrets and variables → Actions 에서 "
            "ANTHROPIC_API_KEY, YOUTUBE_API_KEY, GH_TOKEN 을 등록해 주세요."
        ),
        "section1_stocks":     [],
        "section2_stocks":     [],
        "section3_stocks":     [],
        "investment_strategy": "API 키를 설정하면 AI 투자전략이 자동 생성됩니다.",
    }
    return generate_html(data)
