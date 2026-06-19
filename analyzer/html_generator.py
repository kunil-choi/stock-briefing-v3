"""
AI 주식 브리핑 HTML 생성 엔진

수정 이력:
(기존 이력 전체 유지)
...
- [13] JS-NEWLINE: safe_name_js 줄바꿈 문자 제거
- FIX-PRICE-4  : verified_price → price/change_pct/price_label 필드로 읽기 통일
                 ai_analyzer.py FIX-PRICE-4와 맞춤 (관심종목·히든픽 모두 적용)
- TIER-FILTER-1: _HP_SOURCE_META, _TAG_META에 증권사 항목 추가
"""

import re
import html as _he
from datetime import datetime, timedelta, timezone

KST         = timezone(timedelta(hours=9))
PARA_TITLES = ["📌 시장 개요", "📊 주요 이슈", "🔍 투자 포인트", "⚠️ 리스크 요인", "💡 전망"]

_HP_SOURCE_META = {
    "애널리스트": {"color": "#51cf66", "icon": "📊", "label": "애널리스트"},
    "경제방송TV": {"color": "#ffa94d", "icon": "📺", "label": "경제방송TV"},
    "경제방송":   {"color": "#74c0fc", "icon": "📡", "label": "경제방송"},
    "증권사":     {"color": "#cc5de8", "icon": "🏦", "label": "증권사 채널"},  # TIER-FILTER-1
}
_HP_SOURCE_DEFAULT = {"color": "#adb5bd", "icon": "📌", "label": "단독 언급"}

_INDICATOR_DEFS = [
    ("코스피",             ["kospi",           "KOSPI"]),
    ("코스닥",             ["kosdaq",          "KOSDAQ"]),
    ("나스닥",             ["nasdaq",          "NASDAQ"]),
    ("S&P500",             ["sp500",           "SP500", "s&p500"]),
    ("다우존스",           ["dow",             "DOW",   "dow_jones"]),
    ("KOSPI200 야간선물",  ["kospi200_night",  "kospi200night"]),
    ("KOSDAQ150 야간선물", ["kosdaq150_night", "kosdaq150night"]),
    ("달러/원",            ["usd_krw",         "USD_KRW", "usd"]),
]

_TAG_META = {
    "뉴스":       {"bg": "#2d3a4a", "color": "#74c0fc"},
    "경제방송":   {"bg": "#3a2d1a", "color": "#ffa94d"},
    "경제방송TV": {"bg": "#3a2d1a", "color": "#ffa94d"},
    "유튜브":     {"bg": "#2d1a3a", "color": "#cc5de8"},
    "애널리스트": {"bg": "#1a3a2d", "color": "#51cf66"},
    "증권사":     {"bg": "#2d1a3a", "color": "#cc5de8"},  # TIER-FILTER-1
}

# FIX-SIG-4: 키를 list of tuples로 명확화
_SIGNAL_MAP = [
    (["강력매수", "매수", "buy", "긍정", "positive"], ("signal-positive", "#51cf66", "긍정")),
    (["관망", "hold", "중립", "neutral"],             ("signal-neutral",  "#adb5bd", "중립")),
    (["매도", "sell", "부정", "negative"],            ("signal-negative", "#74c0fc", "부정")),
]
_SIGNAL_DEFAULT = ("signal-neutral", "#adb5bd", "중립")


# ──────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────

def _resolve_signal(signal: str) -> tuple:
    if not signal:
        return _SIGNAL_DEFAULT
    sig_l = signal.strip().lower()
    for keywords, meta in _SIGNAL_MAP:
        if any(k in sig_l for k in keywords):
            return meta
    return _SIGNAL_DEFAULT


def _safe_chart_key(prefix: str, name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9가-힣]", "_", name)
    return f"{prefix}_{safe}"


def _safe_js_str(s: str) -> str:
    # [13] JS-NEWLINE: 줄바꿈 문자 제거 후 JS 이스케이프
    s = s.replace('\r', '').replace('\n', ' ')
    return s.replace("\\", "\\\\").replace("'", "\\'")


