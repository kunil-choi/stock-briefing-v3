"""
HTML 생성기 - v3
AI 분석 결과를 HTML 브리핑 페이지로 변환
"""
import os
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import quote  # ✅ 수정: requests.utils.quote → 표준 라이브러리

KST = timezone(timedelta(hours=9))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "stock2026!")


def generate_html(analysis_result: dict, archive_dates: list = None) -> str:
    """
    AI 분석 결과를 받아 완성된 HTML 페이지 생성
    analysis_result 구조:
      {
        "date": "2026-05-22",
        "market_summary": {...},
        "section1": [...],
        "section2": [...],
        "section3": [...],
        "strategy": "..."
      }
    """
    now_kst = datetime.now(KST)
    date_str = analysis_result.get("date", now_kst.strftime("%Y-%m-%d"))
    market_summary = analysis_result.get("market_summary", {})
    section1_items = analysis_result.get("section1", [])
    section2_items = analysis_result.get("section2", [])
    section3_items = analysis_result.get("section3", [])
    strategy = analysis_result.get("strategy", "")

    archive_html = _build_archive_links(archive_dates or [])
    market_html = _format_market_summary(market_summary)
    s1_html = _render_section1_cards(section1_items)
    s2_html = _render_section2_cards(section2_items)
    s3_html = _render_section3_cards(section3_items)

    css = _get_css()
    js = _get_js()

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


def _format_market_summary(summary: dict) -> str:
    """시장 요약 섹션 HTML 생성"""
    if not summary:
        return ""

    kospi = summary.get("kospi", {})
    kosdaq = summary.get("kosdaq", {})
    usd_krw = summary.get("usd_krw", {})
    sp500 = summary.get("sp500", {})

    def fmt_change(val):
        if val is None:
            return ""
        try:
            fval = float(val)
            cls = "up" if fval > 0 else ("down" if fval < 0 else "flat")
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


def _render_section1_cards(items: list) -> str:
    """섹션1 유튜브 카드 렌더링"""
    if not items:
        return ""

    cards = []
    for item in items:
        title = item.get("title", "")
        channel = item.get("channel_name", item.get("channel", ""))
        url = item.get("url", "#")
        thumbnail = item.get("thumbnail", "")
        summary = item.get("ai_summary", item.get("summary", ""))
        reasons = item.get("reasons", [])
        published = item.get("published", "")

        reasons_html = _render_reasons(reasons)
        thumb_html = f'<img src="{thumbnail}" alt="" class="card-thumb" loading="lazy">' if thumbnail else ""

        cards.append(f"""
<div class="video-card">
  <a href="{url}" target="_blank" rel="noopener">
    {thumb_html}
    <div class="card-body">
      <div class="card-channel">{channel}</div>
      <h3 class="card-title">{title}</h3>
      {f'<p class="card-summary">{summary}</p>' if summary else ''}
      {reasons_html}
      {f'<span class="card-date">{published[:10]}</span>' if published else ''}
    </div>
  </a>
</div>""")

    return "\n".join(cards)


def _render_section2_cards(items: list) -> str:
    """섹션2 증권TV 카드 렌더링 (섹션1과 동일 구조)"""
    return _render_section1_cards(items)


def _render_section3_cards(items: list) -> str:
    """섹션3 애널리스트 리포트 카드 렌더링"""
    if not items:
        return ""

    cards = []
    for item in items:
        stock_name = item.get("stock_name", "")
        opinion = item.get("opinion", "")
        target_price = item.get("target_price", "")
        broker = item.get("broker", "")
        summary = item.get("ai_summary", item.get("summary", ""))
        date = item.get("date", "")
        url = item.get("url", "#")

        opinion_cls = ""
        if "매수" in opinion or "BUY" in opinion.upper():
            opinion_cls = "buy"
        elif "중립" in opinion or "HOLD" in opinion.upper():
            opinion_cls = "hold"
        elif "매도" in opinion or "SELL" in opinion.upper():
            opinion_cls = "sell"

        cards.append(f"""
<div class="report-card">
  <div class="report-header">
    <span class="stock-name">{stock_name}</span>
    {f'<span class="opinion {opinion_cls}">{opinion}</span>' if opinion else ''}
    {f'<span class="target-price">목표가 {target_price}</span>' if target_price else ''}
  </div>
  <div class="report-meta">
    {f'<span class="broker">{broker}</span>' if broker else ''}
    {f'<span class="report-date">{date}</span>' if date else ''}
  </div>
  {f'<p class="report-summary">{summary}</p>' if summary else ''}
  {f'<a href="{url}" target="_blank" rel="noopener" class="report-link">리포트 보기 →</a>' if url != "#" else ''}
</div>""")

    return "\n".join(cards)


