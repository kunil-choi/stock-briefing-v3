# analyzer/html_generator.py
import os
from urllib.parse import quote as url_quote
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

PARA_TITLES = [
    "야간선물 시장 동향", "미국 증시 마감 요약",
    "전일 국내 증시 흐름", "오늘 국내 증시 예상 흐름", "주요 섹터 포커스",
]

# BUG-HP-1: 히든픽 소스 타입별 배지 색상/라벨 매핑
_HP_SOURCE_META = {
    "애널리스트":  {"color": "#51cf66", "icon": "📊", "label": "애널리스트 단독"},
    "경제방송TV": {"color": "#ffa94d", "icon": "📺", "label": "경제방송TV 단독"},
    "경제방송":   {"color": "#74c0fc", "icon": "📡", "label": "경제방송 단독"},
}
_HP_SOURCE_DEFAULT = {"color": "#a8b4ff", "icon": "💡", "label": "전문가 단독"}


# ── 인디케이터 배지 ────────────────────────────────────────────────────────────

def _indicator_badge(label: str, value, pct, direction: str = "") -> str:
    # BUG-9: 0.0도 숫자로 정상 처리
    try:
        pct_f = float(
            str(pct).replace(",", "").replace("%", "").replace("+", "")
        ) if pct is not None else 0.0
    except Exception:
        pct_f = 0.0

    if direction == "call":   color_cls = "ind-call"
    elif direction == "put":  color_cls = "ind-put"
    elif pct_f > 0:           color_cls = "ind-call"
    elif pct_f < 0:           color_cls = "ind-put"
    else:                     color_cls = "ind-neutral"

    sign    = "▲" if pct_f > 0 else ("▼" if pct_f < 0 else "─")
    pct_str = f"{abs(pct_f):.2f}%"

    if isinstance(value, (int, float)) and value is not None:
        val_str = f"{value:,.2f}"
    elif value and str(value).strip():
        val_str = str(value)
    else:
        val_str = "N/A"

    return (
        f'<div class="ind-badge {color_cls}">'
        f'<span class="ind-label">{label}</span>'
        f'<span class="ind-value">{val_str}</span>'
        f'<span class="ind-pct">{sign} {pct_str}</span>'
        f'</div>'
    )


def _build_market_indicators(market_overview: dict) -> str:
    if not market_overview:
        return ""
    nf = market_overview.get("night_futures", {})
    us = market_overview.get("us_market",     {})
    kr = market_overview.get("korea_market",  {})

    # BUG-IND-1: 순서 변경 → 전일 코스피 > 전일 코스닥 > 나스닥 > S&P500 > 다우존스 > 야간선물 > 달러/원
    badges  = _indicator_badge("전일 코스피", kr.get("kospi",  {}).get("price"), kr.get("kospi",  {}).get("change_pct"))
    badges += _indicator_badge("전일 코스닥", kr.get("kosdaq", {}).get("price"), kr.get("kosdaq", {}).get("change_pct"))
    badges += _indicator_badge("나스닥",      us.get("nasdaq", {}).get("price"), us.get("nasdaq", {}).get("change_pct"))
    badges += _indicator_badge("S&P500",      us.get("sp500",  {}).get("price"), us.get("sp500",  {}).get("change_pct"))
    badges += _indicator_badge("다우존스",    us.get("dow",    {}).get("price"), us.get("dow",    {}).get("change_pct"))
    badges += _indicator_badge("야간선물",    nf.get("price"),                   nf.get("change_pct"), direction=nf.get("direction", ""))
    badges += _indicator_badge("달러/원",     us.get("usd_krw",{}).get("price"), us.get("usd_krw",{}).get("change_pct"))
    return f'<div class="ind-row">{badges}</div>'


# ── 5단락 시장 요약 렌더링 ────────────────────────────────────────────────────

def _render_market_summary(market_summary: str) -> str:
    if not market_summary:
        return '<p style="color:#888;">시장 요약 생성 중...</p>'
    icons      = ["🌙", "🇺🇸", "🇰🇷", "📊", "🔥"]
    paragraphs = [p.strip() for p in market_summary.split("\n\n") if p.strip()]
    html       = '<div class="summary-grid">\n'
    for i, para in enumerate(paragraphs):
        icon = icons[i] if i < len(icons) else "📌"
        if ":" in para:
            idx   = para.index(":")
            title = para[:idx].strip()
            body  = para[idx + 1:].strip()
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


# ── BUG-HP-2: 히든픽 소스 배지 헬퍼 ─────────────────────────────────────────

