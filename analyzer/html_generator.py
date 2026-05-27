# analyzer/html_generator.py
"""
AI 주식 브리핑 HTML 생성 엔진

수정 이력:
- BUG-9     : _indicator_badge에서 0.0을 유효한 숫자로 처리
- BUG-NEW-6 : overlap_count를 channel_counts에서 재산출
- BUG-H5    : hidden_picks signal 필터 확장 (긍정/매수/강력/상승/positive/buy)
- BUG-W-3   : reason 렌더링 시 detail → reason → text 우선순위 적용
- BUG-M6    : archive 경로를 __file__ 기준 절대경로로 계산
- HP-BADGE  : hidden_pick 소스 타입별 배지 표시
- CR-NEW-1  : chart_key 종목명 특수문자 → JS 문법 오류 방지
- SIM-P5-1  : onclick name 작은따옴표 이스케이프 처리
- BUG-AC-12 : analyst_category single_broker / first_in_6months 양쪽 허용

섹션 순서:
  시장 지표 → 시장 요약 → 주목 섹터 → 관심 종목 → 오늘의 픽
  → 애널리스트 리포트 분석 → 경제방송TV 추천 → AI 투자 전략 → 아카이브
"""

import os
import re
from datetime import datetime, timedelta, timezone

# ── 상수 ──────────────────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))

PARA_TITLES = ["📌 시장 개요", "📊 주요 이슈", "🔍 투자 포인트", "⚠️ 리스크 요인", "💡 전망"]

_HP_SOURCE_META = {
    "애널리스트": {"color": "#51cf66", "icon": "📊", "label": "애널리스트"},
    "경제방송TV": {"color": "#ffa94d", "icon": "📺", "label": "경제방송TV"},
    "경제방송":   {"color": "#74c0fc", "icon": "📡", "label": "경제방송"},
}
_HP_SOURCE_DEFAULT = {"color": "#adb5bd", "icon": "📌", "label": "단독 언급"}

_INDICATOR_ORDER = [
    ("전일 코스피", "kospi"),
    ("전일 코스닥", "kosdaq"),
    ("나스닥",      "nasdaq"),
    ("S&P500",      "sp500"),
    ("다우존스",    "dow"),
    ("야간선물",    "night_future"),
    ("달러/원",     "usd_krw"),
]

_TAG_META = {
    "뉴스":       {"bg": "#2d3a4a", "color": "#74c0fc"},
    "경제방송":   {"bg": "#3a2d1a", "color": "#ffa94d"},
    "경제방송TV": {"bg": "#3a2d1a", "color": "#ffa94d"},
    "유튜브":     {"bg": "#2d1a3a", "color": "#cc5de8"},
    "애널리스트": {"bg": "#1a3a2d", "color": "#51cf66"},
}


# ─────────────────────────────────────────────────────────────────────────────
# 내부 헬퍼 함수들
# ─────────────────────────────────────────────────────────────────────────────

def _indicator_badge(label: str, value, pct, direction: str = "") -> str:
    """
    시장 지표 배지 HTML 한 개 생성.
    BUG-9: pct == 0.0 도 유효한 숫자로 처리.
    """
    if value is None:
        return ""

    if pct is None:
        pct_num = 0.0
    else:
        try:
            pct_num = float(pct)
        except (TypeError, ValueError):
            pct_num = 0.0

    if not direction:
        if pct_num > 0:
            direction = "up"
        elif pct_num < 0:
            direction = "down"
        else:
            direction = "flat"

    color_map = {"up": "#ff6b6b", "down": "#74c0fc", "flat": "#adb5bd"}
    arrow_map  = {"up": "▲",      "down": "▼",       "flat": "━"}
    color = color_map.get(direction, "#adb5bd")
    arrow = arrow_map.get(direction, "━")

    val_str = (
        f"{value:,.2f}" if isinstance(value, float)
        else f"{value:,}" if isinstance(value, int)
        else str(value)
    )
    pct_str = f"{pct_num:+.2f}%"

    return (
        f'<div class="indicator-badge">'
        f'<span class="ind-label">{label}</span>'
        f'<span class="ind-value">{val_str}</span>'
        f'<span class="ind-pct" style="color:{color};">{arrow} {pct_str}</span>'
        f'</div>'
    )


