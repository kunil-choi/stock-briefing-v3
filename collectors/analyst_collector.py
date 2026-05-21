# collectors/analyst_collector.py
"""
애널리스트 리포트 수집기 - v3
네이버 금융 리서치에서 최근 24시간 이내 리포트 수집
분류: ① 증권사 동시 언급 ② 6개월 내 첫 언급 ③ 신규 커버리지
"""
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import time

REPORT_DAYS = 1

BROKERS = [
    "NH투자증권", "삼성증권", "KB증권", "미래에셋증권",
    "한국투자증권", "신한투자증권", "하나증권", "키움증권",
    "대신증권", "메리츠증권", "한화투자증권", "유진투자증권",
    "LS증권", "IBK투자증권", "DB금융투자", "SK증권",
    "현대차증권", "BNK투자증권", "iM증권", "교보증권",
    "다올투자증권", "한양증권", "흥국증권", "토스증권",
]

NAVER_FINANCE_BASE = "https://finance.naver.com"
NAVER_RESEARCH_BASE = "https://finance.naver.com/research"

# 신규 커버리지를 나타내는 키워드
COVERAGE_KEYWORDS = ["커버리지", "신규", "개시", "Coverage Initiation", "Initiation"]
FIRST_MENTION_KEYWORDS = ["첫 분석", "첫 리포트", "처음"]


def _build_link(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return NAVER_FINANCE_BASE + href
    return NAVER_RESEARCH_BASE + "/" + href


def is_within_days(date_str: str, days: int = REPORT_DAYS) -> bool:
    try:
        date_str = date_str.strip().replace(" ", "")
        if len(date_str) == 8:
            report_date = datetime.strptime(date_str, "%y.%m.%d")
        elif len(date_str) == 10:
            report_date = datetime.strptime(date_str, "%Y.%m.%d")
        else:
            return True
        cutoff = datetime.now() - timedelta(days=days)
        return report_date >= cutoff.replace(hour=0, minute=0, second=0)
    except Exception:
        return True


def is_new_coverage(title: str) -> bool:
    """신규 커버리지 개시 여부 확인"""
    return any(k in title for k in COVERAGE_KEYWORDS)


def collect_naver_research() -> list:
    """네이버 금융 리서치에서 종목분석 리포트 수집"""
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    }

    for page in range(1, 6):
        url = f"https://finance.naver.com/research/company_list.naver?&page={page}"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("table.type_1 tr")

            page_has_recent = False
            for row in rows:
                cols = row.select("td")
                if len(cols) < 5:
                    continue

                date_str = cols[4].get_text(strip=True)
                if not is_within_days(date_str, REPORT_DAYS):
                    continue
                page_has_recent = True

                stock_name = cols[0].get_text(strip=True)
                report_title = cols[1].get_text(strip=True)
                broker = cols[2].get_text(strip=True)
                analyst = cols[3].get_text(strip=True) if len(cols) > 3 else ""

                # 목표주가 및 투자의견 추출 시도
                target_price = ""
                opinion = ""
                title_text = cols[1].get_text(strip=True)
                
                # 링크 추출
                link_tag = cols[1].find("a")
                link = _build_link(link_tag.get("href", "")) if link_tag else ""

                # PDF 링크 추출
                pdf_tag = cols[5].find("a") if len(cols) > 5 else None
                pdf_link = _build_link(pdf_tag.get("href", "")) if pdf_tag else ""

                # 신규 커버리지 여부 판단
                new_coverage = is_new_coverage(report_title)

                results.append({
                    "source_type": "애널리스트",
                    "source_name": broker,
                    "stock_name": stock_name,
                    "report_title": report_title,
                    "analyst": analyst,
                    "target_price": target_price,
                    "opinion": opinion,
                    "date": date_str,
                    "link": link or pdf_link,
                    "new_coverage": new_coverage,
                    "section": "section3",
                    "title": f"[{broker}] {stock_name} - {report_title}",
                    "summary": f"증권사: {broker} | 담당: {analyst} | 종목: {stock_name} | 리포트: {report_title}",
                })

            if not page_has_recent and page > 1:
                break

            time.sleep(0.5)

        except Exception as e:
            print(f"  [리서치 페이지 {page}] 오류: {e}")

    return results


def classify_analyst_reports(reports: list) -> dict:
    """
    애널리스트 리포트를 3가지 카테고리로 분류:
    - simultaneous: 24시간 내 복수 증권사가 동일 종목 리포트 발행
    - first_in_6months: 6개월 내 첫 언급 (오늘 데이터 기준 추정)
    - new_coverage: 신규 커버리지 개시
    """
    from collections import defaultdict

    # 종목별 증권사 그룹핑
    stock_brokers = defaultdict(list)
    for r in reports:
        stock_name = r.get("stock_name", "")
        if stock_name:
            stock_brokers[stock_name].append(r)

    simultaneous = []   # ① 복수 증권사 동시 언급
    new_coverage = []   # ③ 신규 커버리지
    first_mention = []  # ② 6개월 내 첫 언급 (단일 증권사)

    seen_stocks = set()

    for stock_name, stock_reports in stock_brokers.items():
        unique_brokers = list({r.get("source_name", "") for r in stock_reports})

        # ③ 신규 커버리지 먼저 분류
        for r in stock_reports:
            if r.get("new_coverage", False):
                new_coverage.append(r)
                seen_stocks.add(f"{stock_name}_{r.get('source_name', '')}")

        # ① 복수 증권사 동시 언급
        if len(unique_brokers) >= 2:
            # 대표 리포트 + 전체 증권사 정보 포함
            primary = stock_reports[0].copy()
            primary["simultaneous_brokers"] = unique_brokers
            primary["all_reports"] = stock_reports
            simultaneous.append(primary)
        else:
            # ② 단일 증권사 (신규 커버리지 아닌 경우)
            for r in stock_reports:
                key = f"{stock_name}_{r.get('source_name', '')}"
                if key not in seen_stocks and not r.get("new_coverage", False):
                    first_mention.append(r)

    return {
        "simultaneous": simultaneous,
        "first_in_6months": first_mention,
        "new_coverage": new_coverage,
    }


def collect_analyst() -> list:
    """
    애널리스트 리포트 수집 메인 함수
    반환: 분류 정보가 포함된 리포트 리스트
    """
    print("\n=== 섹션 3: 애널리스트 리포트 수집 ===")

    reports = collect_naver_research()
    print(f"  → 총 {len(reports)}건 수집")

    # 분류 정보 추가
    classified = classify_analyst_reports(reports)

    for r in classified["simultaneous"]:
        r["analyst_category"] = "simultaneous"
    for r in classified["first_in_6months"]:
        r["analyst_category"] = "first_in_6months"
    for r in classified["new_coverage"]:
        r["analyst_category"] = "new_coverage"

    # 분류된 리포트 통합 (중복 제거)
    all_classified = (
        classified["simultaneous"]
        + classified["new_coverage"]
        + classified["first_in_6months"]
    )

    # 중복 제거 (동일 종목+증권사)
    seen = set()
    unique_reports = []
    for r in all_classified:
        key = f"{r.get('stock_name', '')}_{r.get('source_name', '')}"
        if key not in seen:
            seen.add(key)
            unique_reports.append(r)

    print(f"  → 분류 완료: 동시언급 {len(classified['simultaneous'])}건, "
          f"신규커버리지 {len(classified['new_coverage'])}건, "
          f"첫언급 {len(classified['first_in_6months'])}건")

    return unique_reports