def _hidden_pick_source_badge(channel_type: str) -> str:
    """
    channel_type에 따라 색상·아이콘이 다른 배지 HTML 반환.
    '전문가 소스에서만 단독 발굴됨'을 시각적으로 표현.
    """
    meta  = _HP_SOURCE_META.get(channel_type, _HP_SOURCE_DEFAULT)
    color = meta["color"]
    icon  = meta["icon"]
    label = meta["label"]
    return (
        f'<span class="hp-source-badge" '
        f'style="background:{color}20;color:{color};border-color:{color}50;">'
        f'{icon} {label}'
        f'</span>'
    )


# ── 애널리스트 리포트 렌더링 ──────────────────────────────────────────────────

def _build_analyst_html(all_data: list) -> str:
    """애널리스트 리포트를 3개 카테고리로 분류하여 렌더링"""
    if not all_data:
        return ""
    analyst_items = [d for d in all_data if d.get("source_type") == "애널리스트"]
    if not analyst_items:
        return ""

    simultaneous  = [r for r in analyst_items if r.get("analyst_category") == "simultaneous"]
    new_coverage  = [r for r in analyst_items if r.get("analyst_category") == "new_coverage"]
# 변경:
# BUG-AC-12: analyst_collector의 카테고리명 변경(single_broker)에 맞춰 양쪽 모두 허용
    first_mention = [
       r for r in analyst_items
       if r.get("analyst_category") in ("single_broker", "first_in_6months")
    ]

    def _report_card(r):
        stock     = r.get("stock_name", "")
        broker    = r.get("source_name", "")
        title     = r.get("report_title", "") or r.get("title", "")
        date      = r.get("date", "") or r.get("published", "")
        link      = r.get("link", "")
        naver_url = (
            link if link else
            f"https://finance.naver.com/research/company_list.naver"
            f"?searchType=itemCode&itemName={url_quote(stock)}"
        )
        link_html = f'<a href="{naver_url}" target="_blank" class="source-link">🔗 리포트</a>'
        is_new    = r.get("new_coverage", False)
        badge     = '<span class="new-cov-badge">신규커버리지</span>' if is_new else ""
        return (
            f'<div class="report-card">'
            f'<div class="report-header">'
            f'<span class="report-stock">{stock}</span>{badge}'
            f'<span class="report-broker">{broker}</span>'
            f'{link_html}'
            f'</div>'
            f'<div class="report-title">{title}</div>'
            f'<div class="report-date">{date}</div>'
            f'</div>\n'
        )

    html = ""
    if simultaneous:
        html += '<div class="sec3-group">\n'
        html += '<h3 class="sec3-subtitle">① 증권사 동시 언급</h3>\n'
        for r in simultaneous[:10]:
            html += _report_card(r)
        html += '</div>\n'

    if new_coverage:
        html += '<div class="sec3-group">\n'
        html += '<h3 class="sec3-subtitle">② 신규 커버리지 개시</h3>\n'
        for r in new_coverage[:10]:
            html += _report_card(r)
        html += '</div>\n'

    if first_mention:
        html += '<div class="sec3-group">\n'
        # 변경:
        html += '<h3 class="sec3-subtitle">③ 단독 언급</h3>\n'
        for r in first_mention[:10]:
            html += _report_card(r)
        html += '</div>\n'

    # 카테고리 미분류 항목도 표시
    categorized = set(
        id(r) for r in simultaneous + new_coverage + first_mention
    )
    uncategorized = [r for r in analyst_items if id(r) not in categorized]
    if uncategorized:
        html += '<div class="sec3-group">\n'
        html += '<h3 class="sec3-subtitle">📄 오늘의 리포트</h3>\n'
        for r in uncategorized[:15]:
            html += _report_card(r)
        html += '</div>\n'

    return html


# ── 경제방송TV 전문가 추천 렌더링 ─────────────────────────────────────────────

def _build_section2_html(all_data: list) -> str:
    """경제방송TV에서 수집된 전문가 출연 영상 카드 렌더링"""
    if not all_data:
        return ""
    items = [d for d in all_data if d.get("source_type") == "경제방송TV"]
    if not items:
        return ""

    html = ""
    seen_titles = set()
    for item in items:
        title = item.get("title", "")
        if title in seen_titles:
            continue
        seen_titles.add(title)

        source_name = item.get("source_name", "")
        published   = item.get("published", "")
        link        = item.get("link", "")
        summary     = item.get("summary", "")[:200]

        link_html = (
            f' <a href="{link}" target="_blank" class="source-link">🔗 영상 보기</a>'
            if link else ""
        )
        html += (
            f'<div class="sec2-card">'
            f'<div class="sec2-header">'
            f'<span class="sec2-channel">{source_name}</span>'
            f'<span class="sec2-date">{published}</span>'
            f'{link_html}'
            f'</div>'
            f'<div class="sec2-title">{title}</div>'
            + (f'<p class="sec2-summary">{summary}</p>' if summary else "")
            + f'</div>\n'
        )
    return html


