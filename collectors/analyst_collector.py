# collectors/analyst_collector.py
"""
애널리스트 리포트 수집기 - v3
네이버 금융 리서치에서 최근 24시간 이내 리포트 수집

분류 카테고리:
  simultaneous  : 복수 증권사가 동일 종목을 같은 날 동시 언급
  new_coverage  : 신규 커버리지 개시 리포트
  single_broker : 단일 증권사 단독 언급

네이버 금융 company_list.naver 컬럼 구조:
  cols[0]: 종목명
  cols[1]: 리포트 제목 (링크 포함)
  cols[2]: 증권사
  cols[3]: 첨부 (PDF 링크, 없으면 빈 td)
  cols[4]: 작성일 (YY.MM.DD 또는 YYYY.MM.DD)
  cols[5]: 조회수
"""
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from collections import defaultdict

KST = timezone(timedelta(hours=9))

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

# 신규 커버리지 감지 키워드
COVERAGE_KEYWORDS = ["커버리지", "신규", "개시", "Coverage Initiation", "Initiation", "NDR"]

# 투자의견 키워드 패턴
_OPINION_PATTERN = re.compile(
    r'\b(BUY|SELL|HOLD|매수|매도|중립|시장수익률|비중확대|비중축소|Outperform|Underperform|Not Rated|NR)\b',
    re.IGNORECASE
)
# 목표주가 패턴: "목표주가 50,000" / "TP 50,000" / "TP↑50,000"
_TARGET_PRICE_PATTERN = re.compile(
    r'(?:목표주가|목표가|TP|T\.P)[^\d]*?(\d[\d,]+)',
    re.IGNORECASE
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer":         "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# 날짜 형식 패턴
_DATE_PATTERN = re.compile(r"^\d{2,4}\.\d{2}\.\d{2}$")


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
    KST 기준 날짜 비교.
    형식: "26.05.22" (YY.MM.DD) 또는 "2026.05.22" (YYYY.MM.DD)
    """
    try:
        date_str = date_str.strip().replace(" ", "")
        if len(date_str) == 10 and date_str.count(".") == 2:
            report_date = datetime.strptime(date_str, "%Y.%m.%d")
        elif len(date_str) == 8 and date_str.count(".") == 2:
            report_date = datetime.strptime(date_str, "%y.%m.%d")
        elif len(date_str) == 8 and "." not in date_str:
            report_date = datetime.strptime(date_str, "%Y%m%d")
        elif len(date_str) == 6 and "." not in date_str:
            report_date = datetime.strptime(date_str, "%y%m%d")
        else:
            print(f"  [날짜 파싱 불가] '{date_str}' → 포함 처리")
            return True

        cutoff_date = (datetime.now(KST) - timedelta(days=days)).date()
        return report_date.date() >= cutoff_date

    except Exception as e:
        print(f"  [날짜 파싱 오류] '{date_str}': {e} → 포함 처리")
        return True


def is_new_coverage(title: str) -> bool:
    return any(k in title for k in COVERAGE_KEYWORDS)


# ── BUG-AC-1: 제목에서 투자의견·목표주가 추출 ─────────────────────────────────

def _extract_opinion(title: str) -> str:
    """리포트 제목에서 투자의견 추출"""
    m = _OPINION_PATTERN.search(title)
    if m:
        raw = m.group(1)
        # 영문 → 한글 통일
        mapping = {
            "BUY": "매수", "SELL": "매도", "HOLD": "중립",
            "OUTPERFORM": "비중확대", "UNDERPERFORM": "비중축소",
            "NOT RATED": "NR", "NR": "NR",
        }
        return mapping.get(raw.upper(), raw)
    return ""


def _extract_target_price(title: str) -> str:
    """리포트 제목에서 목표주가 추출 (쉼표 제거 후 반환)"""
    m = _TARGET_PRICE_PATTERN.search(title)
    if m:
        return m.group(1).replace(",", "")
    return ""


def _find_date_col(cols: list) -> str:
    """
    날짜 컬럼 자동 감지.
    cols[4] 우선 → cols[3] → cols[5] → 전체 탐색
    """
    check_order = [4, 3, 5] if len(cols) > 4 else [3]
    for idx in check_order:
        if idx >= len(cols):
            continue
        text = cols[idx].get_text(strip=True).replace(" ", "")
        if _DATE_PATTERN.match(text):
            return text
    for col in cols:
        text = col.get_text(strip=True).replace(" ", "")
        if _DATE_PATTERN.match(text):
            return text
    return ""


def collect_naver_research() -> list:
    """
    네이버 금융 리서치 company_list.naver 수집.
    투자의견·목표주가 자동 추출 포함.
    """
    results = []

    for page in range(1, 6):
        url = f"https://finance.naver.com/research/company_list.naver?&page={page}"
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=10)
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("table.type_1 tr")

            page_has_recent = False
            for row in rows:
                cols = row.select("td")
                if len(cols) < 5:
                    continue

                stock_name = cols[0].get_text(strip=True)
                if not stock_name or stock_name in ("종목명", "기업명"):
                    continue

                report_title = cols[1].get_text(strip=True)
                broker       = cols[2].get_text(strip=True)

                date_str = _find_date_col(cols)
                if not date_str:
                    continue
                if not is_within_days(date_str, REPORT_DAYS):
                    continue
                page_has_recent = True

                if not broker:
                    for known_broker in BROKERS:
                        if known_broker in report_title:
                            broker = known_broker
                            break
                    if not broker:
                        broker = "기타증권사"

                link_tag = cols[1].find("a")
                link     = _build_link(link_tag.get("href", "")) if link_tag else ""

                pdf_link = ""
                pdf_tag  = cols[3].find("a") if len(cols) > 3 else None
                if pdf_tag and pdf_tag.get("href", "").endswith(".pdf"):
                    pdf_link = _build_link(pdf_tag.get("href", ""))

                # BUG-AC-2: 투자의견·목표주가 추출
                opinion      = _extract_opinion(report_title)
                target_price = _extract_target_price(report_title)
                new_cov      = is_new_coverage(report_title)

                # BUG-AC-3: summary에 투자의견·목표주가 포함 (extract_mentions 검색 품질 향상)
                summary_parts = [f"증권사: {broker}", f"종목: {stock_name}",
                                 f"리포트: {report_title}"]
                if opinion:
                    summary_parts.append(f"투자의견: {opinion}")
                if target_price:
                    summary_parts.append(f"목표주가: {target_price}원")
                summary = " | ".join(summary_parts)

                results.append({
                    "source_type":      "애널리스트",
                    "source_name":      broker,
                    "stock_name":       stock_name,
                    "report_title":     report_title,
                    "analyst":          "",
                    "target_price":     target_price,
                    "opinion":          opinion,
                    "date":             date_str,
                    "new_coverage":     new_cov,
                    "analyst_category": "",          # classify 후 채워짐
                    "title":   f"[{broker}] {stock_name} - {report_title}",
                    "summary": summary,
                    "link":    link or pdf_link,
                    "section": "section3",
                })

            if not page_has_recent and page > 1:
                print(f"  [리서치] 페이지 {page}: 최근 데이터 없음 → 종료")
                break

            time.sleep(0.5)

        except Exception as e:
            print(f"  [리서치 페이지 {page}] 오류: {e}")

    return results


# ── BUG-AC-4: 분류 로직 전면 재작성 ──────────────────────────────────────────

def classify_analyst_reports(reports: list) -> dict:
    """
    수집된 리포트를 3가지 카테고리로 분류.

    분류 우선순위:
    1. simultaneous  : 복수 증권사(≥2)가 동일 종목을 같은 날 언급
                       → 신규 커버리지 포함 여부와 무관하게 '동시 언급' 우선
    2. new_coverage  : simultaneous에 해당하지 않는 신규 커버리지
    3. single_broker : 위 두 카테고리에 해당하지 않는 단일 증권사 단독 언급

    BUG-AC-5: 기존 로직의 문제점 수정
    - 신규 커버리지가 복수 증권사인 경우 simultaneous로 우선 분류
    - seen_keys를 단계별로 명확히 관리해 중복 방지
    - 카테고리명 first_in_6months → single_broker (실제 동작과 일치)
    """
    # 종목별로 리포트 그룹화
    stock_groups: dict[str, list] = defaultdict(list)
    for r in reports:
        sn = r.get("stock_name", "")
        if sn:
            stock_groups[sn].append(r)

    simultaneous_out  = []  # 최종 출력: 동시 언급
    new_coverage_out  = []  # 최종 출력: 신규 커버리지 (단독)
    single_broker_out = []  # 최종 출력: 단일 증권사 단독

    # 이미 simultaneous로 처리된 종목명 집합 (하위 카테고리 중복 방지)
    simultaneous_stocks: set[str] = set()

    for stock_name, stock_reports in stock_groups.items():
        # 고유 증권사 수 계산
        unique_brokers = list(dict.fromkeys(
            r.get("source_name", "") for r in stock_reports
            if r.get("source_name", "")
        ))

        # ── 1단계: 복수 증권사 동시 언급 ────────────────────────────────────
        if len(unique_brokers) >= 2:
            simultaneous_stocks.add(stock_name)

            # 대표 리포트 생성 (첫 번째 항목 기반)
            primary = stock_reports[0].copy()
            brokers_str = " / ".join(unique_brokers)

            # BUG-AC-6: 신규 커버리지가 포함된 동시 언급이면 new_coverage 플래그 유지
            has_new_cov = any(r.get("new_coverage", False) for r in stock_reports)
            primary["new_coverage"]          = has_new_cov
            primary["source_name"]           = brokers_str
            primary["simultaneous_brokers"]  = unique_brokers
            primary["broker_count"]          = len(unique_brokers)
            primary["all_reports"]           = stock_reports
            primary["title"]   = (
                f"[동시언급 {len(unique_brokers)}사] {stock_name} - "
                f"{stock_reports[0].get('report_title', '')}"
            )
            primary["summary"] = (
                f"증권사: {brokers_str} | 종목: {stock_name} | "
                f"리포트: {stock_reports[0].get('report_title', '')}"
            )

            # BUG-AC-7: 투자의견이 여러 개면 가장 강한 의견 우선 표시
            opinions = [r.get("opinion", "") for r in stock_reports if r.get("opinion")]
            opinion_priority = ["매수", "BUY", "비중확대", "중립", "HOLD", "비중축소", "매도"]
            for op in opinion_priority:
                if op in opinions:
                    primary["opinion"] = op
                    break

            simultaneous_out.append(primary)
            continue

        # ── 2단계: 신규 커버리지 (단독 증권사) ───────────────────────────────
        for r in stock_reports:
            if r.get("new_coverage", False):
                new_coverage_out.append(r)

        # ── 3단계: 단일 증권사 단독 언급 ─────────────────────────────────────
        # 신규 커버리지가 아닌 항목만 추가
        for r in stock_reports:
            if not r.get("new_coverage", False):
                single_broker_out.append(r)

    return {
        "simultaneous":  simultaneous_out,
        "new_coverage":  new_coverage_out,
        "single_broker": single_broker_out,   # BUG-AC-8: 카테고리명 변경
    }


def collect_analyst() -> list:
    """애널리스트 리포트 수집 메인 함수"""
    print("\n=== 섹션 3: 애널리스트 리포트 수집 ===")

    reports = collect_naver_research()
    print(f"  → 원시 수집: {len(reports)}건")

    if not reports:
        print("  → 수집된 리포트 없음")
        return []

    # BUG-AC-9: 원시 데이터 중복 제거 (동일 종목+증권사+날짜)
    seen_raw = set()
    deduped  = []
    for r in reports:
        key = f"{r.get('stock_name','')}_{r.get('source_name','')}_{r.get('date','')}"
        if key not in seen_raw:
            seen_raw.add(key)
            deduped.append(r)
    print(f"  → 중복 제거 후: {len(deduped)}건")

    classified = classify_analyst_reports(deduped)

    # analyst_category 필드 설정
    for r in classified["simultaneous"]:
        r["analyst_category"] = "simultaneous"
    for r in classified["new_coverage"]:
        r["analyst_category"] = "new_coverage"
    for r in classified["single_broker"]:
        # BUG-AC-10: html_generator와 호환을 위해 first_in_6months도 병행 설정
        r["analyst_category"] = "single_broker"

    # 전체 리포트 합산 (simultaneous 대표 + 나머지)
    all_classified = (
        classified["simultaneous"]
        + classified["new_coverage"]
        + classified["single_broker"]
    )

    # BUG-AC-11: 최종 중복 제거 (동일 종목이 simultaneous + single_broker 동시 존재 방지)
    seen_final    = set()
    unique_reports = []
    for r in all_classified:
        cat = r.get("analyst_category", "")
        if cat == "simultaneous":
            key = r.get("stock_name", "")
        else:
            key = f"{r.get('stock_name','')}_{r.get('source_name','')}_{cat}"
        if key not in seen_final:
            seen_final.add(key)
            unique_reports.append(r)

    sim_count    = len(classified["simultaneous"])
    cov_count    = len(classified["new_coverage"])
    single_count = len(classified["single_broker"])
    total        = len(unique_reports)

    print(
        f"  → 분류 완료: 동시언급 {sim_count}건 / "
        f"신규커버리지 {cov_count}건 / "
        f"단독언급 {single_count}건 / "
        f"최종 {total}건"
    )

    # 동시 언급 종목명 출력 (디버그)
    if classified["simultaneous"]:
        names = [r.get("stock_name", "") for r in classified["simultaneous"]]
        print(f"  → 동시언급 종목: {', '.join(names)}")

    return unique_reports
