# analyzer/html_generator.py
"""
HTML 생성기 - v3
v2 디자인 언어를 계승하되 4개 섹션(시장요약 + 섹션1~3 + 종합투자전략) 구조로 확장
admin/index.html의 %%ADMIN_PASSWORD%% 플레이스홀더 자동 치환 포함
"""
import os
import requests
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def generate_html(data: dict, channels_data: dict = None, gh_repo: str = "") -> str:
    now_kst = datetime.now(KST)
    briefing_date     = data.get("briefing_date", now_kst.strftime("%Y-%m-%d"))
    briefing_datetime = now_kst.strftime("%Y년 %m월 %d일 %H:%M")

    market_summary   = data.get("market_summary", "")
    section1_stocks  = data.get("section1_stocks", [])
    section2_stocks  = data.get("section2_stocks", [])
    section3_stocks  = data.get("section3_stocks", [])
    investment_strategy = data.get("investment_strategy", "")

    # 전일 날짜 (섹션 2 기준 날짜)
    yesterday = (now_kst - timedelta(days=1)).strftime("%Y년 %m월 %d일")

    # ── 시장 요약 HTML ──
    formatted_summary = _format_market_summary(market_summary)

    # ── 섹션 1 HTML ──
    section1_html = _render_stock_cards(section1_stocks, section="section1")

    # ── 섹션 2 HTML ──
    section2_html = _render_stock_cards(section2_stocks, section="section2")

    # ── 섹션 3 HTML ──
    section3_html = _render_section3_cards(section3_stocks)

    # ── 아카이브 링크 ──
    archive_links = _build_archive_links(gh_repo)

    # ── HTML 조립 ──
    html = _build_full_html(
        briefing_date=briefing_date,
        briefing_datetime=briefing_datetime,
        yesterday=yesterday,
        formatted_summary=formatted_summary,
        section1_html=section1_html,
        section2_html=section2_html,
        section3_html=section3_html,
        investment_strategy=investment_strategy,
        archive_links=archive_links,
    )

    # ✅ admin/index.html 비밀번호 치환
    _inject_admin_password()

    return html


def _inject_admin_password():
    """
    docs/admin/index.html 의 %%ADMIN_PASSWORD%% 플레이스홀더를
    환경변수 ADMIN_PASSWORD 값으로 치환합니다.
    GitHub Actions에서 매 빌드 시 자동 주입됩니다.
    """
    admin_html_path = "docs/admin/index.html"
    password = os.getenv("ADMIN_PASSWORD", "stock2026!")

    if not os.path.exists(admin_html_path):
        print("  [관리자] admin/index.html 파일 없음, 치환 건너뜀")
        return

    try:
        with open(admin_html_path, "r", encoding="utf-8") as f:
            content = f.read()

        if "%%ADMIN_PASSWORD%%" in content:
            content = content.replace("%%ADMIN_PASSWORD%%", password)
            with open(admin_html_path, "w", encoding="utf-8") as f:
                f.write(content)
            print("  [관리자] admin/index.html 비밀번호 주입 완료")
        else:
            print("  [관리자] admin/index.html 플레이스홀더 없음 (이미 치환됨)")
    except Exception as e:
        print(f"  [관리자] 비밀번호 주입 실패: {e}")


def _format_market_summary(market_summary: str) -> str:
    """시장 요약 텍스트를 HTML 블록으로 변환"""
    formatted = ""
    paragraphs = market_summary.split("\n\n")
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if ":" in para:
            colon_idx  = para.index(":")
            title_part = para[:colon_idx].strip()
            body_part  = para[colon_idx + 1:].strip()
            formatted += (
                '<div class="summary-block">'
                '<h3 class="summary-subtitle">' + title_part + '</h3>'
                '<p class="summary-text">' + body_part + '</p>'
                '</div>\n'
            )
        else:
            formatted += (
                '<div class="summary-block">'
                '<p class="summary-text">' + para + '</p>'
                '</div>\n'
            )
    return formatted


