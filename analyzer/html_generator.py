# analyzer/html_generator.py
"""
AI 주식 브리핑 HTML 생성 엔진
- BUG-9    : _indicator_badge에서 0.0을 유효한 숫자로 처리
- BUG-NEW-6: overlap_count를 channel_counts에서 재산출
- BUG-H5   : hidden_picks signal 필터 — "긍정" 부분 일치 또는 "positive" 허용
- BUG-M6   : archive 경로를 __file__ 기준 절대경로로 계산
- BUG-W-3  : reason 렌더링 시 detail → reason → text 우선순위 적용
- HP-BADGE : hidden_pick 소스 타입별 배지(색상·아이콘) 표시
섹션 순서: 시장 지표 → 시장 요약 → 주목 섹터 → 관심 종목 → 오늘의 픽
           → 애널리스트 리포트 분석 → 경제방송TV 추천 → AI 투자 전략 → 아카이브
"""

import os
import re
from datetime import datetime, timedelta, timezone

# ── 상수 ─────────────────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))

# 시장 요약 단락 기본 제목
PARA_TITLES = ["📌 시장 개요", "📊 주요 이슈", "🔍 투자 포인트", "⚠️ 리스크 요인", "💡 전망"]

# hidden_pick 소스 타입 메타 (BUG-HP-BADGE)
_HP_SOURCE_META = {
    "애널리스트":   {"color": "#51cf66", "icon": "📊", "label": "애널리스트"},
    "경제방송TV":   {"color": "#ffa94d", "icon": "📺", "label": "경제방송TV"},
    "경제방송":     {"color": "#74c0fc", "icon": "📡", "label": "경제방송"},
}
_HP_SOURCE_DEFAULT = {"color": "#adb5bd", "icon": "📌", "label": "단독 언급"}

# 인디케이터 순서 정의 (BUG-ORDER)
_INDICATOR_ORDER = [
    ("전일 코스피",  "kospi"),
    ("전일 코스닥",  "kosdaq"),
    ("나스닥",       "nasdaq"),
    ("S&P500",       "sp500"),
    ("다우존스",     "dow"),
    ("야간선물",     "night_future"),
    ("달러/원",      "usd_krw"),
]


# ─────────────────────────────────────────────────────────────────────────────
# 헬퍼: 시장 인디케이터 배지
# ─────────────────────────────────────────────────────────────────────────────

def _indicator_badge(label: str, value, pct, direction: str = "") -> str:
    """
    시장 지표 배지 HTML 생성.
    BUG-9: pct == 0.0 도 유효한 숫자로 처리 (None 체크를 별도로 수행).
    """
    # None 체크 (0.0은 유효값이므로 'is None'으로만 검사)
    if value is None:
        return ""

    # pct 안전 처리
    if pct is None:
        pct_num = 0.0
    else:
        try:
            pct_num = float(pct)
        except (TypeError, ValueError):
            pct_num = 0.0

    # direction 자동 추론 (명시되지 않은 경우)
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

    # 값 포맷
    if isinstance(value, float) and value < 100:
        val_str = f"{value:,.2f}"
    elif isinstance(value, (int, float)):
        val_str = f"{value:,.2f}" if isinstance(value, float) else f"{int(value):,}"
    else:
        val_str = str(value)

    pct_str = f"{pct_num:+.2f}%"

    return (
        f'<div class="indicator-badge">'
        f'  <span class="ind-label">{label}</span>'
        f'  <span class="ind-value">{val_str}</span>'
        f'  <span class="ind-pct" style="color:{color}">{arrow} {pct_str}</span>'
        f'</div>'
    )


def _build_market_indicators(market_overview: dict) -> str:
    """
    시장 인디케이터 배지 행 HTML 생성.
    순서: 전일 코스피 → 전일 코스닥 → 나스닥 → S&P500 → 다우존스 → 야간선물 → 달러/원
    """
    if not market_overview:
        return '<div class="market-indicators"><p style="color:#666;font-size:.85em;">시장 데이터 없음</p></div>'

    badges_html = ""
    for label, key in _INDICATOR_ORDER:
        item = market_overview.get(key, {})
        if not item:
            continue
        value = item.get("value") or item.get("close") or item.get("price")
        pct   = item.get("change_pct") or item.get("pct") or item.get("percent")
        direction = item.get("direction", "")
        badge = _indicator_badge(label, value, pct, direction)
        if badge:
            badges_html += badge

    if not badges_html:
        return '<div class="market-indicators"><p style="color:#666;font-size:.85em;">시장 데이터 없음</p></div>'

    return f'<div class="market-indicators">{badges_html}</div>'


