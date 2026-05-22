"""
HTML 생성기 - v3
AI 분석 결과를 HTML 브리핑 페이지로 변환
"""
import os
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

KST = timezone(timedelta(hours=9))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "stock2026!")


def generate_html(
    analysis_result: dict,
    archive_dates: list = None,
    channels_data: dict = None,
    gh_repo: str = "",
) -> str:
    """
    AI 분석 결과를 받아 완성된 HTML 페이지 생성.

    analysis_result 키 (ai_analyzer.py 기준):
      briefing_date       : "2026-05-22"
      market_summary      : 문자열 (AI가 생성한 시장 요약 텍스트)
      section1_stocks     : 섹션1 종목 리스트
      section2_stocks     : 섹션2 종목 리스트
      section3_stocks     : 섹션3 종목 리스트
      investment_strategy : 종합 투자전략 텍스트
    """
    now_kst = datetime.now(KST)

    # ── 키 이름 ai_analyzer.py와 일치 ────────────────────────────
    date_str        = analysis_result.get("briefing_date", now_kst.strftime("%Y-%m-%d"))
    market_summary  = analysis_result.get("market_summary", "")
    section1_stocks = analysis_result.get("section1_stocks", [])
    section2_stocks = analysis_result.get("section2_stocks", [])
    section3_stocks = analysis_result.get("section3_stocks", [])
    strategy        = analysis_result.get("investment_strategy", "")

    # ── 아카이브 링크 ─────────────────────────────────────────────
    archive_html = _build_archive_links(archive_dates or [])

    # ── 각 섹션 HTML ─────────────────────────────────────────────
    market_html = _format_market_summary(market_summary)
    s1_html     = _render_stock_cards(section1_stocks, section="section1")
    s2_html     = _render_stock_cards(section2_stocks, section="section2")
    s3_html     = _render_section3_cards(section3_stocks)

    # ── 어드민 비밀번호 주입 ─────────────────────────────────────
    _inject_admin_password()

    css = _get_css()
    js  = _get_js()

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>주식 브리핑 {date_str}</title>
  <style>{css}</style>
</head>
<body>
  <header class="header">
    <div class="header-inner">
      <h1 class="logo">📈 주식 브리핑</h1>
      <div class="header-right">
        <span class="date-badge">{date_str}</span>
        <a href="admin/" class="admin-btn">⚙️ 관리</a>
      </div>
    </div>
  </header>

  {archive_html}

  <main class="container">
    {market_html}

    <nav class="tab-nav" role="tablist">
      <button class="tab-btn active" data-tab="section1" role="tab">
        🎬 유튜브 브리핑
      </button>
      <button class="tab-btn" data-tab="section2" role="tab">
        📺 증권TV 분석
      </button>
      <button class="tab-btn" data-tab="section3" role="tab">
        📊 애널리스트 리포트
      </button>
      <button class="tab-btn" data-tab="strategy" role="tab">
        🧭 투자 전략
      </button>
    </nav>

    <section id="section1" class="tab-content active">
      <h2 class="section-title">🎬 오늘의 유튜브 브리핑</h2>
      {s1_html if s1_html else '<p class="empty-msg">수집된 데이터가 없습니다.</p>'}
    </section>

    <section id="section2" class="tab-content">
      <h2 class="section-title">📺 증권TV 핵심 분석</h2>
      {s2_html if s2_html else '<p class="empty-msg">수집된 데이터가 없습니다.</p>'}
    </section>

    <section id="section3" class="tab-content">
      <h2 class="section-title">📊 애널리스트 리포트</h2>
      {s3_html if s3_html else '<p class="empty-msg">수집된 데이터가 없습니다.</p>'}
    </section>

    <section id="strategy" class="tab-content">
      <h2 class="section-title">🧭 AI 투자 전략 제언</h2>
      <div class="strategy-box">
        {strategy.replace(chr(10), '<br>') if strategy else '데이터 없음'}
      </div>
    </section>
  </main>

  <footer class="footer">
    <p>⚠️ 본 브리핑은 AI 분석 기반으로 투자 권유가 아닙니다. 최종 판단은 본인의 책임입니다.</p>
    <p>Generated at {now_kst.strftime('%Y-%m-%d %H:%M')} KST</p>
  </footer>

  <script>{js}</script>
