# analyzer/naver_finance.py
"""
네이버 금융 주가 조회 - v3
종목 목록: 네이버 금융 시가총액 목록
현재가 + 2주간 실제 주가 데이터: 네이버 금융 JSON API 사용
✅ 수정 3/3: 2주간 실제 주가 흐름 데이터 조회 추가
"""
import requests
import json
import re
import time
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))

_NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


# ══════════════════════════════════════════════
# 종목 목록 로드
# ══════════════════════════════════════════════

def load_stock_names() -> dict:
    """
    네이버 금융 시가총액 목록에서 종목명→코드 매핑 로드
    당일 캐시가 있으면 재사용합니다.
    """
    import os

    cache_path = "data/stock_names_cache.json"
    today = datetime.now(KST).strftime("%Y-%m-%d")

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("date") == today and len(cache.get("stocks", {})) > 100:
                print(f"  [종목목록] 캐시 사용 ({len(cache['stocks'])}개, {today})")
                return cache["stocks"]
        except Exception:
            pass

    print("  [종목목록] 네이버 금융에서 종목 목록 로드 중...")
    stock_map = {}

    for sosok, market_name in [(0, "코스피"), (1, "코스닥")]:
        count = _load_naver_market_stocks(sosok, stock_map, max_pages=10)
        print(f"  [{market_name}] {count}개 로드")

    if len(stock_map) < 50:
        print("  [종목목록] 네이버 금융 실패 → 주요 종목 폴백 사용")
        stock_map = _get_fallback_stocks()

    os.makedirs("data", exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"date": today, "stocks": stock_map}, f, ensure_ascii=False)

    print(f"  [종목목록] 총 {len(stock_map)}개 로드 완료")
    return stock_map


def _load_naver_market_stocks(
    sosok: int, stock_map: dict, max_pages: int = 10
) -> int:
    """네이버 금융 시가총액 목록 수집 (sosok: 0=코스피, 1=코스닥)"""
    added = 0
    for page in range(1, max_pages + 1):
        url = (
            f"https://finance.naver.com/sise/sise_market_sum.naver"
            f"?sosok={sosok}&page={page}"
        )
        try:
            r = requests.get(url, headers=_NAVER_HEADERS, timeout=15)
            r.encoding = "euc-kr"
            soup = BeautifulSoup(r.text, "html.parser")

            rows = soup.select("table.type_2 tbody tr")
            page_count = 0
            for row in rows:
                link = row.select_one("a.tltle")
                if link and link.get("href"):
                    href = link["href"]
                    m = re.search(r"code=(\d{6})", href)
                    if m:
                        code = m.group(1)
                        name = link.get_text(strip=True)
                        if name and code and name not in stock_map:
                            stock_map[name] = code
                            page_count += 1
                            added += 1

            if page_count == 0:
                break

            time.sleep(0.2)

        except Exception as e:
            print(f"    [네이버 금융 오류] page={page}: {e}")
            break

    return added


# ══════════════════════════════════════════════
# ✅ 수정 3/3 핵심: 현재가 + 2주간 주가 흐름 통합 조회
# ══════════════════════════════════════════════

