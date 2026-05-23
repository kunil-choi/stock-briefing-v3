# analyzer/html_generator.py
import os
import requests
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


# ─── 인디케이터 배지 헬퍼 ─────────────────────────────────────────────────────

def _indicator_badge(label: str, value, pct, direction: str = "") -> str:
    """
    시장 인디케이터 하나를 색상 배지로 렌더링.
    direction: 'call'→초록, 'put'→빨강, ''→등락률 기준 자동
    """
    try:
        pct_f = float(str(pct).replace(",", "").replace("%","").replace("+",""))
    except Exception:
        pct_f = 0.0

    if direction == "call":
        color_cls = "ind-call"
    elif direction == "put":
        color_cls = "ind-put"
    elif pct_f > 0:
        color_cls = "ind-call"
    elif pct_f < 0:
        color_cls = "ind-put"
    else:
        color_cls = "ind-neutral"

    sign    = "▲" if pct_f > 0 else ("▼" if pct_f < 0 else "-")
    pct_str = f"{abs(pct_f):.2f}%" if pct_f != 0 else "0.00%"
    val_str = f"{value:,.2f}" if isinstance(value, float) else str(value) if value else "N/A"

    return (
        f'<div class="ind-badge {color_cls}">'
        f'<span class="ind-label">{label}</span>'
        f'<span class="ind-value">{val_str}</span>'
        f'<span class="ind-pct">{sign} {pct_str}</span>'
        f'</div>'
    )


def _build_market_indicators(market_overview: dict) -> str:
    """market_overview → 인디케이터 배지 행 HTML"""
    if not market_overview:
        return ""

    nf = market_overview.get("night_futures", {})
    us = market_overview.get("us_market", {})
    kr = market_overview.get("korea_market", {})

    badges = ""
    # 야간선물
    badges += _indicator_badge(
        "야간선물", nf.get("price"), nf.get("change_pct"),
        direction=nf.get("direction", "")
    )
    # 미국 3대 지수
    badges += _indicator_badge("S&P500",  us.get("sp500",  {}).get("price"), us.get("sp500",  {}).get("change_pct"))
    badges += _indicator_badge("나스닥",   us.get("nasdaq", {}).get("price"), us.get("nasdaq", {}).get("change_pct"))
    badges += _indicator_badge("다우존스", us.get("dow",    {}).get("price"), us.get("dow",    {}).get("change_pct"))
    badges += _indicator_badge("달러/원",  us.get("usd_krw",{}).get("price"), us.get("usd_krw",{}).get("change_pct"))
    # 한국 지수
    badges += _indicator_badge("코스피",  kr.get("kospi",  {}).get("price"), kr.get("kospi",  {}).get("change_pct"))
    badges += _indicator_badge("코스닥",  kr.get("kosdaq", {}).get("price"), kr.get("kosdaq", {}).get("change_pct"))

    return f'<div class="ind-row">{badges}</div>'


# ─── 5단락 시장 요약 파싱 (V2 방식 확장) ────────────────────────────────────

PARA_TITLES = [
    "야간선물 시장 동향",
    "미국 증시 마감 요약",
    "전일 국내 증시 흐름",
    "오늘 국내 증시 예상 흐름",
    "주요 섹터 포커스",
]


def _render_market_summary(market_summary: str) -> str:
    """
    V2 포맷(소제목: 내용\\n\\n...)을 카드 그리드로 렌더링.
    소제목이 5개 단락에 매핑되면 아이콘을 붙임.
    """
    icons = ["🌙", "🇺🇸", "🇰🇷", "📊", "🔥"]
    paragraphs = [p.strip() for p in market_summary.split("\n\n") if p.strip()]
    if not paragraphs:
        return ""

    html = '<div class="summary-grid">\n'
    for i, para in enumerate(paragraphs):
        icon = icons[i] if i < len(icons) else "📌"
        if ":" in para:
            idx   = para.index(":")
            title = para[:idx].strip()
            body  = para[idx+1:].strip()
        else:
            title = PARA_TITLES[i] if i < len(PARA_TITLES) else f"요약 {i+1}"
            body  = para

        html += (
            f'<div class="summary-block">'
            f'<h3 class="summary-subtitle">{icon} {title}</h3>'
            f'<p class="summary-text">{body}</p>'
            f'</div>\n'
        )
    html += '</div>\n'
    return html


# ─── 메인 HTML 생성 ──────────────────────────────────────────────────────────