</body>
</html>"""

    return html


# ══════════════════════════════════════════════════════════════
#  시장 요약
# ══════════════════════════════════════════════════════════════

def _format_market_summary(summary) -> str:
    """
    시장 요약 HTML 생성.
    ai_analyzer.py는 summary를 문자열(텍스트)로 전달하므로
    문자열과 dict 두 가지 형태 모두 처리.
    """
    if not summary:
        return ""

    # ai_analyzer가 전달하는 형태: 순수 텍스트 문자열
    if isinstance(summary, str):
        formatted = summary.replace("\n", "<br>")
        return f"""
<div class="market-summary-text">
  <h2 class="section-title">📰 오늘의 시장 요약</h2>
  <div class="market-text">{formatted}</div>
</div>"""

    # dict 형태 (향후 확장용 — KOSPI/KOSDAQ 수치 제공 시)
    kospi  = summary.get("kospi",   {})
    kosdaq = summary.get("kosdaq",  {})
    usd_krw= summary.get("usd_krw", {})
    sp500  = summary.get("sp500",   {})

    def fmt_change(val):
        if val is None:
            return ""
        try:
            fval = float(val)
            cls  = "up" if fval > 0 else ("down" if fval < 0 else "flat")
            sign = "▲" if fval > 0 else ("▼" if fval < 0 else "")
            return f'<span class="change {cls}">{sign}{abs(fval):.2f}%</span>'
        except (ValueError, TypeError):
            return str(val)

    return f"""
<div class="market-summary">
  <div class="market-card">
    <div class="market-label">KOSPI</div>
    <div class="market-value">{kospi.get('value', '-')}</div>
    {fmt_change(kospi.get('change_pct'))}
  </div>
  <div class="market-card">
    <div class="market-label">KOSDAQ</div>
    <div class="market-value">{kosdaq.get('value', '-')}</div>
    {fmt_change(kosdaq.get('change_pct'))}
  </div>
  <div class="market-card">
    <div class="market-label">USD/KRW</div>
    <div class="market-value">{usd_krw.get('value', '-')}</div>
    {fmt_change(usd_krw.get('change_pct'))}
  </div>
  <div class="market-card">
    <div class="market-label">S&P 500</div>
    <div class="market-value">{sp500.get('value', '-')}</div>
    {fmt_change(sp500.get('change_pct'))}
  </div>
</div>"""


# ══════════════════════════════════════════════════════════════
#  섹션 1 / 2 카드 렌더링 (AI 분석 결과 종목 카드)
# ══════════════════════════════════════════════════════════════

def _render_stock_cards(stocks: list, section: str = "section1") -> str:
    """
    섹션1(유튜브), 섹션2(증권TV) 공통 종목 카드 렌더링.
    ai_analyzer가 반환한 종목 객체 구조:
      name, krx_code, signal, description, price_trend,
      price_display, catalyst, risk, reasons[]
    """
    if not stocks:
        return ""

    cards = []
    for stock in stocks:
        name          = stock.get("name", "")
        krx_code      = stock.get("krx_code", "")
        signal        = stock.get("signal", "중립")
        description   = stock.get("description", "")
        price_display = stock.get("price_display", "")
        price_trend   = stock.get("price_trend", "")
        catalyst      = stock.get("catalyst", "")
        risk          = stock.get("risk", "")
        reasons       = stock.get("reasons", [])

        signal_cls = {"긍정": "positive", "부정": "negative", "중립": "neutral"}.get(signal, "neutral")
        signal_icon = {"긍정": "▲", "부정": "▼", "중립": "–"}.get(signal, "–")

        # 주가 배지
        price_badge = ""
        if price_display:
            price_badge = f'<span class="price-badge">{price_display}</span>'
        elif krx_code:
            price_badge = f'<span class="price-badge code">{krx_code}</span>'

        # 선정 이유
        reasons_html = _render_reasons(reasons)

        # 주가 흐름 (실제 데이터 prefix 포함)
        trend_html = ""
        if price_trend:
            trend_lines = price_trend.replace("\n", "<br>")
            trend_html = f'<div class="price-trend">{trend_lines}</div>'

        # 촉매/리스크
        detail_html = ""
        if catalyst:
            detail_html += f'<div class="detail-item catalyst">🚀 {catalyst}</div>'
        if risk:
            detail_html += f'<div class="detail-item risk">⚠️ {risk}</div>'

        cards.append(f"""