def _build_market_indicators(market_overview: dict) -> str:
    """시장 인디케이터 배지 행 HTML 생성."""
    if not market_overview:
        return (
            '<div class="market-indicators">'
            '<p style="color:#666;font-size:.85em;">시장 데이터 없음</p>'
            '</div>'
        )

    badges_html = ""
    for label, key in _INDICATOR_ORDER:
        item = market_overview.get(key)
        if not item or not isinstance(item, dict):
            continue
        value     = item.get("value") or item.get("close") or item.get("price")
        pct       = item.get("change_pct") or item.get("pct") or item.get("percent")
        direction = item.get("direction", "")
        badge = _indicator_badge(label, value, pct, direction)
        if badge:
            badges_html += badge

    if not badges_html:
        return (
            '<div class="market-indicators">'
            '<p style="color:#666;font-size:.85em;">시장 데이터 없음</p>'
            '</div>'
        )
    return f'<div class="market-indicators">{badges_html}</div>'


def _render_market_summary(market_summary: str) -> str:
    """시장 요약 텍스트를 단락별 카드 HTML로 변환."""
    if not market_summary or not market_summary.strip():
        return '<p style="color:#666;">시장 요약 데이터 없음</p>'

    raw_paras = re.split(r'\n\s*\n|\n(?=\d+\.)', market_summary.strip())
    paras = [p.strip() for p in raw_paras if p.strip()]

    if not paras:
        return f'<p style="color:#ccc;">{market_summary.strip()}</p>'

    html = ""
    for i, para in enumerate(paras):
        clean = re.sub(r'^\d+\.\s*', '', para).strip()
        if not clean:
            continue
        title = PARA_TITLES[i] if i < len(PARA_TITLES) else f"📎 포인트 {i + 1}"
        html += (
            f'<div class="summary-block">'
            f'<div class="summary-title">{title}</div>'
            f'<p class="summary-text">{clean}</p>'
            f'</div>'
        )
    return html or f'<p style="color:#ccc;">{market_summary.strip()}</p>'


def _build_section2_html(all_data: list) -> str:
    """경제방송TV 전문가 카드 렌더링. 중복 제목 제거, 최대 20개."""
    if not all_data:
        return '<p style="color:#666;">경제방송 데이터 없음</p>'

    items = [d for d in all_data
             if d.get("source_type") in ("경제방송TV", "경제방송")]
    if not items:
        return '<p style="color:#666;">경제방송 데이터 없음</p>'

    seen_titles = set()
    cards_html  = ""

    for item in items[:20]:
        title = (item.get("title") or "").strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)

        channel = item.get("source_name", "")
        date    = item.get("date", "")
        link    = item.get("link") or item.get("url", "")
        stock   = item.get("stock_name", "")

        title_html = (
            f'<a href="{link}" target="_blank" rel="noopener" '
            f'style="color:#74c0fc;text-decoration:none;">{title}</a>'
            if link else f'<span>{title}</span>'
        )
        stock_badge = (
            f'<span class="source-tag" style="background:#2d4a6b;">{stock}</span>'
            if stock else ""
        )

        cards_html += (
            f'<div class="tv-card">'
            f'<div class="tv-card-header">'
            f'{stock_badge}'
            f'<span class="tv-channel">{channel}</span>'
            f'<span class="tv-date">{date}</span>'
            f'</div>'
            f'<div class="tv-card-title">{title_html}</div>'
            f'</div>'
        )

    return cards_html or '<p style="color:#666;">경제방송 데이터 없음</p>'


def _build_analyst_html(all_data: list) -> str:
    """
    애널리스트 리포트 세 범주 분류 렌더링.
    BUG-AC-12: single_broker / first_in_6months 두 값 모두 허용.
    """
    if not all_data:
        return '<p style="color:#666;">애널리스트 리포트 데이터 없음</p>'

    analyst_items = [d for d in all_data if d.get("source_type") == "애널리스트"]
    if not analyst_items:
        return '<p style="color:#666;">애널리스트 리포트 데이터 없음</p>'

    simultaneous = [r for r in analyst_items
                    if r.get("analyst_category") == "simultaneous"]
    new_cov      = [r for r in analyst_items
                    if r.get("analyst_category") == "new_coverage"]
    single       = [r for r in analyst_items
                    if r.get("analyst_category") in
                    ("single_broker", "first_in_6months")]

    def _report_card(r: dict) -> str:
        stock   = r.get("stock_name", "")
        title   = r.get("report_title") or r.get("title", "")
        broker  = r.get("brokers") or r.get("source_name", "")
        summary = r.get("summary", "")
        link    = r.get("link", "")
        is_new  = r.get("new_coverage", False)

        if not link and stock:
            encoded = stock.replace(" ", "+")
            link = (
                "https://finance.naver.com/research/company_list.naver"
                f"?searchType=keyword&keyword={encoded}"
            )

        new_badge = (
            '<span class="new-coverage-badge">신규 커버리지</span>'
            if is_new else ""
        )
        title_html = (
            f'<a href="{link}" target="_blank" rel="noopener" '
            f'style="color:#74c0fc;text-decoration:none;">{title}</a>'
            if link else f'<span>{title}</span>'
        )
        summary_html = (
            f'<p class="analyst-summary">{summary}</p>' if summary else ""
        )
        return (
            f'<div class="analyst-card">'
            f'<div class="analyst-card-header">'
            f'<span class="analyst-stock">{stock}</span>'
            f'{new_badge}'
            f'<span class="analyst-broker">{broker}</span>'
            f'</div>'
            f'<div class="analyst-title">{title_html}</div>'
            f'{summary_html}'
            f'</div>'
        )

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
    """hidden_pick 소스 타입에 따른 배지 HTML 반환."""
    meta = _HP_SOURCE_META.get(channel_type, _HP_SOURCE_DEFAULT)
    return (
        f'<span class="hp-source-badge" '
        f'style="background:{meta["color"]}22;color:{meta["color"]};'
        f'border:1px solid {meta["color"]}55;">'
        f'{meta["icon"]} {meta["label"]}'
        f'</span>'
    )