def get_stock_price(stock_code: str) -> dict:
    """
    네이버 금융 JSON API로 현재가 및 등락 정보 조회
    반환값:
    {
        "code":       str,
        "price":      str,   # 현재가 (예: "181000")
        "change":     str,   # 전일 대비 등락 (예: "+10700")
        "change_pct": str,   # 등락률 (예: "+5.58%")
    }
    """
    if not stock_code or stock_code == "NONE":
        return {}

    try:
        url = f"https://finance.naver.com/item/sise.nhn?code={stock_code}"
        resp = requests.get(url, headers=_NAVER_HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")

        # 현재가: #_nowVal
        price_el = soup.select_one("#_nowVal")
        if not price_el:
            price_el = (
                soup.select_one(".no_today .blind")
                or soup.select_one("strong#_nowVal")
            )

        if not price_el:
            return {"code": stock_code, "price": "", "change": "", "change_pct": ""}

        price = price_el.get_text(strip=True).replace(",", "")

        # 전일 대비: #_diff / 등락률: #_rate
        change = ""
        rate   = ""
        change_el = soup.select_one("#_diff")
        rate_el   = soup.select_one("#_rate")

        if change_el:
            change = change_el.get_text(strip=True).replace(",", "")
        if rate_el:
            rate = rate_el.get_text(strip=True).replace("%", "").strip()

        # 상승/하락 방향 판단
        sign = ""
        up_el   = soup.select_one(".no_today .up")
        down_el = soup.select_one(".no_today .down")
        if up_el:
            sign = "+"
        elif down_el:
            sign = "-"

        if sign and change and not change.startswith(("+", "-")):
            change = sign + change
        if sign and rate and not rate.startswith(("+", "-")):
            rate = sign + rate

        return {
            "code":       stock_code,
            "price":      price,
            "change":     change,
            "change_pct": rate + "%" if rate else "",
        }

    except Exception as e:
        print(f"  [주가조회 실패] {stock_code}: {e}")
        return {"code": stock_code, "price": "", "change": "", "change_pct": ""}


def get_stock_price_history(stock_code: str, days: int = 14) -> dict:
    """
    ✅ 수정 3/3: 네이버 금융 일별 시세에서 최근 N일 주가 데이터 조회
    반환값:
    {
        "code":          str,
        "dates":         list[str],   # 날짜 목록 (최신순)
        "closes":        list[int],   # 종가 목록 (최신순)
        "period_change": str,         # 2주간 등락률 (예: "+12.3%")
        "period_high":   str,         # 2주 최고가
        "period_low":    str,         # 2주 최저가
        "start_price":   str,         # 2주 전 시작가
        "end_price":     str,         # 현재가 (최신 종가)
        "summary":       str,         # 요약 텍스트
    }
    """
    if not stock_code or stock_code == "NONE":
        return {}

    try:
        url = (
            f"https://finance.naver.com/item/sise_day.naver"
            f"?code={stock_code}&page=1"
        )
        resp = requests.get(url, headers=_NAVER_HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")

        rows = soup.select("table.type2 tr")

        dates  = []
        closes = []

        for row in rows:
            cols = row.select("td")
            if len(cols) < 2:
                continue

            date_text  = cols[0].get_text(strip=True)
            close_text = cols[1].get_text(strip=True).replace(",", "")

            # 날짜 형식 확인 (YYYY.MM.DD)
            if not re.match(r"\d{4}\.\d{2}\.\d{2}", date_text):
                continue
            # 종가 숫자 확인
            if not close_text.isdigit():
                continue

            dates.append(date_text)
            closes.append(int(close_text))

            if len(dates) >= days:
                break

        if len(closes) < 2:
            return {
                "code":          stock_code,
                "dates":         dates,
                "closes":        closes,
                "period_change": "",
                "period_high":   "",
                "period_low":    "",
                "start_price":   "",
                "end_price":     str(closes[0]) if closes else "",
                "summary":       "주가 데이터 부족",
            }

        # 최신가 = closes[0], 시작가 = closes[-1]
        end_price   = closes[0]
        start_price = closes[-1]
        period_high = max(closes)
        period_low  = min(closes)

        # 2주간 등락률 계산
        change_pct = ((end_price - start_price) / start_price) * 100
        sign = "+" if change_pct >= 0 else ""
        period_change_str = f"{sign}{change_pct:.1f}%"

        # 방향 표시 문자
        direction = "▲" if change_pct >= 0 else "▼"

        # 요약 텍스트 생성
        summary = (
            f"{start_price:,}원 → {end_price:,}원 "
            f"({direction} {abs(change_pct):.1f}%, "
            f"{len(dates)}거래일 기준) | "
            f"최고 {period_high:,}원 / 최저 {period_low:,}원"
        )

        return {
            "code":          stock_code,
            "dates":         dates,
            "closes":        closes,
            "period_change": period_change_str,
            "period_high":   f"{period_high:,}",
            "period_low":    f"{period_low:,}",
            "start_price":   f"{start_price:,}",
            "end_price":     f"{end_price:,}",
            "summary":       summary,
        }

    except Exception as e:
        print(f"  [주가이력 조회 실패] {stock_code}: {e}")
        return {}


def get_stock_full_info(stock_code: str) -> dict:
    """
    ✅ 수정 3/3: 현재가 + 2주간 주가 흐름을 통합하여 반환
    ai_analyzer.py 에서 이 함수 하나만 호출하면 됩니다.

    반환값:
    {
        "code":          str,
        "price":         str,    # 현재가
        "change":        str,    # 전일 대비
        "change_pct":    str,    # 전일 대비 등락률
        "period_change": str,    # 2주간 누적 등락률
        "period_high":   str,    # 2주 최고가
        "period_low":    str,    # 2주 최저가
        "start_price":   str,    # 2주 전 시작가
        "price_display": str,    # 표시용 문자열
        "history_summary": str,  # 주가 흐름 요약
    }
    """
    if not stock_code or stock_code == "NONE":
        return {}

    # 현재가 조회
    current = get_stock_price(stock_code)

    # 2주간 이력 조회 (14거래일 ≈ 20일)
    history = get_stock_price_history(stock_code, days=14)

    if not current.get("price"):
        return {}

    price       = current.get("price", "")
    change      = current.get("change", "")
    change_pct  = current.get("change_pct", "")

    # 방향 화살표
    if change.startswith("+"):
        arrow = "▲"
    elif change.startswith("-"):
        arrow = "▼"
    else:
        arrow = ""

    change_abs = change.lstrip("+-") if change else ""
    period_change = history.get("period_change", "")

    # 표시용 문자열 조합
    # 예: "181,000원 ▲10,700 (+5.58%) | 2주 변동: +12.3%"
    price_display_parts = []
    if price:
        price_fmt = f"{int(price):,}" if price.isdigit() else price
        price_display_parts.append(f"{price_fmt}원")
    if arrow and change_abs:
        price_display_parts.append(f"{arrow}{change_abs}")
    if change_pct:
        price_display_parts.append(f"({change_pct})")
    if period_change:
        price_display_parts.append(f"| 2주 변동: {period_change}")

    price_display = " ".join(price_display_parts)

    return {
        "code":            stock_code,
        "price":           price,
        "change":          change,
        "change_pct":      change_pct,
        "period_change":   period_change,
        "period_high":     history.get("period_high", ""),
        "period_low":      history.get("period_low", ""),
        "start_price":     history.get("start_price", ""),
        "price_display":   price_display,
        "history_summary": history.get("summary", ""),
    }


# ══════════════════════════════════════════════
# 폴백 종목 목록
# ══════════════════════════════════════════════

def _get_fallback_stocks() -> dict:
    """네이버 금융 실패 시 주요 종목 폴백 목록"""
    return {
        # 코스피 대형주
        "삼성전자":       "005930",
        "SK하이닉스":     "000660",
        "LG에너지솔루션": "373220",
        "삼성바이오로직스": "207940",
        "현대차":         "005380",
        "기아":           "000270",
        "셀트리온":       "068270",
        "POSCO홀딩스":    "005490",
        "KB금융":         "105560",
        "신한지주":       "055550",
        "하나금융지주":   "086790",
        "우리금융지주":   "316140",
        "LG화학":         "051910",
        "삼성SDI":        "006400",
        "현대모비스":     "012330",
        "카카오":         "035720",
        "NAVER":          "035420",
        "LG전자":         "066570",
        "삼성물산":       "028260",
        "SK텔레콤":       "017670",
        "KT":             "030200",
        "한화에어로스페이스": "012450",
        "두산에너빌리티": "034020",
        "HD현대중공업":   "329180",
        "HD한국조선해양": "009540",
        "현대건설":       "000720",
        "LIG넥스원":      "079550",
        "한국항공우주":   "047810",
        "현대로템":       "064350",
        "한화시스템":     "272210",
        "한화오션":       "042660",
        "삼성전기":       "009150",
        "삼성SDS":        "018260",
        "LS ELECTRIC":    "010120",
        "HD현대일렉트릭": "267260",
        "효성중공업":     "298040",
        "카카오페이":     "377300",
        "카카오뱅크":     "323410",
        "HMM":            "011200",
        "고려아연":       "010130",
        "대한항공":       "003490",
        "포스코퓨처엠":   "003670",
        "엘앤에프":       "066970",
        "SK이노베이션":   "096770",
        "삼성생명":       "032830",
        "삼성화재":       "000810",
        "한국전력":       "015760",
        "한국가스공사":   "036460",
        "현대미포조선":   "010620",
        "HL만도":         "204320",
        "현대위아":       "011210",
        # 코스닥 주요 종목
        "에코프로":       "086520",
        "에코프로비엠":   "247540",
        "HLB":            "028300",
        "유한양행":       "000100",
        "알테오젠":       "196170",
        "레인보우로보틱스": "277810",
        "두산로보틱스":   "454910",
        "HPSP":           "403870",
        "피에스케이":     "319660",
        "엔씨소프트":     "036570",
        "크래프톤":       "259960",
        "하이브":         "352820",
        "SM":             "041510",
        "JYP Ent":        "035900",
        "이수페타시스":   "007660",
        "솔브레인":       "357780",
        "리노공업":       "058470",
        "이오테크닉스":   "039030",
        "주성엔지니어링": "036930",
        "한미약품":       "128940",
        "종근당":         "185750",
        "셀트리온제약":   "068760",
        "메디톡스":       "086900",
        "휴젤":           "145020",
        "클래시스":       "214150",
        "덴티움":         "145720",
        "코스맥스":       "044820",
        "한국콜마":       "161890",
        "펄어비스":       "263750",
        "넷마블":         "251270",
        "카카오게임즈":   "293490",
        "파크시스템스":   "140860",
        "테크윙":         "089030",
        "포스코DX":       "022100",
        "포스코인터내셔널": "047050",
        "SKC":            "011790",
        "케이씨텍":       "064760",
    }