def _render_reasons(reasons: list) -> str:
    """선정 이유 렌더링"""
    if not reasons:
        return ""
    items_html = "".join(f"<li>{r}</li>" for r in reasons[:3])
    return f'<ul class="reasons">{items_html}</ul>'


def _inject_admin_password() -> None:
    """
    docs/admin/index.html의 %%ADMIN_PASSWORD%% 플레이스홀더를
    실제 비밀번호로 교체.
    템플릿 파일(docs/admin/index.html.template)이 있으면 그걸 사용,
    없으면 기존 파일에서 직접 교체.
    """
    admin_dir = os.path.join("docs", "admin")
    template_path = os.path.join(admin_dir, "index.html.template")
    output_path = os.path.join(admin_dir, "index.html")

    if os.path.exists(template_path):
        # 템플릿 기반 생성 (권장)
        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("%%ADMIN_PASSWORD%%", ADMIN_PASSWORD)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  ✅ admin 비밀번호 주입 완료 (템플릿 기반)")
    elif os.path.exists(output_path):
        # 기존 파일에서 직접 교체 (폴백)
        with open(output_path, "r", encoding="utf-8") as f:
            content = f.read()
        if "%%ADMIN_PASSWORD%%" in content:
            content = content.replace("%%ADMIN_PASSWORD%%", ADMIN_PASSWORD)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"  ✅ admin 비밀번호 주입 완료 (직접 교체)")
        else:
            print(f"  ℹ️ admin 비밀번호: 플레이스홀더 없음 (이미 주입됨)")
    else:
        print(f"  ⚠️ admin/index.html 파일 없음")


def _build_archive_links(dates: list) -> str:
    """아카이브 링크 HTML 생성"""
    if not dates:
        return ""

    links = []
    for d in sorted(dates, reverse=True)[:10]:
        encoded = quote(str(d))
        links.append(f'<a href="archive/{d}.html" class="archive-link">{d}</a>')

    links_html = "\n".join(links)
    return f"""
<div class="archive-bar">
  <span class="archive-label">📁 이전 브리핑:</span>
  {links_html}
</div>"""


def _get_css() -> str:
    """페이지 CSS 반환"""
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

.header {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 100;
  padding: 12px 20px;
}

.header-inner {
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.logo {
  font-size: 1.4rem;
  font-weight: 700;
  color: var(--accent);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.date-badge {
  background: var(--surface2);
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 0.85rem;
  color: var(--text-muted);
}

.admin-btn {
  background: var(--accent);
  color: white;
  padding: 6px 14px;
  border-radius: 8px;
  text-decoration: none;
  font-size: 0.85rem;
  transition: opacity 0.2s;
}

.admin-btn:hover { opacity: 0.85; }

.archive-bar {
  background: var(--surface);
  padding: 8px 20px;
  border-bottom: 1px solid var(--border);
  font-size: 0.8rem;
  overflow-x: auto;
  white-space: nowrap;
  display: flex;
  align-items: center;
  gap: 8px;
}

.archive-label { color: var(--text-muted); }

.archive-link {
  color: var(--accent);
  text-decoration: none;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid var(--border);
  transition: background 0.2s;
}

.archive-link:hover { background: var(--surface2); }

.container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 20px;
}

.market-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}

.market-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px;
  text-align: center;
}