def _render_stock_cards(stocks: list, section: str = "section1") -> str:
    """종목 카드 HTML 렌더링 (섹션 1, 2 공통)"""
    if not stocks:
        return '<p class="no-data">수집된 데이터가 없습니다.</p>'

    html = ""
    for i, stock in enumerate(stocks, 1):
        name          = stock.get("name", "")
        krx_code      = stock.get("krx_code", "")
        signal        = stock.get("signal", "중립")
        description   = stock.get("description", "")
        price_trend   = stock.get("price_trend", "")
        price_display = stock.get("price_display", "")
        catalyst      = stock.get("catalyst", "")
        risk          = stock.get("risk", "")
        reasons       = stock.get("reasons", [])
        verified_price = stock.get("verified_price")

        # 감성 레이블 클래스
        if signal == "긍정":
            signal_class = "signal-positive"
        elif signal == "부정":
            signal_class = "signal-negative"
        else:
            signal_class = "signal-neutral"

        # 주가 정보 텍스트
        price_info_text = ""
        if verified_price and verified_price.get("price"):
            p          = verified_price
            change_val = p.get("change", "")
            if change_val.startswith("+"):
                sign = "▲"
            elif change_val.startswith("-"):
                sign = "▼"
            else:
                sign = ""
            change_display  = change_val.lstrip("+-") if change_val else ""
            change_pct_display = p.get("change_pct", "")
            if sign and change_display:
                price_info_text = (
                    " (" + p["price"] + "원 "
                    + sign + change_display + " "
                    + change_pct_display + ")"
                )
            else:
                price_info_text = " (" + (p.get("price") or "") + "원)"
        elif price_display:
            price_info_text = " (" + price_display + ")"

        # 차트 버튼
        if krx_code:
            naver_url = "https://finance.naver.com/item/main.naver?code=" + krx_code
        else:
            naver_url = (
                "https://finance.naver.com/search/searchResult.naver?query="
                + requests.utils.quote(name)
            )
        chart_btn = (
            ' <a href="' + naver_url + '" target="_blank"'
            ' class="chart-icon" title="네이버 금융 차트 보기">&#x1F4C8; 차트보기</a>'
        )

        # 채널별 언급 내용
        reasons_html = _render_reasons(reasons)

        # KRX 코드 배지
        code_badge = ""
        if krx_code:
            code_badge = (
                f'<span class="krx-code" data-code="{krx_code}" '
                f'title="KRX 종목코드">{krx_code}</span>'
            )

        html += (
            '<div class="stock-card" data-krx="' + krx_code + '">'
            '<div class="stock-header">'
            '<span class="stock-rank">#' + str(i) + '</span>'
            '<span class="stock-name">' + name + '</span>'
            '<span class="stock-signal ' + signal_class + '">' + signal + '</span>'
            + code_badge +
            '</div>'
            '<div class="info-block"><h4>&#x1F4CB; 종목 요약</h4>'
            '<p>' + description + '</p></div>'
            '<div class="info-block">'
            '<h4>&#x1F4C8; 최근 2주간 주가 흐름' + price_info_text + chart_btn + '</h4>'
            '<p>' + price_trend + '</p></div>'
            '<div class="info-block"><h4>&#x1F680; 상승 촉매</h4>'
            '<p>' + catalyst + '</p></div>'
            '<div class="info-block"><h4>&#x26A0;&#xFE0F; 리스크</h4>'
            '<p>' + risk + '</p></div>'
            '<div class="reasons-section">'
            '<h4>&#x1F4E2; 채널별 언급 내용</h4>'
            + reasons_html +
            '</div>'
            '</div>\n'
        )

    return html


def _render_section3_cards(stocks: list) -> str:
    """섹션 3 애널리스트 리포트 카드 렌더링 (3개 카테고리 그룹화)"""
    if not stocks:
        return '<p class="no-data">수집된 리포트 데이터가 없습니다.</p>'

    simultaneous = [s for s in stocks if s.get("analyst_category") == "simultaneous"]
    new_coverage = [s for s in stocks if s.get("analyst_category") == "new_coverage"]
    first_mention = [s for s in stocks if s.get("analyst_category") == "first_in_6months"]

    html = ""

    if simultaneous:
        html += (
            '<div class="analyst-group">'
            '<div class="analyst-group-title">'
            '<span class="group-badge badge-simultaneous">① 증권사 동시 언급</span>'
            '<span class="group-desc">24시간 내 복수의 증권사가 동일 종목 리포트 발행</span>'
            '</div>'
        )
        html += _render_analyst_cards(simultaneous)
        html += '</div>\n'

    if new_coverage:
        html += (
            '<div class="analyst-group">'
            '<div class="analyst-group-title">'
            '<span class="group-badge badge-new-coverage">③ 신규 커버리지</span>'
            '<span class="group-desc">해당 증권사가 해당 종목 커버리지 최초 개시</span>'
            '</div>'
        )
        html += _render_analyst_cards(new_coverage)
        html += '</div>\n'

    if first_mention:
        html += (
            '<div class="analyst-group">'
            '<div class="analyst-group-title">'
            '<span class="group-badge badge-first-mention">② 6개월 내 첫 언급</span>'
            '<span class="group-desc">해당 증권사가 최근 6개월 이내 처음 발행한 리포트</span>'
            '</div>'
        )
        html += _render_analyst_cards(first_mention)
        html += '</div>\n'

    return html


