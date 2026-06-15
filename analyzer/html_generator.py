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
    ("전일 코스피",  ["kospi",        "KOSPI"]),
    ("전일 코스닥",  ["kosdaq",       "KOSDAQ"]),
    ("나스닥",       ["nasdaq",       "NASDAQ"]),
    ("S&P500",       ["sp500",        "SP500", "s&p500"]),
    ("다우존스",     ["dow",          "DOW",   "dow_jones"]),
    ("야간선물",     ["night_future", "night", "futures"]),
    ("달러/원",      ["usd_krw",      "USD_KRW", "usd"]),
]

_TAG_META = {
    "뉴스":       {"bg": "#2d3a4a", "color": "#74c0fc"},
    "경제방송":   {"bg": "#3a2d1a", "color": "#ffa94d"},
    "경제방송TV": {"bg": "#3a2d1a", "color": "#ffa94d"},
    "유튜브":     {"bg": "#2d1a3a", "color": "#cc5de8"},
    "애널리스트": {"bg": "#1a3a2d", "color": "#51cf66"},
}


# ── 헬퍼 함수 ────────────────────────────────────────────────────────────────

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


def _build_tv_html(all_data: list) -> str:
    tv_types = {"경제방송tv", "경제방송", "broadcast", "tv"}
    items    = [d for d in all_data
                if str(d.get("source_type", "")).lower().replace(" ", "") in tv_types]
    if not items:
        return '<p style="color:#666;">경제방송 데이터 없음</p>'

    channel_map = OrderedDict()
    seen_titles = set()

    for item in items:
        channel  = item.get("source_name", "알 수 없음").strip()
        title    = item.get("title", "").strip()
        stock    = item.get("stock_name", "").strip()
        link     = item.get("link") or item.get("url", "")
        date_str = item.get("date", "")

        if not title or title in seen_titles:
            continue
        seen_titles.add(title)

        if channel not in channel_map:
            channel_map[channel] = {"date": date_str, "stocks": []}

        channel_map[channel]["stocks"].append((stock, title, link))

    if not channel_map:
        return '<p style="color:#666;">경제방송 데이터 없음</p>'

    html = ""
    for channel, info in channel_map.items():
        date_str   = info["date"]
        items_html = ""
        for (stock, title, link) in info["stocks"]:
            stock_html = f'<span class="tv-stock-name">{stock}</span>' if stock else ""
            if link:
                title_html = (
                    f'<a href="{link}" target="_blank" rel="noopener" '
                    f'style="color:#adb5bd;text-decoration:none;">{title}</a>'
                )
            else:
                title_html = f'<span style="color:#adb5bd;">{title}</span>'

            if stock_html:
                items_html += (
                    f'<li>{stock_html}'
                    f'<span class="tv-item-sep">·</span>'
                    f'{title_html}</li>'
                )
            else:
                items_html += f'<li>{title_html}</li>'

        html += f"""
<div class="tv-channel-card">
  <div class="tv-channel-header">
    <span class="tv-channel-name">📺 {channel}</span>
    <span class="tv-date">{date_str}</span>
  </div>
  <ul class="tv-stock-list">{items_html}</ul>
</div>"""

    return html


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
    """가중치 점수를 별 5개 기준으로 시각화. 채워진 별★ / 빈 별☆"""
    try:
        score = float(weighted_score)
    except (TypeError, ValueError):
        score = 0.0
    # 0~max_score 범위를 0~5 별점으로 변환, 최소 1개 보장
    filled = max(1, round((score / max_score) * 5)) if score > 0 else 0
    filled = min(filled, 5)
    empty  = 5 - filled
    stars  = (
        f'<span class="star filled">{"★" * filled}</span>'
        f'<span class="star empty">{"☆" * empty}</span>'
    )
    return f'<span class="star-rating">{stars}</span>'