def _safe_chart_key(prefix: str, name: str) -> str:
    """
    CR-NEW-1: 종목명 특수문자를 '_'로 치환해 JS 키로 안전한 문자열 반환.
    예) 'SK하이닉스' → 'chart_SK하이닉스'
        'A"B'        → 'chart_A_B'
    """
    safe = re.sub(r'[^a-zA-Z0-9가-힣]', '_', name)
    return f"{prefix}_{safe}"


def _safe_js_str(s: str) -> str:
    """
    SIM-P5-1: JS 문자열 내 작은따옴표 이스케이프.
    onclick="showChart('key','name')" 에서 name에 ' 포함 시 구문 오류 방지.
    """
    return s.replace("'", "\\'")


def _render_reasons(reasons: list) -> str:
    """
    사유 목록을 <ul> HTML로 변환.
    BUG-W-3: detail → reason → text 우선순위 적용.
    """
    if not reasons:
        return ""

    items_html = ""
    for reason in reasons:
        if isinstance(reason, str):
            rd, rl = reason.strip(), ""
        elif isinstance(reason, dict):
            rd = (
                reason.get("detail")
                or reason.get("reason")
                or reason.get("text", "")
            )
            rd = (rd or "").strip()
            rl = reason.get("link") or reason.get("url", "")
        else:
            continue

        if not rd:
            continue

        if rl:
            items_html += (
                f'<li><a href="{rl}" target="_blank" rel="noopener" '
                f'style="color:#adb5bd;text-decoration:underline dotted;">'
                f'{rd}</a></li>'
            )
        else:
            items_html += f'<li>{rd}</li>'

    return f'<ul class="reasons-list">{items_html}</ul>' if items_html else ""


# ─────────────────────────────────────────────────────────────────────────────
# 메인 HTML 생성 함수
# ─────────────────────────────────────────────────────────────────────────────