def _render_analyst_cards(stocks: list) -> str:
    """애널리스트 카드 렌더링"""
    html = ""
    for i, stock in enumerate(stocks, 1):
        name          = stock.get("name", "")
        krx_code      = stock.get("krx_code", "")
        signal        = stock.get("signal", "긍정")
        description   = stock.get("description", "")
        price_trend   = stock.get("price_trend", "")
        catalyst      = stock.get("catalyst", "")
        risk          = stock.get("risk", "")
        broker        = stock.get("broker", stock.get("source_name", ""))
        analyst_name  = stock.get("analyst_name", stock.get("analyst", ""))
        target_price  = stock.get("target_price", "")
        opinion       = stock.get("opinion", "")
        reasons       = stock.get("reasons", [])
        verified_price = stock.get("verified_price")

        if signal == "긍정":
            signal_class = "signal-positive"
        elif signal == "부정":
            signal_class = "signal-negative"
        else:
            signal_class = "signal-neutral"

        # 주가 정보
        price_info_text = ""
        if verified_price and verified_price.get("price"):
            p          = verified_price
            change_val = p.get("change", "")
            sign = "▲" if change_val.startswith("+") else (
                "▼" if change_val.startswith("-") else ""
            )
            change_display = change_val.lstrip("+-") if change_val else ""
            if sign and change_display:
                price_info_text = (
                    " (" + p["price"] + "원 "
                    + sign + change_display + " "
                    + p.get("change_pct", "") + ")"
                )
            elif p.get("price"):
                price_info_text = " (" + p["price"] + "원)"

        # 차트 버튼
        if krx_code:
            naver_url = "https://finance.naver.com/item/main.naver?code=" + krx_code
        else:
            naver_url = (
                "https://finance.naver.com/search/searchResult.naver?query="
                + requests.utils.quote(name)
            )
        chart_btn = (
            ' <a href="' + naver_url + '" target="_blank"'
            ' class="chart-icon">&#x1F4C8; 차트보기</a>'
        )

        # 애널리스트 정보 박스
        analyst_info_parts = []
        if broker:
            analyst_info_parts.append(
                f'<span class="analyst-broker">{broker}</span>'
            )
        if analyst_name:
            analyst_info_parts.append(
                f'<span class="analyst-name">담당: {analyst_name}</span>'
            )
        if target_price:
            analyst_info_parts.append(
                f'<span class="analyst-target">목표가: {target_price}원</span>'
            )
        if opinion:
            opinion_class = (
                "opinion-buy" if "BUY" in opinion.upper() or "매수" in opinion
                else "opinion-sell" if "SELL" in opinion.upper() or "매도" in opinion
                else "opinion-hold"
            )
            analyst_info_parts.append(
                f'<span class="analyst-opinion {opinion_class}">{opinion}</span>'
            )

        analyst_info = ""
        if analyst_info_parts:
            analyst_info = (
                '<div class="analyst-info">'
                + " | ".join(analyst_info_parts)
                + '</div>'
            )

        reasons_html = _render_reasons(reasons)

        code_badge = ""
        if krx_code:
            code_badge = (
                f'<span class="krx-code" data-code="{krx_code}">'
                f'{krx_code}</span>'
            )

        html += (
            '<div class="stock-card analyst-card">'
            '<div class="stock-header">'
            '<span class="stock-rank">#' + str(i) + '</span>'
            '<span class="stock-name">' + name + '</span>'
            '<span class="stock-signal ' + signal_class + '">' + signal + '</span>'
            + code_badge +
            '</div>'
            + analyst_info +
            '<div class="info-block"><h4>&#x1F4CB; 종목 요약</h4>'
            '<p>' + description + '</p></div>'
            '<div class="info-block">'
            '<h4>&#x1F4C8; 최근 2주간 주가 흐름' + price_info_text + chart_btn + '</h4>'
            '<p>' + price_trend + '</p></div>'
            '<div class="info-block"><h4>&#x1F680; 상승 촉매</h4>'
            '<p>' + catalyst + '</p></div>'
            '<div class="info-block"><h4>&#x26A0;&#xFE0F; 리스크</h4>'
            '<p>' + risk + '</p></div>'
            '<div class="reasons-section">'
            '<h4>&#x1F4E2; 채널별 언급 내용</h4>'
            + reasons_html +
            '</div>'
            '</div>\n'
        )

    return html