<div class="stock-card signal-{signal_cls}">
  <div class="stock-card-header">
    <div class="stock-name-row">
      <span class="stock-name">{name}</span>
      {f'<span class="stock-code">{krx_code}</span>' if krx_code else ''}
      <span class="signal-badge {signal_cls}">{signal_icon} {signal}</span>
    </div>
    {price_badge}
  </div>
  <div class="stock-card-body">
    {f'<p class="stock-desc">{description}</p>' if description else ''}
    {trend_html}
    {detail_html}
    {reasons_html}
  </div>
</div>""")

    return "\n".join(cards)


# ══════════════════════════════════════════════════════════════
#  섹션 3 카드 렌더링 (애널리스트 리포트)
# ══════════════════════════════════════════════════════════════

def _render_section3_cards(stocks: list) -> str:
    """
    섹션3 애널리스트 리포트 종목 카드 렌더링.
    ai_analyzer가 반환한 종목 객체 구조:
      name, krx_code, analyst_category, signal,
      description, price_display, price_trend,
      catalyst, risk, broker, analyst_name,
      target_price, opinion, reasons[]
    """
    if not stocks:
        return ""

    # analyst_category 순서: simultaneous → new_coverage → first_in_6months
    order = {"simultaneous": 0, "new_coverage": 1, "first_in_6months": 2}
    sorted_stocks = sorted(stocks, key=lambda s: order.get(s.get("analyst_category", ""), 3))

    cards = []
    for stock in sorted_stocks:
        name             = stock.get("name", "")
        krx_code         = stock.get("krx_code", "")
        analyst_category = stock.get("analyst_category", "")
        opinion          = stock.get("opinion", "")
        target_price     = stock.get("target_price", "")
        broker           = stock.get("broker", "")
        analyst_name     = stock.get("analyst_name", "")
        description      = stock.get("description", "")
        price_display    = stock.get("price_display", "")
        price_trend      = stock.get("price_trend", "")
        catalyst         = stock.get("catalyst", "")
        risk             = stock.get("risk", "")
        reasons          = stock.get("reasons", [])

        # 카테고리 뱃지
        cat_label = {
            "simultaneous":    "🔥 복수 증권사 동시 언급",
            "new_coverage":    "🆕 신규 커버리지",
            "first_in_6months":"📌 단일 증권사 언급",
        }.get(analyst_category, "")
        cat_cls = {
            "simultaneous":    "cat-simultaneous",
            "new_coverage":    "cat-new",
            "first_in_6months":"cat-first",
        }.get(analyst_category, "")

        # 의견 클래스
        opinion_cls = ""
        op_upper = opinion.upper()
        if "매수" in opinion or "BUY" in op_upper or "비중확대" in opinion:
            opinion_cls = "buy"
        elif "중립" in opinion or "HOLD" in op_upper or "시장수익률" in opinion:
            opinion_cls = "hold"
        elif "매도" in opinion or "SELL" in op_upper:
            opinion_cls = "sell"

        # 주가 흐름
        trend_html = ""
        if price_trend:
            trend_lines = price_trend.replace("\n", "<br>")
            trend_html = f'<div class="price-trend">{trend_lines}</div>'

        # 촉매/리스크
        detail_html = ""
        if catalyst:
            detail_html += f'<div class="detail-item catalyst">🚀 {catalyst}</div>'
        if risk:
            detail_html += f'<div class="detail-item risk">⚠️ {risk}</div>'

        reasons_html = _render_reasons(reasons)

        cards.append(f"""