def generate_html(
    data: dict,
    channels_data: dict = None,
    gh_repo: str = "",
    gh_token: str = "",
    market_overview: dict = None,
    all_data: list = None,
) -> str:
    """
    브리핑 데이터를 받아 완성된 HTML 문서 문자열을 반환합니다.

    Args:
        data           : AI 분석 결과 dict
        channels_data  : channels.json 내용 (확장용)
        gh_repo        : "owner/repo" 형식 GitHub 저장소명
        gh_token       : GitHub token (확장용)
        market_overview: 시장 지표 dict
        all_data       : 원본 수집 데이터 list
    """
    # ── 기본값 처리 ───────────────────────────────────────────────────────────
    data            = data            or {}
    market_overview = market_overview or {}
    all_data        = all_data        or []

    stocks        = data.get("stocks",       [])
    hidden_picks  = data.get("hidden_picks", [])
    market_sum    = data.get("market_summary", "")
    hot_sectors   = data.get("hot_sectors",  [])
    ai_strategy   = data.get("ai_strategy",  "")
    briefing_date = data.get("briefing_date", "")

    now_kst = datetime.now(KST)
    if not briefing_date:
        briefing_date = now_kst.strftime("%Y년 %m월 %d일")
    briefing_time = now_kst.strftime("%H:%M")

    # ── BUG-NEW-6: overlap_count 재산출 ──────────────────────────────────────
    for stock in stocks:
        cc = stock.get("channel_counts", {})
        if cc:
            stock["overlap_count"] = sum(1 for v in cc.values() if v > 0)

    # ── overlap_count >= 2 필터 ───────────────────────────────────────────────
    filtered_stocks = [s for s in stocks if s.get("overlap_count", 0) >= 2]

    # ── BUG-H5 + W-NEW-1: signal 필터 ────────────────────────────────────────
    def _is_positive_signal(sig) -> bool:
        if not sig:
            return False
        sig_lower = str(sig).lower()
        return any(kw in sig_lower
                   for kw in ("긍정", "매수", "강력", "상승", "positive", "buy"))

    filtered_hidden = [
        h for h in hidden_picks if _is_positive_signal(h.get("signal"))
    ]

    # ── 시장 인디케이터 / 요약 / 섹터 HTML ───────────────────────────────────
    market_indicators_html = _build_market_indicators(market_overview)
    market_summary_html    = _render_market_summary(market_sum)

    sector_badges_html = ""
    for sector in hot_sectors:
        if isinstance(sector, dict):
            s_name   = sector.get("name", "")
            s_reason = sector.get("reason", "")
            sector_badges_html += (
                f'<div class="sector-badge" title="{s_reason}">{s_name}</div>'
            )
        elif sector:
            sector_badges_html += f'<div class="sector-badge">{sector}</div>'

    # ── chart data 수집 ───────────────────────────────────────────────────────
    chart_data_entries: list[str] = []

    # ── 관심 종목 카드 ────────────────────────────────────────────────────────
    stocks_html = ""
    for rank, stock in enumerate(filtered_stocks, start=1):
        name         = stock.get("name", "")
        signal       = stock.get("signal", "")
        overlap      = stock.get("overlap_count", 0)
        channel_cnts = stock.get("channel_counts", {})
        price        = stock.get("verified_price")
        naver_code   = stock.get("naver_code") or stock.get("code", "")
        naver_url    = stock.get("naver_url", "")
        chart_b64    = stock.get("chart_base64", "")
        reasons      = stock.get("reasons", [])

        # Naver URL 보완
        if not naver_url:
            if naver_code:
                naver_url = (
                    f"https://finance.naver.com/item/main.naver?code={naver_code}"
                )
            elif name:
                naver_url = (
                    "https://finance.naver.com/search/searchResult.naver"
                    f"?query={name.replace(' ', '+')}"
                )

        # signal 색상
        sig_l = (signal or "").lower()
        if "강력" in sig_l or "매수" in sig_l:
            sig_class, sig_color = "signal-strong-buy", "#ff6b6b"
        elif "긍정" in sig_l or "positive" in sig_l:
            sig_class, sig_color = "signal-positive",   "#ffa94d"
        elif "중립" in sig_l or "neutral" in sig_l:
            sig_class, sig_color = "signal-neutral",    "#adb5bd"
        else:
            sig_class, sig_color = "signal-default",    "#74c0fc"

        # 소스 태그
        source_tags_html = ""
        for src_type, cnt in channel_cnts.items():
            if cnt and int(cnt) > 0:
                meta = _TAG_META.get(
                    src_type, {"bg": "#2d2d44", "color": "#adb5bd"}
                )
                source_tags_html += (
                    f'<span class="source-tag" '
                    f'style="background:{meta["bg"]};color:{meta["color"]};">'
                    f'{src_type} {cnt}</span>'
                )

        # 가격
        if price and price != "N/A":
            price_html = (
                f'<span class="price-value">{price:,}원</span>'
                if isinstance(price, int)
                else f'<span class="price-value">{price}</span>'
            )
        else:
            price_html = (
                '<span class="price-value" style="color:#666;">가격 조회 중</span>'
            )

        # 차트 버튼 — CR-NEW-1 + SIM-P5-1
        if chart_b64:
            chart_key   = _safe_chart_key("chart", name)
            safe_name_js = _safe_js_str(name)
            chart_data_entries.append(
                f'"{chart_key}": "data:image/png;base64,{chart_b64}"'
            )
            chart_btn_html = (
                f'<button class="chart-btn" '
                f"onclick=\"showChart('{chart_key}','{safe_name_js}')\">"
                f'📈 차트 보기</button>'
            )
        elif naver_url:
            chart_btn_html = (
                f'<a href="{naver_url}" target="_blank" rel="noopener" '
                f'class="chart-btn">🔗 Naver 차트</a>'
            )
        else:
            chart_btn_html = ""

        reasons_block = _render_reasons(reasons)

        stocks_html += f"""
<div class="stock-card">
  <div class="stock-card-header">
    <div class="stock-rank">#{rank}</div>
    <div class="stock-name-block">
      <a href="{naver_url}" target="_blank" rel="noopener"
         class="stock-name">{name}</a>
      <span class="signal-badge {sig_class}"
            style="border-color:{sig_color};color:{sig_color};">{signal}</span>
    </div>
    <div class="overlap-badge" title="채널 중복 언급 수">🔥 {overlap}개 채널</div>
  </div>
  <div class="stock-card-body">
    <div class="source-tags">{source_tags_html}</div>
    <div class="price-row">{price_html}{chart_btn_html}</div>
    {reasons_block}
  </div>
</div>
"""

    if not stocks_html:
        stocks_html = (
            '<p style="color:#666;text-align:center;padding:2rem;">'
            '오늘은 복수 채널 교차 언급 종목이 없습니다.</p>'
        )

    # ── 오늘의 픽 카드 ────────────────────────────────────────────────────────
    hidden_html = ""
    for idx, hp in enumerate(filtered_hidden, start=1):
        name         = hp.get("name", "")
        signal       = hp.get("signal", "")
        channel_type = hp.get("channel_type", "")
        weighted_sc  = hp.get("weighted_score", 0)
        price        = hp.get("verified_price")
        naver_code   = hp.get("naver_code") or hp.get("code", "")
        naver_url    = hp.get("naver_url", "")
        chart_b64    = hp.get("chart_base64", "")
        reasons      = hp.get("reasons", [])

        if not naver_url:
            if naver_code:
                naver_url = (
                    f"https://finance.naver.com/item/main.naver?code={naver_code}"
                )
            elif name:
                naver_url = (
                    "https://finance.naver.com/search/searchResult.naver"
                    f"?query={name.replace(' ', '+')}"
                )

        source_badge_html = _hidden_pick_source_badge(channel_type)
        score_str = (
            f"{weighted_sc:.1f}"
            if isinstance(weighted_sc, (int, float)) else str(weighted_sc)
        )
        score_badge_html = (
            f'<span class="hp-score-badge">Pick #{idx} · {score_str}pt</span>'
        )

        if price and price != "N/A":
            price_html = (
                f'<span class="price-value">{price:,}원</span>'
                if isinstance(price, int)
                else f'<span class="price-value">{price}</span>'
            )
        else:
            price_html = (
                '<span class="price-value" style="color:#666;">가격 조회 중</span>'
            )

        # 차트 버튼 — CR-NEW-1 + SIM-P5-1
        if chart_b64:
            chart_key    = _safe_chart_key("hpchart", name)
            safe_name_js = _safe_js_str(name)
            chart_data_entries.append(
                f'"{chart_key}": "data:image/png;base64,{chart_b64}"'
            )
            chart_btn_html = (
                f'<button class="chart-btn" '
                f"onclick=\"showChart('{chart_key}','{safe_name_js}')\">"
                f'📈 차트 보기</button>'
            )
        elif naver_url:
            chart_btn_html = (
                f'<a href="{naver_url}" target="_blank" rel="noopener" '
                f'class="chart-btn">🔗 Naver 차트</a>'
            )
        else:
            chart_btn_html = ""

        reasons_block = _render_reasons(reasons)

        hidden_html += f"""
<div class="hidden-pick-card">
  <div class="hp-card-header">
    <div class="hp-badges">{source_badge_html}{score_badge_html}</div>
    <a href="{naver_url}" target="_blank" rel="noopener"
       class="hp-stock-name">{name}</a>
    <span class="hp-signal">{signal}</span>
  </div>
  <div class="hp-card-body">
    <div class="price-row">{price_html}{chart_btn_html}</div>
    {reasons_block}
  </div>
</div>
"""

    if not hidden_html:
        hidden_html = (
            '<p style="color:#666;text-align:center;padding:1.5rem;">'
            '오늘의 픽 없음</p>'
        )

    # ── chart data JS ─────────────────────────────────────────────────────────
    chart_data_js = (
        "const chartDataMap = {\n  "
        + ",\n  ".join(chart_data_entries)
        + "\n};"
        if chart_data_entries
        else "const chartDataMap = {};"
    )

    # ── 섹션 2·3 HTML ─────────────────────────────────────────────────────────
    section2_html = _build_section2_html(all_data)
    section3_html = _build_analyst_html(all_data)

    # ── BUG-M6: archive 링크 ──────────────────────────────────────────────────
    archive_html = ""
    try:
        base_dir    = os.path.dirname(os.path.abspath(__file__))
        archive_dir = os.path.normpath(
            os.path.join(base_dir, "..", "docs", "archive")
        )
        if os.path.isdir(archive_dir) and gh_repo and "/" in gh_repo:
            owner = gh_repo.split("/")[0]
            repo  = gh_repo.split("/")[1]
            html_files = sorted(
                [f for f in os.listdir(archive_dir) if f.endswith(".html")],
                reverse=True,
            )[:14]
            if html_files:
                links = "".join(
                    f'<a href="https://{owner}.github.io/{repo}/archive/{fname}" '
                    f'target="_blank" rel="noopener" class="archive-link">'
                    f'{fname.replace(".html","")}</a>'
                    for fname in html_files
                )
                archive_html = f'<div class="archive-list">{links}</div>'
    except Exception as e:
        print(f"  [ARCHIVE] 링크 생성 실패: {e}")

    # ── CSS ───────────────────────────────────────────────────────────────────
    css = """
/* ── Reset & Base ── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Noto Sans KR', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
  background: #0a0a14;
  color: #e0e0e0;
  min-height: 100vh;
  line-height: 1.6;
}
a { color: inherit; }

/* ── Layout ── */
.container { max-width: 960px; margin: 0 auto; padding: 1rem 1.2rem 3rem; }

/* ── Header ── */
.briefing-header {
  text-align: center;
  padding: 2rem 1rem 1.5rem;
  border-bottom: 1px solid #1e1e2e;
  margin-bottom: 1.5rem;
}
.briefing-header h1 {
  font-size: 1.6rem; color: #e0e0e0; font-weight: 700; letter-spacing: -.5px;
}
.briefing-header .subtitle { font-size: .85rem; color: #666; margin-top: .4rem; }

/* ── Section ── */
.section { margin-bottom: 2rem; }
.section-title {
  font-size: 1.05rem; font-weight: 700; color: #c8c8d8;
  border-left: 3px solid #74c0fc; padding-left: .7rem; margin-bottom: 1rem;
}

/* ── Market Indicators ── */
.market-indicators {
  display: flex; flex-wrap: wrap; gap: .6rem;
  padding: 1rem; background: #111122;
  border-radius: 10px; border: 1px solid #1e1e2e;
}
.indicator-badge {
  display: flex; flex-direction: column; align-items: center;
  background: #16162a; border: 1px solid #2a2a3e;
  border-radius: 8px; padding: .45rem .8rem; min-width: 90px;
}
.ind-label { font-size: .7rem; color: #888; margin-bottom: .15rem; }
.ind-value { font-size: .95rem; font-weight: 700; color: #e0e0e0; }
.ind-pct   { font-size: .75rem; margin-top: .1rem; }

/* ── Market Summary ── */
.summary-block {
  background: #111122; border: 1px solid #1e1e2e;
  border-radius: 8px; padding: .8rem 1rem; margin-bottom: .7rem;
}
.summary-title { font-size: .85rem; font-weight: 700; color: #74c0fc; margin-bottom: .35rem; }
.summary-text  { font-size: .88rem; color: #c0c0d0; line-height: 1.65; }

/* ── Sector Badges ── */
.sector-list { display: flex; flex-wrap: wrap; gap: .5rem; }
.sector-badge {
  background: #1a1a2e; border: 1px solid #2a2a4e;
  border-radius: 20px; padding: .3rem .85rem;
  font-size: .82rem; color: #c8c8ff; cursor: default; transition: background .15s;
}
.sector-badge:hover { background: #22224a; }

/* ── Stock Card ── */
.stock-card {
  background: #111122; border: 1px solid #1e1e2e;
  border-radius: 12px; padding: 1rem 1.2rem;
  margin-bottom: 1rem; transition: border-color .2s;
}
.stock-card:hover { border-color: #3a3a5e; }
.stock-card-header {
  display: flex; align-items: center; gap: .7rem;
  margin-bottom: .7rem; flex-wrap: wrap;
}
.stock-rank {
  background: #1e1e3a; color: #74c0fc; font-size: .8rem; font-weight: 700;
  padding: .2rem .5rem; border-radius: 6px; min-width: 2rem; text-align: center;
}
.stock-name-block { display: flex; align-items: center; gap: .5rem; flex: 1; }
.stock-name {
  font-size: 1.05rem; font-weight: 700; color: #e8e8f8; text-decoration: none;
}
.stock-name:hover { color: #74c0fc; }
.signal-badge {
  font-size: .72rem; border: 1px solid; border-radius: 12px;
  padding: .15rem .55rem; white-space: nowrap;
}
.overlap-badge {
  font-size: .8rem; color: #ffa94d; background: #2a1e0a;
  border: 1px solid #4a3010; border-radius: 12px;
  padding: .2rem .6rem; white-space: nowrap;
}
.stock-card-body { padding-top: .3rem; }
.source-tags { display: flex; flex-wrap: wrap; gap: .4rem; margin-bottom: .6rem; }
.source-tag  { font-size: .72rem; padding: .15rem .55rem; border-radius: 10px; }
.price-row   {
  display: flex; align-items: center; gap: .8rem;
  margin-bottom: .6rem; flex-wrap: wrap;
}
.price-value { font-size: .95rem; font-weight: 700; color: #ffd43b; }
.chart-btn {
  font-size: .78rem; background: #1a1a2e; color: #74c0fc;
  border: 1px solid #2a3a5e; border-radius: 8px; padding: .25rem .7rem;
  cursor: pointer; text-decoration: none; transition: background .15s;
  display: inline-block;
}
.chart-btn:hover { background: #22223a; }
.reasons-list { list-style: none; padding: 0; margin-top: .4rem; }
.reasons-list li {
  font-size: .84rem; color: #a0a0b8;
  padding: .2rem 0 .2rem .9rem; position: relative; line-height: 1.55;
}
.reasons-list li::before { content: "·"; position: absolute; left: .2rem; color: #555; }

/* ── Hidden Pick Card ── */
.hidden-pick-card {
  background: #0f1a1a; border: 1px solid #1a2e2e;
  border-radius: 12px; padding: 1rem 1.2rem;
  margin-bottom: 1rem; transition: border-color .2s;
}
.hidden-pick-card:hover { border-color: #2a4a4a; }
.hp-card-header {
  display: flex; align-items: center; gap: .7rem;
  margin-bottom: .7rem; flex-wrap: wrap;
}
.hp-badges { display: flex; gap: .4rem; align-items: center; }
.hp-source-badge { font-size: .72rem; border-radius: 10px; padding: .15rem .55rem; white-space: nowrap; }
.hp-score-badge {
  font-size: .72rem; background: #1a2a1a; color: #51cf66;
  border: 1px solid #2a4a2a; border-radius: 10px; padding: .15rem .55rem; white-space: nowrap;
}
.hp-stock-name {
  font-size: 1.05rem; font-weight: 700; color: #e8e8f8; text-decoration: none;
}
.hp-stock-name:hover { color: #51cf66; }
.hp-signal {
  font-size: .78rem; color: #51cf66; background: #1a3a1a;
  border: 1px solid #2a5a2a; border-radius: 10px; padding: .15rem .55rem;
}
.hp-card-body { padding-top: .3rem; }

/* ── Analyst / TV Cards ── */
.analyst-category-title {
  font-size: .88rem; font-weight: 700; color: #ffa94d;
  margin: 1rem 0 .5rem; padding-left: .4rem; border-left: 2px solid #ffa94d;
}
.analyst-card, .tv-card {
  background: #111122; border: 1px solid #1e1e2e;
  border-radius: 10px; padding: .7rem 1rem; margin-bottom: .6rem;
}
.analyst-card-header, .tv-card-header {
  display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; margin-bottom: .35rem;
}
.analyst-stock, .tv-channel { font-size: .82rem; font-weight: 700; color: #e0e0f0; }
.new-coverage-badge {
  font-size: .68rem; background: #1a3a1a; color: #51cf66;
  border: 1px solid #2a5a2a; border-radius: 8px; padding: .1rem .45rem;
}
.analyst-broker, .tv-date { font-size: .75rem; color: #777; margin-left: auto; }
.analyst-title, .tv-card-title { font-size: .85rem; color: #b0b0c8; line-height: 1.5; }
.analyst-summary { font-size: .8rem; color: #888; margin-top: .3rem; line-height: 1.5; }

/* ── AI Strategy ── */
.ai-strategy-box {
  background: #0d0d1a; border: 1px solid #2a2a4a; border-radius: 12px;
  padding: 1.2rem 1.4rem; font-size: .88rem; color: #c0c0d8;
  line-height: 1.75; white-space: pre-wrap; word-break: keep-all;
}

/* ── Archive ── */
.archive-list { display: flex; flex-wrap: wrap; gap: .5rem; }
.archive-link {
  font-size: .78rem; background: #111122; color: #74c0fc;
  border: 1px solid #1e2e3e; border-radius: 8px; padding: .25rem .65rem;
  text-decoration: none; transition: background .15s;
}
.archive-link:hover { background: #1a1a3a; }

/* ── Disclaimer ── */
.disclaimer {
  font-size: .75rem; color: #555; text-align: center;
  margin-top: 2.5rem; line-height: 1.7;
  border-top: 1px solid #1a1a2e; padding-top: 1rem;
}

/* ── Chart Modal ── */
.modal-overlay {
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,.85); z-index: 999;
  align-items: center; justify-content: center;
}
.modal-overlay.active { display: flex; }
.modal-box {
  background: #111122; border: 1px solid #2a2a4a;
  border-radius: 14px; padding: 1.2rem;
  max-width: 680px; width: 95%; position: relative;
}
.modal-title {
  font-size: 1rem; font-weight: 700; color: #e0e0f0;
  margin-bottom: .8rem; text-align: center;
}
.modal-img   { width: 100%; border-radius: 8px; }
.modal-close {
  position: absolute; top: .6rem; right: .8rem;
  background: none; border: none; color: #888;
  font-size: 1.3rem; cursor: pointer; line-height: 1;
}
.modal-close:hover { color: #e0e0e0; }

/* ── Responsive ── */
@media (max-width: 600px) {
  .market-indicators { gap: .4rem; }
  .indicator-badge   { min-width: 78px; padding: .35rem .55rem; }
  .stock-card-header { gap: .4rem; }
  .briefing-header h1 { font-size: 1.3rem; }
  .hp-card-header    { gap: .4rem; }
}
"""

    # ── 섹션 가시성 판단 ──────────────────────────────────────────────────────
    has_summary  = bool(market_sum and market_sum.strip())
    has_sectors  = bool(hot_sectors)
    has_hidden   = bool(filtered_hidden)
    has_sec2     = any(
        d.get("source_type") in ("경제방송TV", "경제방송") for d in all_data
    )
    has_sec3     = any(
        d.get("source_type") == "애널리스트" for d in all_data
    )
    has_strategy = bool(ai_strategy and ai_strategy.strip())
    has_archive  = bool(archive_html)

    def _section(title: str, content: str, show: bool = True) -> str:
        if not show:
            return ""
        return (
            f'<section class="section">'
            f'<div class="section-title">{title}</div>'
            f'{content}'
            f'</section>\n'
        )

    # ── 본문 조립 ─────────────────────────────────────────────────────────────
    html_body = (
        _section("📊 시장 지표",            market_indicators_html)
        + _section("📰 시장 요약",           market_summary_html,    show=has_summary)
        + _section("🔥 주목 섹터",
                   f'<div class="sector-list">{sector_badges_html}</div>',
                   show=has_sectors)
        + _section("👀 관심 종목",           stocks_html)
        + _section("⭐ 오늘의 픽",           hidden_html,            show=has_hidden)
        + _section("📋 애널리스트 리포트 분석", section3_html,         show=has_sec3)
        + _section("📺 경제방송TV 추천",     section2_html,          show=has_sec2)
        + _section("🤖 AI 투자 전략",
                   f'<div class="ai-strategy-box">'
                   f'{ai_strategy or "분석 데이터 없음"}</div>',
                   show=has_strategy)
        + _section("🗂 지난 브리핑",         archive_html,           show=has_archive)
    )

    # ── 최종 HTML ─────────────────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="description" content="AI 기반 한국 주식 모닝브리핑 - {briefing_date}">
  <title>AI 주식 브리핑 · {briefing_date}</title>
  <style>{css}</style>
