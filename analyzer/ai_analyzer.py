# analyzer/ai_analyzer.py - v3
"""
AI 분석기: Claude API를 사용하여 수집 데이터를 분석하고 HTML 생성
JSON 파싱 강화 버전 (Expecting ',' delimiter 오류 해결)
"""

import os
import re
import json
import anthropic
from datetime import datetime
from zoneinfo import ZoneInfo

from .api_client import call_claude_with_retry
from .html_generator import generate_html
from .naver_finance import get_stock_full_info, load_stock_names

KST = ZoneInfo("Asia/Seoul")
MODEL = "claude-sonnet-4-6"

SKIP_NAMES = {
    "주식", "시장", "증권", "투자", "경제", "금융", "코스피", "코스닥",
    "거래소", "펀드", "채권", "환율", "금리", "원자재", "글로벌",
    "국내", "해외", "미국", "중국", "일본", "유럽",
}


# ══════════════════════════════════════════════════════════════
#  메인 진입점
# ══════════════════════════════════════════════════════════════
def analyze_and_generate_html(
    all_data: list,
    api_key: str,
    channels: dict = None,
    github_repo: str = "",
    market_overview: dict = None,   # ← 추가
) -> str:
    if not api_key:
        return _generate_fallback_html()

    client = anthropic.Anthropic(api_key=api_key.strip())
    stock_map = load_stock_names()
    now = datetime.now(KST)

    # 데이터 분류
    news      = [d for d in all_data if d.get("source_type") == "뉴스"]
    section1  = [d for d in all_data if d.get("section") == "section1"]
    section2  = [d for d in all_data if d.get("section") == "section2"]
    section3  = [d for d in all_data if d.get("section") == "section3"]

    print(f"  분류: 뉴스={len(news)} 섹션1={len(section1)} "
          f"섹션2={len(section2)} 섹션3={len(section3)}")

    # ── 시장 요약 (5단락) ─────────────────────────────────────────────
    market_summary = _generate_market_summary(
        client, news, section1, market_overview or {}
    )

    # ── 섹션별 AI 분석 ────────────────────────────────────────────────
    print("  섹션1 분석 중...")
    s1_stocks = _analyze_section1(client, section1, stock_map)
    print(f"  섹션1 종목: {len(s1_stocks)}개")

    print("  섹션2 분석 중...")
    s2_stocks = _analyze_section2(client, section2, stock_map)
    print(f"  섹션2 종목: {len(s2_stocks)}개")

    print("  섹션3 분석 중...")
    s3_stocks = _analyze_section3(client, section3, stock_map)
    print(f"  섹션3 종목: {len(s3_stocks)}개")

    # ── 투자전략 ──────────────────────────────────────────────────────
    print("  투자전략 생성 중...")
    strategy = _generate_investment_strategy(
        client, s1_stocks, s2_stocks, s3_stocks, market_overview or {}
    )

    # ── 가격 데이터 enrichment ────────────────────────────────────────
    print("  가격 데이터 조회 중...")
    s1_stocks = _enrich_stocks_with_price(s1_stocks, stock_map)
    s2_stocks = _enrich_stocks_with_price(s2_stocks, stock_map)
    s3_stocks = _enrich_stocks_with_price(s3_stocks, stock_map)

    # ── 아카이브 날짜 목록 ────────────────────────────────────────────
    archive_dates = _get_archive_dates()

    result = {
        "briefing_date":   now.strftime("%Y년 %m월 %d일"),
        "market_summary":  market_summary,
        "section1_stocks": s1_stocks,
        "section2_stocks": s2_stocks,
        "section3_stocks": s3_stocks,
        "investment_strategy": strategy,
    }

    return generate_html(
        analysis_result=result,
        archive_dates=archive_dates,
        channels=channels,
        github_repo=github_repo,
    )