def _render_reasons(reasons: list) -> str:
    """채널별 언급 내용 렌더링"""
    html = ""
    for reason in reasons:
        rs   = reason.get("source_type", "")
        rn   = reason.get("source_name", "")
        rd   = reason.get("detail", "")
        rurl = reason.get("source_url", "")

        link_html = ""
        if rurl:
            link_html = (
                ' <a href="' + rurl + '" target="_blank"'
                ' class="source-link" title="원본 보기">&#x1F517; 바로보기</a>'
            )

        html += (
            '<div class="reason-item">'
            '<div class="reason-header">'
            '<span class="reason-source">[' + rs + '] ' + rn + '</span>'
            + link_html +
            '</div>'
            '<p class="reason-detail">' + rd + '</p>'
            '</div>'
        )
    return html


def _build_archive_links(gh_repo: str) -> str:
    """아카이브 링크 생성"""
    archive_links = ""
    try:
        archive_dir = "docs/archive"
        if os.path.exists(archive_dir):
            html_files = sorted(
                [f for f in os.listdir(archive_dir) if f.endswith(".html")],
                reverse=True,
            )
            repo_owner = gh_repo.split("/")[0] if gh_repo and "/" in gh_repo else ""
            repo_name  = gh_repo.split("/")[1] if gh_repo and "/" in gh_repo else ""
            for af in html_files[:14]:
                date_str = af.replace(".html", "")
                if repo_owner and repo_name:
                    url = f"https://{repo_owner}.github.io/{repo_name}/archive/{af}"
                else:
                    url = f"archive/{af}"
                archive_links += (
                    f'<a href="{url}" class="archive-link">{date_str}</a>\n'
                )
    except Exception as e:
        print(f"  [아카이브] 오류: {e}")
    return archive_links


def _build_full_html(
    briefing_date: str,
    briefing_datetime: str,
    yesterday: str,
    formatted_summary: str,
    section1_html: str,
    section2_html: str,
    section3_html: str,
    investment_strategy: str,
    archive_links: str,
) -> str:
    """전체 HTML 조립"""
    css = _get_css()
    js  = _get_js()

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 주식 브리핑 v3 - {briefing_date}</title>
<link rel="preconnect" href="https://cdn.jsdelivr.net">
<link rel="stylesheet" as="style" crossorigin
  href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard-dynamic-subset.min.css">