.market-label {
  font-size: 0.75rem;
  color: var(--text-muted);
  margin-bottom: 6px;
  text-transform: uppercase;
}

.market-value {
  font-size: 1.4rem;
  font-weight: 700;
  margin-bottom: 4px;
}

.change { font-size: 0.85rem; font-weight: 600; }
.change.up { color: var(--up); }
.change.down { color: var(--down); }
.change.flat { color: var(--text-muted); }

.tab-nav {
  display: flex;
  gap: 8px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}

.tab-btn {
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text-muted);
  padding: 8px 16px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.9rem;
  transition: all 0.2s;
}

.tab-btn:hover { border-color: var(--accent); color: var(--text); }

.tab-btn.active {
  background: var(--accent);
  border-color: var(--accent);
  color: white;
}

.tab-content { display: none; }
.tab-content.active { display: block; }

.section-title {
  font-size: 1.2rem;
  margin-bottom: 16px;
  color: var(--text);
  border-left: 3px solid var(--accent);
  padding-left: 12px;
}

.video-card, .report-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  margin-bottom: 16px;
  overflow: hidden;
  transition: border-color 0.2s, transform 0.2s;
}

.video-card:hover, .report-card:hover {
  border-color: var(--accent);
  transform: translateY(-2px);
}

.video-card a {
  display: flex;
  gap: 0;
  text-decoration: none;
  color: var(--text);
  flex-direction: row;
}

.card-thumb {
  width: 180px;
  min-width: 180px;
  height: 100px;
  object-fit: cover;
}

.card-body {
  padding: 14px;
  flex: 1;
}

.card-channel {
  font-size: 0.75rem;
  color: var(--accent);
  margin-bottom: 4px;
  font-weight: 600;
}

.card-title {
  font-size: 0.95rem;
  font-weight: 600;
  margin-bottom: 8px;
  line-height: 1.4;
}

.card-summary {
  font-size: 0.85rem;
  color: var(--text-muted);
  margin-bottom: 8px;
}

.reasons {
  font-size: 0.8rem;
  color: var(--text-muted);
  padding-left: 16px;
  margin-bottom: 6px;
}

.card-date {
  font-size: 0.75rem;
  color: var(--text-muted);
}

.report-card { padding: 16px; }

.report-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.stock-name {
  font-size: 1rem;
  font-weight: 700;
}

.opinion {
  padding: 2px 10px;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: 600;
}

.opinion.buy { background: rgba(38,194,129,0.2); color: var(--up); }
.opinion.hold { background: rgba(255,165,0,0.2); color: orange; }
.opinion.sell { background: rgba(231,76,60,0.2); color: var(--down); }

.target-price {
  font-size: 0.85rem;
  color: var(--accent);
  font-weight: 600;
}

.report-meta {
  font-size: 0.8rem;
  color: var(--text-muted);
  margin-bottom: 8px;
  display: flex;
  gap: 10px;
}

.report-summary {
  font-size: 0.85rem;
  color: var(--text-muted);
  margin-bottom: 8px;
}

.report-link {
  font-size: 0.8rem;
  color: var(--accent);
  text-decoration: none;
}

.report-link:hover { text-decoration: underline; }

.strategy-box {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 24px;
  line-height: 1.8;
  font-size: 0.95rem;
}

.empty-msg {
  color: var(--text-muted);
  text-align: center;
  padding: 40px;
}

.footer {
  text-align: center;
  padding: 20px;
  color: var(--text-muted);
  font-size: 0.8rem;
  border-top: 1px solid var(--border);
  margin-top: 40px;
}

@media (max-width: 600px) {
  .video-card a { flex-direction: column; }
  .card-thumb { width: 100%; height: 180px; min-width: unset; }
  .tab-btn { font-size: 0.8rem; padding: 6px 10px; }
}
"""


def _get_js() -> str:
    """페이지 JavaScript 반환"""
    return """
document.addEventListener('DOMContentLoaded', function() {
  const tabBtns = document.querySelectorAll('.tab-btn');
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