# ─────────────────────────────────────────────────────────────────────────────
# 헬퍼: 시장 요약 렌더링
# ─────────────────────────────────────────────────────────────────────────────

def _render_market_summary(market_summary: str) -> str:
    """
    시장 요약 텍스트를 단락별 카드로 렌더링.
    줄바꿈/번호 기준으로 분리 후 PARA_TITLES 아이콘 매핑.
    """
    if not market_summary or not market_summary.strip():
        return '<p style="color:#666;">시장 요약 데이터 없음</p>'

    # 번호 패턴(1. 2. 등) 또는 빈 줄로 분리
    raw_paras = re.split(r'\n\s*\n|\n(?=\d+\.)', market_summary.strip())
    paras = [p.strip() for p in raw_paras if p.strip()]

    if not paras:
        return f'<p style="color:#ccc;">{market_summary.strip()}</p>'

    html = ""
    for i, para in enumerate(paras):
        # 선행 번호 제거 (1. 2. 등)
        clean = re.sub(r'^\d+\.\s*', '', para).strip()
        if not clean:
            continue
        title = PARA_TITLES[i] if i < len(PARA_TITLES) else f"📎 포인트 {i+1}"
        html += (
            f'<div class="summary-block">'
            f'  <div class="summary-title">{title}</div>'
            f'  <p class="summary-text">{clean}</p>'
            f'</div>'
        )

    return html or f'<p style="color:#ccc;">{market_summary.strip()}</p>'


# ─────────────────────────────────────────────────────────────────────────────
# 헬퍼: 섹션2 – 경제방송TV 추천
# ─────────────────────────────────────────────────────────────────────────────

def _build_section2_html(all_data: list) -> str:
    """
    경제방송TV 소스 항목에서 전문가 추천 카드 렌더링.
    중복 제목 제거, 채널명·날짜·링크 포함.
    """
    if not all_data:
        return '<p style="color:#666;">경제방송 데이터 없음</p>'

    items = [d for d in all_data if d.get("source_type") in ("경제방송TV", "경제방송")]
    if not items:
        return '<p style="color:#666;">경제방송 데이터 없음</p>'

    seen_titles = set()
    cards_html = ""

    for item in items[:20]:  # 최대 20개
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
            f'  <div class="tv-card-header">'
            f'    {stock_badge}'
            f'    <span class="tv-channel">{channel}</span>'
            f'    <span class="tv-date">{date}</span>'
            f'  </div>'
            f'  <div class="tv-card-title">{title_html}</div>'
            f'</div>'
        )

    return cards_html or '<p style="color:#666;">경제방송 데이터 없음</p>'


# ─────────────────────────────────────────────────────────────────────────────
# 헬퍼: 섹션3 – 애널리스트 리포트 분석
# ─────────────────────────────────────────────────────────────────────────────