# [5] DAWN-NONE: None 값 float() 변환 TypeError 방어용 헬퍼
def _safe_float(d: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(d.get(key) or default)
    except (TypeError, ValueError):
        return default


def _indicator_badge(label: str, value, pct, direction: str = "",
                     is_premarket: bool = False) -> str:
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

    # [4] IND-VAL: float 통일 후 정수 여부 판단으로 변환 로직 개선
    try:
        num = float(str(value).replace(',', '').replace(' ', ''))
        val_str = f"{num:,.0f}" if num == int(num) else f"{num:,.2f}"
    except Exception:
        val_str = str(value)

    pct_str   = f"{pct_num:+.2f}%"
    pre_label = (' <span style="font-size:.65rem;color:#adb5bd;">(전일 종가)</span>'
                 if is_premarket else "")
    return (
        f'<div class="indicator-badge">'
        f'<span class="ind-label">{label}{pre_label}</span>'
        f'<span class="ind-value">{val_str}</span>'
        f'<span class="ind-pct" style="color:{color_map[direction]};">'
        f'{arrow_map[direction]} {pct_str}</span></div>'
    )


def _build_market_indicators(market_overview: dict) -> str:
    if not market_overview:
        return ('<div class="market-indicators">'
                '<p style="color:#666;font-size:.85em;">시장 데이터 없음</p></div>')
    badges = ""
    for label, key_candidates in _INDICATOR_DEFS:
        item = None
        for key in key_candidates:
            item = market_overview.get(key)
            if item and isinstance(item, dict):
                break
        if not item or not isinstance(item, dict):
            continue
        value     = (item.get("value") or item.get("close") or
                     item.get("price") or item.get("index"))
        pct       = (item.get("change_pct") or item.get("pct") or
                     item.get("percent")    or item.get("change_percent"))
        direction = item.get("direction", "")
        is_pre    = item.get("is_premarket", False)
        badge     = _indicator_badge(label, value, pct, direction, is_pre)
        if badge:
            badges += badge
    if not badges:
        return ('<div class="market-indicators">'
                '<p style="color:#666;font-size:.85em;">시장 데이터 없음</p></div>')
    return f'<div class="market-indicators">{badges}</div>'


def _build_dawn_market_html(market_overview: dict) -> str:
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
        pct   = _safe_float(kospi_night, "change_pct")
        val   = _safe_float(kospi_night, "value")
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
        pct   = _safe_float(kosdaq_night, "change_pct")
        val   = _safe_float(kosdaq_night, "value")
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
        usd_val = _safe_float(usd_krw, "value")
        usd_pct = _safe_float(usd_krw, "change_pct")
        if usd_pct >= 0.1:
            usd_label, usd_color, usd_arrow = "원화 약세 (소폭 풋 방향)", "#74c0fc", "▲"
        elif usd_pct <= -0.1:
            usd_label, usd_color, usd_arrow = "원화 강세 (소폭 콜 방향)", "#ff6b6b", "▼"
        else:
            usd_label, usd_color, usd_arrow = "환율 안정", "#adb5bd", "━"
        rows += (
            f'<div class="dawn-row">'
            f'<span class="dawn-icon">💱</span>'
            f'<span class="dawn-name">역외환율</span>'
            f'<span class="dawn-val" style="color:{usd_color};">'
            f'{usd_arrow} {usd_val:,.2f}원 ({usd_pct:+.2f}%) {usd_label}</span>'
            f'</div>'
        )

    kospi_pct  = _safe_float(kospi_night,  "change_pct") if kospi_night  else 0.0
    kosdaq_pct = _safe_float(kosdaq_night, "change_pct") if kosdaq_night else 0.0
    avg_pct    = (kospi_pct + kosdaq_pct) / max(
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
    # [12] SUMMARY-SPLIT: 단락 분리 패턴 개선
    paras = [
        p.strip()
        for p in re.split(r'\n{2,}|\n(?=\d+[\.\)])', market_summary.strip())
        if p.strip()
    ]
    html = ""
    for i, para in enumerate(paras):
        clean = re.sub(r'^\d+[\.\)]\s*', '', para).strip()
        title = PARA_TITLES[i] if i < len(PARA_TITLES) else f"📎 포인트 {i + 1}"
        html += (
            f'<div class="summary-block">'
            f'<div class="summary-title">{title}</div>'
            f'<p class="summary-text">{clean}</p></div>'
        )
    return html or f'<p style="color:#ccc;">{_he.escape(market_summary.strip())}</p>'


def _build_analyst_html(all_data: list) -> str:

    def _report_card(r: dict) -> str:
        stock  = r.get("stock_name", "")
        title  = r.get("report_title") or r.get("title", "")
        brokers_raw = r.get("brokers") or r.get("source_name", "")
        broker = (", ".join(brokers_raw)
                  if isinstance(brokers_raw, list) else str(brokers_raw))
        link   = r.get("link", "")
        is_new = r.get("new_coverage", False)

        if not link and stock:
            enc  = stock.replace(" ", "+")
            link = (f"https://finance.naver.com/research/company_list.naver"
                    f"?searchType=keyword&keyword={enc}")

        new_badge = ('<span class="new-coverage-badge">신규 커버리지</span>'
                     if is_new else "")
        if link:
            title_html = (f'<a href="{link}" target="_blank" rel="noopener" '
                          f'class="analyst-title-link">{_he.escape(title)}</a>')
        else:
            title_html = f'<span class="analyst-title-text">{_he.escape(title)}</span>'

        ai_summary   = r.get("ai_summary", "").strip()
        summary_html = (
            f'<p class="analyst-summary" style="color:#adb5bd;font-size:.88rem;'
            f'margin-top:.4rem;font-style:italic;">💬 {_he.escape(ai_summary)}</p>'
            if ai_summary else ""
        )

        return (
            f'<div class="analyst-card">'
            f'<div class="analyst-card-meta">'
            f'<span class="analyst-stock">{_he.escape(stock)}</span>'
            f'<span class="analyst-broker">{_he.escape(broker)}</span>'
            f'{new_badge}'
            f'</div>'
            f'<div class="analyst-card-title">{title_html}</div>'
            f'{summary_html}'
            f'</div>'
        )

    analyst_items = [d for d in all_data if d.get("source_type") == "애널리스트"]
    if not analyst_items:
        return '<p style="color:#666;">애널리스트 리포트 데이터 없음</p>'

    simultaneous = [r for r in analyst_items
                    if r.get("analyst_category") == "simultaneous"]
    new_cov      = [r for r in analyst_items
                    if r.get("analyst_category") == "new_coverage"]
    single       = [r for r in analyst_items
                    if r.get("analyst_category") not in ("simultaneous", "new_coverage")]

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
    score  = max(0.0, score)
    filled = max(1, round((score / max_score) * 5)) if score > 0 else 0
    filled = min(filled, 5)
    empty  = 5 - filled
    stars  = (
        f'<span class="star filled">{"★" * filled}</span>'
        f'<span class="star empty">{"☆" * empty}</span>'
    )
    return f'<span class="star-rating">{stars}</span>'


def _render_reasons(reasons: list) -> str:
    """히든픽 전용 reasons 렌더링."""
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
                f'{_he.escape(rn)}</span> '
            )
        if rl and rl.startswith(("http://", "https://")):
            text_html = (
                f'<a href="{rl}" target="_blank" rel="noopener" '
                f'style="color:#adb5bd;text-decoration:none;">'
                f'{_he.escape(rd)}</a>'
            )
        else:
            text_html = f'<span style="color:#adb5bd;">{_he.escape(rd)}</span>'
        items += f'<li>{source_html}{text_html}</li>'
    return f'<ul class="reasons-list">{items}</ul>' if items else ""


def _is_positive_signal(sig) -> bool:
    if not sig:
        return False
    sig_l = str(sig).lower()
    return any(k in sig_l for k in ("긍정", "매수", "강력", "positive", "buy"))


def _render_stock_detail(stock: dict) -> str:
    """종목 카드 상세 렌더링. channel_mentions만 렌더링."""
    html = ""

    summary = (stock.get("summary") or stock.get("description") or "").strip()
    if summary:
        html += (
            f'<div class="stock-section">'
            f'<span class="stock-section-label">📋 종목 요약</span>'
            f'<p class="stock-section-text">{_he.escape(summary)}</p></div>'
        )

    catalyst = (stock.get("catalyst") or stock.get("price_trend") or "").strip()
    if catalyst:
        html += (
            f'<div class="stock-section">'
            f'<span class="stock-section-label">🚀 상승 촉매</span>'
            f'<p class="stock-section-text">{_he.escape(catalyst)}</p></div>'
        )

    risk = (stock.get("risk") or "").strip()
    if risk:
        html += (
            f'<div class="stock-section">'
            f'<span class="stock-section-label">⚠️ 리스크</span>'
            f'<p class="stock-section-text">{_he.escape(risk)}</p></div>'
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
                f'{_he.escape(sname)}</span>'
            )
            if url and url.startswith(("http://", "https://")):
                text_html = (
                    f'<a href="{url}" target="_blank" rel="noopener" '
                    f'style="color:#adb5bd;text-decoration:none;">'
                    f'{_he.escape(content)}</a>'
                )
            else:
                text_html = (
                    f'<span style="color:#8b949e;">'
                    f'{_he.escape(content)}</span>'
                )
            cm_items += f'<li>{name_html} {text_html}</li>'
        html += (
            f'<div class="stock-section">'
            f'<span class="stock-section-label">📢 채널별 언급 내용</span>'
            f'<ul class="reasons-list">{cm_items}</ul></div>'
        )

    return html


def _render_ai_strategy(ai_strategy: str) -> str:
    if not ai_strategy or not ai_strategy.strip():
        return '<p style="color:#666;">AI 전략 데이터 없음</p>'

    raw_sections = re.split(r'\n(?=■ )', ai_strategy.strip())
    sections     = [s.strip() for s in raw_sections if s.strip().startswith("■")]

    if not sections:
        return f'<p style="color:#ccc;">{_he.escape(ai_strategy.strip())}</p>'

    icon_map = {
        "핵심 시나리오":        "🎯",
        "섹터 로테이션":        "🔄",
        "오늘의 주목 포인트":   "📌",
        "리스크 시나리오":      "⚠️",
        "애널리스트 종합 시각": "📊",
    }

    html = ""
    for sec in sections:
        lines      = sec.split("\n")
        title_line = lines[0].replace("■ ", "").strip()
        body_lines = [l.strip() for l in lines[1:] if l.strip()]
        icon       = next((v for k, v in icon_map.items() if k in title_line), "📌")

        body_html = ""
        for line in body_lines:
            escaped = _he.escape(line)
            if line.startswith("•") or line.startswith("["):
                body_html += f'<div class="strat-item">{escaped}</div>'
            else:
                body_html += f'<p class="strat-text">{escaped}</p>'

        html += (
            f'<div class="strat-section">'
            f'<div class="strat-title">{icon} {_he.escape(title_line)}</div>'
            f'<div class="strat-body">{body_html}</div>'
            f'</div>'
        )

    return html


def _filter_stocks_tiered(stocks: list, target: int = 10) -> list:
    """
    TIER-FILTER-1과 동기화:
    overlap_count는 ai_analyzer에서 non_news_channel_types 기준으로 이미 설정됨.
    html_generator에서는 그 값을 그대로 사용하여 필터링.

    1차: overlap_count >= 2 (비뉴스 채널타입 2종 이상)
    2차: total_count >= 4
    3차: total_count >= 2
    """
    selected       = []
    selected_names = set()

    for s in stocks:
        cc = s.get("channel_counts", {})
        if cc and s.get("overlap_count", 0) == 0:
            # overlap_count가 아직 0이면 channel_counts 기반으로 재계산 (하위호환)
            safe_count = 0
            for v in cc.values():
                try:
                    if v is not None and int(v) > 0:
                        safe_count += 1
                except (TypeError, ValueError):
                    pass
            s["overlap_count"] = safe_count

    # 1차
    for s in stocks:
        if len(selected) >= target:
            break
        name = s.get("name")
        if not name:
            continue
        if s.get("overlap_count", 0) >= 2 and name not in selected_names:
            selected.append(s)
            selected_names.add(name)

    # 2차
    if len(selected) < target:
        for s in stocks:
            if len(selected) >= target:
                break
            name = s.get("name")
            if not name or name in selected_names:
                continue
            if s.get("total_count", 0) >= 4:
                selected.append(s)
                selected_names.add(name)

    # 3차
    if len(selected) < target:
        for s in stocks:
            if len(selected) >= target:
                break
            name = s.get("name")
            if not name or name in selected_names:
                continue
            if s.get("total_count", 0) >= 2:
                selected.append(s)
                selected_names.add(name)

    return selected


# ──────────────────────────────────────────────
# 주가 표시 헬퍼 — FIX-PRICE-4 / FIX-PRICE-5
# ──────────────────────────────────────────────

def _render_price_html(item: dict) -> str:
    """
    ai_analyzer.py에서 병합한 price/change_pct/price_label 필드를 읽어
    주가 HTML을 생성한다.

    FIX-PRICE-5: 한국 주식시장은 프리마켓 없음.
    - price_label = "현재가"  → 09:00 이후 정규장
    - price_label = "전일종가" → 09:00 이전 (Naver API 반환값이 전일종가)
    - price = 0 또는 없음     → "전일 종가 조회 중" 표시 제거, 빈 상태로 처리
    """
    price       = item.get("price", 0)
    change_pct  = item.get("change_pct", 0.0)
    price_label = item.get("price_label", "전일종가")

    # FIX-PRICE-4 경로: ai_analyzer가 병합한 price 필드
    if isinstance(price, (int, float)) and price > 0:
        try:
            pct_num = float(change_pct)
        except (TypeError, ValueError):
            pct_num = 0.0

        pct_color = "#ff6b6b" if pct_num > 0 else "#74c0fc" if pct_num < 0 else "#adb5bd"
        pct_arrow = "▲" if pct_num > 0 else "▼" if pct_num < 0 else "━"

        label_html = (
            f'<span style="font-size:.7rem;color:#adb5bd;margin-left:.3rem;">'
            f'({price_label})</span>'
        )
        pct_html = (
            f'<span style="font-size:.82rem;color:{pct_color};margin-left:.35rem;">'
            f'{pct_arrow} {pct_num:+.2f}%</span>'
        )
        return (
            f'<span class="price-value">{int(price):,}원</span>'
            f'{pct_html}{label_html}'
        )

    # 레거시 호환: verified_price
    verified = item.get("verified_price")
    if isinstance(verified, int):
        return f'<span class="price-value">{verified:,}원</span>'
    if verified and str(verified).strip() not in ("None", "N/A", ""):
        return f'<span class="price-value">{_he.escape(str(verified))}</span>'

    # FIX-PRICE-5: 미수집 시 "전일 종가 조회 중" 문구 제거
    # → 주가 미수집 상태를 조용히 처리 (빈 span)
    return '<span class="price-value" style="color:#666;">-</span>'


# ──────────────────────────────────────────────
# 메인 HTML 생성
# ──────────────────────────────────────────────

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

    stocks        = data.get("stocks",         [])
    hidden_picks  = data.get("hidden_picks",   [])
    market_sum    = data.get("market_summary", "")
    hot_sectors   = data.get("hot_sectors",    [])
    ai_strategy   = data.get("ai_strategy",    "")
    briefing_date = data.get("briefing_date",  "")

    now_kst = datetime.now(KST)
    if not briefing_date:
        briefing_date = now_kst.strftime("%Y년 %m월 %d일")
    briefing_time = now_kst.strftime("%H:%M")

    filtered_stocks = _filter_stocks_tiered(stocks)

    # FIX-H3: signal 없거나 긍정이면 포함
    filtered_hidden = [
        h for h in hidden_picks
        if not h.get("signal") or _is_positive_signal(h.get("signal"))
    ]

    market_indicators_html = _build_market_indicators(market_overview)
    market_summary_html    = _render_market_summary(market_sum)
    dawn_market_html       = _build_dawn_market_html(market_overview)

    # [6] SECTOR-ESC
    sector_badges_html = ""
    for sector in hot_sectors:
        if isinstance(sector, dict):
            reason_esc = sector.get("reason", "").replace('"', '&quot;')
            name_esc   = _he.escape(sector.get("name", ""))
            sector_badges_html += (
                f'<div class="sector-badge" title="{reason_esc}">'
                f'{name_esc}</div>'
            )
        elif sector:
            sector_badges_html += (
                f'<div class="sector-badge">{_he.escape(str(sector))}</div>'
            )

    # [10] CHART-DUP: 중복 key 방지를 위해 dict 사용
    chart_data_dict = {}
    stocks_html     = ""

    for rank, stock in enumerate(filtered_stocks, 1):
        name         = stock.get("name", "")
        signal       = stock.get("signal", "")
        overlap      = stock.get("overlap_count", 0)
        channel_cnts = stock.get("channel_counts", {})
        naver_code   = stock.get("naver_code") or stock.get("code", "")
        naver_url    = stock.get("naver_url", "")
        chart_b64    = stock.get("chart_base64", "")

        if not naver_url:
            if naver_code:
                naver_url = (f"https://finance.naver.com/item/main.naver"
                             f"?code={naver_code}")
            elif name:
                naver_url = (f"https://finance.naver.com/search/searchResult.naver"
                             f"?query={name.replace(' ', '+')}")

        sig_class, sig_color, signal_label = _resolve_signal(signal)

        source_tags_html = ""
        for src_type, cnt in channel_cnts.items():
            try:
                cnt_int = int(cnt) if cnt is not None else 0
            except (TypeError, ValueError):
                cnt_int = 0
            if cnt_int > 0:
                meta = _TAG_META.get(src_type, {"bg": "#2d2d44", "color": "#adb5bd"})
                source_tags_html += (
                    f'<span class="source-tag" '
                    f'style="background:{meta["bg"]};color:{meta["color"]};">'
                    f'{_he.escape(src_type)} {cnt_int}</span>'
                )

        price_html = _render_price_html(stock)

        if chart_b64:
            chart_key    = _safe_chart_key("chart", name)
            safe_name_js = _safe_js_str(name)
            chart_data_dict[chart_key] = f"data:image/png;base64,{chart_b64}"
            chart_btn_html = (
                f"<button class=\"chart-btn\" "
                f"onclick=\"showChart('{chart_key}','{safe_name_js}')\""
                f">📈 차트 보기</button>"
            )
        elif naver_url:
            chart_btn_html = (
                f'<a href="{naver_url}" target="_blank" rel="noopener" '
                f'class="chart-btn">🔗 Naver 차트</a>'
            )
        else:
            chart_btn_html = ""

        detail_html = _render_stock_detail(stock)

        stocks_html += f"""
<div class="stock-card">
  <div class="stock-card-header">
    <div class="stock-rank">#{rank}</div>
    <div class="stock-name-block">
      <a href="{naver_url}" target="_blank" rel="noopener"
         class="stock-name">{_he.escape(name)}</a>
      <span class="signal-badge {sig_class}"
            style="border-color:{sig_color};color:{sig_color};">{signal_label}</span>
    </div>
    <div class="overlap-badge" title="비뉴스 채널 중복 언급 수">🔥 {overlap}개</div>
  </div>
  <div class="stock-card-body">
    <div class="source-tags">{source_tags_html}</div>
    <div class="price-row">{price_html}{chart_btn_html}</div>
    {detail_html}
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
        naver_code   = hp.get("naver_code") or hp.get("code", "")
        naver_url    = hp.get("naver_url", "")
        chart_b64    = hp.get("chart_base64", "")
        reasons      = hp.get("reasons", [])

        if not naver_url:
            if naver_code:
                naver_url = (f"https://finance.naver.com/item/main.naver"
                             f"?code={naver_code}")
            elif name:
                naver_url = (f"https://finance.naver.com/search/searchResult.naver"
                             f"?query={name.replace(' ', '+')}")

        source_badge_html = _hidden_pick_source_badge(channel_type)
        star_html         = _render_star_rating(weighted_sc)
        pick_badge_html   = f'<span class="hp-score-badge">Pick #{idx}</span>'

        price_html = _render_price_html(hp)

        if chart_b64:
            chart_key    = _safe_chart_key("hpchart", name)
            safe_name_js = _safe_js_str(name)
            chart_data_dict[chart_key] = f"data:image/png;base64,{chart_b64}"
            chart_btn_html = (
                f"<button class=\"chart-btn\" "
                f"onclick=\"showChart('{chart_key}','{safe_name_js}')\""
                f">📈 차트 보기</button>"
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
    <a href="{naver_url}" target="_blank" rel="noopener"
       class="hp-stock-name">{_he.escape(name)}</a>
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

    # [10] CHART-DUP: dict → JS 객체 문자열 변환
    if chart_data_dict:
        entries       = [f'"{k}": "{v}"' for k, v in chart_data_dict.items()]
        chart_data_js = "const chartDataMap = {\n  " + ",\n  ".join(entries) + "\n};"
    else:
        chart_data_js = "const chartDataMap = {};"

    analyst_html  = _build_analyst_html(all_data)
    strategy_html = _render_ai_strategy(ai_strategy)

    css = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:         #0d1117;
  --surface:    #161b22;
  --surface2:   #21262d;
  --border:     #30363d;
  --text:       #e6edf3;
  --text-muted: #8b949e;
  --accent:     #58a6ff;
  --up:         #ff6b6b;
  --down:       #74c0fc;
  --flat:       #adb5bd;
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
  font-size: 1.15rem; font-weight: 700; color: var(--text);
  border-left: 4px solid var(--accent);
  padding-left: .75rem; margin-bottom: 1rem;
}
.market-indicators { display: flex; flex-wrap: wrap; gap: .6rem; }
.indicator-badge {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: .5rem .9rem;
  display: flex; flex-direction: column; align-items: center; min-width: 100px;
}
.ind-label { font-size: .75rem; color: var(--text-muted); margin-bottom: .15rem; }
.ind-value { font-size: .95rem; font-weight: 600; }
.ind-pct   { font-size: .8rem; margin-top: .1rem; }
.dawn-market-box {
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
  border: 1px solid #2d4a6e; border-radius: 12px;
  padding: 1.2rem 1.4rem; margin-bottom: 1.5rem;
}
.dawn-header {
  font-size: 1.05rem; font-weight: 700; color: #74c0fc;
  margin-bottom: .85rem; letter-spacing: .02em;
}
.dawn-row {
  display: flex; align-items: baseline; gap: .6rem;
  margin-bottom: .5rem; flex-wrap: wrap;
}
.dawn-icon { font-size: .9rem; flex-shrink: 0; }
.dawn-name {
  font-size: .9rem; color: var(--text-muted);
  min-width: 160px; flex-shrink: 0;
}
.dawn-val  { font-size: .9rem; font-weight: 600; }
.dawn-summary {
  margin-top: .9rem; padding-top: .7rem;
  border-top: 1px solid #2d4a6e;
  font-size: .9rem; color: var(--text-muted);
}
.dawn-star { color: #74c0fc; margin-right: .3rem; }
.summary-block { margin-bottom: 1.2rem; }
.summary-title {
  font-size: .85rem; font-weight: 700; color: var(--accent);
  margin-bottom: .35rem; text-transform: uppercase; letter-spacing: .04em;
}
.summary-text { font-size: .93rem; color: var(--text-muted); line-height: 1.65; }
.sector-badges { display: flex; flex-wrap: wrap; gap: .5rem; margin-bottom: 1rem; }
.sector-badge {
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: 20px; padding: .3rem .85rem;
  font-size: .82rem; color: var(--accent); cursor: default;
}
.stock-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; margin-bottom: 1rem; overflow: hidden;
}
.stock-card-header {
  display: flex; align-items: center; gap: .75rem;
  padding: .85rem 1rem; border-bottom: 1px solid var(--border);
  background: var(--surface2);
}
.stock-rank {
  font-size: .8rem; font-weight: 700; color: var(--text-muted);
  min-width: 28px;
}
.stock-name-block { display: flex; align-items: center; gap: .5rem; flex: 1; }
.stock-name {
  font-size: 1.05rem; font-weight: 700; color: var(--text);
}
.stock-name:hover { color: var(--accent); }
.signal-badge {
  font-size: .72rem; padding: .15rem .5rem;
  border-radius: 4px; border: 1px solid; font-weight: 600;
}
.overlap-badge {
  font-size: .78rem; color: #ffa94d; font-weight: 600;
  white-space: nowrap;
}
.stock-card-body { padding: .85rem 1rem; }
.source-tags { display: flex; flex-wrap: wrap; gap: .4rem; margin-bottom: .6rem; }
.source-tag {
  font-size: .72rem; padding: .15rem .5rem; border-radius: 4px; font-weight: 600;
}
.price-row {
  display: flex; align-items: center; gap: .75rem;
  margin-bottom: .75rem; flex-wrap: wrap;
}
.price-value { font-size: 1.05rem; font-weight: 700; }
.chart-btn {
  font-size: .78rem; padding: .3rem .75rem; border-radius: 6px;
  background: var(--surface2); border: 1px solid var(--border);
  color: var(--accent); cursor: pointer; text-decoration: none;
  transition: background .15s;
}
.chart-btn:hover { background: var(--border); text-decoration: none; }
.stock-section { margin-bottom: .75rem; }
.stock-section-label {
  font-size: .78rem; font-weight: 700; color: var(--accent);
  display: block; margin-bottom: .25rem;
}
.stock-section-text {
  font-size: .88rem; color: var(--text-muted); line-height: 1.6;
}
.reasons-list {
  list-style: none; padding: 0; margin: 0;
  display: flex; flex-direction: column; gap: .4rem;
}
.reasons-list li { font-size: .85rem; line-height: 1.55; }
.reason-source {
  font-size: .72rem; padding: .1rem .4rem; border-radius: 3px;
  font-weight: 600; margin-right: .3rem;
}
.hidden-pick-card {
  background: linear-gradient(135deg, var(--surface) 0%, #1a1f2e 100%);
  border: 1px solid #3d4f6e; border-radius: 12px; margin-bottom: 1rem;
  overflow: hidden;
}
.hp-card-header {
  display: flex; align-items: center; gap: .75rem;
  padding: .85rem 1rem; border-bottom: 1px solid #3d4f6e;
  background: #1a2235; flex-wrap: wrap;
}
.hp-badges { display: flex; gap: .4rem; align-items: center; }
.hp-source-badge {
  font-size: .72rem; padding: .2rem .6rem; border-radius: 20px; font-weight: 700;
}
.hp-score-badge {
  font-size: .72rem; padding: .2rem .6rem; border-radius: 20px;
  background: #ffa94d22; color: #ffa94d; border: 1px solid #ffa94d55;
  font-weight: 700;
}
.hp-stock-name {
  font-size: 1.05rem; font-weight: 700; color: var(--text); flex: 1;
}
.hp-stock-name:hover { color: var(--accent); }
.star-rating { font-size: 1rem; }
.star.filled { color: #ffa94d; }
.star.empty  { color: #444; }
.hp-card-body { padding: .85rem 1rem; }
.analyst-card {
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: 8px; padding: .75rem 1rem; margin-bottom: .6rem;
}
.analyst-card-meta {
  display: flex; align-items: center; gap: .5rem;
  margin-bottom: .3rem; flex-wrap: wrap;
}
.analyst-stock { font-weight: 700; font-size: .9rem; }
.analyst-broker { font-size: .8rem; color: var(--text-muted); }
.new-coverage-badge {
  font-size: .68rem; padding: .1rem .4rem; border-radius: 4px;
  background: #51cf6622; color: #51cf66; border: 1px solid #51cf6655;
  font-weight: 700;
}
.analyst-title-link, .analyst-title-text {
  font-size: .88rem; color: var(--text-muted); line-height: 1.5;
}
.analyst-title-link:hover { color: var(--accent); }
.analyst-category-title {
  font-size: .85rem; font-weight: 700; color: var(--accent);
  margin: 1rem 0 .5rem; padding-left: .5rem;
  border-left: 3px solid var(--accent);
}
.strat-section { margin-bottom: 1.2rem; }
.strat-title {
  font-size: .9rem; font-weight: 700; color: var(--accent);
  margin-bottom: .4rem;
}
.strat-body { font-size: .88rem; color: var(--text-muted); }
.strat-text { margin-bottom: .3rem; line-height: 1.6; }
.strat-item { margin-bottom: .25rem; line-height: 1.55; }
.modal-overlay {
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,.75); z-index: 1000;
  align-items: center; justify-content: center;
}
.modal-overlay.active { display: flex; }
.modal-box {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 1.5rem; max-width: 700px; width: 95%;
  position: relative;
}
.modal-title {
  font-size: 1rem; font-weight: 700; margin-bottom: 1rem; color: var(--text);
}
.modal-close {
  position: absolute; top: .75rem; right: .75rem;
  background: none; border: none; color: var(--text-muted);
  font-size: 1.2rem; cursor: pointer;
}
.modal-close:hover { color: var(--text); }
#modal-chart-img { width: 100%; border-radius: 8px; }
@media (max-width: 600px) {
  .briefing-header h1 { font-size: 1.4rem; }
  .stock-card-header  { flex-wrap: wrap; }
  .stock-name         { font-size: .95rem; }
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

  <div class="briefing-header">
    <h1>📈 AI 주식 브리핑</h1>
    <div class="subtitle">{briefing_date} · 생성 시각 {briefing_time} KST</div>
  </div>

  <!-- 시장 지표 -->
  <div class="section">
    <div class="section-title">📊 시장 지표</div>
    {market_indicators_html}
  </div>

  <!-- 새벽시장 -->
  {dawn_market_html}

  <!-- 시장 요약 -->
  <div class="section">
    <div class="section-title">🗞 오늘의 시장 요약</div>
    {market_summary_html}
  </div>

  <!-- 핫 섹터 -->
  <div class="section">
    <div class="section-title">🔥 오늘의 핫 섹터</div>
    <div class="sector-badges">{sector_badges_html}</div>
  </div>

  <!-- 관심종목 -->
  <div class="section">
    <div class="section-title">👀 오늘의 관심종목</div>
    {stocks_html}
  </div>

  <!-- 오늘의 픽 -->
  <div class="section">
    <div class="section-title">💎 오늘의 픽</div>
    {hidden_html}
  </div>

  <!-- 애널리스트 리포트 -->
  <div class="section">
    <div class="section-title">📋 오늘의 증권사 리포트</div>
    {analyst_html}
  </div>

  <!-- AI 전략 -->
  <div class="section">
    <div class="section-title">🤖 AI 투자 전략</div>
    {strategy_html}
  </div>

</div>

<!-- 차트 모달 -->
<div class="modal-overlay" id="chartModal">
  <div class="modal-box">
    <button class="modal-close" onclick="closeChart()">✕</button>
    <div class="modal-title" id="modal-chart-title"></div>
    <img id="modal-chart-img" src="" alt="차트">
  </div>
</div>

<script>
{chart_data_js}

function showChart(key, name) {{
  const src = chartDataMap[key];
  if (!src) return;
  document.getElementById('modal-chart-title').textContent = name + ' 주가 차트';
  document.getElementById('modal-chart-img').src = src;
  document.getElementById('chartModal').classList.add('active');
}}

function closeChart() {{
  document.getElementById('chartModal').classList.remove('active');
  document.getElementById('modal-chart-img').src = '';
}}

document.getElementById('chartModal').addEventListener('click', function(e) {{
  if (e.target === this) closeChart();
}});
</script>

</body>
</html>"""
