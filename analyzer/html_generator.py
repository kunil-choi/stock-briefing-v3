# analyzer/html_generator.py
"""
AI 주식 브리핑 HTML 생성 엔진

수정 이력:
- BUG-9    : _indicator_badge에서 0.0을 유효값으로 처리
- BUG-NEW-6: overlap_count를 channel_counts에서 재산출
- BUG-H5   : signal 필터 확대
- BUG-W-3  : reason 필드 렌더링 우선순위 (detail > reason > text)
- BUG-M6   : archive 절대경로 안전 처리
- CR-NEW-1 : chart_key 특수문자 안전 변환
- SIM-P5-1 : onclick 작은따옴표 이스케이프
- FIX-CSS-1: stock-card 종목명 누락 CSS 수정
- FIX-TV-1 : 경제방송TV 섹션 source_type 매칭 보완
- FIX-IND-1: 시장 지표 키 유연 탐색
- V2-CARD  : 종목 카드에 summary/catalyst/risk/channel_mentions 섹션 추가
- FIX-ANA-1: _build_analyst_html 들여쓰기 버그 수정 (_report_card 내부화)
- FIX-ARC-1: 사용하지 않는 archive_html 생성 블록 제거
- FIX-SIG-1: _is_positive_signal에서 "상승" 키워드 제거
- FIX-SIG-2: filtered_hidden signal 없을 때 오늘의 픽 전체 미표시 버그 수정
- FIX-JS-1 : showChart 방어코드 추가 (key 없을 때 빈 이미지 방지)
- FIX-ANA-2: analyst-card 제목 말줄임 제거, 웹에서 전체 표시
- FIX-HP-1 : 오늘의 픽 가중치 점수를 별점 5개로 시각화, signal 텍스트 제거
- FIX-RSN-1: reasons 목록에 source_name 표시 추가
- BUG-3    : signal 매핑 버그 수정 — 매수/강력매수/관망/매도 등 실제 값 정상 표시
- NIGHT-1  : 야간선물(KOSPI200/KOSDAQ150) 지표 추가 및 새벽시장 포인트 섹션 신규
- NIGHT-1B : 역외환율 표시 버그 수정 — 하드코딩 +0.4 제거, 등락 화살표 방향 동적 처리
- REM-TV-1 : 경제방송TV 섹션 제거 — 08:00 실행 기준 당일 데이터 수집 불가로 삭제
"""

import os
import re
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

KST         = timezone(timedelta(hours=9))
PARA_TITLES = ["📌 시장 개요", "📊 주요 이슈", "🔍 투자 포인트", "⚠️ 리스크 요인", "💡 전망"]

_HP_SOURCE_META = {
    "애널리스트":  {"color": "#51cf66", "icon": "📊", "label": "애널리스트"},
    "경제방송TV":  {"color": "#ffa94d", "icon": "📺", "label": "경제방송TV"},
    "경제방송":    {"color": "#74c0fc", "icon": "📡", "label": "경제방송"},
}
_HP_SOURCE_DEFAULT = {"color": "#adb5bd", "icon": "📌", "label": "단독 언급"}

_INDICATOR_DEFS = [
    ("전일 코스피",        ["kospi",          "KOSPI"]),
    ("전일 코스닥",        ["kosdaq",         "KOSDAQ"]),
    ("나스닥",             ["nasdaq",         "NASDAQ"]),
    ("S&P500",             ["sp500",          "SP500", "s&p500"]),
    ("다우존스",           ["dow",            "DOW",   "dow_jones"]),
    ("KOSPI200 야간선물",  ["kospi200_night", "kospi200night"]),
    ("KOSDAQ150 야간선물", ["kosdaq150_night","kosdaq150night"]),
    ("달러/원",            ["usd_krw",        "USD_KRW", "usd"]),
]

_TAG_META = {
    "뉴스":       {"bg": "#2d3a4a", "color": "#74c0fc"},
    "경제방송":   {"bg": "#3a2d1a", "color": "#ffa94d"},
    "경제방송TV": {"bg": "#3a2d1a", "color": "#ffa94d"},
    "유튜브":     {"bg": "#2d1a3a", "color": "#cc5de8"},
    "애널리스트": {"bg": "#1a3a2d", "color": "#51cf66"},
}

_SIGNAL_MAP = {
    ("강력매수",):                        ("signal-strong-buy",  "#ff4757", "강력매수"),
    ("매수", "buy", "긍정", "positive"):  ("signal-buy",         "#51cf66", "매수"),
    ("관망", "hold", "중립", "neutral"):  ("signal-neutral",     "#adb5bd", "관망"),
    ("매도", "sell", "부정", "negative"): ("signal-sell",        "#74c0fc", "매도"),
}
_SIGNAL_DEFAULT = ("signal-neutral", "#adb5bd", "중립")