<style>
{css}
</style>
</head>
<body>
<div class="container">

  <!-- 헤더 -->
  <div class="header">
    <div class="header-top">
      <h1>&#x1F4CA; AI 주식 브리핑 <span class="version-badge">v3</span></h1>
      <button class="refresh-btn" onclick="location.reload()"
              title="페이지 새로고침">&#x1F504; 새로고침</button>
    </div>
    <div class="date">최종 업데이트: {briefing_datetime}</div>
    <div class="desc">
      유튜브·미디어 채널 언급 종목 | 증권TV 전문가 추천 |
      증권사 애널리스트 리포트를 통합 분석합니다.
    </div>
    <div class="nav-tabs">
      <a href="#section-market"   class="nav-tab">🌍 시장 요약</a>
      <a href="#section1"         class="nav-tab">📺 섹션 1</a>
      <a href="#section2"         class="nav-tab">📡 섹션 2</a>
      <a href="#section3"         class="nav-tab">📋 섹션 3</a>
      <a href="#section-strategy" class="nav-tab">💰 투자전략</a>
      <a href="admin/index.html"  class="nav-tab nav-admin">⚙️ 관리자</a>
    </div>
  </div>

  <!-- 시장 요약 -->
  <div class="section" id="section-market">
    <h2 class="section-title">&#x1F30D; 시장 요약</h2>
    {formatted_summary}
  </div>

  <!-- 섹션 1: 유튜브·미디어 채널 언급 종목 -->
  <div class="section" id="section1">
    <h2 class="section-title">
      &#x1F4FA; 섹션 1
      <span class="section-sub">유튜브·미디어 채널 언급 종목 분석</span>
    </h2>
    <div class="section-desc">
      지난 24시간 이내 방송사·개인유튜브·증권사 채널에서 언급된
      KOSPI/KOSDAQ 상장 종목을 분석합니다.
      각 종목마다 출처 채널과 핵심 언급 내용을 표시합니다.
    </div>
    {section1_html}
  </div>

  <!-- 섹션 2: 증권TV 전문가 출연 추천 종목 -->
  <div class="section" id="section2">
    <h2 class="section-title">
      &#x1F4E1; 섹션 2
      <span class="section-sub">증권TV 전문가 출연 추천 종목</span>
    </h2>
    <div class="section-notice">
      ※ 본 섹션은 전일({yesterday}) 방송 기준입니다.
      오전 방송이 오후에 업로드되는 특성을 고려하여
      전일 방송 데이터를 기준으로 합니다.
    </div>
    {section2_html}
  </div>

  <!-- 섹션 3: 증권사 애널리스트 리포트 -->
  <div class="section" id="section3">
    <h2 class="section-title">
      &#x1F4C4; 섹션 3
      <span class="section-sub">증권사 애널리스트 리포트 분석</span>
    </h2>
    <div class="section-desc">
      지난 24시간 이내 발행된 국내 증권사 애널리스트 리포트를 수집하여 분류합니다.
      섹션 1·2의 유튜브 언급 횟수 집계와 완전히 분리된 독립 섹션입니다.
    </div>
    {section3_html}
  </div>

  <!-- 종합 투자전략 -->
  <div class="section" id="section-strategy">
    <h2 class="section-title">&#x1F4B0; 종합 투자전략</h2>
    <div class="section-desc">
      섹션 1(유튜브·미디어) + 섹션 2(증권TV) + 섹션 3(애널리스트 리포트) 통합 교차 분석
    </div>
    <div class="strategy-block">
      <p>{investment_strategy}</p>
    </div>
  </div>

  <!-- 지난 브리핑 -->
  {f'<div class="section archive-section"><h2 class="section-title">&#x1F4C5; 지난 브리핑</h2>{archive_links}</div>' if archive_links else ''}

  <!-- 면책 고지 -->
  <div class="disclaimer">
    &#x26A0;&#xFE0F; 본 브리핑은 AI가 자동 생성한 참고 자료이며,
    투자 권유가 아닙니다.<br>
    투자 판단의 책임은 투자자 본인에게 있습니다.
  </div>

</div><!-- /container -->

<script>
{js}
</script>
</body>
</html>"""

    return html


def _get_css() -> str:
    """v3 CSS 스타일"""
    return """
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Pretendard', -apple-system, BlinkMacSystemFont,
    'Segoe UI', Roboto, sans-serif;
  background: #0a0a14;
  color: #e0e0e0;
  line-height: 1.6;
}
.container { max-width: 860px; margin: 0 auto; padding: 20px; }