# ── 메인 HTML 생성 ────────────────────────────────────────────────────────────

def generate_html(data, channels_data=None, gh_repo="", gh_token="",
                  market_overview=None, all_data=None):
    now_kst             = datetime.now(KST)
    briefing_date       = data.get("briefing_date", now_kst.strftime("%Y-%m-%d"))
    briefing_datetime   = now_kst.strftime("%Y-%m-%d %H:%M")
    market_summary      = data.get("market_summary", "")
    hot_sectors         = data.get("hot_sectors", [])
    stocks              = data.get("stocks", [])
    hidden_picks        = data.get("hidden_picks", [])
    investment_strategy = data.get("investment_strategy", data.get("final_summary", ""))

    # BUG-NEW-6: overlap_count를 channel_counts 기준으로 재계산
    for s in stocks:
        if s.get("channel_counts"):
            recalc = sum(1 for v in s["channel_counts"].values() if v > 0)
            if recalc > s.get("overlap_count", 0):
                s["overlap_count"] = recalc
    stocks = [s for s in stocks if s.get("overlap_count", 0) >= 2]

    # BUG-H5: signal 필터 — positive 영문 기준 통일 (ai_analyzer 프롬프트와 일치)
    hidden_picks = [
        s for s in hidden_picks
        if s.get("signal", "").lower() == "positive"
        or "긍정" in s.get("signal", "")   # 레거시 한글값 호환
    ]

    indicators_html   = _build_market_indicators(market_overview)
    formatted_summary = _render_market_summary(market_summary)
    sectors_html      = "".join(f'<span class="sector-badge">{s}</span>\n' for s in hot_sectors)

    # ── 관심종목 카드 ──────────────────────────────────────────────────────────
    stocks_html = ""
    for stock in stocks:
        name        = stock.get("name", "")
        rank        = stock.get("rank", "")
        signal      = stock.get("signal", "neutral")
        description = stock.get("description", "")
        price_trend = stock.get("price_trend", "")
        catalyst    = stock.get("catalyst", "")
        risk        = stock.get("risk", "")
        overlap     = stock.get("overlap_count", 0)
        source_types= stock.get("source_types", [])
        reasons     = stock.get("reasons", [])
        verified_price = stock.get("verified_price")
        chart_b64   = stock.get("chart_base64")
        market_type = stock.get("market", "국내")
        naver_code  = stock.get("naver_code", "")
        weighted_score = stock.get("weighted_score", 0)
        if not naver_code and isinstance(verified_price, dict):
            naver_code = verified_price.get("code", "")

        signal_class = (
            "signal-positive" if signal == "positive" or "긍정" in signal else
            "signal-negative" if signal == "negative" or "부정" in signal else
            "signal-neutral"
        )
        signal_label = (
            "긍정" if signal == "positive" else
            "부정" if signal == "negative" else "중립"
        )

        channel_counts = stock.get("channel_counts", {})
        total_count    = stock.get("total_count", overlap)
        if channel_counts:
            parts = [
                f"{ch} {cnt}회"
                for ch in ["뉴스", "경제방송", "경제방송TV", "유튜브", "애널리스트"]
                for cnt in [channel_counts.get(ch, 0)] if cnt > 0
            ]
            overlap_badge = (
                f'<span class="overlap-badge">총 {total_count}회 / 가중 {weighted_score:.1f}점 '
                f'({" / ".join(parts)})</span>'
            )
        else:
            overlap_badge = f'<span class="overlap-badge">{overlap}개 채널 언급</span>'

        source_tags = "".join(f'<span class="source-tag">{st}</span>' for st in source_types)

        price_info_text = ""
        if isinstance(verified_price, dict):
            p  = verified_price
            cv = str(p.get("change", "") or "")
            sign_c = "▲" if cv.startswith("+") else ("▼" if cv.startswith("-") else "")
            cd     = cv.lstrip("+-")
            if sign_c and cd:
                price_info_text = f' ({p.get("price","??")}원 {sign_c}{cd} {p.get("change_pct","")})'
            else:
                price_info_text = f' ({p.get("price","??")}원)'
        elif market_type == "해외":
            price_info_text = " (해외 종목)"

        if chart_b64:
            chart_btn_html = (
                f' <span class="chart-icon"'
                f' onclick="openChartWindow(\'{name}\', \'{rank}\')"'
                f' title="14일 주가 차트">📈 차트</span>'
            )
        else:
            naver_url = (
                f"https://finance.naver.com/item/main.naver?code={naver_code}"
                if naver_code else
                f"https://finance.naver.com/search/searchResult.naver?query={url_quote(name)}"
            )
            chart_btn_html = (
                f' <a href="{naver_url}" target="_blank"'
                f' class="chart-icon" title="네이버 금융">📈 차트</a>'
            )

        reasons_html = ""
        for reason in reasons:
            rs   = reason.get("source_type", "")
            rn   = reason.get("source_name", "")
            rd = reason.get("detail", "") or reason.get("reason", "")
            rurl = reason.get("source_url", "")
            if not rurl and "애널리스트" in rs:
                rurl = (
                    "https://finance.naver.com/research/company_list.naver"
                    f"?searchType=itemCode&itemName={url_quote(name)}"
                )
            link_html = (
                f' <a href="{rurl}" target="_blank" class="source-link">🔗 바로보기</a>'
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
            f'<span class="stock-signal {signal_class}">{signal_label}</span>'
            f'{overlap_badge}</div>'
            f'<div class="source-tags">{source_tags}</div>'
            f'<div class="info-block"><h4>📋 종목 요약</h4><p>{description}</p></div>'
            f'<div class="info-block">'
            f'<h4>📈 주가 흐름{price_info_text}{chart_btn_html}</h4>'
            f'<p>{price_trend}</p></div>'
            f'<div class="info-block"><h4>🚀 상승 촉매</h4><p>{catalyst}</p></div>'
            f'<div class="info-block"><h4>⚠️ 리스크</h4><p>{risk}</p></div>'
            f'<div class="reasons-section">'
            f'<h4>📢 채널별 언급 내용</h4>{reasons_html}'
            f'</div></div>\n'
        )

    # ── 오늘의 픽 (히든픽) 카드 ────────────────────────────────────────────────
    hidden_html = ""
    for idx, hp in enumerate(hidden_picks, 1):
        hp_name        = hp.get("name", "")
        hp_rank        = hp.get("rank", idx)
        hp_desc        = hp.get("description", "")
        hp_catalyst    = hp.get("catalyst", "")
        hp_risk        = hp.get("risk", "")
        hp_reasons     = hp.get("reasons", [])
        hp_verified    = hp.get("verified_price")
        hp_market      = hp.get("market", "국내")
        hp_chart_b64   = hp.get("chart_base64")
        hp_naver_code  = hp.get("naver_code", "")
        hp_channel_type = hp.get("channel_type", "")
        hp_score       = hp.get("weighted_score", 0)
        if not hp_naver_code and isinstance(hp_verified, dict):
            hp_naver_code = hp_verified.get("code", "")

        # BUG-HP-3: 소스 배지 생성
        source_badge_html = _hidden_pick_source_badge(hp_channel_type) if hp_channel_type else ""

        # BUG-HP-4: 가중치 점수 배지
        score_badge_html = (
            f'<span class="hp-score-badge">가중점수 {hp_score:.1f}</span>'
            if hp_score else ""
        )

        hp_price_html = ""
        if isinstance(hp_verified, dict):
            p  = hp_verified
            cv = str(p.get("change", "") or "")
            cc = ("price-up"   if cv.startswith("+") else
                  "price-down" if cv.startswith("-") else "price-note")
            hp_price_html = (
                f'<div class="price-box">'
                f'<span class="current-price">{p.get("price","??")}원</span>'
                f'<span class="{cc}">'
                + (f'{cv} ({p.get("change_pct","")})' if cv else "등락 정보 없음")
                + '</span></div>'
            )
        elif hp_market == "해외":
            hp_price_html = '<div class="price-box"><span class="price-note">해외 종목</span></div>'

        if hp_chart_b64:
            hp_chart_btn = (
                f' <span class="chart-icon"'
                f' onclick="openChartWindow(\'{hp_name}\', \'hp_{hp_rank}\')">'
                f'📈 차트</span>'
            )
        else:
            hp_naver_url = (
                f"https://finance.naver.com/item/main.naver?code={hp_naver_code}"
                if hp_naver_code else
                f"https://finance.naver.com/search/searchResult.naver?query={url_quote(hp_name)}"
            )
            hp_chart_btn = (
                f' <a href="{hp_naver_url}" target="_blank" class="chart-icon">📈 차트</a>'
            )

        hp_reasons_html = ""
        for reason in hp_reasons:
            rs   = reason.get("source_type", "")
            rn   = reason.get("source_name", "")
            rd   = reason.get("reason", "") or reason.get("detail", "")
            rurl = reason.get("source_url", "")
            if not rurl and "애널리스트" in rs:
                rurl = (
                    "https://finance.naver.com/research/company_list.naver"
                    f"?searchType=itemCode&itemName={url_quote(hp_name)}"
                )
            link_html = (
                f' <a href="{rurl}" target="_blank" class="source-link">🔗 바로보기</a>'
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

        # BUG-HP-5: 카드 헤더에 소스배지 + 점수배지 추가
        hidden_html += (
            f'<div class="hidden-pick-card">'
            f'<div class="stock-header">'
            f'<span class="stock-rank">Pick #{hp_rank}</span>'
            f'<span class="stock-name">{hp_name}</span>'
            f'{source_badge_html}'
            f'{score_badge_html}'
            f'</div>'
            f'{hp_price_html}'
            f'<div class="info-block"><h4>📋 기업 소개</h4><p>{hp_desc}</p></div>'
            f'<div class="info-block"><h4>🚀 주목 이유{hp_chart_btn}</h4><p>{hp_catalyst}</p></div>'
            f'<div class="info-block"><h4>⚠️ 리스크</h4><p>{hp_risk}</p></div>'
            + (f'<div class="reasons-section"><h4>📢 발굴 근거</h4>{hp_reasons_html}</div>'
               if hp_reasons_html else "")
            + f'</div>\n'
        )

    # ── 차트 JS ────────────────────────────────────────────────────────────────
    chart_data_js = "var chartDataMap = {};\n"
    for stock in stocks:
        b64 = stock.get("chart_base64")
        if b64:
            c = b64.replace('\n', '').replace('\r', '')
            chart_data_js += (
                f'chartDataMap["{stock.get("rank","")}"] = '
                f'"data:image/png;base64,{c}";\n'
            )
    for hp in hidden_picks:
        b64 = hp.get("chart_base64")
        if b64:
            c = b64.replace('\n', '').replace('\r', '')
            chart_data_js += (
                f'chartDataMap["hp_{hp.get("rank","")}"] = '
                f'"data:image/png;base64,{c}";\n'
            )

    # ── 섹션 데이터 준비 ───────────────────────────────────────────────────────
    section2_html = _build_section2_html(all_data or [])
    analyst_html  = _build_analyst_html(all_data or [])

    # ── 아카이브 링크 ──────────────────────────────────────────────────────────
    archive_links = ""
    try:
        # BUG-M6: __file__ 기준 절대 경로 사용
        base_dir    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        archive_dir = os.path.join(base_dir, "docs", "archive")
        if os.path.exists(archive_dir):
            html_files = sorted(
                [f for f in os.listdir(archive_dir) if f.endswith(".html")],
                reverse=True,
            )
            repo_owner = gh_repo.split("/")[0] if gh_repo and "/" in gh_repo else ""
            repo_name  = gh_repo.split("/")[1] if gh_repo and "/" in gh_repo else ""
            for af in html_files[:14]:
                date_str = af.replace(".html", "")
                url = (
                    f"https://{repo_owner}.github.io/{repo_name}/archive/{af}"
                    if repo_owner and repo_name else f"archive/{af}"
                )
                archive_links += f'<a href="{url}" class="archive-link">{date_str}</a>\n'
            print(f"  [아카이브] {len(html_files)}개 링크 생성")
    except Exception as e:
        print(f"  [아카이브] 오류: {e}")

    # ── CSS ────────────────────────────────────────────────────────────────────
    css = """
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:'Pretendard',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#0a0a14; color:#e0e0e0; line-height:1.6; }
.container { max-width:860px; margin:0 auto; padding:20px; }
.header { text-align:center; padding:30px 0; border-bottom:1px solid #1e1e2e; margin-bottom:30px; }
.header h1 { font-size:1.8em; color:#fff; margin-bottom:8px; }
.header .date { color:#888; font-size:.95em; }
.header .desc { color:#aaa; font-size:.85em; margin-top:8px; }
.section { margin-bottom:35px; }
.section-title { font-size:1.3em; color:#fff; margin-bottom:15px;
                 padding-left:12px; border-left:3px solid #667eea; }
.ind-row { display:flex; flex-wrap:wrap; gap:10px; margin-bottom:20px; }
.ind-badge { display:flex; flex-direction:column; align-items:center;
             padding:10px 16px; border-radius:12px; min-width:90px; border:1px solid transparent; }
.ind-call    { background:#ff6b6b18; border-color:#ff6b6b50; }
.ind-put     { background:#339af018; border-color:#339af050; }
.ind-neutral { background:#ffd43b18; border-color:#ffd43b50; }
.ind-label { font-size:.72em; color:#888; margin-bottom:4px; }
.ind-value { font-size:1.05em; font-weight:700; color:#fff; }
.ind-call .ind-value { color:#ff6b6b; }
.ind-put  .ind-value { color:#339af0; }
.ind-pct { font-size:.78em; margin-top:2px; }
.ind-call .ind-pct    { color:#ff6b6b; }
.ind-put  .ind-pct    { color:#339af0; }
.ind-neutral .ind-pct { color:#ffd43b; }
.summary-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
@media(max-width:600px){ .summary-grid{ grid-template-columns:1fr; } }
.summary-block { background:#141420; border-radius:12px; padding:18px; border:1px solid #1e1e2e; }
.summary-subtitle { color:#667eea; font-size:1em; margin-bottom:8px; }
.summary-text { color:#ccc; font-size:.88em; line-height:1.7; }
.sector-badge { display:inline-block; background:linear-gradient(135deg,#667eea20,#764ba220);
                color:#a8b4ff; padding:6px 14px; border-radius:20px;
                margin:4px; font-size:.85em; border:1px solid #667eea40; }
.stock-card,.hidden-pick-card { background:#141420; border-radius:12px;
    padding:20px; margin-bottom:16px; border:1px solid #1e1e2e; transition:border-color .3s; }
.stock-card:hover { border-color:#667eea60; }
.hidden-pick-card { border-left:3px solid #ffd43b; }
.hidden-pick-card:hover { border-color:#ffd43b60; }
.stock-header { display:flex; align-items:center; gap:10px; margin-bottom:12px; flex-wrap:wrap; }
.stock-rank { background:#667eea; color:#fff; padding:2px 10px; border-radius:12px;
              font-size:.85em; font-weight:700; }
.stock-name { font-size:1.15em; font-weight:700; color:#fff; }
.stock-signal { padding:3px 10px; border-radius:10px; font-size:.8em; font-weight:600; }
.signal-positive { background:#ff6b6b20; color:#ff6b6b; border:1px solid #ff6b6b40; }
.signal-negative { background:#339af020; color:#339af0; border:1px solid #339af040; }
.signal-neutral  { background:#ffd43b20; color:#ffd43b; border:1px solid #ffd43b40; }
.overlap-badge { background:#51cf6620; color:#51cf66; padding:3px 10px;
                 border-radius:10px; font-size:.8em; border:1px solid #51cf6640;
                 white-space:nowrap; }
/* BUG-HP-6: 히든픽 전용 배지 CSS */
.hp-source-badge { padding:3px 10px; border-radius:10px; font-size:.8em;
                   font-weight:600; border:1px solid transparent; white-space:nowrap; }
.hp-score-badge  { background:#764ba220; color:#c084fc; padding:3px 10px;
                   border-radius:10px; font-size:.8em; border:1px solid #764ba240;
                   white-space:nowrap; }
.source-tags { margin-bottom:12px; }
.source-tag { display:inline-block; background:#1e1e2e; color:#888;
              padding:3px 8px; border-radius:6px; font-size:.75em; margin:2px; }
.price-box { margin-bottom:12px; padding:10px; background:#1a1a2e; border-radius:8px; }
.current-price { font-size:1.3em; font-weight:700; color:#fff; margin-right:10px; }
.price-up   { color:#ff6b6b; font-weight:600; }
.price-down { color:#339af0; font-weight:600; }
.price-note { color:#888; font-size:.85em; }
.chart-icon { cursor:pointer; color:#667eea; font-size:.85em;
              padding:3px 8px; border-radius:6px; background:#667eea15;
              border:1px solid #667eea30; margin-left:4px;
              white-space:nowrap; text-decoration:none; display:inline-block; }
.chart-icon:hover { background:#667eea30; }
.info-block { margin-bottom:12px; }
.info-block h4 { color:#a8b4ff; font-size:.9em; margin-bottom:4px;
                 display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
.info-block p { color:#bbb; font-size:.88em; }
.reasons-section { margin-top:12px; }
.reasons-section h4 { color:#a8b4ff; font-size:.9em; margin-bottom:8px; }
.reason-item { background:#1a1a2e; border-radius:8px; padding:10px; margin-bottom:6px; }
.reason-header { display:flex; align-items:center; gap:8px; margin-bottom:4px; flex-wrap:wrap; }
.reason-source { color:#667eea; font-size:.82em; font-weight:600; }
.source-link { color:#51cf66; font-size:.78em; text-decoration:none; }
.source-link:hover { text-decoration:underline; }
.reason-detail { color:#aaa; font-size:.85em; }
.strategy-block { background:linear-gradient(135deg,#141420,#1a1a2e);
                  border:1px solid #667eea30; border-radius:12px; padding:20px; }
.strategy-block p { color:#ccc; font-size:.92em; line-height:1.8; }
.disclaimer { text-align:center; color:#666; font-size:.78em;
              margin-top:30px; padding:15px; border-top:1px solid #1e1e2e; }
.archive-section { margin-top:20px; }
.archive-link { display:inline-block; color:#667eea; text-decoration:none;
                padding:4px 10px; margin:3px; border:1px solid #667eea30;
                border-radius:6px; font-size:.82em; }
.archive-link:hover { background:#667eea20; }
.chart-modal { display:none; position:fixed; top:0; left:0; width:100%; height:100%;
               background:rgba(0,0,0,.85); z-index:1000;
               justify-content:center; align-items:center; }
.chart-modal img { max-width:95%; max-height:80%; border-radius:8px; }
.chart-modal .close-btn { position:absolute; top:20px; right:30px;
                           color:#fff; font-size:2em; cursor:pointer; }
/* ── 경제방송TV ── */
.sec2-card { background:#141420; border-radius:10px; padding:14px;
             margin-bottom:10px; border:1px solid #1e1e2e;
             border-left:3px solid #ffa94d; }
.sec2-card:hover { border-color:#ffa94d80; }
.sec2-header { display:flex; align-items:center; gap:8px;
               margin-bottom:6px; flex-wrap:wrap; }
.sec2-channel { background:#ffa94d20; color:#ffa94d; padding:2px 8px;
                border-radius:8px; font-size:.78em; font-weight:700; }
.sec2-date { color:#666; font-size:.75em; }
.sec2-title { color:#ddd; font-size:.9em; font-weight:600; margin-bottom:4px; }
.sec2-summary { color:#999; font-size:.82em; line-height:1.6; }
/* ── 애널리스트 리포트 ── */
.sec3-group { margin-bottom:20px; }
.sec3-subtitle { color:#a8b4ff; font-size:.95em; margin-bottom:10px;
                 padding-left:10px; border-left:2px solid #667eea; }
.report-card { background:#141420; border-radius:10px; padding:12px;
               margin-bottom:8px; border:1px solid #1e1e2e;
               border-left:3px solid #51cf66; }
.report-card:hover { border-color:#51cf6680; }
.report-header { display:flex; align-items:center; gap:8px;
                 margin-bottom:4px; flex-wrap:wrap; }
.report-stock { font-weight:700; color:#fff; font-size:.92em; }
.report-broker { color:#667eea; font-size:.78em;
                 background:#667eea15; padding:2px 8px; border-radius:6px; }
.report-title { color:#bbb; font-size:.85em; margin-bottom:2px; }
.report-date { color:#666; font-size:.75em; }
.new-cov-badge { background:#ffd43b20; color:#ffd43b; padding:2px 8px;
                 border-radius:6px; font-size:.75em; font-weight:700;
                 border:1px solid #ffd43b40; margin-left:4px; }
"""

    # ── HTML 조립 (섹션 순서: 시장지표 > 시장요약 > 주목섹터 > 관심종목 > 오늘의픽 > 애널리스트 > 경제방송TV > AI전략 > 지난브리핑) ──
    html = (
        '<!DOCTYPE html>\n<html lang="ko">\n<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f'<title>AI 주식 브리핑 - {briefing_date}</title>\n'
        f'<style>\n{css}\n</style>\n'
        '</head>\n<body>\n<div class="container">\n'
        '  <div class="header">\n'
        '    <h1>📊 AI 주식 브리핑</h1>\n'
        f'    <div class="date">{briefing_datetime} 기준</div>\n'
        '    <div class="desc">뉴스·경제방송·유튜브·증권사 보고서 교차 분석 브리핑</div>\n'
        '  </div>\n\n'
    )

    # 1. 시장 지표
    if indicators_html:
        html += (
            '  <div class="section">\n'
            '    <h2 class="section-title">📡 시장 지표</h2>\n'
            f'    {indicators_html}\n'
            '  </div>\n\n'
        )

    # 2. 시장 요약
    html += (
        '  <div class="section">\n'
        '    <h2 class="section-title">🌍 시장 요약</h2>\n'
        f'    {formatted_summary}\n'
        '  </div>\n\n'
    )

    # 3. 주목 섹터
    html += (
        '  <div class="section">\n'
        '    <h2 class="section-title">🔥 주목 섹터</h2>\n'
        f'    {sectors_html}\n'
        '  </div>\n\n'
    )

    # 4. 관심 종목
    html += (
        '  <div class="section">\n'
        '    <h2 class="section-title">🎯 관심 종목</h2>\n'
        '    <p style="color:#888;font-size:.82em;margin-bottom:12px">'
        '2개 이상 채널 유형에서 공통 언급 · 채널 가중치 점수 기준 정렬</p>\n'
        f'    {stocks_html if stocks_html else "<p style=color:#666>오늘 해당 종목 없음</p>"}\n'
        '  </div>\n\n'
    )

    # 5. 오늘의 픽 (히든픽)
    if hidden_html:
        html += (
            '  <div class="section">\n'
            '    <h2 class="section-title">💎 오늘의 픽</h2>\n'
            '    <p style="color:#888;font-size:.82em;margin-bottom:12px">'
            '전문가 소스(애널리스트·경제방송TV)에서만 단독 포착된 종목</p>\n'
            f'    {hidden_html}\n'
            '  </div>\n\n'
        )

    # 6. 애널리스트 리포트 분석
    if analyst_html:
        html += (
            '  <div class="section">\n'
            '    <h2 class="section-title">📋 애널리스트 리포트 분석</h2>\n'
            '    <p style="color:#888;font-size:.82em;margin-bottom:12px">'
            '최근 24시간 이내 증권사 리서치 리포트</p>\n'
            f'    {analyst_html}\n'
            '  </div>\n\n'
        )

    # 7. 경제방송TV 전문가 추천
    if section2_html:
        html += (
            '  <div class="section">\n'
            '    <h2 class="section-title">📺 경제방송TV 전문가 추천</h2>\n'
            '    <p style="color:#888;font-size:.82em;margin-bottom:12px">'
            '전일(D-1) 기준 전문가 출연 종목추천 프로그램</p>\n'
            f'    {section2_html}\n'
            '  </div>\n\n'
        )

    # 8. AI 투자 전략
    if investment_strategy:
        html += (
            '  <div class="section">\n'
            '    <h2 class="section-title">💰 AI 투자 전략</h2>\n'
            '    <div class="strategy-block">\n'
            f'      <p>{investment_strategy}</p>\n'
            '    </div>\n'
            '  </div>\n\n'
        )

    # 9. 지난 브리핑
    if archive_links:
        html += (
            '  <div class="section archive-section">\n'
            '    <h2 class="section-title">📅 지난 브리핑</h2>\n'
            f'    {archive_links}\n'
            '  </div>\n\n'
        )

    html += (
        '  <div class="disclaimer">\n'
        '    ⚠️ 본 브리핑은 AI가 자동 생성한 참고 자료이며, 투자 권유가 아닙니다.<br>\n'
        '    투자 판단의 책임은 투자자 본인에게 있습니다.\n'
        '  </div>\n'
        '</div>\n\n'
        '<div class="chart-modal" id="chartModal" onclick="closeChart()">\n'
        '  <span class="close-btn" onclick="closeChart()">&times;</span>\n'
        '  <img id="chartImg" src="" alt="차트">\n'
        '</div>\n\n'
        '<script>\n'
        + chart_data_js
        + '\nfunction openChartWindow(n,k){'
        'var s=chartDataMap[k];if(s){'
        'document.getElementById("chartImg").src=s;'
        'document.getElementById("chartModal").style.display="flex";}}\n'
        'function closeChart(){'
        'document.getElementById("chartModal").style.display="none";}\n'
        'document.addEventListener("keydown",function(e){'
        'if(e.key==="Escape")closeChart();});\n'
        '</script>\n</body>\n</html>'
    )
    return html