# ══════════════════════════════════════════════════════════════
#  시장 요약 — 5단락 구조
# ══════════════════════════════════════════════════════════════
def _generate_market_summary(
    client, news: list, section1: list, market_overview: dict
) -> dict:
    """
    5단락 구조:
    1. 야간선물 (call/put 예측)
    2. 미국 증시
    3. 전일 한국 증시
    4. 오늘 예상 흐름
    5. 주요 관심 섹터
    """
    mo = market_overview

    # 수집된 시장 데이터 요약 텍스트
    night_f = mo.get("night_futures", {})
    us      = mo.get("us_market", {})
    korea   = mo.get("korea_market", {})

    night_txt = (
        f"코스피200 야간선물: {night_f.get('price','N/A')} "
        f"({night_f.get('change_pct','N/A'):+}%) "
        f"신호: {night_f.get('signal','N/A')}"
    ) if night_f.get("price") else "야간선물 데이터 없음"

    sp = us.get("sp500", {})
    nq = us.get("nasdaq", {})
    dw = us.get("dow", {})
    fx = us.get("usd_krw", {})
    us_txt = (
        f"S&P500: {sp.get('price','N/A')} ({sp.get('change_pct','N/A'):+}%), "
        f"나스닥: {nq.get('price','N/A')} ({nq.get('change_pct','N/A'):+}%), "
        f"다우: {dw.get('price','N/A')} ({dw.get('change_pct','N/A'):+}%), "
        f"달러/원: {fx.get('price','N/A')}"
    ) if sp.get("price") else "미국 증시 데이터 없음"

    kp = korea.get("kospi", {})
    kd = korea.get("kosdaq", {})
    kr_txt = (
        f"코스피: {kp.get('price','N/A')} ({kp.get('change_pct','N/A'):+}%), "
        f"코스닥: {kd.get('price','N/A')} ({kd.get('change_pct','N/A'):+}%)"
    ) if kp.get("price") else "한국 증시 데이터 없음"

    # 뉴스 컨텍스트
    news_ctx = "\n".join(
        f"- {n.get('title','')} ({n.get('source_name','')})"
        for n in news[:15]
    )
    media_ctx = "\n".join(
        f"- {s.get('title','')} ({s.get('source_name','')})"
        for s in section1[:8]
    )

    system = (
        "당신은 한국 주식시장 전문 애널리스트입니다. "
        "아래 시장 데이터와 뉴스를 분석하여 정확하고 통찰력 있는 시장 요약을 작성하세요. "
        "반드시 순수 텍스트만 사용하고, 마크다운(#, ##, **, *, -, ``` 등) 문법은 절대 사용하지 마세요. "
        "각 단락은 소제목으로 시작하고 2~4문장으로 구성하세요."
    )

    prompt = f"""다음 시장 데이터를 바탕으로 오늘 주식 시장 브리핑 요약을 작성해주세요.

[야간선물 데이터]
{night_txt}

[미국 증시 데이터]
{us_txt}

[전일 한국 증시]
{kr_txt}

[주요 뉴스]
{news_ctx}

[미디어/유튜브 주요 언급]
{media_ctx}

아래 5개 단락을 각각 소제목과 함께 작성해주세요. 각 단락은 반드시 소제목으로 시작하고 본문 2~4문장이 이어져야 합니다. 마크다운 없이 순수 텍스트로 작성하세요.

【야간선물 동향 및 콜/풋 예측】
코스피200 야간선물의 등락과 거래량을 분석하여 다음 날 한국 증시의 콜(상승) 또는 풋(하락) 방향성을 예측하세요. 야간선물 신호와 함께 매수/매도 의견을 구체적으로 제시하세요.

【미국 증시 마감 동향】
전날 밤 마감된 미국 S&P500·나스닥·다우의 등락을 정리하고, 한국 증시에 미치는 영향을 분석하세요. 달러/원 환율 움직임도 포함하세요.

【전일 한국 증시 흐름】
코스피와 코스닥의 전일 종가, 외국인·기관 수급 흐름, 주요 특징을 정리하세요. 시장 전반의 분위기를 2~3문장으로 서술하세요.

【오늘 예상 흐름】
위 데이터를 종합하여 오늘 한국 증시의 시초가 방향, 장중 변동성, 주요 리스크 요인을 예측하세요. 구체적 지수 범위 예측이 있으면 포함하세요.

【주요 관심 섹터】
오늘 특히 주목해야 할 섹터 2~3개와 그 이유를 설명하세요. 최근 뉴스 흐름과 연결하여 구체적인 투자 포인트를 제시하세요."""

    try:
        resp = call_claude_with_retry(
            client=client,
            model=MODEL,
            max_tokens=1200,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()

        # 5단락 파싱
        summary = _parse_market_summary_paragraphs(raw, night_f, us, kp, kd, fx)
        return summary

    except Exception as e:
        print(f"  [시장요약] 생성 실패: {e}")
        return _fallback_market_summary(night_f, us, kp, kd, fx)


def _parse_market_summary_paragraphs(
    raw_text: str,
    night_f: dict, us: dict, kp: dict, kd: dict, fx: dict
) -> dict:
    """Claude 응답에서 5단락 추출"""
    markers = [
        ("night_futures_para", ["야간선물", "콜/풋", "야간 선물", "선물 동향"]),
        ("us_market_para",     ["미국 증시", "S&P", "나스닥", "미국증시", "마감 동향"]),
        ("korea_prev_para",    ["전일 한국", "코스피", "코스닥", "전일 증시", "한국 증시 흐름"]),
        ("today_outlook_para", ["오늘 예상", "예상 흐름", "시초가", "장 전망"]),
        ("sector_para",        ["관심 섹터", "주요 섹터", "주목 섹터", "섹터"]),
    ]

    result = {k: "" for k, _ in markers}
    lines = raw_text.split("\n")

    current_key = None
    buffer = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if buffer and current_key:
                buffer.append("")
            continue

        # 소제목 감지
        matched_key = None
        for key, keywords in markers:
            if any(kw in stripped for kw in keywords):
                # 소제목 줄인지 판단 (짧고 키워드 포함)
                if len(stripped) < 30 or stripped.startswith("【") or stripped.startswith("["):
                    matched_key = key
                    break

        if matched_key:
            # 이전 버퍼 저장
            if current_key and buffer:
                result[current_key] = "\n".join(buffer).strip()
            current_key = matched_key
            buffer = []
        else:
            if current_key:
                buffer.append(stripped)

    # 마지막 버퍼 저장
    if current_key and buffer:
        result[current_key] = "\n".join(buffer).strip()

    # 빈 단락 채우기
    result = _fill_empty_paragraphs(result, night_f, us, kp, kd, fx)

    # 수치 데이터 주입
    sp = us.get("sp500", {})
    nq = us.get("nasdaq", {})
    dw = us.get("dow", {})

    result["night_futures_data"] = {
        "price":      night_f.get("price"),
        "change_pct": night_f.get("change_pct"),
        "direction":  night_f.get("direction", "neutral"),
        "signal":     night_f.get("signal", ""),
    }
    result["us_market_data"] = {
        "sp500":  sp,
        "nasdaq": nq,
        "dow":    dw,
        "usd_krw": fx,
    }
    result["korea_market_data"] = {
        "kospi":  kp,
        "kosdaq": kd,
    }

    return result


def _fill_empty_paragraphs(result, night_f, us, kp, kd, fx) -> dict:
    sp = us.get("sp500", {})
    nq = us.get("nasdaq", {})

    if not result.get("night_futures_para"):
        direction = night_f.get("direction", "neutral")
        pct = night_f.get("change_pct")
        if direction == "call":
            result["night_futures_para"] = (
                f"코스피200 야간선물이 {pct:+.2f}% 상승하며 강한 콜(매수) 신호를 보이고 있습니다. "
                "야간선물 상승 흐름은 다음 날 코스피·코스닥 시초가의 갭 상승 가능성을 시사합니다."
            )
        elif direction == "put":
            result["night_futures_para"] = (
                f"코스피200 야간선물이 {pct:+.2f}% 하락하며 풋(매도) 압력이 나타나고 있습니다. "
                "야간선물 약세는 익일 시초가 갭 하락 위험을 높이므로 매수 시 신중한 접근이 필요합니다."
            )
        else:
            result["night_futures_para"] = (
                "코스피200 야간선물이 보합권에서 거래되며 뚜렷한 방향성을 보이지 않고 있습니다. "
                "중립 신호로 장 시작 후 개별 재료에 따른 종목별 차별화 흐름이 예상됩니다."
            )

    if not result.get("us_market_para"):
        sp_pct = sp.get("change_pct", 0) or 0
        nq_pct = nq.get("change_pct", 0) or 0
        fx_price = fx.get("price")
        result["us_market_para"] = (
            f"미국 S&P500은 전일 대비 {sp_pct:+.2f}% 마감, "
            f"나스닥은 {nq_pct:+.2f}%를 기록했습니다. "
            + (f"달러/원 환율은 {fx_price:.0f}원대로 원화 강약에 주목할 필요가 있습니다." if fx_price else "")
        )

    if not result.get("korea_prev_para"):
        kp_pct = kp.get("change_pct", 0) or 0
        kd_pct = kd.get("change_pct", 0) or 0
        result["korea_prev_para"] = (
            f"전일 코스피는 {kp_pct:+.2f}%, 코스닥은 {kd_pct:+.2f}%로 마감했습니다. "
            "외국인과 기관의 수급 동향에 따라 지수 방향성이 결정될 것으로 보입니다."
        )

    return result


def _fallback_market_summary(night_f, us, kp, kd, fx) -> dict:
    result = {
        "night_futures_para":  "야간선물 데이터를 수집하지 못했습니다.",
        "us_market_para":      "미국 증시 데이터를 수집하지 못했습니다.",
        "korea_prev_para":     "전일 한국 증시 데이터를 수집하지 못했습니다.",
        "today_outlook_para":  "시장 데이터 부족으로 오늘 흐름 예측이 어렵습니다.",
        "sector_para":         "섹터 데이터를 수집하지 못했습니다.",
        "night_futures_data":  {"direction": "neutral", "signal": ""},
        "us_market_data":      {},
        "korea_market_data":   {},
    }
    result = _fill_empty_paragraphs(result, night_f, us, kp, kd, fx)
    return result


# ══════════════════════════════════════════════════════════════
#  JSON 파싱 강화 (핵심 버그 수정)
# ══════════════════════════════════════════════════════════════
def _parse_stocks_json(text: str) -> list:
    """
    Claude 응답에서 JSON 배열을 추출합니다.
    - 코드 블록 제거
    - 이스케이프된 따옴표 처리
    - 불완전 JSON 복구 시도
    - 빈 배열 반환 (실패 시)
    """
    if not text:
        return []

    # 1단계: 코드 블록 제거
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    text = text.strip()

    # 2단계: JSON 배열 추출
    start = text.find("[")
    end   = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    text = text[start:end + 1]

    # 3단계: 이스케이프 및 제어문자 정제
    # 잘못된 이스케이프 시퀀스 제거 (\" 안에 있는 줄바꿈 등)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)

    # 4단계: 직접 파싱 시도
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 5단계: 자동 복구 — 따옴표 내부의 줄바꿈 제거
    try:
        # 문자열 값 안의 줄바꿈을 공백으로 교체
        fixed = re.sub(
            r'"((?:[^"\\]|\\.)*)"',
            lambda m: '"' + m.group(1).replace('\n', ' ').replace('\r', '') + '"',
            text,
        )
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 6단계: 개별 객체 추출 (배열이 불완전한 경우)
    objects = []
    for match in re.finditer(r'\{[^{}]+\}', text, re.DOTALL):
        try:
            obj_text = match.group(0)
            obj_text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', obj_text)
            obj = json.loads(obj_text)
            if isinstance(obj, dict) and obj.get("stock_name"):
                objects.append(obj)
        except json.JSONDecodeError:
            continue

    if objects:
        print(f"  [JSON 복구] 개별 객체 추출: {len(objects)}개")
        return objects

    print(f"  [JSON 파싱 실패] 텍스트 앞 200자: {text[:200]}")
    return []


# ══════════════════════════════════════════════════════════════
#  섹션별 분석
# ══════════════════════════════════════════════════════════════
def _analyze_section1(client, data: list, stock_map: dict) -> list:
    if not data:
        return []

    items_text = "\n".join(
        f"[{i+1}] 채널:{d.get('source_name','')} | "
        f"제목:{d.get('title','')} | "
        f"요약:{str(d.get('summary',''))[:200]}"
        for i, d in enumerate(data[:40])
    )

    system = (
        "당신은 한국 주식 분석 전문가입니다. "
        "유튜브/방송 채널의 영상 데이터에서 언급된 종목을 추출합니다. "
        "반드시 유효한 JSON 배열만 반환하세요. "
        "문자열 값 안에 줄바꿈, 큰따옴표를 넣지 마세요."
    )

    prompt = f"""아래 유튜브/방송 데이터에서 언급된 한국 주식 종목을 추출하세요.

{items_text}

다음 JSON 형식으로 최대 15개 종목을 반환하세요:
[
  {{
    "stock_name": "삼성전자",
    "reasons": ["이유1", "이유2"],
    "sentiment": "긍정",
    "source_name": "채널명",
    "mention_count": 3
  }}
]

규칙:
- stock_name은 실제 한국 주식 종목명만 (회사명 전체)
- reasons는 2~3개, 각 항목은 50자 이내 순수 텍스트
- sentiment는 "긍정", "부정", "중립" 중 하나
- 마크다운, 줄바꿈, 특수기호 없이 순수 텍스트
- JSON 외 다른 텍스트 없이 배열만 반환"""

    try:
        resp = call_claude_with_retry(
            client=client, model=MODEL, max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_stocks_json(resp.content[0].text)
    except Exception as e:
        print(f"  [섹션1 분석 오류] {e}")
        return []


def _analyze_section2(client, data: list, stock_map: dict) -> list:
    if not data:
        return []

    items_text = "\n".join(
        f"[{i+1}] 채널:{d.get('source_name','')} | "
        f"제목:{d.get('title','')} | "
        f"요약:{str(d.get('summary',''))[:200]}"
        for i, d in enumerate(data[:20])
    )

    system = (
        "당신은 한국 증권TV 전문가입니다. "
        "증권TV 방송에서 전문가가 추천한 종목을 추출합니다. "
        "반드시 유효한 JSON 배열만 반환하세요. "
        "문자열 값 안에 줄바꿈, 큰따옴표를 넣지 마세요."
    )

    prompt = f"""아래 증권TV 방송 데이터에서 전문가가 추천한 종목을 추출하세요.

{items_text}

다음 JSON 형식으로 최대 10개 종목을 반환하세요:
[
  {{
    "stock_name": "현대차",
    "expert_name": "전문가명",
    "recommendation": "매수",
    "reasons": ["이유1", "이유2"],
    "source_name": "채널명"
  }}
]

규칙:
- stock_name은 실제 한국 주식 종목명만
- recommendation은 "매수", "매도", "보유", "관심" 중 하나
- reasons는 2~3개, 각 50자 이내 순수 텍스트
- JSON 배열만 반환, 다른 텍스트 없음"""

    try:
        resp = call_claude_with_retry(
            client=client, model=MODEL, max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_stocks_json(resp.content[0].text)
    except Exception as e:
        print(f"  [섹션2 분석 오류] {e}")
        return []


def _analyze_section3(client, data: list, stock_map: dict) -> list:
    if not data:
        return []

    items_text = "\n".join(
        f"[{i+1}] 증권사:{d.get('source_name','')} | "
        f"종목:{d.get('stock_name','')} | "
        f"제목:{d.get('report_title','')} | "
        f"목표가:{d.get('target_price','')} | "
        f"날짜:{d.get('date','')}"
        for i, d in enumerate(data[:40])
    )

    system = (
        "당신은 한국 증권사 애널리스트 리포트 분석 전문가입니다. "
        "리포트 데이터에서 종목 정보를 추출합니다. "
        "반드시 유효한 JSON 배열만 반환하세요. "
        "문자열 값 안에 줄바꿈, 큰따옴표를 넣지 마세요."
    )

    prompt = f"""아래 증권사 리포트 데이터에서 종목 정보를 추출하세요.

{items_text}

다음 JSON 형식으로 최대 20개 종목을 반환하세요:
[
  {{
    "stock_name": "삼성전자",
    "source_name": "삼성증권",
    "report_title": "리포트 제목",
    "target_price": "90000",
    "opinion": "매수",
    "analyst_category": "일반",
    "reasons": ["핵심 투자포인트 요약"]
  }}
]

규칙:
- stock_name은 리포트의 종목명 그대로
- source_name은 증권사명
- target_price는 숫자만 (없으면 빈 문자열)
- opinion은 "매수", "중립", "매도", "신규" 중 하나 (없으면 빈 문자열)
- analyst_category: 동시언급이면 "simultaneous", 신규커버리지면 "new_coverage", 일반이면 "general"
- reasons는 1~2개, 각 60자 이내
- JSON 배열만 반환"""

    try:
        resp = call_claude_with_retry(
            client=client, model=MODEL, max_tokens=2500,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        stocks = _parse_stocks_json(resp.content[0].text)

        # fallback 보완
        for s in stocks:
            s.setdefault("source_name", "")
            s.setdefault("target_price", "")
            s.setdefault("opinion", "")
            s.setdefault("analyst_category", "general")
            s.setdefault("reasons", [])

        return stocks
    except Exception as e:
        print(f"  [섹션3 분석 오류] {e}")
        return []


# ══════════════════════════════════════════════════════════════
#  투자전략
# ══════════════════════════════════════════════════════════════
def _generate_investment_strategy(
    client, s1: list, s2: list, s3: list, market_overview: dict
) -> str:
    # 공통 언급 종목 추출
    all_names = (
        [s.get("stock_name", "") for s in s1] +
        [s.get("stock_name", "") for s in s2] +
        [s.get("stock_name", "") for s in s3]
    )
    from collections import Counter
    common = [name for name, cnt in Counter(all_names).most_common(10) if cnt >= 2 and name]

    night_sig = market_overview.get("night_futures", {}).get("signal", "")
    direction = market_overview.get("night_futures", {}).get("direction", "neutral")

    stocks_text = "\n".join(
        f"- {s.get('stock_name','')} (섹션1: 미디어 언급)" for s in s1[:5]
    ) + "\n" + "\n".join(
        f"- {s.get('stock_name','')} (섹션2: TV 전문가 추천)" for s in s2[:5]
    ) + "\n" + "\n".join(
        f"- {s.get('stock_name','')} (섹션3: 리포트 {s.get('source_name','')})" for s in s3[:5]
    )

    common_text = ", ".join(common) if common else "없음"

    system = (
        "당신은 한국 주식시장 투자전략 전문가입니다. "
        "마크다운 없이 순수 텍스트로 500~700자의 투자전략을 작성하세요."
    )

    prompt = f"""오늘의 종합 투자전략을 작성해주세요.

[야간선물 신호] {night_sig} (방향: {direction})
[복수 채널 언급 종목] {common_text}
[주요 종목 목록]
{stocks_text}

미디어·증권TV·리포트 3개 채널에서 공통으로 언급된 종목을 중심으로 오늘의 투자전략을 500~700자로 작성하세요.
야간선물 신호를 반드시 반영하여 콜/풋 관점에서 매수·매도 전략을 제시하세요.
마크다운 기호 없이 순수 텍스트로 작성하세요."""

    try:
        resp = call_claude_with_retry(
            client=client, model=MODEL, max_tokens=800,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"  [투자전략 오류] {e}")
        return "투자전략 생성에 실패했습니다."


# ══════════════════════════════════════════════════════════════
#  가격 데이터 enrichment
# ══════════════════════════════════════════════════════════════
def _enrich_stocks_with_price(stocks: list, stock_map: dict) -> list:
    enriched = []
    for s in stocks:
        name = s.get("stock_name", "")
        if not name or name in SKIP_NAMES:
            enriched.append(s)
            continue
        code = stock_map.get(name)
        if code:
            try:
                info = get_stock_full_info(code)
                s["krx_code"]       = code
                s["verified_price"] = info.get("current_price")
                s["price_display"]  = info.get("price_display", "")
                s["price_trend"]    = info.get("history_summary", "")
            except Exception:
                pass
        enriched.append(s)
    return enriched


# ══════════════════════════════════════════════════════════════
#  아카이브 날짜 목록
# ══════════════════════════════════════════════════════════════
def _get_archive_dates() -> list:
    archive_dir = "docs/archive"
    if not os.path.isdir(archive_dir):
        return []
    dates = []
    for fname in sorted(os.listdir(archive_dir), reverse=True)[:30]:
        if fname.endswith(".html"):
            dates.append(fname.replace(".html", ""))
    return dates


# ══════════════════════════════════════════════════════════════
#  Fallback HTML
# ══════════════════════════════════════════════════════════════
def _generate_fallback_html() -> str:
    now = datetime.now(KST)
    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"><title>AI 주식 브리핑</title></head>
<body style="background:#1a1a2e;color:#e0e0e0;font-family:sans-serif;padding:40px;">
<h1>AI 주식 브리핑</h1>
<p>{now.strftime('%Y년 %m월 %d일')}</p>
<p>ANTHROPIC_API_KEY가 설정되지 않아 브리핑을 생성할 수 없습니다.</p>
</body>
</html>"""