def generate_html(data, channels_data=None, gh_repo="", gh_token="",
                  market_overview=None):
    now_kst           = datetime.now(KST)
    briefing_date     = data.get("briefing_date", now_kst.strftime("%Y-%m-%d"))
    briefing_datetime = now_kst.strftime("%Y-%m-%d %H:%M")
    market_summary    = data.get("market_summary", "")
    hot_sectors       = data.get("hot_sectors", [])
    stocks            = data.get("stocks", [])
    hidden_picks      = data.get("hidden_picks", [])
    investment_strategy = data.get("investment_strategy",
                                   data.get("final_summary", ""))

    stocks       = [s for s in stocks       if s.get("overlap_count", 0) >= 2]
    hidden_picks = [s for s in hidden_picks if s.get("signal", "") == "긍정"]

    # ── 시장 인디케이터 배지 ────────────────────────────────────────────
    indicators_html = _build_market_indicators(market_overview)

    # ── 5단락 시장 요약 ─────────────────────────────────────────────────
    formatted_summary = _render_market_summary(market_summary)

    # ── 섹터 배지 ───────────────────────────────────────────────────────
    sectors_html = "".join(
        f'<span class="sector-badge">{sector}</span>\n'
        for sector in hot_sectors
    )

    # ── 관심 종목 카드 (V2 그대로) ──────────────────────────────────────
    stocks_html = ""
    for stock in stocks:
        name        = stock.get("name", "")
        rank        = stock.get("rank", "")
        signal      = stock.get("signal", "중립")
        description = stock.get("description", "")
        price_trend = stock.get("price_trend", "")
        catalyst    = stock.get("catalyst", "")
        risk        = stock.get("risk", "")
        overlap     = stock.get("overlap_count", 0)
        source_types= stock.get("source_types", [])
        reasons     = stock.get("reasons", [])
        verified_price = stock.get("verified_price")
        chart_b64   = stock.get("chart_base64")
        market      = stock.get("market", "국내")
        naver_code  = stock.get("naver_code", "")
        if not naver_code and verified_price:
            naver_code = verified_price.get("code", "")

        signal_class = (
            "signal-positive" if signal == "긍정" else
            "signal-negative" if signal == "부정" else
            "signal-neutral"
        )

        channel_counts = stock.get("channel_counts", {})
        total_count    = stock.get("total_count", overlap)
        if channel_counts:
            parts = [f"{ch} {cnt}회"
                     for ch in ["뉴스","경제방송","유튜브","애널리스트"]
                     for cnt in [channel_counts.get(ch, 0)] if cnt > 0]
            overlap_badge = (
                f'<span class="overlap-badge">총 {total_count}회 언급 '
                f'({" / ".join(parts)})</span>'
            )
        else:
            overlap_badge = f'<span class="overlap-badge">{overlap}개 채널 언급</span>'

        source_tags = "".join(
            f'<span class="source-tag">{st}</span>' for st in source_types
        )

        price_info_text = ""
        if verified_price:
            p  = verified_price
            cv = p.get("change", "")
            sign = "▲" if cv.startswith("+") else ("▼" if cv.startswith("-") else "")
            cd   = cv.lstrip("+-") if cv else ""
            price_info_text = (
                f' ({p["price"]}원 {sign}{cd} {p.get("change_pct","")})' if sign and cd
                else f' ({p["price"]}원)'
            )
        elif market == "해외":
            price_info_text = " (해외 종목)"

        if chart_b64:
            chart_btn_html = (
                f' <span class="chart-icon"'
                f' onclick="openChartWindow(\'{name}\', \'{rank}\')"'
                f' title="14일 주가 차트 보기">📈 차트보기</span>'
            )
        else:
            naver_url = (
                f"https://finance.naver.com/item/main.naver?code={naver_code}"
                if naver_code else
                f"https://finance.naver.com/search/searchResult.naver?query={requests.utils.quote(name)}"
            )
            chart_btn_html = (
                f' <a href="{naver_url}" target="_blank"'
                f' class="chart-icon" title="네이버 금융에서 차트 보기">📈 차트보기</a>'
            )

        reasons_html = ""
        for reason in reasons:
            rs   = reason.get("source_type","")
            rn   = reason.get("source_name","")
            rd   = reason.get("detail","")
            rurl = reason.get("source_url","")
            if not rurl and "애널리스트" in rs:
                rurl = (
                    "https://finance.naver.com/research/company_list.naver"
                    f"?searchType=itemCode&itemName={requests.utils.quote(name)}"
                )
            link_html = (
                f' <a href="{rurl}" target="_blank"'
                f' class="source-link" title="원본 보기">🔗 바로보기</a>'
                if rurl else ""
            )
            reasons_html += (
                f'<div class="reason-item">'
                f'<div class="reason-header">'
                f'<span class="reason-source">[{rs}] {rn}</span>{link_html}'
                f'</div>'
                f'<p class="reason-detail">{rd}</p>'
                f'</div>'
            )

        stocks_html += (
            f'<div class="stock-card">'
            f'<div class="stock-header">'
            f'<span class="stock-rank">#{rank}</span>'
            f'<span class="stock-name">{name}</span>'
            f'<span class="stock-signal {signal_class}">{signal}</span>'
            f'{overlap_badge}'
            f'</div>'
            f'<div class="source-tags">{source_tags}</div>'
            f'<div class="info-block"><h4>📋 종목 요약</h4><p>{description}</p></div>'
            f'<div class="info-block">'
            f'<h4>📈 주가 흐름{price_info_text}{chart_btn_html}</h4>'
            f'<p>{price_trend}</p></div>'
            f'<div class="info-block"><h4>🚀 상승 촉매</h4><p>{catalyst}</p></div>'
            f'<div class="info-block"><h4>⚠️ 리스크</h4><p>{risk}</p></div>'
            f'<div class="reasons-section">'
            f'<h4>📢 채널별 언급 내용</h4>{reasons_html}'
            f'</div>'
            f'</div>\n'
        )

    # ── 히든픽 카드 (V2 그대로) ─────────────────────────────────────────
    hidden_html = ""
    for hp in hidden_picks:
        hp_name      = hp.get("name","")
        hp_rank      = hp.get("rank","")
        hp_desc      = hp.get("description","")
        hp_catalyst  = hp.get("catalyst","")
        hp_risk      = hp.get("risk","")
        hp_reasons   = hp.get("reasons",[])
        hp_verified  = hp.get("verified_price")
        hp_market    = hp.get("market","국내")
        hp_chart_b64 = hp.get("chart_base64")
        hp_naver_code= hp.get("naver_code","")
        if not hp_naver_code and hp_verified:
            hp_naver_code = hp_verified.get("code","")

        hp_price_html = ""
        if hp_verified:
            p  = hp_verified
            cv = p.get("change","")
            cc = ("price-up" if cv.startswith("+") else
                  "price-down" if cv.startswith("-") else "price-note")
            hp_price_html = (
                f'<div class="price-box">'
                f'<span class="current-price">{p["price"]}원</span>'
                f'<span class="{cc}">'
                + (f'{cv} ({p.get("change_pct","")})' if cv else "등락 정보 없음")
                + '</span></div>'
            )
        elif hp_market == "해외":
            hp_price_html = (
                '<div class="price-box">'
                '<span class="price-note">해외 종목 (실시간 가격 미제공)</span>'
                '</div>'
            )

        if hp_chart_b64:
            hp_chart_btn = (
                f' <span class="chart-icon"'
                f' onclick="openChartWindow(\'{hp_name}\', \'hp_{hp_rank}\')"'
                f' title="14일 주가 차트 보기">📈 차트보기</span>'
            )
        else:
            hp_naver_url = (
                f"https://finance.naver.com/item/main.naver?code={hp_naver_code}"
                if hp_naver_code else
                f"https://finance.naver.com/search/searchResult.naver?query={requests.utils.quote(hp_name)}"
            )
            hp_chart_btn = (
                f' <a href="{hp_naver_url}" target="_blank"'
                f' class="chart-icon" title="네이버 금융에서 차트 보기">📈 차트보기</a>'
            )

        hp_reasons_html = ""
        for reason in hp_reasons:
            rs   = reason.get("source_type","")
            rn   = reason.get("source_name","")
            rd   = reason.get("detail","")
            rurl = reason.get("source_url","")
            if not rurl and "애널리스트" in rs:
                rurl = (
                    "https://finance.naver.com/research/company_list.naver"
                    f"?searchType=itemCode&itemName={requests.utils.quote(hp_name)}"
                )
            link_html = (
                f' <a href="{rurl}" target="_blank"'
                f' class="source-link" title="원본 보기">🔗 바로보기</a>'
                if rurl else ""
            )
            hp_reasons_html += (
                f'<div class="reason-item">'
                f'<div class="reason-header">'
                f'<span class="reason-source">[{rs}] {rn}</span>{link_html}'
                f'</div>'
                f'<p class="reason-detail">{rd}</p>'
                f'</div>'
            )

        hidden_html += (
            f'<div class="hidden-pick-card">'
            f'<div class="stock-header">'
            f'<span class="stock-rank">Hidden #{hp_rank}</span>'
            f'<span class="stock-name">{hp_name}</span>'
            f'</div>'
            f'{hp_price_html}'
            f'<div class="info-block"><h4>📋 기업 소개</h4><p>{hp_desc}</p></div>'
            f'<div class="info-block"><h4>🚀 주목 이유{hp_chart_btn}</h4>'
            f'<p>{hp_catalyst}</p></div>'
            f'<div class="info-block"><h4>⚠️ 리스크</h4><p>{hp_risk}</p></div>'
            f'<div class="reasons-section">'
            f'<h4>📢 채널별 언급 내용</h4>{hp_reasons_html}'
            f'</div>'
            f'</div>\n'
        )

    # ── 차트 데이터 JS ───────────────────────────────────────────────────
    chart_data_js = "var chartDataMap = {};\n"
    for stock in stocks:
        b64 = stock.get("chart_base64")
        if b64:
            clean = b64.replace('\n','').replace('\r','')
            chart_data_js += f'chartDataMap["{stock.get("rank","")}"] = "data:image/png;base64,{clean}";\n'
    for hp in hidden_picks:
        b64 = hp.get("chart_base64")
        if b64:
            clean = b64.replace('\n','').replace('\r','')
            chart_data_js += f'chartDataMap["hp_{hp.get("rank","")}"] = "data:image/png;base64,{clean}";\n'

    # ── 아카이브 링크 (V2 그대로: 로컬 파일 스캔) ───────────────────────
    archive_links = ""
    try:
        archive_dir = "docs/archive"
        if os.path.exists(archive_dir):
            html_files = sorted(
                [f for f in os.listdir(archive_dir) if f.endswith(".html")],
                reverse=True
            )
            repo_owner = gh_repo.split("/")[0] if gh_repo and "/" in gh_repo else ""
            repo_name  = gh_repo.split("/")[1] if gh_repo and "/" in gh_repo else ""
            for af in html_files[:14]:
                date_str = af.replace(".html","")
                url = (
                    f"https://{repo_owner}.github.io/{repo_name}/archive/{af}"
                    if repo_owner and repo_name else f"archive/{af}"
                )
                archive_links += f'<a href="{url}" class="archive-link">{date_str}</a>\n'
            print(f"  [아카이브] {len(html_files)}개 파일 링크 생성 완료")
    except Exception as e:
        print(f"  [아카이브] 오류: {e}")

    # ── CSS (V2 + 인디케이터 배지 스타일 추가) ──────────────────────────
    css = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #0a0a14; color: #e0e0e0; line-height: 1.6; }