def _build_analyst_html(all_data: list) -> str:
    """
    애널리스트 리포트 소스 항목을 세 범주로 분류하여 렌더링.
    범주: 동시 언급(simultaneous) → 신규 커버리지(new_coverage) → 단독 언급(single_broker/first_in_6months)
    BUG-AC-12: single_broker와 first_in_6months 모두 허용
    """
    if not all_data:
        return '<p style="color:#666;">애널리스트 리포트 데이터 없음</p>'

    analyst_items = [d for d in all_data if d.get("source_type") == "애널리스트"]
    if not analyst_items:
        return '<p style="color:#666;">애널리스트 리포트 데이터 없음</p>'

    simultaneous = [r for r in analyst_items if r.get("analyst_category") == "simultaneous"]
    new_cov      = [r for r in analyst_items if r.get("analyst_category") == "new_coverage"]
    single       = [r for r in analyst_items
                    if r.get("analyst_category") in ("single_broker", "first_in_6months")]

    def _report_card(r: dict) -> str:
        stock   = r.get("stock_name", "")
        title   = r.get("report_title") or r.get("title", "")
        broker  = r.get("brokers") or r.get("source_name", "")
        summary = r.get("summary", "")
        link    = r.get("link", "")
        is_new  = r.get("new_coverage", False)

        # 네이버 리서치 링크 생성 (link 없을 때 fallback)
        if not link and stock:
            encoded = stock.replace(" ", "+")
            link = (f"https://finance.naver.com/research/company_list.naver"
                    f"?searchType=keyword&keyword={encoded}")

        new_badge = (
            '<span class="new-coverage-badge">신규 커버리지</span>'
            if is_new else ""
        )
        title_html = (
            f'<a href="{link}" target="_blank" rel="noopener" '
            f'style="color:#74c0fc;text-decoration:none;">{title}</a>'
            if link else f'<span>{title}</span>'
        )

        return (
            f'<div class="analyst-card">'
            f'  <div class="analyst-card-header">'
            f'    <span class="analyst-stock">{stock}</span>'
            f'    {new_badge}'
            f'    <span class="analyst-broker">{broker}</span>'
            f'  </div>'
            f'  <div class="analyst-title">{title_html}</div>'
            f'  {"<p class=\"analyst-summary\">" + summary + "</p>" if summary else ""}'
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


# ─────────────────────────────────────────────────────────────────────────────
# 헬퍼: hidden_pick 소스 배지
# ─────────────────────────────────────────────────────────────────────────────

def _hidden_pick_source_badge(channel_type: str) -> str:
    """hidden_pick의 소스 타입에 따른 인라인 스타일 배지 반환"""
    meta = _HP_SOURCE_META.get(channel_type, _HP_SOURCE_DEFAULT)
    return (
        f'<span class="hp-source-badge" '
        f'style="background:{meta["color"]}22;color:{meta["color"]};'
        f'border:1px solid {meta["color"]}55;">'
        f'{meta["icon"]} {meta["label"]}'
        f'</span>'
    )


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
    브리핑 데이터를 받아 완성된 HTML 문서를 반환합니다.

    Args:
        data           : AI 분석 결과 dict
                         {"stocks": [...], "hidden_picks": [...],
                          "market_summary": "...", "hot_sectors": [...],
                          "ai_strategy": "...", "briefing_date": "..."}
        channels_data  : channels.json 내용 (현재 미사용, 확장용)
        gh_repo        : "owner/repo" 형식 GitHub 저장소명
        gh_token       : GitHub token (현재 미사용, 확장용)
        market_overview: 시장 지표 dict
        all_data       : 원본 수집 데이터 list (섹션2·3 렌더링에 사용)
    """
    # ── 기본값 처리 ────────────────────────────────────────────────────────
    data            = data or {}
    market_overview = market_overview or {}
    all_data        = all_data or []

    stocks       = data.get("stocks", [])
    hidden_picks = data.get("hidden_picks", [])
    market_sum   = data.get("market_summary", "")
    hot_sectors  = data.get("hot_sectors", [])
    ai_strategy  = data.get("ai_strategy", "")
    briefing_date = data.get("briefing_date", "")

    now_kst = datetime.now(KST)
    if not briefing_date:
        briefing_date = now_kst.strftime("%Y년 %m월 %d일")
    briefing_time = now_kst.strftime("%H:%M")

    # ── BUG-NEW-6: overlap_count 재산출 ────────────────────────────────────
    for stock in stocks:
        cc = stock.get("channel_counts", {})
        if cc:
            stock["overlap_count"] = sum(1 for v in cc.values() if v > 0)

    # ── 필터: overlap_count >= 2 ────────────────────────────────────────────
    filtered_stocks = [s for s in stocks if s.get("overlap_count", 0) >= 2]

    # ── BUG-H5: hidden_picks signal 필터 ───────────────────────────────────
    def _is_positive_signal(sig: str) -> bool:
        if not sig:
            return False
        sig_lower = sig.lower()
        return "긍정" in sig_lower or sig_lower == "positive" or "positive" in sig_lower

    filtered_hidden = [h for h in hidden_picks if _is_positive_signal(h.get("signal", ""))]

    # ── 시장 인디케이터 HTML ───────────────────────────────────────────────
    market_indicators_html = _build_market_indicators(market_overview)

    # ── 시장 요약 HTML ─────────────────────────────────────────────────────
    market_summary_html = _render_market_summary(market_sum)

    # ── 주목 섹터 배지 HTML ────────────────────────────────────────────────
    sector_badges_html = ""
    for sector in hot_sectors:
        if isinstance(sector, dict):
            name   = sector.get("name", "")
            reason = sector.get("reason", "")
            badge  = f'<div class="sector-badge" title="{reason}">{name}</div>'
        else:
            badge = f'<div class="sector-badge">{sector}</div>'
        sector_badges_html += badge

    # ── chart data JS 준비 ─────────────────────────────────────────────────
    chart_data_entries = []

    # ── 관심 종목 카드 HTML ────────────────────────────────────────────────
    stocks_html = ""
    for rank, stock in enumerate(filtered_stocks, start=1):
        name         = stock.get("name", "")
        signal       = stock.get("signal", "")
        overlap      = stock.get("overlap_count", 0)
        weighted_sc  = stock.get("weighted_score", 0)
        channel_cnts = stock.get("channel_counts", {})
        reasons      = stock.get("reasons", [])
        price        = stock.get("verified_price")
        naver_code   = stock.get("naver_code") or stock.get("code", "")
        naver_url    = stock.get("naver_url", "")
        chart_b64    = stock.get("chart_base64", "")

        # Naver URL 생성
        if not naver_url and naver_code:
            naver_url = f"https://finance.naver.com/item/main.naver?code={naver_code}"
        elif not naver_url and name:
            encoded = name.replace(" ", "+")
            naver_url = f"https://finance.naver.com/search/searchResult.naver?query={encoded}"

        # signal 클래스
        sig_lower = (signal or "").lower()
        if "강력" in sig_lower or "매수" in sig_lower:
            sig_class, sig_color = "signal-strong-buy", "#ff6b6b"
        elif "긍정" in sig_lower or "positive" in sig_lower:
            sig_class, sig_color = "signal-positive", "#ffa94d"
        elif "중립" in sig_lower or "neutral" in sig_lower:
            sig_class, sig_color = "signal-neutral", "#adb5bd"
        else:
            sig_class, sig_color = "signal-default", "#74c0fc"

        # 소스 태그
        source_tags_html = ""
        tag_meta = {
            "뉴스":     {"bg": "#2d3a4a", "color": "#74c0fc"},
            "경제방송": {"bg": "#3a2d1a", "color": "#ffa94d"},
            "경제방송TV":{"bg": "#3a2d1a", "color": "#ffa94d"},
            "유튜브":   {"bg": "#2d1a3a", "color": "#cc5de8"},
            "애널리스트":{"bg": "#1a3a2d", "color": "#51cf66"},
        }
        for src_type, cnt in channel_cnts.items():
            if cnt > 0:
                meta = tag_meta.get(src_type, {"bg": "#2d2d44", "color": "#adb5bd"})
                source_tags_html += (
                    f'<span class="source-tag" '
                    f'style="background:{meta["bg"]};color:{meta["color"]};">'
                    f'{src_type} {cnt}'
                    f'</span>'
                )

        # 가격 정보
        if price and price != "N/A":
            price_html = f'<span class="price-value">{price:,}원</span>' if isinstance(price, int) else f'<span class="price-value">{price}</span>'
        else:
            price_html = '<span class="price-value" style="color:#666;">가격 조회 중</span>'

        # 차트 버튼 또는 Naver 링크
        if chart_b64:
            chart_key = f"chart_{name}"
            chart_data_entries.append(f'"{chart_key}": "data:image/png;base64,{chart_b64}"')
            chart_btn_html = (
                f'<button class="chart-btn" onclick="showChart(\'{chart_key}\',\'{name}\')">'
                f'📈 차트 보기</button>'
            )
        elif naver_url:
            chart_btn_html = (
                f'<a href="{naver_url}" target="_blank" rel="noopener" class="chart-btn">'
                f'🔗 Naver 차트</a>'
            )
        else:
            chart_btn_html = ""

        # 사유 목록
        reasons_html = ""
        for reason in (reasons or []):
            if isinstance(reason, str):
                rd = reason.strip()
                rl = ""
            elif isinstance(reason, dict):
                # BUG-W-3: detail → reason → text 우선순위
                rd = (reason.get("detail") or reason.get("reason") or reason.get("text", "")).strip()
                rl = reason.get("link") or reason.get("url", "")
            else:
                continue
            if not rd:
                continue
            if rl:
                reasons_html += (
                    f'<li><a href="{rl}" target="_blank" rel="noopener" '
                    f'style="color:#adb5bd;text-decoration:underline dotted;">{rd}</a></li>'
                )
            else:
                reasons_html += f'<li>{rd}</li>'

        stocks_html += f"""
<div class="stock-card">
  <div class="stock-card-header">
    <div class="stock-rank">#{rank}</div>
    <div class="stock-name-block">
      <a href="{naver_url}" target="_blank" rel="noopener" class="stock-name">{name}</a>
      <span class="signal-badge {sig_class}" style="border-color:{sig_color};color:{sig_color};">{signal}</span>
    </div>
    <div class="overlap-badge" title="채널 중복 언급 수">
      🔥 {overlap}개 채널
    </div>
  </div>
  <div class="stock-card-body">
    <div class="source-tags">{source_tags_html}</div>
    <div class="price-row">
      {price_html}
      {chart_btn_html}
    </div>
    {"<ul class='reasons-list'>" + reasons_html + "</ul>" if reasons_html else ""}
  </div>
</div>
"""

    if not stocks_html:
        stocks_html = '<p style="color:#666;text-align:center;padding:2rem;">오늘은 복수 채널 교차 언급 종목이 없습니다.</p>'

    # ── 오늘의 픽 (hidden_picks) 카드 HTML ────────────────────────────────
    hidden_html = ""
    for idx, hp in enumerate(filtered_hidden, start=1):
        name         = hp.get("name", "")
        signal       = hp.get("signal", "")
        channel_type = hp.get("channel_type", "")
        weighted_sc  = hp.get("weighted_score", 0)
        reasons      = hp.get("reasons", [])
        price        = hp.get("verified_price")
        naver_code   = hp.get("naver_code") or hp.get("code", "")
        naver_url    = hp.get("naver_url", "")
        chart_b64    = hp.get("chart_base64", "")

        if not naver_url and naver_code:
            naver_url = f"https://finance.naver.com/item/main.naver?code={naver_code}"
        elif not naver_url and name:
            encoded = name.replace(" ", "+")
            naver_url = f"https://finance.naver.com/search/searchResult.naver?query={encoded}"

        # 소스 배지
        source_badge_html = _hidden_pick_source_badge(channel_type)

        # 점수 배지
        score_str = f"{weighted_sc:.1f}" if isinstance(weighted_sc, float) else str(weighted_sc)
        score_badge_html = f'<span class="hp-score-badge">Pick #{idx} · {score_str}pt</span>'

        # 가격
        if price and price != "N/A":
            price_html = f'<span class="price-value">{price:,}원</span>' if isinstance(price, int) else f'<span class="price-value">{price}</span>'
        else:
            price_html = '<span class="price-value" style="color:#666;">가격 조회 중</span>'

        # 차트
        if chart_b64:
            chart_key = f"hpchart_{name}"
            chart_data_entries.append(f'"{chart_key}": "data:image/png;base64,{chart_b64}"')
            chart_btn_html = (
                f'<button class="chart-btn" onclick="showChart(\'{chart_key}\',\'{name}\')">'
                f'📈 차트 보기</button>'
            )
        elif naver_url:
            chart_btn_html = (
                f'<a href="{naver_url}" target="_blank" rel="noopener" class="chart-btn">'
                f'🔗 Naver 차트</a>'
            )
        else:
            chart_btn_html = ""

        # 사유
        hp_reasons_html = ""
        for reason in (reasons or []):
            if isinstance(reason, str):
                rd, rl = reason.strip(), ""
            elif isinstance(reason, dict):
                # BUG-W-3 동일 적용
                rd = (reason.get("detail") or reason.get("reason") or reason.get("text", "")).strip()
                rl = reason.get("link") or reason.get("url", "")
            else:
                continue
            if not rd:
                continue
            if rl:
                hp_reasons_html += (
                    f'<li><a href="{rl}" target="_blank" rel="noopener" '
                    f'style="color:#adb5bd;text-decoration:underline dotted;">{rd}</a></li>'
                )
            else:
                hp_reasons_html += f'<li>{rd}</li>'

        hidden_html += f"""
<div class="hidden-pick-card">
  <div class="hp-card-header">
    <div class="hp-badges">
      {source_badge_html}
      {score_badge_html}
    </div>
    <a href="{naver_url}" target="_blank" rel="noopener" class="hp-stock-name">{name}</a>
    <span class="hp-signal">{signal}</span>
  </div>
  <div class="hp-card-body">
    <div class="price-row">
      {price_html}
      {chart_btn_html}
    </div>
    {"<ul class='reasons-list'>" + hp_reasons_html + "</ul>" if hp_reasons_html else ""}
  </div>
</div>
"""

    if not hidden_html:
        hidden_html = '<p style="color:#666;text-align:center;padding:1.5rem;">오늘의 픽 없음</p>'

    # ── chart data JS ──────────────────────────────────────────────────────
    if chart_data_entries:
        chart_data_js = "const chartDataMap = {\n  " + ",\n  ".join(chart_data_entries) + "\n};"
    else:
        chart_data_js = "const chartDataMap = {};"

    # ── 섹션2·3 HTML ──────────────────────────────────────────────────────
    section2_html = _build_section2_html(all_data)
    section3_html = _build_analyst_html(all_data)

    # ── BUG-M6: archive 경로 절대경로 계산 ────────────────────────────────
    archive_html = ""
    try:
        base_dir    = os.path.dirname(os.path.abspath(__file__))
        archive_dir = os.path.normpath(os.path.join(base_dir, "..", "docs", "archive"))

        if os.path.isdir(archive_dir) and gh_repo and "/" in gh_repo:
            owner = gh_repo.split("/")[0]
            repo  = gh_repo.split("/")[1]
            html_files = sorted(
                [f for f in os.listdir(archive_dir) if f.endswith(".html")],
                reverse=True
            )[:14]

            if html_files:
                archive_html = '<div class="archive-list">'
                for fname in html_files:
                    date_part = fname.replace(".html", "")
                    url = f"https://{owner}.github.io/{repo}/archive/{fname}"
                    archive_html += (
                        f'<a href="{url}" target="_blank" rel="noopener" '
                        f'class="archive-link">{date_part}</a>'
                    )
                archive_html += '</div>'
    except Exception as e:
        print(f"  [ARCHIVE] 링크 생성 실패: {e}")

    # ── CSS ────────────────────────────────────────────────────────────────
    css = """
/* ── Reset & Base ── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Noto Sans KR', 'Apple SD Gothic Neo', sans-serif;
  background: #0a0a14;
  color: #e0e0e0;
  min-height: 100vh;
  line-height: 1.6;
}
a { color: inherit; }

/* ── Layout ── */
.container {
  max-width: 960px;
  margin: 0 auto;
  padding: 1rem 1.2rem 3rem;
}

/* ── Header ── */
.briefing-header {
  text-align: center;
  padding: 2rem 1rem 1.5rem;
  border-bottom: 1px solid #1e1e2e;
  margin-bottom: 1.5rem;
}
.briefing-header h1 {
  font-size: 1.6rem;
  color: #e0e0e0;
  font-weight: 700;
  letter-spacing: -0.5px;
}
.briefing-header .subtitle {
  font-size: .85rem;
  color: #666;
  margin-top: .4rem;
}

/* ── Section ── */
.section {
  margin-bottom: 2rem;
}
.section-title {
  font-size: 1.05rem;
  font-weight: 700;
  color: #c8c8d8;
  border-left: 3px solid #74c0fc;
  padding-left: .7rem;
  margin-bottom: 1rem;
}

/* ── Market Indicators ── */
.market-indicators {
  display: flex;
  flex-wrap: wrap;
  gap: .6rem;
  padding: 1rem;
  background: #111122;
  border-radius: 10px;
  border: 1px solid #1e1e2e;
}
.indicator-badge {
  display: flex;
  flex-direction: column;
  align-items: center;
  background: #16162a;
  border: 1px solid #2a2a3e;
  border-radius: 8px;
  padding: .45rem .8rem;
  min-width: 90px;
}
.ind-label { font-size: .7rem; color: #888; margin-bottom: .15rem; }
.ind-value { font-size: .95rem; font-weight: 700; color: #e0e0e0; }
.ind-pct   { font-size: .75rem; margin-top: .1rem; }

/* ── Market Summary ── */
.summary-block {
  background: #111122;
  border: 1px solid #1e1e2e;
  border-radius: 8px;
  padding: .8rem 1rem;
  margin-bottom: .7rem;
}
.summary-title { font-size: .85rem; font-weight: 700; color: #74c0fc; margin-bottom: .35rem; }
.summary-text  { font-size: .88rem; color: #c0c0d0; line-height: 1.65; }

/* ── Sector Badges ── */
.sector-list {
  display: flex;
  flex-wrap: wrap;
  gap: .5rem;
}
.sector-badge {
  background: #1a1a2e;
  border: 1px solid #2a2a4e;
  border-radius: 20px;
  padding: .3rem .85rem;
  font-size: .82rem;
  color: #c8c8ff;
  cursor: default;
}
.sector-badge:hover { background: #22224a; }

/* ── Stock Card ── */
.stock-card {
  background: #111122;
  border: 1px solid #1e1e2e;
  border-radius: 12px;
  padding: 1rem 1.2rem;
  margin-bottom: 1rem;
  transition: border-color .2s;
}
.stock-card:hover { border-color: #3a3a5e; }
.stock-card-header {
  display: flex;
  align-items: center;
  gap: .7rem;
  margin-bottom: .7rem;
  flex-wrap: wrap;
}
.stock-rank {
  background: #1e1e3a;
  color: #74c0fc;
  font-size: .8rem;
  font-weight: 700;
  padding: .2rem .5rem;
  border-radius: 6px;
  min-width: 2rem;
  text-align: center;
}
.stock-name-block { display: flex; align-items: center; gap: .5rem; flex: 1; }
.stock-name {
  font-size: 1.05rem;
  font-weight: 700;
  color: #e8e8f8;
  text-decoration: none;
}
.stock-name:hover { color: #74c0fc; }
.signal-badge {
  font-size: .72rem;
  border: 1px solid;
  border-radius: 12px;
  padding: .15rem .55rem;
  white-space: nowrap;
}
.overlap-badge {
  font-size: .8rem;
  color: #ffa94d;
  background: #2a1e0a;
  border: 1px solid #4a3010;
  border-radius: 12px;
  padding: .2rem .6rem;
  white-space: nowrap;
}
.stock-card-body { padding-top: .3rem; }
.source-tags { display: flex; flex-wrap: wrap; gap: .4rem; margin-bottom: .6rem; }
.source-tag {
  font-size: .72rem;
  padding: .15rem .55rem;
  border-radius: 10px;
}
.price-row {
  display: flex;
  align-items: center;
  gap: .8rem;
  margin-bottom: .6rem;
  flex-wrap: wrap;
}
.price-value { font-size: .95rem; font-weight: 700; color: #ffd43b; }
.chart-btn {
  font-size: .78rem;
  background: #1a1a2e;
  color: #74c0fc;
  border: 1px solid #2a3a5e;
  border-radius: 8px;
  padding: .25rem .7rem;
  cursor: pointer;
  text-decoration: none;
  transition: background .15s;
}
.chart-btn:hover { background: #22223a; }
.reasons-list {
  list-style: none;
  padding: 0;
  margin-top: .4rem;
}
.reasons-list li {
  font-size: .84rem;
  color: #a0a0b8;
  padding: .2rem 0;
  padding-left: .9rem;
  position: relative;
  line-height: 1.55;
}
.reasons-list li::before {
  content: "·";
  position: absolute;
  left: .2rem;
  color: #555;
}

/* ── Hidden Pick Card ── */
.hidden-pick-card {
  background: #0f1a1a;
  border: 1px solid #1a2e2e;
  border-radius: 12px;
  padding: 1rem 1.2rem;
  margin-bottom: 1rem;
  transition: border-color .2s;
}
.hidden-pick-card:hover { border-color: #2a4a4a; }
.hp-card-header {
  display: flex;
  align-items: center;
  gap: .7rem;
  margin-bottom: .7rem;
  flex-wrap: wrap;
}
.hp-badges { display: flex; gap: .4rem; align-items: center; }
.hp-source-badge {
  font-size: .72rem;
  border-radius: 10px;
  padding: .15rem .55rem;
  white-space: nowrap;
}
.hp-score-badge {
  font-size: .72rem;
  background: #1a2a1a;
  color: #51cf66;
  border: 1px solid #2a4a2a;
  border-radius: 10px;
  padding: .15rem .55rem;
  white-space: nowrap;
}
.hp-stock-name {
  font-size: 1.05rem;
  font-weight: 700;
  color: #e8e8f8;
  text-decoration: none;
}
.hp-stock-name:hover { color: #51cf66; }
.hp-signal {
  font-size: .78rem;
  color: #51cf66;
  background: #1a3a1a;
  border: 1px solid #2a5a2a;
  border-radius: 10px;
  padding: .15rem .55rem;
}
.hp-card-body { padding-top: .3rem; }

/* ── Analyst / TV Cards ── */
.analyst-category-title {
  font-size: .88rem;
  font-weight: 700;
  color: #ffa94d;
  margin: 1rem 0 .5rem;
  padding-left: .4rem;
  border-left: 2px solid #ffa94d;
}
.analyst-card, .tv-card {
  background: #111122;
  border: 1px solid #1e1e2e;
  border-radius: 10px;
  padding: .7rem 1rem;
  margin-bottom: .6rem;
}
.analyst-card-header, .tv-card-header {
  display: flex;
  align-items: center;
  gap: .5rem;
  flex-wrap: wrap;
  margin-bottom: .35rem;
}
.analyst-stock, .tv-channel {
  font-size: .82rem;
  font-weight: 700;
  color: #e0e0f0;
}
.new-coverage-badge {
  font-size: .68rem;
  background: #1a3a1a;
  color: #51cf66;
  border: 1px solid #2a5a2a;
  border-radius: 8px;
  padding: .1rem .45rem;
}
.analyst-broker, .tv-date {
  font-size: .75rem;
  color: #777;
  margin-left: auto;
}
.analyst-title, .tv-card-title {
  font-size: .85rem;
  color: #b0b0c8;
  line-height: 1.5;
}
.analyst-summary {
  font-size: .8rem;
  color: #888;
  margin-top: .3rem;
  line-height: 1.5;
}

/* ── AI Strategy ── */
.ai-strategy-box {
  background: #0d0d1a;
  border: 1px solid #2a2a4a;
  border-radius: 12px;
  padding: 1.2rem 1.4rem;
  font-size: .88rem;
  color: #c0c0d8;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: keep-all;
}

/* ── Archive ── */
.archive-list {
  display: flex;
  flex-wrap: wrap;
  gap: .5rem;
}
.archive-link {
  font-size: .78rem;
  background: #111122;
  color: #74c0fc;
  border: 1px solid #1e2e3e;
  border-radius: 8px;
  padding: .25rem .65rem;
  text-decoration: none;
  transition: background .15s;
}
.archive-link:hover { background: #1a1a3a; }

/* ── Disclaimer ── */
.disclaimer {
  font-size: .75rem;
  color: #555;
  text-align: center;
  margin-top: 2.5rem;
  line-height: 1.7;
  border-top: 1px solid #1a1a2e;
  padding-top: 1rem;
}

/* ── Chart Modal ── */
.modal-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,.85);
  z-index: 999;
  align-items: center;
  justify-content: center;
}
.modal-overlay.active { display: flex; }
.modal-box {
  background: #111122;
  border: 1px solid #2a2a4a;
  border-radius: 14px;
  padding: 1.2rem;
  max-width: 680px;
  width: 95%;
  position: relative;
}
.modal-title {
  font-size: 1rem;
  font-weight: 700;
  color: #e0e0f0;
  margin-bottom: .8rem;
  text-align: center;
}
.modal-img { width: 100%; border-radius: 8px; }
.modal-close {
  position: absolute;
  top: .6rem;
  right: .8rem;
  background: none;
  border: none;
  color: #888;
  font-size: 1.3rem;
  cursor: pointer;
}
.modal-close:hover { color: #e0e0e0; }

/* ── Responsive ── */
@media (max-width: 600px) {
  .market-indicators { gap: .4rem; }
  .indicator-badge   { min-width: 78px; padding: .35rem .55rem; }
  .stock-card-header { gap: .4rem; }
  .briefing-header h1 { font-size: 1.3rem; }
}
"""

    # ── 최종 HTML 조립 ────────────────────────────────────────────────────
    # 섹션 가시성 제어
    has_stocks  = bool(filtered_stocks)
    has_hidden  = bool(filtered_hidden)
    has_summary = bool(market_sum)
    has_sectors = bool(hot_sectors)
    has_sec2    = bool([d for d in all_data if d.get("source_type") in ("경제방송TV","경제방송")])
    has_sec3    = bool([d for d in all_data if d.get("source_type") == "애널리스트"])
    has_archive = bool(archive_html)
    has_strategy = bool(ai_strategy)

    def _section(title: str, content: str, show: bool = True, extra_class: str = "") -> str:
        if not show:
            return ""
        return (
            f'<section class="section {extra_class}">'
            f'  <div class="section-title">{title}</div>'
            f'  {content}'
            f'</section>'
        )

    html_body = f"""
{_section("📊 시장 지표", market_indicators_html)}
{_section("📰 시장 요약", market_summary_html, show=has_summary)}
{_section("🔥 주목 섹터", '<div class="sector-list">' + sector_badges_html + '</div>', show=has_sectors)}
{_section("👀 관심 종목", stocks_html)}
{_section("⭐ 오늘의 픽", hidden_html, show=has_hidden)}
{_section("📋 애널리스트 리포트 분석", section3_html, show=has_sec3)}
{_section("📺 경제방송TV 추천", section2_html, show=has_sec2)}
{_section("🤖 AI 투자 전략", '<div class="ai-strategy-box">' + (ai_strategy or "분석 데이터 없음") + '</div>', show=has_strategy)}
{_section("🗂 지난 브리핑", archive_html, show=has_archive)}
"""

    full_html = f"""<!DOCTYPE html>
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

  <!-- 헤더 -->
  <header class="briefing-header">
    <h1>📈 AI 주식 모닝브리핑</h1>
    <p class="subtitle">{briefing_date} · {briefing_time} KST · 다중 채널 교차분석</p>
  </header>

  <!-- 본문 섹션들 -->
  {html_body}

  <!-- 면책 고지 -->
  <div class="disclaimer">
    본 브리핑은 AI가 공개 데이터를 수집·분석하여 자동 생성한 정보입니다.<br>
    투자 판단의 최종 책임은 투자자 본인에게 있으며, 투자 권유가 아닙니다.<br>
    © {now_kst.year} AI Stock Briefing · 자동 생성
  </div>

</div><!-- /container -->

<!-- 차트 모달 -->
<div class="modal-overlay" id="chartModal" onclick="closeChart(event)">
  <div class="modal-box">
    <button class="modal-close" onclick="document.getElementById('chartModal').classList.remove('active')">✕</button>
    <div class="modal-title" id="chartModalTitle"></div>
    <img class="modal-img" id="chartModalImg" src="" alt="차트">
  </div>
</div>

<script>
{chart_data_js}

function showChart(key, name) {{
  const src = chartDataMap[key];
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

    return full_html
