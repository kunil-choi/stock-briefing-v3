# collectors/analyst_collector.py
"""
애널리스트 리포트 수집기 - v3
네이버 금융 리서치에서 최근 24시간 이내 리포트 수집
분류: ① 증권사 동시 언급 ② 6개월 내 첫 언급 ③ 신규 커버리지

네이버 금융 company_list.naver 실제 컬럼 구조:
  cols[0]: 종목명
  cols[1]: 리포트 제목 (링크 포함)
  cols[2]: 증권사
  cols[3]: 첨부 (PDF 링크, 없으면 빈 td)
  cols[4]: 작성일 (YY.MM.DD)
  cols[5]: 조회수
※ 목표주가·담당 애널리스트·의견 컬럼 없음
"""
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import time
from collections import defaultdict

REPORT_DAYS = 1

BROKERS = [
    "NH투자증권", "삼성증권", "KB증권", "미래에셋증권",
    "한국투자증권", "신한투자증권", "하나증권", "키움증권",
    "대신증권", "메리츠증권", "한화투자증권", "유진투자증권",
    "LS증권", "IBK투자증권", "DB금융투자", "SK증권",
    "현대차증권", "BNK투자증권", "iM증권", "교보증권",
    "다올투자증권", "한양증권", "흥국증권", "토스증권",
]

NAVER_FINANCE_BASE  = "https://finance.naver.com"
NAVER_RESEARCH_BASE = "https://finance.naver.com/research"

COVERAGE_KEYWORDS     = ["커버리지", "신규", "개시", "Coverage Initiation", "Initiation"]
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
    """
    네이버 금융 날짜 파싱.
    실제 형식: "26.05.22" (YY.MM.DD, 점 포함 8자)
    """
    try:
        date_str = date_str.strip().replace(" ", "")

        if len(date_str) == 10 and date_str.count(".") == 2:
            # "2026.05.22" 형식
            report_date = datetime.strptime(date_str, "%Y.%m.%d")
        elif len(date_str) == 8 and date_str.count(".") == 2:
            # "26.05.22" 형식 (실제 네이버 형식)
            report_date = datetime.strptime(date_str, "%y.%m.%d")
        elif len(date_str) == 8 and "." not in date_str:
            report_date = datetime.strptime(date_str, "%Y%m%d")
        elif len(date_str) == 6 and "." not in date_str:
            report_date = datetime.strptime(date_str, "%y%m%d")
        else:
            print(f"  [날짜 파싱 불가] '{date_str}' → 포함 처리")
            return True

        cutoff = datetime.now() - timedelta(days=days)
        return report_date >= cutoff.replace(hour=0, minute=0, second=0, microsecond=0)

    except Exception as e:
        print(f"  [날짜 파싱 오류] '{date_str}': {e} → 포함 처리")
        return True


def is_new_coverage(title: str) -> bool:
    """신규 커버리지 개시 여부 확인"""
    return any(k in title for k in COVERAGE_KEYWORDS)


def collect_naver_research() -> list:
    """
    네이버 금융 리서치 company_list.naver 수집.
    실제 확인된 컬럼 구조:
      cols[0]: 종목명
      cols[1]: 리포트 제목 (링크)
      cols[2]: 증권사
      cols[3]: 첨부 (PDF 링크, 없으면 빈 td)
      cols[4]: 작성일 (YY.MM.DD)
      cols[5]: 조회수
    """
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://finance.naver.com/",
        "Accept-Language": "ko-KR,ko;q=0.9",
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

                stock_name   = cols[0].get_text(strip=True)
                report_title = cols[1].get_text(strip=True)
                broker       = cols[2].get_text(strip=True)
                # cols[3] = 첨부(PDF) 컬럼 — get_text()는 빈 문자열
                date_str     = cols[4].get_text(strip=True)

                if not is_within_days(date_str, REPORT_DAYS):
                    continue
                page_has_recent = True

                if not stock_name or not broker:
                    continue

                # 리포트 본문 링크
                link_tag = cols[1].find("a")
                link     = _build_link(link_tag.get("href", "")) if link_tag else ""

                # PDF 링크: cols[3] 첨부 컬럼에서 추출
                pdf_link = ""
                pdf_tag  = cols[3].find("a")
                if pdf_tag and pdf_tag.get("href", "").endswith(".pdf"):
                    pdf_link = _build_link(pdf_tag.get("href", ""))

                new_coverage = is_new_coverage(report_title)

                results.append({
                    "source_type":  "애널리스트",
                    "source_name":  broker,
                    "stock_name":   stock_name,
                    "report_title": report_title,
                    "analyst":      "",   # 목록 페이지에 담당자 컬럼 없음
                    "target_price": "",   # 목록 페이지에 목표가 컬럼 없음
                    "opinion":      "",   # 목록 페이지에 의견 컬럼 없음
                    "date":         date_str,
                    "link":         link or pdf_link,
                    "new_coverage": new_coverage,
                    "section":      "section3",
                    "title":  f"[{broker}] {stock_name} - {report_title}",
                    "summary": f"증권사: {broker} | 종목: {stock_name} | 리포트: {report_title}",
                })

            if not page_has_recent and page > 1:
                print(f"  [리서치] 페이지 {page}에 최근 데이터 없음 → 수집 종료")
                break

            time.sleep(0.5)

        except Exception as e:
            print(f"  [리서치 페이지 {page}] 오류: {e}")

    return results