<div class="report-card {cat_cls}">
  <div class="report-header">
    <div class="stock-name-row">
      <span class="stock-name">{name}</span>
      {f'<span class="stock-code">{krx_code}</span>' if krx_code else ''}
      {f'<span class="cat-badge {cat_cls}">{cat_label}</span>' if cat_label else ''}
    </div>
    <div class="report-meta-row">
      {f'<span class="opinion {opinion_cls}">{opinion}</span>' if opinion else ''}
      {f'<span class="target-price">목표가 {int(target_price):,}원</span>' if target_price and str(target_price).isdigit() else (f'<span class="target-price">목표가 {target_price}</span>' if target_price else '')}
      {f'<span class="price-badge">{price_display}</span>' if price_display else ''}
    </div>
  </div>
  <div class="report-broker">
    {f'<span class="broker">{broker}</span>' if broker else ''}
    {f'<span class="analyst-name">담당: {analyst_name}</span>' if analyst_name else ''}
  </div>
  <div class="report-body">
    {f'<p class="stock-desc">{description}</p>' if description else ''}
    {trend_html}
    {detail_html}
    {reasons_html}
  </div>
</div>""")

    return "\n".join(cards)


# ══════════════════════════════════════════════════════════════
#  공통 헬퍼
# ══════════════════════════════════════════════════════════════

def _render_reasons(reasons: list) -> str:
    """선정 이유 목록 렌더링 (최대 3개)"""
    if not reasons:
        return ""

    items_html = ""
    for r in reasons[:3]:
        if isinstance(r, dict):
            src_type = r.get("source_type", "")
            src_name = r.get("source_name", "")
            detail   = r.get("detail", "")
            src_url  = r.get("source_url", "")
            label    = f"[{src_type}] {src_name}" if src_name else src_type
            if src_url:
                items_html += f'<li><a href="{src_url}" target="_blank" rel="noopener"><strong>{label}</strong></a>: {detail}</li>'
            else:
                items_html += f'<li><strong>{label}</strong>: {detail}</li>'
        else:
            items_html += f"<li>{r}</li>"

    return f'<ul class="reasons">{items_html}</ul>'


def _inject_admin_password() -> None:
    """
    docs/admin/index.html의 %%ADMIN_PASSWORD%% 플레이스홀더를
    실제 비밀번호로 교체.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    admin_dir     = os.path.join(base_dir, "docs", "admin")
    template_path = os.path.join(admin_dir, "index.html.template")
    output_path   = os.path.join(admin_dir, "index.html")

    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("%%ADMIN_PASSWORD%%", ADMIN_PASSWORD)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        print("  ✅ admin 비밀번호 주입 완료 (템플릿 기반)")
    elif os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            content = f.read()
        if "%%ADMIN_PASSWORD%%" in content:
            content = content.replace("%%ADMIN_PASSWORD%%", ADMIN_PASSWORD)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)
            print("  ✅ admin 비밀번호 주입 완료 (직접 교체)")
        else:
            print("  ℹ️ admin 비밀번호: 플레이스홀더 없음 (이미 주입됨)")
    else:
        print("  ⚠️ docs/admin/index.html 파일 없음")


def _build_archive_links(dates: list) -> str:
    """아카이브 링크 HTML 생성"""
    if not dates:
        return ""

    links = []
    for d in sorted(dates, reverse=True)[:10]:
        links.append(f'<a href="archive/{d}.html" class="archive-link">{d}</a>')

    links_html = "\n".join(links)
    return f"""
<div class="archive-bar">
  <span class="archive-label">📁 이전 브리핑:</span>
  {links_html}
</div>"""


# ══════════════════════════════════════════════════════════════
#  CSS
# ══════════════════════════════════════════════════════════════