</head>
<body>
<div class="container">

  <header class="briefing-header">
    <h1>📈 AI 주식 모닝브리핑</h1>
    <p class="subtitle">{briefing_date} · {briefing_time} KST · 다중 채널 교차분석</p>
  </header>

  {html_body}

  <div class="disclaimer">
    본 브리핑은 AI가 공개 데이터를 수집·분석하여 자동 생성한 정보입니다.<br>
    투자 판단의 최종 책임은 투자자 본인에게 있으며, 투자 권유가 아닙니다.<br>
    © {now_kst.year} AI Stock Briefing · 자동 생성
  </div>

</div>

<!-- 차트 모달 -->
<div class="modal-overlay" id="chartModal" onclick="closeChart(event)">
  <div class="modal-box">
    <button class="modal-close"
      onclick="document.getElementById('chartModal').classList.remove('active')">✕</button>
    <div class="modal-title" id="chartModalTitle"></div>
    <img class="modal-img" id="chartModalImg" src="" alt="차트">
  </div>
</div>

<script>
{chart_data_js}

function showChart(key, name) {{
  var src = chartDataMap[key];
  if (!src) {{ alert('차트 데이터가 없습니다.'); return; }}
  document.getElementById('chartModalTitle').textContent = name + ' 차트';
  document.getElementById('chartModalImg').src = src;
  document.getElementById('chartModal').classList.add('active');
}}

function closeChart(e) {{
  if (e.target.id === 'chartModal') {{
    document.getElementById('chartModal').classList.remove('active');
  }}
}}

document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') {{
    document.getElementById('chartModal').classList.remove('active');
  }}
}});
</script>
</body>
</html>"""