def classify_analyst_reports(reports: list) -> dict:
    """
    애널리스트 리포트를 3가지 카테고리로 분류:
    - simultaneous     : 24시간 내 복수 증권사 동일 종목 리포트
    - new_coverage     : 신규 커버리지 개시
    - first_in_6months : 단일 증권사 단독 언급
    """
    stock_brokers = defaultdict(list)
    for r in reports:
        stock_name = r.get("stock_name", "")
        if stock_name:
            stock_brokers[stock_name].append(r)

    simultaneous  = []
    new_coverage  = []
    first_mention = []
    seen_keys     = set()

    for stock_name, stock_reports in stock_brokers.items():
        # 고유 증권사 목록 (순서 유지)
        seen_b = set()
        unique_brokers = []
        for r in stock_reports:
            b = r.get("source_name", "")
            if b and b not in seen_b:
                unique_brokers.append(b)
                seen_b.add(b)

        # 신규 커버리지 먼저 분류
        for r in stock_reports:
            if r.get("new_coverage", False):
                key = f"{stock_name}_{r.get('source_name', '')}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    new_coverage.append(r)

        # 복수 증권사 동시 언급
        if len(unique_brokers) >= 2:
            primary = stock_reports[0].copy()
            brokers_str = " / ".join(unique_brokers)
            primary["source_name"]          = brokers_str
            primary["simultaneous_brokers"] = unique_brokers
            primary["all_reports"]          = stock_reports
            primary["title"]   = f"[동시언급: {brokers_str}] {stock_name} - {stock_reports[0].get('report_title', '')}"
            primary["summary"] = f"증권사: {brokers_str} | 종목: {stock_name} | 리포트: {stock_reports[0].get('report_title', '')}"
            simultaneous.append(primary)
        else:
            # 단일 증권사 첫 언급
            for r in stock_reports:
                key = f"{stock_name}_{r.get('source_name', '')}"
                if key not in seen_keys and not r.get("new_coverage", False):
                    seen_keys.add(key)
                    first_mention.append(r)

    return {
        "simultaneous":     simultaneous,
        "first_in_6months": first_mention,
        "new_coverage":     new_coverage,
    }


def collect_analyst() -> list:
    """애널리스트 리포트 수집 메인 함수"""
    print("\n=== 섹션 3: 애널리스트 리포트 수집 ===")

    reports = collect_naver_research()
    print(f"  → 총 {len(reports)}건 수집")

    if not reports:
        print("  → 수집된 리포트가 없습니다.")
        return []

    classified = classify_analyst_reports(reports)

    for r in classified["simultaneous"]:
        r["analyst_category"] = "simultaneous"
    for r in classified["first_in_6months"]:
        r["analyst_category"] = "first_in_6months"
    for r in classified["new_coverage"]:
        r["analyst_category"] = "new_coverage"

    all_classified = (
        classified["simultaneous"]
        + classified["new_coverage"]
        + classified["first_in_6months"]
    )

    # 중복 제거
    seen = set()
    unique_reports = []
    for r in all_classified:
        cat = r.get("analyst_category", "")
        key = (
            r.get("stock_name", "")
            if cat == "simultaneous"
            else f"{r.get('stock_name','')}_{r.get('source_name','')}"
        )
        if key not in seen:
            seen.add(key)
            unique_reports.append(r)

    print(
        f"  → 분류 완료: 동시언급 {len(classified['simultaneous'])}건, "
        f"신규커버리지 {len(classified['new_coverage'])}건, "
        f"첫언급 {len(classified['first_in_6months'])}건"
    )

    return unique_reports