def _get_css() -> str:
    return """
:root {
  --bg: #0f0f13;
  --surface: #1a1a22;
  --surface2: #252530;
  --accent: #6c63ff;
  --accent2: #ff6584;
  --text: #e8e8f0;
  --text-muted: #8888aa;
  --border: #2e2e3e;
  --up: #26c281;
  --down: #e74c3c;
  --radius: 12px;
  --shadow: 0 4px 20px rgba(0,0,0,0.4);
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Noto Sans KR', 'Apple SD Gothic Neo', sans-serif;
  line-height: 1.6;
}

/* ── 헤더 ── */
.header {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 100;
  padding: 12px 20px;
}
.header-inner {
  max-width: 1200px; margin: 0 auto;
  display: flex; justify-content: space-between; align-items: center;
}
.logo { font-size: 1.4rem; font-weight: 700; color: var(--accent); }
.header-right { display: flex; align-items: center; gap: 12px; }
.date-badge {
  background: var(--surface2); padding: 4px 12px;
  border-radius: 20px; font-size: 0.85rem; color: var(--text-muted);
}
.admin-btn {
  background: var(--accent); color: white;
  padding: 6px 14px; border-radius: 8px;
  text-decoration: none; font-size: 0.85rem; transition: opacity 0.2s;
}
.admin-btn:hover { opacity: 0.85; }

/* ── 아카이브 바 ── */
.archive-bar {
  background: var(--surface); padding: 8px 20px;
  border-bottom: 1px solid var(--border);
  font-size: 0.8rem; overflow-x: auto; white-space: nowrap;
  display: flex; align-items: center; gap: 8px;
}
.archive-label { color: var(--text-muted); }
.archive-link {
  color: var(--accent); text-decoration: none;
  padding: 2px 8px; border-radius: 4px;
  border: 1px solid var(--border); transition: background 0.2s;
}
.archive-link:hover { background: var(--surface2); }

/* ── 컨테이너 ── */
.container { max-width: 1200px; margin: 0 auto; padding: 20px; }

/* ── 시장 요약 (텍스트형) ── */
.market-summary-text {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px 24px;
  margin-bottom: 24px;
}
.market-text {
  font-size: 0.92rem;
  color: var(--text);
  line-height: 1.9;
}

/* ── 시장 요약 (수치 카드형, 향후 확장) ── */
.market-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px; margin-bottom: 24px;
}
.market-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 16px; text-align: center;
}
.market-label { font-size: 0.75rem; color: var(--text-muted); margin-bottom: 6px; }
.market-value { font-size: 1.4rem; font-weight: 700; margin-bottom: 4px; }
.change { font-size: 0.85rem; font-weight: 600; }
.change.up   { color: var(--up); }
.change.down { color: var(--down); }
.change.flat { color: var(--text-muted); }

/* ── 탭 ── */
.tab-nav { display: flex; gap: 8px; margin-bottom: 20px; flex-wrap: wrap; }
.tab-btn {
  background: var(--surface); border: 1px solid var(--border);
  color: var(--text-muted); padding: 8px 16px;
  border-radius: 8px; cursor: pointer; font-size: 0.9rem; transition: all 0.2s;
}
.tab-btn:hover { border-color: var(--accent); color: var(--text); }
.tab-btn.active { background: var(--accent); border-color: var(--accent); color: white; }
.tab-content { display: none; }
.tab-content.active { display: block; }
.section-title {
  font-size: 1.2rem; margin-bottom: 16px; color: var(--text);
  border-left: 3px solid var(--accent); padding-left: 12px;
}

/* ── 종목 카드 (섹션1/2) ── */
.stock-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); margin-bottom: 16px;
  overflow: hidden; transition: border-color 0.2s, transform 0.2s;
}
.stock-card:hover { border-color: var(--accent); transform: translateY(-2px); }
.stock-card.signal-positive { border-left: 3px solid var(--up); }
.stock-card.signal-negative { border-left: 3px solid var(--down); }
.stock-card.signal-neutral  { border-left: 3px solid var(--text-muted); }

.stock-card-header {
  padding: 14px 16px 10px;
  display: flex; justify-content: space-between; align-items: flex-start;
  flex-wrap: wrap; gap: 8px;
  border-bottom: 1px solid var(--border);
}
.stock-name-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.stock-name { font-size: 1.1rem; font-weight: 700; }
.stock-code {
  font-size: 0.75rem; color: var(--accent); font-family: monospace;
  background: var(--surface2); padding: 2px 6px; border-radius: 4px;
}
.signal-badge {
  font-size: 0.75rem; font-weight: 600; padding: 2px 8px; border-radius: 4px;
}
.signal-badge.positive { background: rgba(38,194,129,0.15); color: var(--up); }
.signal-badge.negative { background: rgba(231,76,60,0.15);  color: var(--down); }
.signal-badge.neutral  { background: var(--surface2); color: var(--text-muted); }

.price-badge {
  font-size: 0.82rem; font-weight: 600; color: var(--accent);
  background: var(--surface2); padding: 4px 10px; border-radius: 6px;
  white-space: nowrap;
}
.price-badge.code { color: var(--text-muted); }

.stock-card-body { padding: 14px 16px; }
.stock-desc { font-size: 0.88rem; color: var(--text); line-height: 1.7; margin-bottom: 10px; }

.price-trend {
  font-size: 0.82rem; color: var(--text-muted);
  background: var(--surface2); border-radius: 6px;
  padding: 8px 12px; margin-bottom: 10px; line-height: 1.6;
}

.detail-item {
  font-size: 0.83rem; padding: 6px 10px; border-radius: 6px; margin-bottom: 6px;
}
.detail-item.catalyst { background: rgba(38,194,129,0.08); color: var(--up); }
.detail-item.risk     { background: rgba(231,76,60,0.08);  color: var(--down); }

.reasons {
  font-size: 0.82rem; color: var(--text-muted);
  padding-left: 16px; margin-top: 8px; line-height: 1.7;
}
.reasons li { margin-bottom: 4px; }
.reasons a  { color: var(--accent); text-decoration: none; }
.reasons a:hover { text-decoration: underline; }

/* ── 리포트 카드 (섹션3) ── */
.report-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); margin-bottom: 16px;
  overflow: hidden; transition: border-color 0.2s, transform 0.2s;
}
.report-card:hover { border-color: var(--accent); transform: translateY(-2px); }
.report-card.cat-simultaneous { border-left: 3px solid #f39c12; }
.report-card.cat-new          { border-left: 3px solid var(--accent); }
.report-card.cat-first        { border-left: 3px solid var(--text-muted); }

.report-header {
  padding: 14px 16px 8px;
  border-bottom: 1px solid var(--border);
}
.report-meta-row {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 6px;
}
.cat-badge {
  font-size: 0.72rem; font-weight: 700; padding: 2px 8px; border-radius: 4px;
}
.cat-badge.cat-simultaneous { background: rgba(243,156,18,0.15); color: #f39c12; }
.cat-badge.cat-new          { background: rgba(108,99,255,0.15); color: var(--accent); }
.cat-badge.cat-first        { background: var(--surface2); color: var(--text-muted); }

.opinion {
  padding: 2px 10px; border-radius: 4px; font-size: 0.8rem; font-weight: 600;
}
.opinion.buy  { background: rgba(38,194,129,0.15); color: var(--up); }
.opinion.hold { background: rgba(255,165,0,0.15);  color: orange; }
.opinion.sell { background: rgba(231,76,60,0.15);  color: var(--down); }

.target-price { font-size: 0.85rem; color: var(--accent); font-weight: 600; }

.report-broker {
  padding: 6px 16px;
  font-size: 0.8rem; color: var(--text-muted);
  display: flex; gap: 12px;
  border-bottom: 1px solid var(--border);
}
.broker { font-weight: 600; }
.analyst-name { }

.report-body { padding: 14px 16px; }

/* ── 투자전략 ── */
.strategy-box {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 24px;
  line-height: 1.9; font-size: 0.95rem;
}

.empty-msg { color: var(--text-muted); text-align: center; padding: 40px; }

/* ── 푸터 ── */
.footer {
  text-align: center; padding: 20px;
  color: var(--text-muted); font-size: 0.8rem;
  border-top: 1px solid var(--border); margin-top: 40px;
}

/* ── 반응형 ── */
@media (max-width: 600px) {
  .tab-btn { font-size: 0.8rem; padding: 6px 10px; }
  .stock-card-header { flex-direction: column; }
}
"""


# ══════════════════════════════════════════════════════════════
#  JavaScript
# ══════════════════════════════════════════════════════════════

def _get_js() -> str:
    return """
document.addEventListener('DOMContentLoaded', function() {
  const tabBtns     = document.querySelectorAll('.tab-btn');
  const tabContents = document.querySelectorAll('.tab-content');

  tabBtns.forEach(btn => {
    btn.addEventListener('click', function() {
      const target = this.dataset.tab;
      tabBtns.forEach(b => b.classList.remove('active'));
      tabContents.forEach(c => c.classList.remove('active'));
      this.classList.add('active');
      const targetEl = document.getElementById(target);
      if (targetEl) targetEl.classList.add('active');
    });
  });
});
"""