.container { max-width: 860px; margin: 0 auto; padding: 20px; }
.header { text-align: center; padding: 30px 0; border-bottom: 1px solid #1e1e2e; margin-bottom: 30px; }
.header h1 { font-size: 1.8em; color: #fff; margin-bottom: 8px; }
.header .date { color: #888; font-size: 0.95em; }
.header .desc { color: #aaa; font-size: 0.85em; margin-top: 8px; }
.section { margin-bottom: 35px; }
.section-title { font-size: 1.3em; color: #fff; margin-bottom: 15px;
                 padding-left: 12px; border-left: 3px solid #667eea; }

/* ── 인디케이터 배지 ── */
.ind-row { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 20px; }
.ind-badge { display: flex; flex-direction: column; align-items: center;
             padding: 10px 16px; border-radius: 12px; min-width: 90px;
             border: 1px solid transparent; }
.ind-call    { background: #ff6b6b18; border-color: #ff6b6b50; }
.ind-put     { background: #339af018; border-color: #339af050; }
.ind-neutral { background: #ffd43b18; border-color: #ffd43b50; }
.ind-label { font-size: 0.72em; color: #888; margin-bottom: 4px; }
.ind-value { font-size: 1.05em; font-weight: 700; color: #fff; }
.ind-call  .ind-value { color: #ff6b6b; }
.ind-put   .ind-value { color: #339af0; }
.ind-pct { font-size: 0.78em; margin-top: 2px; }
.ind-call    .ind-pct { color: #ff6b6b; }
.ind-put     .ind-pct { color: #339af0; }
.ind-neutral .ind-pct { color: #ffd43b; }

/* ── 5단락 요약 그리드 ── */
.summary-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 600px) { .summary-grid { grid-template-columns: 1fr; } }
.summary-block { background: #141420; border-radius: 12px; padding: 18px;
                 border: 1px solid #1e1e2e; }
.summary-subtitle { color: #667eea; font-size: 1.0em; margin-bottom: 8px; }
.summary-text { color: #ccc; font-size: 0.88em; line-height: 1.7; }

/* ── 섹터 ── */
.sector-badge { display: inline-block;
                background: linear-gradient(135deg,#667eea20,#764ba220);
                color: #a8b4ff; padding: 6px 14px; border-radius: 20px;
                margin: 4px; font-size: 0.85em; border: 1px solid #667eea40; }

/* ── 종목 카드 ── */
.stock-card, .hidden-pick-card { background: #141420; border-radius: 12px;
    padding: 20px; margin-bottom: 16px; border: 1px solid #1e1e2e;
    transition: border-color 0.3s; }
.stock-card:hover, .hidden-pick-card:hover { border-color: #667eea60; }
.hidden-pick-card { border-left: 3px solid #ffd43b; }
.stock-header { display: flex; align-items: center; gap: 10px;
                margin-bottom: 12px; flex-wrap: wrap; }
.stock-rank { background: #667eea; color: #fff; padding: 2px 10px;
              border-radius: 12px; font-size: 0.85em; font-weight: 700; }
.stock-name { font-size: 1.15em; font-weight: 700; color: #fff; }
.stock-signal { padding: 3px 10px; border-radius: 10px; font-size: 0.8em; font-weight: 600; }
.signal-positive { background: #ff6b6b20; color: #ff6b6b; border: 1px solid #ff6b6b40; }
.signal-negative { background: #339af020; color: #339af0; border: 1px solid #339af040; }
.signal-neutral  { background: #ffd43b20; color: #ffd43b; border: 1px solid #ffd43b40; }
.overlap-badge { background: #51cf6620; color: #51cf66; padding: 3px 10px;
                 border-radius: 10px; font-size: 0.8em; border: 1px solid #51cf6640; }
.source-tags { margin-bottom: 12px; }
.source-tag { display: inline-block; background: #1e1e2e; color: #888;
              padding: 3px 8px; border-radius: 6px; font-size: 0.75em; margin: 2px; }
.price-box { margin-bottom: 12px; padding: 10px; background: #1a1a2e; border-radius: 8px; }
.current-price { font-size: 1.3em; font-weight: 700; color: #fff; margin-right: 10px; }
.price-up   { color: #ff6b6b; font-weight: 600; }
.price-down { color: #339af0; font-weight: 600; }
.price-note { color: #888; font-size: 0.85em; }
.chart-icon { cursor: pointer; color: #667eea; font-size: 0.85em;
              padding: 3px 8px; border-radius: 6px; background: #667eea15;
              border: 1px solid #667eea30; margin-left: 4px;
              white-space: nowrap; text-decoration: none; display: inline-block; }
.chart-icon:hover { background: #667eea30; }
.info-block { margin-bottom: 12px; }
.info-block h4 { color: #a8b4ff; font-size: 0.9em; margin-bottom: 4px;
                 display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.info-block p { color: #bbb; font-size: 0.88em; }
.reasons-section { margin-top: 12px; }
.reasons-section h4 { color: #a8b4ff; font-size: 0.9em; margin-bottom: 8px; }
.reason-item { background: #1a1a2e; border-radius: 8px; padding: 10px; margin-bottom: 6px; }
.reason-header { display: flex; align-items: center; gap: 8px;
                 margin-bottom: 4px; flex-wrap: wrap; }
.reason-source { color: #667eea; font-size: 0.82em; font-weight: 600; }
.source-link { color: #51cf66; font-size: 0.78em; text-decoration: none; }
.source-link:hover { text-decoration: underline; }
.reason-detail { color: #aaa; font-size: 0.85em; }
.strategy-block { background: linear-gradient(135deg,#141420,#1a1a2e);
                  border: 1px solid #667eea30; border-radius: 12px; padding: 20px; }
.strategy-block p { color: #ccc; font-size: 0.92em; line-height: 1.8; }
.disclaimer { text-align: center; color: #666; font-size: 0.78em;
              margin-top: 30px; padding: 15px; border-top: 1px solid #1e1e2e; }
.archive-section { margin-top: 20px; }
.archive-link { display: inline-block; color: #667eea; text-decoration: none;
                padding: 4px 10px; margin: 3px; border: 1px solid #667eea30;
                border-radius: 6px; font-size: 0.82em; }
.archive-link:hover { background: #667eea20; }
.chart-modal { display: none; position: fixed; top: 0; left: 0;
               width: 100%; height: 100%; background: rgba(0,0,0,0.85);
               z-index: 1000; justify-content: center; align-items: center; }
.chart-modal img { max-width: 95%; max-height: 80%; border-radius: 8px; }
.chart-modal .close-btn { position: absolute; top: 20px; right: 30px;
                           color: #fff; font-size: 2em; cursor: pointer; }
"""

    # ── HTML 조립 ────────────────────────────────────────────────────────
    html = (
        '<!DOCTYPE html>\n'
        '<html lang="ko">\n'
        '<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f'<title>AI 주식 브리핑 - {briefing_date}</title>\n'
        f'<style>\n{css}\n</style>\n'
        '</head>\n'
        '<body>\n'
        '<div class="container">\n'
        '    <div class="header">\n'
        '        <h1>📊 AI 주식 브리핑</h1>\n'
        f'        <div class="date">{briefing_datetime} 기준</div>\n'
        '        <div class="desc">최근 뉴스와 경제방송, 구독자 상위권 유튜브, '
        '증권사 보고서에서 공통으로 언급된 종목들에 대한 브리핑입니다.</div>\n'
        '    </div>\n'
        '\n'
    )

    # 인디케이터 배지 (V3 신규)
    if indicators_html:
        html += (
            '    <div class="section">\n'
            '        <h2 class="section-title">📡 시장 지표</h2>\n'
            f'        {indicators_html}\n'
            '    </div>\n\n'
        )

    # 5단락 시장 요약
    html += (
        '    <div class="section">\n'
        '        <h2 class="section-title">🌍 시장 요약</h2>\n'
        f'        {formatted_summary}\n'
        '    </div>\n\n'
    )

    # 섹터
    html += (
        '    <div class="section">\n'
        '        <h2 class="section-title">🔥 주목 섹터</h2>\n'
        f'        {sectors_html}\n'
        '    </div>\n\n'
    )

    # 관심 종목
    html += (
        '    <div class="section">\n'
        '        <h2 class="section-title">🎯 관심 종목</h2>\n'
        f'        {stocks_html}\n'
        '    </div>\n'
    )

    # 히든픽
    if hidden_html:
        html += (
            '\n    <div class="section">\n'
            '        <h2 class="section-title">💎 히든픽</h2>\n'
            f'        {hidden_html}\n'
            '    </div>\n'
        )

    # 투자 전략
    if investment_strategy:
        html += (
            '\n    <div class="section">\n'
            '        <h2 class="section-title">💰 AI 투자 전략</h2>\n'
            '        <div class="strategy-block">\n'
            f'            <p>{investment_strategy}</p>\n'
            '        </div>\n'
            '    </div>\n'
        )

    # 아카이브
    if archive_links:
        html += (
            '\n    <div class="section archive-section">\n'
            '        <h2 class="section-title">📅 지난 브리핑</h2>\n'
            f'        {archive_links}\n'
            '    </div>\n'
        )

    html += (
        '\n    <div class="disclaimer">\n'
        '        ⚠️ 본 브리핑은 AI가 자동 생성한 참고 자료이며, 투자 권유가 아닙니다.<br>\n'
        '        투자 판단의 책임은 투자자 본인에게 있습니다.\n'
        '    </div>\n'
        '</div>\n'
        '\n'
        '<div class="chart-modal" id="chartModal" onclick="closeChart()">\n'
        '    <span class="close-btn" onclick="closeChart()">&times;</span>\n'
        '    <img id="chartImg" src="" alt="차트">\n'
        '</div>\n'
        '\n'
        '<script>\n'
        + chart_data_js
        + '\n'
        'function openChartWindow(stockName, chartKey) {\n'
        '    var src = chartDataMap[chartKey];\n'
        '    if (src) {\n'
        '        document.getElementById(\'chartImg\').src = src;\n'
        '        document.getElementById(\'chartModal\').style.display = \'flex\';\n'
        '    }\n'
        '}\n'
        'function closeChart() {\n'
        '    document.getElementById(\'chartModal\').style.display = \'none\';\n'
        '}\n'
        'document.addEventListener(\'keydown\', function(e) {\n'
        '    if (e.key === \'Escape\') closeChart();\n'
        '});\n'
        '</script>\n'
        '</body>\n'
        '</html>'
    )

    return html