/* ── 헤더 ── */
.header {
  text-align: center;
  padding: 30px 0;
  border-bottom: 1px solid #1e1e2e;
  margin-bottom: 30px;
}
.header-top {
  display: flex; align-items: center;
  justify-content: center; gap: 12px;
  margin-bottom: 8px; flex-wrap: wrap;
}
.header h1 { font-size: 1.8em; color: #fff; }
.version-badge {
  background: linear-gradient(135deg, #667eea, #764ba2);
  color: #fff; padding: 2px 8px; border-radius: 10px;
  font-size: 0.6em; vertical-align: middle;
}
.refresh-btn {
  background: #1e1e2e; color: #667eea;
  border: 1px solid #667eea40;
  padding: 6px 14px; border-radius: 20px;
  cursor: pointer; font-size: 0.82em; transition: background 0.2s;
}
.refresh-btn:hover { background: #667eea20; }
.header .date { color: #888; font-size: 0.95em; }
.header .desc { color: #aaa; font-size: 0.85em; margin-top: 8px; }

/* ── 네비게이션 탭 ── */
.nav-tabs {
  display: flex; gap: 8px;
  justify-content: center; flex-wrap: wrap; margin-top: 16px;
}
.nav-tab {
  display: inline-block; background: #141420; color: #a8b4ff;
  padding: 6px 14px; border-radius: 20px;
  border: 1px solid #667eea30; font-size: 0.82em;
  text-decoration: none; transition: all 0.2s;
}
.nav-tab:hover { background: #667eea20; border-color: #667eea60; }
.nav-tab.active { background: #667eea30; border-color: #667eea; color: #fff; }
.nav-admin { background: #1a1a2e; color: #ffd43b; border-color: #ffd43b30; }
.nav-admin:hover { background: #ffd43b20; }

/* ── 섹션 ── */
.section { margin-bottom: 40px; }
.section-title {
  font-size: 1.3em; color: #fff; margin-bottom: 10px;
  padding-left: 12px; border-left: 3px solid #667eea;
  display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
}
.section-sub { font-size: 0.7em; color: #a8b4ff; font-weight: 400; }
.section-desc {
  color: #888; font-size: 0.84em; margin-bottom: 16px;
  padding: 10px 14px; background: #141420;
  border-radius: 8px; border-left: 2px solid #667eea40;
}
.section-notice {
  color: #ffd43b; font-size: 0.84em; margin-bottom: 16px;
  padding: 10px 14px; background: #1a1a2e;
  border-radius: 8px; border-left: 2px solid #ffd43b60;
}

/* ── 시장 요약 ── */
.summary-block {
  background: #141420; border-radius: 12px;
  padding: 18px; margin-bottom: 12px; border: 1px solid #1e1e2e;
}
.summary-subtitle { color: #667eea; font-size: 1.05em; margin-bottom: 8px; }
.summary-text { color: #ccc; font-size: 0.92em; }

/* ── 종목 카드 ── */
.stock-card {
  background: #141420; border-radius: 12px;
  padding: 20px; margin-bottom: 16px; border: 1px solid #1e1e2e;
  transition: border-color 0.3s;
}
.stock-card:hover { border-color: #667eea60; }
.analyst-card { border-left: 3px solid #51cf66; }
.stock-header {
  display: flex; align-items: center;
  gap: 10px; margin-bottom: 12px; flex-wrap: wrap;
}
.stock-rank {
  background: #667eea; color: #fff;
  padding: 2px 10px; border-radius: 12px;
  font-size: 0.85em; font-weight: 700;
}
.stock-name { font-size: 1.15em; font-weight: 700; color: #fff; }
.stock-signal {
  padding: 3px 10px; border-radius: 10px;
  font-size: 0.8em; font-weight: 600;
}
.signal-positive { background: #ff6b6b20; color: #ff6b6b; border: 1px solid #ff6b6b40; }
.signal-negative { background: #339af020; color: #339af0; border: 1px solid #339af040; }
.signal-neutral  { background: #ffd43b20; color: #ffd43b; border: 1px solid #ffd43b40; }

/* KRX 종목코드 배지 */
.krx-code {
  background: #1e1e2e; color: #888;
  padding: 2px 8px; border-radius: 6px;
  font-size: 0.75em; font-family: monospace; border: 1px solid #333;
}

/* ── 정보 블록 ── */
.info-block { margin-bottom: 12px; }
.info-block h4 {
  color: #a8b4ff; font-size: 0.9em; margin-bottom: 4px;
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
}
.info-block p { color: #bbb; font-size: 0.88em; }

/* 차트 버튼 */
.chart-icon {
  cursor: pointer; color: #667eea; font-size: 0.85em;
  padding: 3px 8px; border-radius: 6px;
  background: #667eea15; border: 1px solid #667eea30;
  margin-left: 4px; white-space: nowrap;
  text-decoration: none; display: inline-block;
}
.chart-icon:hover { background: #667eea30; }

/* ── 채널별 언급 내용 ── */
.reasons-section { margin-top: 12px; }
.reasons-section h4 { color: #a8b4ff; font-size: 0.9em; margin-bottom: 8px; }
.reason-item {
  background: #1a1a2e; border-radius: 8px;
  padding: 10px; margin-bottom: 6px;
}
.reason-header {
  display: flex; align-items: center;
  gap: 8px; margin-bottom: 4px; flex-wrap: wrap;
}
.reason-source { color: #667eea; font-size: 0.82em; font-weight: 600; }
.source-link { color: #51cf66; font-size: 0.78em; text-decoration: none; }
.source-link:hover { text-decoration: underline; }
.reason-detail { color: #aaa; font-size: 0.85em; }

/* ── 애널리스트 전용 ── */
.analyst-info {
  display: flex; flex-wrap: wrap; gap: 8px;
  margin-bottom: 12px; padding: 8px 12px;
  background: #1a1a2e; border-radius: 8px; border: 1px solid #51cf6630;
}
.analyst-broker { color: #51cf66; font-size: 0.85em; font-weight: 600; }
.analyst-name   { color: #a8b4ff; font-size: 0.82em; }
.analyst-target { color: #ffd43b; font-size: 0.82em; font-weight: 600; }
.analyst-opinion {
  padding: 2px 8px; border-radius: 6px;
  font-size: 0.8em; font-weight: 700;
}
.opinion-buy  { background: #ff6b6b20; color: #ff6b6b; }
.opinion-sell { background: #339af020; color: #339af0; }
.opinion-hold { background: #ffd43b20; color: #ffd43b; }

/* ── 섹션 3 그룹화 ── */
.analyst-group { margin-bottom: 24px; }
.analyst-group-title {
  display: flex; align-items: center;
  gap: 10px; margin-bottom: 12px; flex-wrap: wrap;
}
.group-badge {
  padding: 4px 12px; border-radius: 12px;
  font-size: 0.85em; font-weight: 700;
}
.badge-simultaneous { background: #ff6b6b20; color: #ff6b6b; border: 1px solid #ff6b6b40; }
.badge-new-coverage  { background: #51cf6620; color: #51cf66; border: 1px solid #51cf6640; }
.badge-first-mention { background: #ffd43b20; color: #ffd43b; border: 1px solid #ffd43b40; }
.group-desc { color: #888; font-size: 0.82em; }

/* ── 투자전략 ── */
.strategy-block {
  background: linear-gradient(135deg, #141420, #1a1a2e);
  border: 1px solid #667eea30; border-radius: 12px; padding: 20px;
}
.strategy-block p { color: #ccc; font-size: 0.92em; line-height: 1.8; }

/* ── 빈 데이터 ── */
.no-data {
  color: #666; font-size: 0.9em; text-align: center;
  padding: 20px; background: #141420;
  border-radius: 8px; border: 1px dashed #333;
}

/* ── 아카이브 ── */
.archive-section { margin-top: 20px; }
.archive-link {
  display: inline-block; color: #667eea; text-decoration: none;
  padding: 4px 10px; margin: 3px;
  border: 1px solid #667eea30; border-radius: 6px; font-size: 0.82em;
}
.archive-link:hover { background: #667eea20; }

/* ── 면책 고지 ── */
.disclaimer {
  text-align: center; color: #666; font-size: 0.78em;
  margin-top: 30px; padding: 15px; border-top: 1px solid #1e1e2e;
}

/* ── 모바일 반응형 ── */
@media (max-width: 600px) {
  .container { padding: 12px; }
  .header h1 { font-size: 1.4em; }
  .stock-name { font-size: 1em; }
  .section-title { font-size: 1.1em; }
  .nav-tabs { gap: 4px; }
  .nav-tab { padding: 5px 10px; font-size: 0.78em; }
  .analyst-info { gap: 4px; }
}
"""


def _get_js() -> str:
    """JavaScript — 네비게이션 탭 활성화"""
    return """
// 네비게이션 탭 활성화 (IntersectionObserver)
document.addEventListener('DOMContentLoaded', function () {
  const tabs     = document.querySelectorAll('.nav-tab');
  const sections = document.querySelectorAll('.section[id]');

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const id = entry.target.id;
        tabs.forEach(tab => {
          tab.classList.remove('active');
          if (tab.getAttribute('href') === '#' + id) {
            tab.classList.add('active');
          }
        });
      }
    });
  }, { threshold: 0.3 });

  sections.forEach(section => observer.observe(section));
});
"""