def _resolve_signal(signal: str):
    if not signal:
        return _SIGNAL_DEFAULT
    sig_l = signal.strip().lower()
    for keywords, meta in _SIGNAL_MAP.items():
        if any(k in sig_l for k in keywords):
            return meta
    return _SIGNAL_DEFAULT


def _safe_chart_key(prefix: str, name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9가-힣]", "_", name)
    return f"{prefix}_{safe}"


def _safe_js_str(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _indicator_badge(label: str, value, pct, direction: str = "") -> str:
    if value is None:
        return ""
    try:
        pct_num = float(pct) if pct is not None else 0.0
    except (TypeError, ValueError):
        pct_num = 0.0
    if not direction:
        direction = "up" if pct_num > 0 else "down" if pct_num < 0 else "flat"
    color_map = {"up": "#ff6b6b", "down": "#74c0fc", "flat": "#adb5bd"}
    arrow_map = {"up": "▲",       "down": "▼",        "flat": "━"}
    try:
        val_str = (f"{float(value):,.2f}" if "." in str(value)
                   else f"{int(str(value).replace(',', '').replace(' ', '')):,}")
    except Exception:
        val_str = str(value)
    pct_str = f"{pct_num:+.2f}%"
    return (
        f'<div class="indicator-badge">'
        f'<span class="ind-label">{label}</span>'
        f'<span class="ind-value">{val_str}</span>'
        f'<span class="ind-pct" style="color:{color_map[direction]};">'
        f'{arrow_map[direction]} {pct_str}</span></div>'
    )


def _build_market_indicators(market_overview: dict) -> str:
    if not market_overview:
        return '<div class="market-indicators"><p style="color:#666;font-size:.85em;">시장 데이터 없음</p></div>'
    badges = ""
    for label, key_candidates in _INDICATOR_DEFS:
        item = None
        for key in key_candidates:
            item = market_overview.get(key)
            if item and isinstance(item, dict):
                break
        if not item or not isinstance(item, dict):
            continue
        value = (item.get("value") or item.get("close") or
                 item.get("price") or item.get("index"))
        pct   = (item.get("change_pct") or item.get("pct") or
                 item.get("percent")    or item.get("change_percent"))
        direction = item.get("direction", "")
        badge = _indicator_badge(label, value, pct, direction)
        if badge:
            badges += badge
    if not badges:
        return '<div class="market-indicators"><p style="color:#666;font-size:.85em;">시장 데이터 없음</p></div>'
    return f'<div class="market-indicators">{badges}</div>'


def _build_dawn_market_html(market_overview: dict) -> str:
    """
    야간선물(KOSPI200/KOSDAQ150)과 역외환율을 기반으로
    다음날 장 방향을 요약한 '새벽시장 포인트' 섹션을 생성한다.

    - 야간선물 데이터가 없으면 (거래 시간 외) 빈 문자열 반환 → 섹션 미표시
    - 등락률 기준으로 방향 판정: +0.3% 이상 → 콜 방향, -0.3% 이하 → 풋 방향, 그 외 → 중립
    - NIGHT-1B: 역외환율 표시에서 하드코딩 +0.4 제거, 화살표 방향 동적 처리
    """
    if not market_overview:
        return ""

    kospi_night  = market_overview.get("kospi200_night")
    kosdaq_night = market_overview.get("kosdaq150_night")
    usd_krw      = market_overview.get("usd_krw")

    if not kospi_night and not kosdaq_night:
        return ""

    def _direction_label(pct: float) -> tuple:
        if pct >= 0.3:
            return "상승 → 콜 방향",   "#ff6b6b", "▲"
        elif pct <= -0.3:
            return "하락 → 풋 방향",   "#74c0fc", "▼"
        else:
            return "보합 → 중립 방향", "#adb5bd", "━"

    rows = ""

    if kospi_night:
        pct   = float(kospi_night.get("change_pct", 0))
        val   = float(kospi_night.get("value", 0))
        label, color, arrow = _direction_label(pct)
        rows += (
            f'<div class="dawn-row">'
            f'<span class="dawn-icon">📈</span>'
            f'<span class="dawn-name">K야간선물(코스피)</span>'
            f'<span class="dawn-val" style="color:{color};">'
            f'{arrow} {val:,.2f} ({pct:+.2f}%) {label}</span>'
            f'</div>'
        )

    if kosdaq_night:
        pct   = float(kosdaq_night.get("change_pct", 0))
        val   = float(kosdaq_night.get("value", 0))
        label, color, arrow = _direction_label(pct)
        rows += (
            f'<div class="dawn-row">'
            f'<span class="dawn-icon">📈</span>'
            f'<span class="dawn-name">K야간선물(코스닥)</span>'
            f'<span class="dawn-val" style="color:{color};">'
            f'{arrow} {val:,.2f} ({pct:+.2f}%) {label}</span>'
            f'</div>'
        )

    if usd_krw:
        usd_val = float(usd_krw.get("value", 0))
        usd_pct = float(usd_krw.get("change_pct", 0))
        if usd_pct >= 0.1:
            usd_label = "원화 약세 (소폭 풋 방향)"
            usd_color = "#74c0fc"
            usd_arrow = "▲"
        elif usd_pct <= -0.1:
            usd_label = "원화 강세 (소폭 콜 방향)"
            usd_color = "#ff6b6b"
            usd_arrow = "▼"
        else:
            usd_label = "환율 안정"
            usd_color = "#adb5bd"
            usd_arrow = "━"
        rows += (
            f'<div class="dawn-row">'
            f'<span class="dawn-icon">💱</span>'
            f'<span class="dawn-name">역외환율</span>'
            f'<span class="dawn-val" style="color:{usd_color};">'
            f'{usd_arrow} {usd_val:,.2f}원 ({usd_pct:+.2f}%) {usd_label}</span>'
            f'</div>'
        )

    kospi_pct  = float(kospi_night.get("change_pct",  0)) if kospi_night  else 0.0
    kosdaq_pct = float(kosdaq_night.get("change_pct", 0)) if kosdaq_night else 0.0
    avg_pct = (kospi_pct + kosdaq_pct) / max(
        sum(1 for x in [kospi_night, kosdaq_night] if x), 1
    )
    if avg_pct >= 0.3:
        summary_color = "#ff6b6b"
        summary_text  = "콜 방향이며, 갭 상승 출발 예상됩니다."
    elif avg_pct <= -0.3:
        summary_color = "#74c0fc"
        summary_text  = "풋 방향이며, 갭 하락 출발 예상됩니다."
    else:
        summary_color = "#adb5bd"
        summary_text  = "보합권으로, 방향성 불분명합니다."

    return f"""
<div class="dawn-market-box">
  <div class="dawn-header">🌙 새벽시장 포인트!</div>
  {rows}
  <div class="dawn-summary">
    <span class="dawn-star">✦</span>
    내용대로라면
    <strong style="color:{summary_color};">{summary_text}</strong>
  </div>
</div>"""


def _render_market_summary(market_summary: str) -> str:
    if not market_summary or not market_summary.strip():
        return '<p style="color:#666;">시장 요약 데이터 없음</p>'
    paras = [p.strip() for p in re.split(r'\n\s*\n|\n(?=\d+\.)', market_summary.strip()) if p.strip()]
    html  = ""
    for i, para in enumerate(paras):
        clean = re.sub(r'^\d+\.\s*', '', para).strip()
        title = PARA_TITLES[i] if i < len(PARA_TITLES) else f"📎 포인트 {i + 1}"
        html += (
            f'<div class="summary-block">'
            f'<div class="summary-title">{title}</div>'
            f'<p class="summary-text">{clean}</p></div>'
        )
    return html or f'<p style="color:#ccc;">{market_summary.strip()}</p>'


def _build_analyst_html(all_data: list) -> str:
    def _report_card(r: dict) -> str:
        stock  = r.get("stock_name", "")
        title  = r.get("report_title") or r.get("title", "")
        broker = r.get("brokers") or r.get("source_name", "")
        link   = r.get("link", "")
        is_new = r.get("new_coverage", False)
        if not link and stock:
            enc  = stock.replace(" ", "+")
            link = (f"https://finance.naver.com/research/company_list.naver"
                    f"?searchType=keyword&keyword={enc}")
        new_badge = '<span class="new-coverage-badge">신규 커버리지</span>' if is_new else ""
        if link:
            title_html = (f'<a href="{link}" target="_blank" rel="noopener" '
                          f'class="analyst-title-link">{title}</a>')
        else:
            title_html = f'<span class="analyst-title-text">{title}</span>'
        return (
            f'<div class="analyst-card">'
            f'<div class="analyst-card-meta">'
            f'<span class="analyst-stock">{stock}</span>'
            f'<span class="analyst-broker">{broker}</span>'
            f'{new_badge}'
            f'</div>'
            f'<div class="analyst-card-title">{title_html}</div>'
            f'</div>'
        )

    analyst_items = [d for d in all_data if d.get("source_type") == "애널리스트"]
    if not analyst_items:
        return '<p style="color:#666;">애널리스트 리포트 데이터 없음</p>'

    simultaneous = [r for r in analyst_items if r.get("analyst_category") == "simultaneous"]
    new_cov      = [r for r in analyst_items if r.get("analyst_category") == "new_coverage"]
    single       = [r for r in analyst_items
                    if r.get("analyst_category") in ("single_broker", "first_in_6months")]

    html = ""
    if simultaneous:
        html += '<div class="analyst-category-title">🔥 복수 증권사 동시 언급</div>'
        for r in simultaneous[:10]:
            html += _report_card(r)
    if new_cov:
        html += '<div class="analyst-category-title">🆕 신규 커버리지 개시</div>'
        for r in new_cov[:10]:
            html += _report_card(r)
    if single:
        html += '<div class="analyst-category-title">📌 단독 언급</div>'
        for r in single[:10]:
            html += _report_card(r)
    return html or '<p style="color:#666;">분류된 리포트 없음</p>'


def _hidden_pick_source_badge(channel_type: str) -> str:
    meta = _HP_SOURCE_META.get(channel_type, _HP_SOURCE_DEFAULT)
    return (
        f'<span class="hp-source-badge" '
        f'style="background:{meta["color"]}22;color:{meta["color"]};'
        f'border:1px solid {meta["color"]}55;">'
        f'{meta["icon"]} {meta["label"]}</span>'
    )


def _render_star_rating(weighted_score, max_score: float = 5.0) -> str:
    try:
        score = float(weighted_score)
    except (TypeError, ValueError):
        score = 0.0
    filled = max(1, round((score / max_score) * 5)) if score > 0 else 0
    filled = min(filled, 5)
    empty  = 5 - filled
    stars  = (
        f'<span class="star filled">{"★" * filled}</span>'
        f'<span class="star empty">{"☆" * empty}</span>'
    )
    return f'<span class="star-rating">{stars}</span>'


def _render_reasons(reasons: list) -> str:
    if not reasons:
        return ""
    items = ""
    for r in reasons:
        if isinstance(r, str):
            rd, rl, rn, rt = r.strip(), "", "", ""
        elif isinstance(r, dict):
            rd = (r.get("detail") or r.get("reason") or
                  r.get("text") or r.get("summary", "")).strip()
            rl = r.get("source_url") or r.get("link") or r.get("url", "")
            rn = (r.get("source_name") or "").strip()
            rt = (r.get("source_type") or "").strip()
        else:
            continue
        if not rd:
            continue

        source_html = ""
        if rn:
            meta = _TAG_META.get(rt, {"bg": "#2d2d44", "color": "#adb5bd"})
            source_html = (
                f'<span class="reason-source" '
                f'style="background:{meta["bg"]};color:{meta["color"]};">'
                f'{rn}</span> '
            )

        if rl:
            text_html = (
                f'<a href="{rl}" target="_blank" rel="noopener" '
                f'style="color:#adb5bd;text-decoration:none;">{rd}</a>'
            )
        else:
            text_html = f'<span style="color:#adb5bd;">{rd}</span>'

        items += f'<li>{source_html}{text_html}</li>'

    return f'<ul class="reasons-list">{items}</ul>' if items else ""


def _is_positive_signal(sig) -> bool:
    if not sig:
        return False
    sig_l = str(sig).lower()
    return any(k in sig_l for k in ("긍정", "매수", "강력", "positive", "buy"))


def _render_stock_detail(stock: dict) -> str:
    html = ""

    summary = (stock.get("summary") or stock.get("description") or "").strip()
    if summary:
        html += (
            f'<div class="stock-section">'
            f'<span class="stock-section-label">📋 종목 요약</span>'
            f'<p class="stock-section-text">{summary}</p></div>'
        )

    catalyst = (stock.get("catalyst") or stock.get("price_trend") or "").strip()
    if catalyst:
        html += (
            f'<div class="stock-section">'
            f'<span class="stock-section-label">🚀 상승 촉매</span>'
            f'<p class="stock-section-text">{catalyst}</p></div>'
        )

    risk = (stock.get("risk") or "").strip()
    if risk:
        html += (
            f'<div class="stock-section">'
            f'<span class="stock-section-label">⚠️ 리스크</span>'
            f'<p class="stock-section-text">{risk}</p></div>'
        )

    cm_list = stock.get("channel_mentions", [])
    if cm_list:
        cm_items = ""
        for cm in cm_list:
            stype   = cm.get("source_type", "")
            sname   = cm.get("source_name", "")
            content = cm.get("content", "")
            url     = cm.get("url", "")
            meta    = _TAG_META.get(stype, {"bg": "#2d2d44", "color": "#adb5bd"})
            name_html = (
                f'<span style="color:{meta["color"]};font-weight:600;">'
                f'{sname}</span>'
            )
            if url:
                text_html = (
                    f'<a href="{url}" target="_blank" rel="noopener" '
                    f'style="color:#adb5bd;text-decoration:none;">'
                    f'{content}</a>'
                )
            else:
                text_html = f'<span style="color:#8b949e;">{content}</span>'
            cm_items += f'<li>{name_html} {text_html}</li>'
        html += (
            f'<div class="stock-section">'
            f'<span class="stock-section-label">📢 채널별 언급 내용</span>'
            f'<ul class="reasons-list">{cm_items}</ul></div>'
        )

    return html


def generate_html(
    data,
    channels_data=None,
    gh_repo="",
    gh_token="",
    market_overview=None,
    all_data=None,
) -> str:
    data            = data or {}
    market_overview = market_overview or {}
    all_data        = all_data or []

    stocks        = data.get("stocks",        [])
    hidden_picks  = data.get("hidden_picks",  [])
    market_sum    = data.get("market_summary", "")
    hot_sectors   = data.get("hot_sectors",   [])
    ai_strategy   = data.get("ai_strategy",   "")
    briefing_date = data.get("briefing_date",  "")

    now_kst = datetime.now(KST)
    if not briefing_date:
        briefing_date = now_kst.strftime("%Y년 %m월 %d일")
    briefing_time = now_kst.strftime("%H:%M")

    for stock in stocks:
        cc = stock.get("channel_counts", {})
        if cc:
            stock["overlap_count"] = sum(1 for v in cc.values() if v and int(v) > 0)

    filtered_stocks = [s for s in stocks if s.get("overlap_count", 0) >= 2]

    filtered_hidden = [
        h for h in hidden_picks
        if (not h.get("signal"))
        or _is_positive_signal(h.get("signal"))
        or str(h.get("signal", "")).strip().lower() in ("positive", "긍정", "")
    ]

    market_indicators_html = _build_market_indicators(market_overview)
    market_summary_html    = _render_market_summary(market_sum)
    dawn_market_html       = _build_dawn_market_html(market_overview)

    sector_badges_html = ""
    for sector in hot_sectors:
        if isinstance(sector, dict):
            sector_badges_html += (
                f'<div class="sector-badge" title="{sector.get("reason", "")}">'
                f'{sector.get("name", "")}</div>'
            )
        elif sector:
            sector_badges_html += f'<div class="sector-badge">{sector}</div>'

    chart_data_entries = []
    stocks_html        = ""

    for rank, stock in enumerate(filtered_stocks, 1):
        name         = stock.get("name", "")
        signal       = stock.get("signal", "")
        overlap      = stock.get("overlap_count", 0)
        channel_cnts = stock.get("channel_counts", {})
        price        = stock.get("verified_price")
        naver_code   = stock.get("naver_code") or stock.get("code", "")
        naver_url    = stock.get("naver_url", "")
        chart_b64    = stock.get("chart_base64", "")
        reasons      = stock.get("reasons", [])

        if not naver_url:
            if naver_code:
                naver_url = f"https://finance.naver.com/item/main.naver?code={naver_code}"
            elif name:
                naver_url = (f"https://finance.naver.com/search/searchResult.naver"
                             f"?query={name.replace(' ', '+')}")

        sig_class, sig_color, signal_label = _resolve_signal(signal)

        source_tags_html = ""
        for src_type, cnt in channel_cnts.items():
            if cnt and int(cnt) > 0:
                meta = _TAG_META.get(src_type, {"bg": "#2d2d44", "color": "#adb5bd"})
                source_tags_html += (
                    f'<span class="source-tag" '
                    f'style="background:{meta["bg"]};color:{meta["color"]};">'
                    f'{src_type} {cnt}</span>'
                )

        if isinstance(price, int):
            price_html = f'<span class="price-value">{price:,}원</span>'
        elif price and str(price).strip() not in ("None", "N/A", ""):
            price_html = f'<span class="price-value">{price}</span>'
        else:
            price_html = '<span class="price-value" style="color:#666;">가격 조회 중</span>'

        if chart_b64:
            chart_key    = _safe_chart_key("chart", name)
            safe_name_js = _safe_js_str(name)
            chart_data_entries.append(f'"{chart_key}": "data:image/png;base64,{chart_b64}"')
            chart_btn_html = (
                f"<button class=\"chart-btn\" "
                f"onclick=\"showChart('{chart_key}','{safe_name_js}')\">📈 차트 보기</button>"
            )
        elif naver_url:
            chart_btn_html = (
                f'<a href="{naver_url}" target="_blank" rel="noopener" '
                f'class="chart-btn">🔗 Naver 차트</a>'
            )
        else:
            chart_btn_html = ""

        detail_html   = _render_stock_detail(stock)
        reasons_block = _render_reasons(reasons)

        stocks_html += f"""
<div class="stock-card">
  <div class="stock-card-header">
    <div class="stock-rank">#{rank}</div>
    <div class="stock-name-block">
      <a href="{naver_url}" target="_blank" rel="noopener" class="stock-name">{name}</a>
      <span class="signal-badge {sig_class}" style="border-color:{sig_color};color:{sig_color};">{signal_label}</span>
    </div>
    <div class="overlap-badge" title="채널 중복 언급 수">🔥 {overlap}개</div>
  </div>
  <div class="stock-card-body">
    <div class="source-tags">{source_tags_html}</div>
    <div class="price-row">{price_html}{chart_btn_html}</div>
    {detail_html}
    {reasons_block}
  </div>
</div>"""

    if not stocks_html:
        stocks_html = ('<p style="color:#666;text-align:center;padding:2rem;">'
                       '오늘은 복수 채널 교차 언급 종목이 없습니다.</p>')

    hidden_html = ""
    for idx, hp in enumerate(filtered_hidden, 1):
        name         = hp.get("name", "")
        channel_type = hp.get("channel_type", "")
        weighted_sc  = hp.get("weighted_score", 0)
        price        = hp.get("verified_price")
        naver_code   = hp.get("naver_code") or hp.get("code", "")
        naver_url    = hp.get("naver_url", "")
        chart_b64    = hp.get("chart_base64", "")
        reasons      = hp.get("reasons", [])

        if not naver_url:
            if naver_code:
                naver_url = f"https://finance.naver.com/item/main.naver?code={naver_code}"
            elif name:
                naver_url = (f"https://finance.naver.com/search/searchResult.naver"
                             f"?query={name.replace(' ', '+')}")

        source_badge_html = _hidden_pick_source_badge(channel_type)
        star_html         = _render_star_rating(weighted_sc)
        pick_badge_html   = f'<span class="hp-score-badge">Pick #{idx}</span>'

        if isinstance(price, int):
            price_html = f'<span class="price-value">{price:,}원</span>'
        elif price and str(price).strip() not in ("None", "N/A", ""):
            price_html = f'<span class="price-value">{price}</span>'
        else:
            price_html = '<span class="price-value" style="color:#666;">가격 조회 중</span>'

        if chart_b64:
            chart_key    = _safe_chart_key("hpchart", name)
            safe_name_js = _safe_js_str(name)
            chart_data_entries.append(f'"{chart_key}": "data:image/png;base64,{chart_b64}"')
            chart_btn_html = (
                f"<button class=\"chart-btn\" "
                f"onclick=\"showChart('{chart_key}','{safe_name_js}')\">📈 차트 보기</button>"
            )
        elif naver_url:
            chart_btn_html = (
                f'<a href="{naver_url}" target="_blank" rel="noopener" '
                f'class="chart-btn">🔗 Naver 차트</a>'
            )
        else:
            chart_btn_html = ""

        detail_html   = _render_stock_detail(hp)
        reasons_block = _render_reasons(reasons)

        hidden_html += f"""
<div class="hidden-pick-card">
  <div class="hp-card-header">
    <div class="hp-badges">{source_badge_html}{pick_badge_html}</div>
    <a href="{naver_url}" target="_blank" rel="noopener" class="hp-stock-name">{name}</a>
    {star_html}
  </div>
  <div class="hp-card-body">
    <div class="price-row">{price_html}{chart_btn_html}</div>
    {detail_html}
    {reasons_block}
  </div>
</div>"""

    if not hidden_html:
        hidden_html = ('<p style="color:#666;text-align:center;padding:1.5rem;">'
                       '오늘의 픽 없음</p>')

    if chart_data_entries:
        chart_data_js = ("const chartDataMap = {\n  "
                         + ",\n  ".join(chart_data_entries) + "\n};")
    else:
        chart_data_js = "const chartDataMap = {};"

    analyst_html = _build_analyst_html(all_data)
    # REM-TV-1: tv_html 제거

    css = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:        #0d1117;
  --surface:   #161b22;
  --surface2:  #21262d;
  --border:    #30363d;
  --text:      #e6edf3;
  --text-muted:#8b949e;
  --accent:    #58a6ff;
  --up:        #ff6b6b;
  --down:      #74c0fc;
  --flat:      #adb5bd;
}
html { font-size: 16px; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Pretendard', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
  line-height: 1.6;
  padding: 0 0 4rem;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.container { max-width: 900px; margin: 0 auto; padding: 0 1rem; }
.briefing-header {
  text-align: center;
  padding: 2.5rem 1rem 1.5rem;
  border-bottom: 1px solid var(--border);
  margin-bottom: 2rem;
}
.briefing-header h1 { font-size: 1.8rem; font-weight: 700; }
.subtitle { color: var(--text-muted); font-size: .9rem; margin-top: .4rem; }
.section { margin-bottom: 2.5rem; }
.section-title {
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--text);
  border-left: 4px solid var(--accent);
  padding-left: .75rem;
  margin-bottom: 1rem;
}
.market-indicators { display: flex; flex-wrap: wrap; gap: .6rem; }
.indicator-badge {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: .5rem .9rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 100px;
}
.ind-label { font-size: .75rem; color: var(--text-muted); margin-bottom: .15rem; }
.ind-value { font-size: .95rem; font-weight: 600; }
.ind-pct   { font-size: .8rem;  margin-top: .1rem; }
.dawn-market-box {
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
  border: 1px solid #2d4a6e;
  border-radius: 12px;
  padding: 1.2rem 1.4rem;
  margin-bottom: 1.5rem;
}
.dawn-header {
  font-size: 1.05rem;
  font-weight: 700;
  color: #74c0fc;
  margin-bottom: .85rem;
  letter-spacing: .02em;
}
.dawn-row {
  display: flex;
  align-items: baseline;
  gap: .6rem;
  margin-bottom: .5rem;
  flex-wrap: wrap;
}
.dawn-icon { font-size: .9rem; flex-shrink: 0; }
.dawn-name {
  font-size: .9rem;
  color: var(--text-muted);
  min-width: 160px;
  flex-shrink: 0;
}
.dawn-val  { font-size: .9rem; font-weight: 600; }
.dawn-summary {
  margin-top: .85rem;
  padding-top: .75rem;
  border-top: 1px solid #2d4a6e;
  font-size: .88rem;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: .4rem;
  flex-wrap: wrap;
}
.dawn-star { color: #ffd43b; font-size: .9rem; }
.summary-block {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1rem 1.2rem;
  margin-bottom: .75rem;
}
.summary-title { font-weight: 700; margin-bottom: .4rem; color: var(--accent); }
.summary-text  { color: var(--text-muted); font-size: .95rem; }
.sector-list  { display: flex; flex-wrap: wrap; gap: .5rem; }
.sector-badge {
  background: #1c2d3a;
  color: #74c0fc;
  border: 1px solid #1e4a6e;
  border-radius: 20px;
  padding: .3rem .85rem;
  font-size: .85rem;
  font-weight: 600;
}
.stock-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1rem 1.2rem;
  margin-bottom: 1rem;
  transition: border-color .2s;
}
.stock-card:hover { border-color: var(--accent); }
.stock-card-header {
  display: flex;
  align-items: center;
  gap: .75rem;
  margin-bottom: .75rem;
  flex-wrap: wrap;
}
.stock-rank {
  font-size: .85rem;
  font-weight: 700;
  color: var(--text-muted);
  min-width: 2rem;
  flex-shrink: 0;
}
.stock-name-block {
  display: flex;
  align-items: center;
  gap: .5rem;
  flex: 1 1 auto;
  min-width: 0;
}
.stock-name {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.stock-name:hover { color: var(--accent); }
.signal-badge {
  font-size: .75rem;
  border: 1px solid;
  border-radius: 12px;
  padding: .15rem .55rem;
  white-space: nowrap;
  flex-shrink: 0;
}
.overlap-badge {
  font-size: .78rem;
  color: #ffa94d;
  background: #3a2d1a;
  border-radius: 12px;
  padding: .15rem .55rem;
  white-space: nowrap;
}
.stock-card-body { display: flex; flex-direction: column; gap: .6rem; }
.source-tags { display: flex; flex-wrap: wrap; gap: .4rem; }
.source-tag {
  font-size: .75rem;
  border-radius: 10px;
  padding: .1rem .5rem;
}
.price-row {
  display: flex;
  align-items: center;
  gap: .75rem;
  flex-wrap: wrap;
}
.price-value { font-size: 1rem; font-weight: 600; color: var(--text); }
.chart-btn {
  font-size: .8rem;
  padding: .25rem .7rem;
  border-radius: 6px;
  background: var(--surface2);
  border: 1px solid var(--border);
  color: var(--accent);
  cursor: pointer;
  text-decoration: none;
}
.chart-btn:hover { background: #2d3a4a; }
.stock-section { margin-top: .5rem; }
.stock-section-label {
  font-size: .78rem;
  font-weight: 700;
  color: var(--text-muted);
  display: block;
  margin-bottom: .2rem;
}
.stock-section-text { font-size: .88rem; color: var(--text-muted); }
.reasons-list {
  list-style: none;
  margin: .4rem 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: .3rem;
}
.reasons-list li { font-size: .85rem; color: var(--text-muted); }
.reason-source {
  font-size: .72rem;
  border-radius: 8px;
  padding: .1rem .4rem;
  margin-right: .3rem;
}
.hidden-pick-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1rem 1.2rem;
  margin-bottom: 1rem;
  transition: border-color .2s;
}
.hidden-pick-card:hover { border-color: #51cf66; }
.hp-card-header {
  display: flex;
  align-items: center;
  gap: .6rem;
  margin-bottom: .75rem;
  flex-wrap: wrap;
}
.hp-badges { display: flex; gap: .4rem; flex-wrap: wrap; }
.hp-source-badge {
  font-size: .72rem;
  border-radius: 10px;
  padding: .15rem .5rem;
}
.hp-score-badge {
  font-size: .72rem;
  background: #1a2a1a;
  color: #51cf66;
  border: 1px solid #2a4a2a;
  border-radius: 10px;
  padding: .15rem .5rem;
}
.hp-stock-name {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--text);
  flex: 1 1 auto;
}
.hp-stock-name:hover { color: #51cf66; }
.hp-card-body { display: flex; flex-direction: column; gap: .6rem; }
.star-rating { font-size: 1rem; }
.star.filled { color: #ffd43b; }
.star.empty  { color: #444; }
.analyst-category-title {
  font-size: .9rem;
  font-weight: 700;
  color: var(--text-muted);
  margin: 1.2rem 0 .5rem;
  padding-left: .5rem;
  border-left: 3px solid var(--accent);
}
.analyst-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: .75rem 1rem;
  margin-bottom: .5rem;
}
.analyst-card-meta {
  display: flex;
  align-items: center;
  gap: .5rem;
  flex-wrap: wrap;
  margin-bottom: .3rem;
}
.analyst-stock { font-weight: 700; color: var(--text); font-size: .9rem; }
.analyst-broker { font-size: .78rem; color: var(--text-muted); }
.new-coverage-badge {
  font-size: .7rem;
  background: #1a3a2d;
  color: #51cf66;
  border: 1px solid #2a5a3d;
  border-radius: 8px;
  padding: .1rem .4rem;
}
.analyst-card-title { font-size: .85rem; }
.analyst-title-link { color: var(--text-muted); }
.analyst-title-link:hover { color: var(--accent); }
.analyst-title-text { color: var(--text-muted); }
.modal-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,.8);
  z-index: 1000;
  align-items: center;
  justify-content: center;
}
.modal-overlay.active { display: flex; }
.modal-box {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem;
  max-width: 680px;
  width: 95%;
  position: relative;
}
.modal-title { font-weight: 700; margin-bottom: 1rem; }
.modal-img { width: 100%; border-radius: 8px; }
.modal-close {
  position: absolute;
  top: .75rem;
  right: 1rem;
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 1.4rem;
  cursor: pointer;
  line-height: 1;
}
@media (max-width: 600px) {
  .briefing-header h1 { font-size: 1.4rem; }
  .stock-card-header { gap: .5rem; }
  .dawn-name { min-width: 130px; }
}"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 주식 브리핑 — {briefing_date}</title>
<style>{css}</style>
</head>
<body>
<div class="container">

  <header class="briefing-header">
    <h1>📊 AI 주식 브리핑</h1>
    <p class="subtitle">{briefing_date} {briefing_time} 기준 · 자동 생성</p>
  </header>

  <!-- 시장 지표 -->
  <section class="section">
    <div class="section-title">📈 시장 지표</div>
    {market_indicators_html}
    {dawn_market_html}
  </section>

  <!-- 시장 요약 -->
  <section class="section">
    <div class="section-title">📋 시장 요약</div>
    {market_summary_html}
  </section>

  <!-- 핫 섹터 -->
  <section class="section">
    <div class="section-title">🔥 핫 섹터</div>
    <div class="sector-list">{sector_badges_html or '<p style="color:#666;">섹터 데이터 없음</p>'}</div>
  </section>

  <!-- 관심 종목 -->
  <section class="section">
    <div class="section-title">👀 관심 종목 (복수 채널 교차 언급)</div>
    {stocks_html}
  </section>

  <!-- 오늘의 픽 -->
  <section class="section">
    <div class="section-title">⭐ 오늘의 픽</div>
    {hidden_html}
  </section>

  <!-- 애널리스트 리포트 -->
  <section class="section">
    <div class="section-title">📑 애널리스트 리포트</div>
    {analyst_html}
  </section>

  <!-- AI 전략 -->
  <section class="section">
    <div class="section-title">🤖 AI 투자 전략</div>
    <div class="summary-block">
      <p class="summary-text">{ai_strategy or "AI 전략 데이터 없음"}</p>
    </div>
  </section>

</div>

<!-- 차트 모달 -->
<div class="modal-overlay" id="chartModal">
  <div class="modal-box">
    <button class="modal-close" onclick="closeChart()">✕</button>
    <div class="modal-title" id="modalTitle"></div>
    <img class="modal-img" id="modalImg" src="" alt="차트">
  </div>
</div>

<script>
{chart_data_js}

function showChart(key, name) {{
  const src = chartDataMap[key];
  if (!src) return;
  document.getElementById('modalTitle').textContent = name + ' 차트';
  document.getElementById('modalImg').src = src;
  document.getElementById('chartModal').classList.add('active');
}}

function closeChart() {{
  document.getElementById('chartModal').classList.remove('active');
  document.getElementById('modalImg').src = '';
}}

document.getElementById('chartModal').addEventListener('click', function(e) {{
  if (e.target === this) closeChart();
}});
</script>
</body>
</html>"""