def _render_reasons(reasons: list) -> str:
    """
    FIX-RSN-1: source_name 있으면 굵게 앞에 표시, 링크도 적용
    """
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

        # 출처명 배지
        source_html = ""
        if rn:
            meta = _TAG_META.get(rt, {"bg": "#2d2d44", "color": "#adb5bd"})
            source_html = (
                f'<span class="reason-source" '
                f'style="background:{meta["bg"]};color:{meta["color"]};">'
                f'{rn}</span> '
            )

        # 본문 (링크 있으면 감싸기)
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


# ── 메인 함수 ─────────────────────────────────────────────────────────────────

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

    sector_badges_html = ""
    for sector in hot_sectors:
        if isinstance(sector, dict):
            sector_badges_html += (
                f'<div class="sector-badge" title="{sector.get("reason", "")}">'
                f'{sector.get("name", "")}</div>'
            )
        elif sector:
            sector_badges_html += f'<div class="sector-badge">{sector}</div>'

    # ── 관심 종목 카드 ────────────────────────────────────────────────────────
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

        sig_l = (signal or "").lower()
        if "긍정" in sig_l or "positive" in sig_l:
            sig_class, sig_color = "signal-positive", "#ffa94d"
            signal = "긍정"
        elif "부정" in sig_l or "negative" in sig_l:
            sig_class, sig_color = "signal-negative", "#ff6b6b"
            signal = "부정"
        elif "중립" in sig_l or "neutral" in sig_l:
            sig_class, sig_color = "signal-neutral", "#adb5bd"
            signal = "중립"
        else:
            sig_class, sig_color = "signal-default", "#74c0fc"
            signal = "중립"

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
      <span class="signal-badge {sig_class}" style="border-color:{sig_color};color:{sig_color};">{signal}</span>
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

    # ── 오늘의 픽 카드 ────────────────────────────────────────────────────────
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

    # ── 차트 JS ───────────────────────────────────────────────────────────────
    if chart_data_entries:
        chart_data_js = ("const chartDataMap = {\n  "
                         + ",\n  ".join(chart_data_entries) + "\n};")
    else:
        chart_data_js = "const chartDataMap = {};"

    analyst_html = _build_analyst_html(all_data)
    tv_html      = _build_tv_html(all_data)

    # ── CSS ───────────────────────────────────────────────────────────────────
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

/* ── 헤더 ── */
.briefing-header {
  text-align: center;
  padding: 2.5rem 1rem 1.5rem;
  border-bottom: 1px solid var(--border);
  margin-bottom: 2rem;
}
.briefing-header h1 { font-size: 1.8rem; font-weight: 700; }
.subtitle { color: var(--text-muted); font-size: .9rem; margin-top: .4rem; }

/* ── 섹션 ── */
.section { margin-bottom: 2.5rem; }
.section-title {
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--text);
  border-left: 4px solid var(--accent);
  padding-left: .75rem;
  margin-bottom: 1rem;
}

/* ── 시장 지표 ── */
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

/* ── 시장 요약 ── */
.summary-block {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1rem 1.2rem;
  margin-bottom: .75rem;
}
.summary-title { font-weight: 700; margin-bottom: .4rem; color: var(--accent); }
.summary-text  { color: var(--text-muted); font-size: .95rem; }

/* ── 섹터 ── */
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

/* ── 관심 종목 카드 ── */
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
  flex-shrink: 0;
}
.source-tags { display: flex; flex-wrap: wrap; gap: .4rem; margin-bottom: .65rem; }
.source-tag  { font-size: .75rem; border-radius: 10px; padding: .15rem .5rem; }
.price-row {
  display: flex;
  align-items: center;
  gap: .75rem;
  margin-bottom: .6rem;
  flex-wrap: wrap;
}
.price-value { font-size: 1rem; font-weight: 700; color: #ffd43b; }
.chart-btn {
  display: inline-block;
  font-size: .8rem;
  padding: .25rem .7rem;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--surface2);
  color: var(--text);
  cursor: pointer;
  text-decoration: none;
}
.chart-btn:hover { border-color: var(--accent); color: var(--accent); }

