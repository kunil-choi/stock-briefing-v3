# analyzer/html_generator.py
"""
AI 주식 브리핑 HTML 생성 엔진

수정 이력:
- BUG-9      : _indicator_badge에서 0.0을 유효값으로 처리
- BUG-NEW-6  : overlap_count를 channel_counts에서 재산출
- BUG-H5     : signal 필터 확대
- BUG-W-3    : reason 필드 렌더링 우선순위 (detail > reason > text)
- BUG-M6     : archive 절대경로 안전 처리
- CR-NEW-1   : chart_key 특수문자 안전 변환
- SIM-P5-1   : onclick 작은따옴표 이스케이프
- FIX-CSS-1  : stock-card 종목명 누락 CSS 수정
- FIX-TV-1   : 경제방송TV 섹션 source_type 매칭 보완
- FIX-IND-1  : 시장 지표 키 유연 탐색
- V2-CARD    : 종목 카드에 summary/catalyst/risk/channel_mentions 섹션 추가
- FIX-ANA-1  : _build_analyst_html 들여쓰기 버그 수정 (_report_card 내부화)
- FIX-ARC-1  : 사용하지 않는 archive_html 생성 블록 제거
- FIX-SIG-1  : _is_positive_signal에서 "상승" 키워드 제거
- FIX-SIG-2  : filtered_hidden signal 없을 때 오늘의 픽 전체 미표시 버그 수정
- FIX-JS-1   : showChart 방어코드 추가 (key 없을 때 빈 이미지 방지)
- FIX-ANA-2  : analyst-card 제목 말줄임 제거, 웹에서 전체 표시
- FIX-HP-1   : 오늘의 픽 가중치 점수를 별점 5개로 시각화, signal 텍스트 제거
- FIX-RSN-1  : reasons 목록에 source_name 표시 추가
- BUG-3      : signal 매핑 버그 수정
- NIGHT-1    : 야간선물 지표 추가 및 새벽시장 포인트 섹션 신규
- NIGHT-1B   : 역외환율 표시 버그 수정
- REM-TV-1   : 경제방송TV 섹션 제거
- FIX-DUP-1  : channel_mentions/reasons 중복 렌더링 제거 — channel_mentions만 표시
- FIX-ANA-3  : 애널리스트 카드 컬러 복원 + 리포트 본문(summary) 한 단락 추가
- FIX-PRICE-1: 장 전(is_premarket=True) 시 "전일 종가" 라벨 표시
- FIX-STRAT-3: ai_strategy 문자열을 구조화된 섹션 HTML로 렌더링
- FIX-FILTER-2: filtered_stocks 단계별 선정 로직 (1차→2차→3차, 최대 10개)
- FIX-BUG-3  : _filter_stocks_tiered int(v) 타입 안전 처리
- FIX-SIG-3  : 관심종목 signal 뱃지를 긍정/중립/부정으로 변경
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

# FIX-SIG-3: signal → 긍정/중립/부정 3단계로 단순화
_SIGNAL_MAP = {
    ("강력매수", "매수", "buy", "긍정", "positive"): ("signal-positive", "#51cf66", "긍정"),
    ("관망", "hold", "중립", "neutral"):              ("signal-neutral",  "#adb5bd", "중립"),
    ("매도", "sell", "부정", "negative"):             ("signal-negative", "#74c0fc", "부정"),
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
    try:
        val_str = (f"{float(value):,.2f}" if "." in str(value)
                   else f"{int(str(value).replace(',', '').replace(' ', '')):,}")
    except Exception:
        val_str = str(value)
    pct_str   = f"{pct_num:+.2f}%"
    pre_label = ' <span style="font-size:.65rem;color:#555;">(전일)</span>' if is_premarket else ""
    return (
        f'<div class="indicator-badge">'
        f'<span class="ind-label">{label}{pre_label}</span>'
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
        return '<div class="market-indicators"><p style="color:#666;font-size:.85em;">시장 데이터 없음</p></div>'
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

    kospi_pct  = float(kospi_night.get("change_pct",  0)) if kospi_night  else 0.0
    kosdaq_pct = float(kosdaq_night.get("change_pct", 0)) if kosdaq_night else 0.0
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


# ── FIX-ANA-3: 애널리스트 카드 컬러 복원 + 리포트 본문 추가 ──────────────────

def _build_analyst_html(all_data: list) -> str:
    def _report_card(r: dict) -> str:
        stock   = r.get("stock_name", "")
        title   = r.get("report_title") or r.get("title", "")
        broker  = r.get("brokers") or r.get("source_name", "")
        link    = r.get("link", "")
        is_new  = r.get("new_coverage", False)
        summary = (r.get("summary") or r.get("content") or "").strip()
        summary_short = summary[:150] + ("…" if len(summary) > 150 else "") if summary else ""

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

        summary_html = (
            f'<p class="analyst-summary">{summary_short}</p>'
            if summary_short else ""
        )

        return (
            f'<div class="analyst-card">'
            f'<div class="analyst-card-meta">'
            f'<span class="analyst-stock">{stock}</span>'
            f'<span class="analyst-broker">{broker}</span>'
            f'{new_badge}'
            f'</div>'
            f'<div class="analyst-card-title">{title_html}</div>'
            f'{summary_html}'
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
    """히든픽 전용 reasons 렌더링 (관심종목은 channel_mentions만 사용)."""
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
    """
    종목 카드 상세 렌더링.
    FIX-DUP-1: channel_mentions만 렌더링, reasons는 여기서 처리하지 않음.
    """
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


# ── FIX-STRAT-3: ai_strategy 문자열 → 구조화된 HTML 렌더링 ───────────────────

def _render_ai_strategy(ai_strategy: str) -> str:
    if not ai_strategy or not ai_strategy.strip():
        return '<p style="color:#666;">AI 전략 데이터 없음</p>'

    sections = re.split(r'(?=■ )', ai_strategy.strip())
    html     = ""

    for sec in sections:
        sec = sec.strip()
        if not sec:
            continue
        lines      = sec.split("\n")
        title_line = lines[0].replace("■ ", "").strip()
        body_lines = [l.strip() for l in lines[1:] if l.strip()]

        icon_map = {
            "핵심 시나리오":    "🎯",
            "포트폴리오 배분":  "📊",
            "종목별 매매 계획": "📋",
            "현금 정책":        "💵",
            "리스크 시나리오":  "⚠️",
            "테마 상관관계":    "🔗",
        }
        icon = next((v for k, v in icon_map.items() if k in title_line), "📌")

        body_html = ""
        for line in body_lines:
            if line.startswith("•") or line.startswith("["):
                body_html += f'<div class="strat-item">{line}</div>'
            else:
                body_html += f'<p class="strat-text">{line}</p>'

        html += (
            f'<div class="strat-section">'
            f'<div class="strat-title">{icon} {title_line}</div>'
            f'<div class="strat-body">{body_html}</div>'
            f'</div>'
        )

    return html or f'<p style="color:#ccc;">{ai_strategy.strip()}</p>'


# ── FIX-FILTER-2 + FIX-BUG-3: 단계별 관심종목 필터링 ────────────────────────

def _filter_stocks_tiered(stocks: list, target: int = 10) -> list:
    """
    1차: overlap_count >= 2 (서로 다른 채널타입 2종 이상)
    2차: total_count >= 4  (채널타입 무관 4회 이상, 1차 미달 시 추가)
    3차: total_count >= 3  (채널타입 무관 3회 이상, 2차 후도 미달 시 추가)
    FIX-BUG-3: channel_counts 값 타입 안전 처리 (None, 문자열 방어)
    """
    selected       = []
    selected_names = set()

    # overlap_count 재계산 — FIX-BUG-3: 타입 안전 처리
    for s in stocks:
        cc = s.get("channel_counts", {})
        if cc:
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
        if s.get("overlap_count", 0) >= 2 and s.get("name") not in selected_names:
            selected.append(s)
            selected_names.add(s.get("name"))

    # 2차
    if len(selected) < target:
        for s in stocks:
            if len(selected) >= target:
                break
            if s.get("name") in selected_names:
                continue
            if s.get("total_count", 0) >= 4:
                selected.append(s)
                selected_names.add(s.get("name"))

    # 3차
    if len(selected) < target:
        for s in stocks:
            if len(selected) >= target:
                break
            if s.get("name") in selected_names:
                continue
            if s.get("total_count", 0) >= 3:
                selected.append(s)
                selected_names.add(s.get("name"))

    return selected


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

    filtered_stocks = _filter_stocks_tiered(stocks)

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

        if not naver_url:
            if naver_code:
                naver_url = f"https://finance.naver.com/item/main.naver?code={naver_code}"
            elif name:
                naver_url = (f"https://finance.naver.com/search/searchResult.naver"
                             f"?query={name.replace(' ', '+')}")

        # FIX-SIG-3: 긍정/중립/부정 3단계 뱃지
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
                    f'{src_type} {cnt_int}</span>'
                )

        if isinstance(price, int):
            price_html = f'<span class="price-value">{price:,}원</span>'
        elif price and str(price).strip() not in ("None", "N/A", ""):
            price_html = f'<span class="price-value">{price}</span>'
        else:
            price_html = '<span class="price-value" style="color:#666;">전일 종가 조회 중</span>'

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

        detail_html = _render_stock_detail(stock)

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
            price_html = '<span class="price-value" style="color:#666;">전일 종가 조회 중</span>'

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

    analyst_html  = _build_analyst_html(all_data)
    strategy_html = _render_ai_strategy(ai_strategy)

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
  font-size: 1.05rem; font-weight: 700; color: #74c0fc;
  margin-bottom: .85rem; letter-spacing: .02em;
}
.dawn-row {
  display: flex; align-items: baseline; gap: .6rem;
  margin-bottom: .5rem; flex-wrap: wrap;
}
.dawn-icon { font-size: .9rem; flex-shrink: 0; }
.dawn-name { font-size: .9rem; color: var(--text-muted); min-width: 160px; flex-shrink: 0; }
.dawn-val  { font-size: .9rem; font-weight: 600; }
.dawn-summary {
  margin-top: .85rem; padding-top: .75rem; border-top: 1px solid #2d4a6e;
  font-size: .88rem; color: var(--text-muted);
  display: flex; align-items: center; gap: .4rem; flex-wrap: wrap;
}
.dawn-star { color: #ffd43b; font-size: .9rem; }
.summary-block {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 1rem 1.2rem; margin-bottom: .75rem;
}
.summary-title { font-weight: 700; margin-bottom: .4rem; color: var(--accent); }
.summary-text  { color: var(--text-muted); font-size: .95rem; }
.sector-list  { display: flex; flex-wrap: wrap; gap: .5rem; }
.sector-badge {
  background: #1c2d3a; color: #74c0fc;
  border: 1px solid #1e4a6e; border-radius: 20px;
  padding: .3rem .8rem; font-size: .85rem; font-weight: 600; cursor: default;
}
.stock-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; margin-bottom: 1rem; overflow: hidden;
}
.stock-card-header {
  display: flex; align-items: center; gap: .75rem;
  padding: .9rem 1.2rem; border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}
.stock-rank {
  font-size: 1rem; font-weight: 800; color: var(--accent);
  min-width: 2rem; text-align: center;
}
.stock-name-block { display: flex; align-items: center; gap: .5rem; flex: 1; flex-wrap: wrap; }
.stock-name { font-size: 1.05rem; font-weight: 700; color: var(--text); }
.stock-name:hover { color: var(--accent); text-decoration: underline; }
.signal-badge {
  font-size: .75rem; font-weight: 700;
  border: 1.5px solid; border-radius: 4px;
  padding: .15rem .45rem; white-space: nowrap;
}
/* FIX-SIG-3: 긍정/중립/부정 색상 */
.signal-positive { border-color: #51cf66 !important; color: #51cf66 !important; }
.signal-neutral  { border-color: #adb5bd !important; color: #adb5bd !important; }
.signal-negative { border-color: #74c0fc !important; color: #74c0fc !important; }
.overlap-badge {
  font-size: .8rem; color: var(--up);
  background: #2d1a1a; border-radius: 6px;
  padding: .2rem .5rem; white-space: nowrap; margin-left: auto;
}
.stock-card-body { padding: .9rem 1.2rem; }
.source-tags { display: flex; flex-wrap: wrap; gap: .35rem; margin-bottom: .6rem; }
.source-tag {
  font-size: .72rem; font-weight: 600;
  border-radius: 4px; padding: .15rem .4rem;
}
.price-row {
  display: flex; align-items: center; gap: .75rem;
  margin-bottom: .75rem; flex-wrap: wrap;
}
.price-value { font-size: .95rem; font-weight: 700; color: var(--text); }
.chart-btn {
  font-size: .78rem; padding: .25rem .65rem;
  background: var(--surface2); color: var(--accent);
  border: 1px solid var(--border); border-radius: 6px;
  cursor: pointer; text-decoration: none;
}
.chart-btn:hover { background: #1c2d3a; }
.stock-section { margin-bottom: .65rem; }
.stock-section-label {
  font-size: .75rem; font-weight: 700; color: var(--accent);
  display: block; margin-bottom: .2rem;
}
.stock-section-text { font-size: .88rem; color: var(--text-muted); line-height: 1.55; }
.reasons-list { list-style: none; padding: 0; margin: .3rem 0 0; }
.reasons-list li {
  font-size: .83rem; color: var(--text-muted);
  padding: .3rem 0; border-bottom: 1px solid #21262d; line-height: 1.5;
}
.reasons-list li:last-child { border-bottom: none; }
.reason-source {
  font-size: .7rem; font-weight: 700;
  border-radius: 3px; padding: .1rem .3rem; margin-right: .3rem;
}
.hidden-pick-card {
  background: linear-gradient(135deg, #1a1a2e 0%, #161b22 100%);
  border: 1px solid #2d3a4a; border-radius: 12px; margin-bottom: 1rem; overflow: hidden;
}
.hp-card-header {
  display: flex; align-items: center; gap: .6rem;
  padding: .85rem 1.2rem; border-bottom: 1px solid #2d3a4a; flex-wrap: wrap;
}
.hp-badges { display: flex; gap: .4rem; flex-wrap: wrap; }
.hp-source-badge {
  font-size: .72rem; font-weight: 700;
  border-radius: 4px; padding: .15rem .45rem;
}
.hp-score-badge {
  font-size: .72rem; font-weight: 700;
  background: #1c2d3a; color: var(--accent);
  border: 1px solid #1e4a6e; border-radius: 4px; padding: .15rem .45rem;
}
.hp-stock-name {
  font-size: 1.05rem; font-weight: 700; color: var(--text); flex: 1;
}
.hp-stock-name:hover { color: var(--accent); text-decoration: underline; }
.star-rating { font-size: 1rem; letter-spacing: .05em; }
.star.filled { color: #ffd43b; }
.star.empty  { color: #3d3d3d; }
.hp-card-body { padding: .9rem 1.2rem; }
.analyst-category-title {
  font-size: .85rem; font-weight: 700; color: var(--accent);
  margin: 1rem 0 .5rem; padding-bottom: .3rem;
  border-bottom: 1px solid var(--border);
}
.analyst-card {
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: 8px; padding: .75rem 1rem; margin-bottom: .6rem;
}
.analyst-card-meta {
  display: flex; gap: .5rem; flex-wrap: wrap;
  align-items: center; margin-bottom: .35rem;
}
.analyst-stock {
  font-size: .78rem; font-weight: 700;
  background: #1a3a2d; color: #51cf66;
  border-radius: 4px; padding: .1rem .35rem;
}
.analyst-broker {
  font-size: .75rem; color: var(--text-muted);
  background: #2d2d44; border-radius: 4px; padding: .1rem .35rem;
}
.new-coverage-badge {
  font-size: .7rem; font-weight: 700;
  background: #3a1a1a; color: #ff6b6b;
  border: 1px solid #6e1e1e; border-radius: 4px; padding: .1rem .35rem;
}
.analyst-card-title {
  font-size: .88rem; color: var(--text); line-height: 1.5;
  word-break: keep-all; white-space: normal;
}
.analyst-title-link { color: var(--text); }
.analyst-title-link:hover { color: var(--accent); text-decoration: underline; }
.analyst-title-text { color: var(--text); }
.analyst-summary {
  font-size: .82rem; color: var(--text-muted);
  margin-top: .4rem; line-height: 1.55;
  border-top: 1px solid var(--border); padding-top: .4rem;
}
.strat-section {
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: 8px; padding: .85rem 1rem; margin-bottom: .75rem;
}
.strat-title {
  font-size: .9rem; font-weight: 700; color: var(--accent); margin-bottom: .45rem;
}
.strat-body { font-size: .85rem; color: var(--text-muted); }
.strat-item {
  background: #1c2d3a; border-radius: 4px;
  padding: .3rem .6rem; margin-bottom: .3rem; line-height: 1.5;
}
.strat-text { margin-bottom: .3rem; line-height: 1.55; }
.modal-overlay {
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,.85); z-index: 1000;
  align-items: center; justify-content: center;
}
.modal-overlay.active { display: flex; }
.modal-box {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 1.5rem; max-width: 700px; width: 90%; position: relative;
}
.modal-close {
  position: absolute; top: .75rem; right: 1rem;
  font-size: 1.4rem; cursor: pointer; color: var(--text-muted);
  background: none; border: none;
}
.modal-close:hover { color: var(--text); }
#modal-chart-img { width: 100%; border-radius: 8px; margin-top: .5rem; }
@media (max-width: 600px) {
  .briefing-header h1 { font-size: 1.4rem; }
  .stock-card-header   { padding: .7rem .9rem; }
  .stock-card-body     { padding: .7rem .9rem; }
  .hp-card-header      { padding: .7rem .9rem; }
  .hp-card-body        { padding: .7rem .9rem; }
  .dawn-name           { min-width: 120px; }
}"""

    html = f"""<!DOCTYPE html>
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
    <div class="subtitle">{briefing_date} &nbsp;|&nbsp; 생성 시각 {briefing_time} KST</div>
  </div>

  {dawn_market_html}

  <div class="section">
    <div class="section-title">📊 시장 지표</div>
    {market_indicators_html}
  </div>

  <div class="section">
    <div class="section-title">📝 시장 요약</div>
    {market_summary_html}
  </div>

  <div class="section">
    <div class="section-title">🔥 핫 섹터</div>
    <div class="sector-list">{sector_badges_html or '<p style="color:#666;">데이터 없음</p>'}</div>
  </div>

  <div class="section">
    <div class="section-title">👀 관심 종목</div>
    {stocks_html}
  </div>

  <div class="section">
    <div class="section-title">💡 오늘의 픽</div>
    {hidden_html}
  </div>

  <div class="section">
    <div class="section-title">📋 애널리스트 리포트</div>
    {analyst_html}
  </div>

  <div class="section">
    <div class="section-title">🤖 AI 투자 전략</div>
    {strategy_html}
  </div>

</div>

<!-- 차트 모달 -->
<div class="modal-overlay" id="chartModal">
  <div class="modal-box">
    <button class="modal-close" onclick="closeChart()">✕</button>
    <div id="modal-chart-title" style="font-weight:700;margin-bottom:.5rem;"></div>
    <img id="modal-chart-img" src="" alt="차트">
  </div>
</div>

<script>
{chart_data_js}

function showChart(key, name) {{
  if (!chartDataMap[key]) return;
  document.getElementById('modal-chart-title').textContent = name + ' 차트';
  document.getElementById('modal-chart-img').src = chartDataMap[key];
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

    return html