/* ── V2 종목 상세 섹션 ── */
.stock-section {
  margin-top: .75rem;
  padding-top: .75rem;
  border-top: 1px solid var(--border);
}
.stock-section-label {
  display: inline-block;
  font-size: .78rem;
  font-weight: 700;
  color: var(--accent);
  margin-bottom: .35rem;
}
.stock-section-text {
  font-size: .88rem;
  color: var(--text-muted);
  line-height: 1.65;
}

/* ── 카드 본문 여백 ── */
.stock-card-body { padding-top: .25rem; }
.hp-card-body    { padding-top: .25rem; }

/* ── 이유 목록 (FIX-RSN-1: source_name 배지 포함) ── */
.reasons-list { list-style: none; padding-left: 0; margin-top: .4rem; }
.reasons-list li {
  font-size: .88rem;
  color: var(--text-muted);
  margin-bottom: .4rem;
  line-height: 1.55;
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: .35rem;
}
.reason-source {
  font-size: .72rem;
  font-weight: 700;
  border-radius: 6px;
  padding: .1rem .4rem;
  white-space: nowrap;
  flex-shrink: 0;
}

/* ── 별점 (FIX-HP-1) ── */
.star-rating {
  display: inline-flex;
  align-items: center;
  gap: 1px;
  font-size: 1.05rem;
  line-height: 1;
  flex-shrink: 0;
}
.star.filled { color: #ffd43b; }
.star.empty  { color: #3a3a4a; }

/* ── 오늘의 픽 카드 ── */
.hidden-pick-card {
  background: linear-gradient(135deg, #1a2d1a 0%, #162316 100%);
  border: 1px solid #2d5a2d;
  border-radius: 10px;
  padding: 1rem 1.2rem;
  margin-bottom: 1rem;
}
.hp-card-header {
  display: flex;
  align-items: center;
  gap: .65rem;
  margin-bottom: .65rem;
  flex-wrap: wrap;
}
.hp-badges { display: flex; gap: .4rem; flex-wrap: wrap; }
.hp-source-badge, .hp-score-badge {
  font-size: .75rem;
  border-radius: 10px;
  padding: .15rem .55rem;
}
.hp-score-badge {
  background: #1a2d3a;
  color: #74c0fc;
  border: 1px solid #1e4a6e;
}
.hp-stock-name { font-size: 1.05rem; font-weight: 700; flex: 1 1 auto; }

/* ── 애널리스트 ── */
.analyst-category-title {
  font-weight: 700;
  font-size: .95rem;
  margin: 1rem 0 .5rem;
  color: var(--accent);
}
.analyst-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: .6rem 1rem;
  margin-bottom: .5rem;
  display: flex;
  flex-direction: column;
  gap: .3rem;
}
.analyst-card-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: .4rem;
}
.analyst-card-title {
  font-size: .9rem;
  line-height: 1.55;
  word-break: keep-all;
  overflow-wrap: break-word;
}
.analyst-title-link {
  color: #74c0fc;
  text-decoration: none;
  white-space: normal;
}
.analyst-title-link:hover { text-decoration: underline; }
.analyst-title-text { color: #74c0fc; white-space: normal; }
.analyst-stock  { font-weight: 700; }
.analyst-broker { font-size: .8rem; color: var(--text-muted); }
.new-coverage-badge {
  font-size: .7rem;
  background: #1a3a2d;
  color: #51cf66;
  border: 1px solid #2d5a3a;
  border-radius: 8px;
  padding: .1rem .45rem;
}

/* ── TV 카드 ── */
.tv-channel-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: .75rem 1rem;
  margin-bottom: .75rem;
}
.tv-channel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: .55rem;
  flex-wrap: wrap;
  gap: .4rem;
}
.tv-channel-name { font-size: .9rem; font-weight: 700; color: #ffa94d; }
.tv-date         { font-size: .75rem; color: var(--text-muted); }
.tv-stock-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: .35rem;
}
.tv-stock-list li {
  font-size: .88rem;
  color: var(--text-muted);
  display: flex;
  align-items: baseline;
  gap: .4rem;
  flex-wrap: wrap;
}
.tv-stock-name { font-weight: 700; color: var(--text); white-space: nowrap; }
.tv-item-sep   { color: var(--border); font-size: .8rem; }

/* ── AI 전략 ── */
.ai-strategy-box {
  background: var(--surface);
  border: 1px solid #2d4a2d;
  border-radius: 10px;
  padding: 1.2rem 1.4rem;
  font-size: .95rem;
  line-height: 1.8;
  color: var(--text-muted);
  white-space: pre-wrap;
}

/* ── 면책 ── */
.disclaimer {
  text-align: center;
  font-size: .78rem;
  color: var(--text-muted);
  border-top: 1px solid var(--border);
  padding-top: 1.5rem;
  margin-top: 3rem;
  line-height: 1.8;
}

/* ── 모달 ── */
.modal-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,.75);
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
  max-width: 92vw;
  max-height: 90vh;
  overflow: auto;
  position: relative;
}
.modal-close {
  position: absolute;
  top: .75rem; right: .75rem;
  background: none; border: none;
  color: var(--text-muted);
  font-size: 1.2rem;
  cursor: pointer;
}
.modal-title { font-weight: 700; margin-bottom: 1rem; }
.modal-img   { max-width: 100%; border-radius: 8px; display: block; }

/* ── 반응형 ── */
@media (max-width: 600px) {
  .briefing-header h1 { font-size: 1.4rem; }
  .stock-name { font-size: .95rem; }
  .market-indicators { gap: .4rem; }
  .indicator-badge { min-width: 80px; padding: .4rem .6rem; }
  .analyst-card-title { font-size: .85rem; }
  .star-rating { font-size: .95rem; }
}
"""

    # ── 섹션 빌더 ─────────────────────────────────────────────────────────────
    def _section(title: str, content: str, show: bool = True) -> str:
        if not show:
            return ""
        return (
            f'<section class="section">'
            f'<div class="section-title">{title}</div>'
            f'{content}</section>\n'
        )

    html_body = (
        _section("📊 시장 지표",   market_indicators_html)
        + _section("📰 시장 요약", market_summary_html,
                   show=bool(market_sum.strip()))
        + _section("🔥 주목 섹터",
                   f'<div class="sector-list">{sector_badges_html}</div>',
                   show=bool(hot_sectors))
        + _section("👀 관심 종목", stocks_html)
        + _section("⭐ 오늘의 픽", hidden_html,
                   show=bool(filtered_hidden))
        + _section("📋 애널리스트 리포트 분석", analyst_html,
                   show="데이터 없음" not in analyst_html)
        + _section("📺 경제방송TV 추천", tv_html,
                   show="데이터 없음" not in tv_html)
        + _section("🤖 AI 투자 전략",
                   f'<div class="ai-strategy-box">{ai_strategy or "분석 데이터 없음"}</div>',
                   show=bool((ai_strategy or "").strip()))
    )

    # ── JS ────────────────────────────────────────────────────────────────────
    js = f"""
{chart_data_js}

function showChart(key, name) {{
  const src = chartDataMap[key];
  if (!src) {{
    console.warn('차트 데이터 없음:', key);
    return;
  }}
  document.getElementById('modalImg').src   = src;
  document.getElementById('modalTitle').textContent = name + ' 차트';
  document.getElementById('chartModal').classList.add('active');
}}

function closeChart(e) {{
  if (e.target === document.getElementById('chartModal')) {{
    document.getElementById('chartModal').classList.remove('active');
    document.getElementById('modalImg').src = '';
  }}
}}

document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') {{
    document.getElementById('chartModal').classList.remove('active');
    document.getElementById('modalImg').src = '';
  }}
}});
"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
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

<div class="modal-overlay" id="chartModal" onclick="closeChart(event)">
  <div class="modal-box">
    <button class="modal-close"
      onclick="document.getElementById('chartModal').classList.remove('active')">✕</button>
    <div class="modal-title" id="modalTitle"></div>
    <img class="modal-img" id="modalImg" src="" alt="차트">
  </div>
</div>

<script>{js}</script>
</body>
</html>"""
