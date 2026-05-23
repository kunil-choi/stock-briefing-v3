# analyzer/naver_finance.py
"""
네이버 금융 주가 조회 - v3
누락 함수 전체 추가:
  fetch_naver_stock_price()       ← validation.py 에서 호출
  fetch_naver_daily_prices()      ← validation.py 에서 호출
  fetch_naver_company_info()      ← validation.py 에서 호출
  generate_candlestick_base64()   ← validation.py 에서 호출
  search_code_by_autocomplete()   ← validation.py 에서 호출
  verify_stock_via_naver()        ← validation.py 에서 호출
"""
import io
import os
import re
import json
import time
import base64
import requests
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))

_NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


# ══════════════════════════════════════════════════════════════
#  1. 종목 목록 로드
# ══════════════════════════════════════════════════════════════

def load_stock_names() -> dict:
    """
    네이버 금융 시가총액 목록에서 종목명→코드 매핑 로드.
    당일 캐시가 있으면 캐시 사용, 없으면 크롤링 후 캐시 저장.
    """
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


def _load_naver_market_stocks(sosok: int, stock_map: dict, max_pages: int = 10) -> int:
    """네이버 금융 시가총액 목록 수집 (내부 함수)"""
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


# ══════════════════════════════════════════════════════════════
#  2. 현재가 조회 (get_stock_price + fetch_naver_stock_price 별칭)
# ══════════════════════════════════════════════════════════════

def get_stock_price(stock_code: str) -> dict:
    """
    네이버 금융에서 현재가·등락폭·등락률 조회.
    반환: {"code", "price", "change", "change_pct"}
    """
    if not stock_code or stock_code == "NONE":
        return {}

    try:
        url = f"https://finance.naver.com/item/sise.nhn?code={stock_code}"
        resp = requests.get(url, headers=_NAVER_HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")

        price_el = soup.select_one("#_nowVal")
        if not price_el:
            price_el = (
                soup.select_one(".no_today .blind")
                or soup.select_one("strong#_nowVal")
            )
        if not price_el:
            return {"code": stock_code, "price": "", "change": "", "change_pct": ""}

        price = price_el.get_text(strip=True).replace(",", "")
        change = ""
        rate = ""
        change_el = soup.select_one("#_diff")
        rate_el   = soup.select_one("#_rate")
        if change_el:
            change = change_el.get_text(strip=True).replace(",", "")
        if rate_el:
            rate = rate_el.get_text(strip=True).replace("%", "").strip()

        sign = ""
        if soup.select_one(".no_today .up"):
            sign = "+"
        elif soup.select_one(".no_today .down"):
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


def fetch_naver_stock_price(stock_name: str, code_override: str = "") -> dict:
    """
    validation.py 에서 호출하는 함수.
    종목명 또는 코드로 네이버 금융 주가 조회.
    반환: {"code", "price", "change", "change_pct"} or None
    """
    code = code_override or ""
    if not code:
        result = verify_stock_via_naver(stock_name)
        code = result.get("code", "") if result else ""
    if not code:
        return None
    price_info = get_stock_price(code)
    if not price_info or not price_info.get("price"):
        return None
    return price_info


# ══════════════════════════════════════════════════════════════
#  3. 2주 일봉 히스토리 조회 (get_stock_price_history + fetch_naver_daily_prices 별칭)
# ══════════════════════════════════════════════════════════════

def get_stock_price_history(stock_code: str, days: int = 14) -> list:
    """
    네이버 금융 일봉 데이터 API에서 최근 N 거래일 데이터 수집.
    반환: [{"date", "close", "open", "high", "low"}, ...]  최신 날짜 순
    """
    if not stock_code or stock_code == "NONE":
        return []

    try:
        url = (
            f"https://finance.naver.com/item/sise_day.naver"
            f"?code={stock_code}&page=1"
        )
        resp = requests.get(url, headers=_NAVER_HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")

        rows = soup.select("table.type_2 tbody tr")
        history = []
        for row in rows:
            cells = row.select("td span")
            if len(cells) < 6:
                continue
            date_text  = cells[0].get_text(strip=True)
            close_text = cells[1].get_text(strip=True).replace(",", "")
            open_text  = cells[3].get_text(strip=True).replace(",", "")
            high_text  = cells[4].get_text(strip=True).replace(",", "")
            low_text   = cells[5].get_text(strip=True).replace(",", "")

            if not re.match(r"\d{4}\.\d{2}\.\d{2}", date_text):
                continue
            if not close_text or not close_text.isdigit():
                continue

            history.append({
                "date":  date_text,
                "close": close_text,
                "open":  open_text,
                "high":  high_text,
                "low":   low_text,
            })

            if len(history) >= days:
                break

        return history

    except Exception as e:
        print(f"  [히스토리 조회 실패] {stock_code}: {e}")
        return []


def fetch_naver_daily_prices(stock_code: str, days: int = 14) -> list:
    """
    validation.py 에서 호출하는 함수.
    get_stock_price_history()의 별칭.
    """
    return get_stock_price_history(stock_code, days=days)


# ══════════════════════════════════════════════════════════════
#  4. 통합 조회 (현재가 + 2주 히스토리)
# ══════════════════════════════════════════════════════════════

def get_stock_full_info(stock_code: str) -> dict:
    """현재가 + 최근 2주 일봉 히스토리를 통합 조회."""
    base = get_stock_price(stock_code)
    if not base.get("price"):
        return base

    history = get_stock_price_history(stock_code, days=14)

    result = dict(base)
    result["history"] = history

    if len(history) >= 2:
        try:
            latest_price = int(history[0]["close"])
            oldest_price = int(history[-1]["close"])

            highs = [int(h["high"]) for h in history if h.get("high") and h["high"].isdigit()]
            lows  = [int(h["low"])  for h in history if h.get("low")  and h["low"].isdigit()]

            period_diff     = latest_price - oldest_price
            period_diff_pct = (period_diff / oldest_price * 100) if oldest_price else 0
            period_sign     = "+" if period_diff >= 0 else ""

            result["period_change"]     = f"{period_sign}{period_diff:,}"
            result["period_change_pct"] = f"{period_sign}{period_diff_pct:.2f}%"
            result["start_price"]       = f"{oldest_price:,}"
            result["period_high"]       = f"{max(highs):,}" if highs else ""
            result["period_low"]        = f"{min(lows):,}"  if lows  else ""

            change_fmt    = base.get("change", "")
            change_pct_fmt = base.get("change_pct", "")
            price_fmt     = base.get("price", "")

            if change_fmt.startswith("+"):
                arrow, change_abs = "▲", change_fmt[1:]
            elif change_fmt.startswith("-"):
                arrow, change_abs = "▼", change_fmt[1:]
            else:
                arrow, change_abs = "", change_fmt

            if arrow and change_abs:
                result["price_display"] = f"{price_fmt}원 {arrow}{change_abs} ({change_pct_fmt})"
            else:
                result["price_display"] = f"{price_fmt}원"

            trend_word = "상승" if period_diff >= 0 else "하락"
            result["history_summary"] = (
                f"최근 2주간 {oldest_price:,}원 → {latest_price:,}원 "
                f"({period_sign}{period_diff:,}원, {period_sign}{period_diff_pct:.1f}% {trend_word})"
            )

        except (ValueError, ZeroDivisionError, IndexError) as e:
            print(f"  [히스토리 계산 오류] {stock_code}: {e}")
            _fill_price_display(result, base)
    else:
        _fill_price_display(result, base)

    return result


def _fill_price_display(result: dict, base: dict) -> None:
    """히스토리 없을 때 현재가만으로 price_display 구성 (내부 헬퍼)"""
    price      = base.get("price", "")
    change     = base.get("change", "")
    change_pct = base.get("change_pct", "")

    if change.startswith("+"):
        arrow, change_abs = "▲", change[1:]
    elif change.startswith("-"):
        arrow, change_abs = "▼", change[1:]
    else:
        arrow, change_abs = "", change

    if arrow and change_abs:
        result["price_display"]   = f"{price}원 {arrow}{change_abs} ({change_pct})"
        result["history_summary"] = f"현재가 {price}원 {arrow}{change_abs} ({change_pct})"
    else:
        result["price_display"]   = f"{price}원"
        result["history_summary"] = f"현재가 {price}원"


# ══════════════════════════════════════════════════════════════
#  5. 네이버 자동완성 API로 종목 코드 검색
# ══════════════════════════════════════════════════════════════

def search_code_by_autocomplete(stock_name: str) -> dict:
    """
    validation.py 에서 호출하는 함수.
    네이버 금융 자동완성 API로 종목명 → 코드 검색.
    반환: {"code": "000000", "name": "종목명"} or {}
    """
    if not stock_name:
        return {}
    try:
        url = "https://ac.stock.naver.com/ac"
        params = {
            "q":    stock_name,
            "target": "stock,index,marketindicator",
        }
        resp = requests.get(url, params=params, headers=_NAVER_HEADERS, timeout=8)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        items = data.get("items", [])
        if not items:
            # 다른 응답 구조 시도
            items = data.get("result", {}).get("items", [])
        for item_group in items:
            if isinstance(item_group, list):
                for item in item_group:
                    name = item.get("name", "") or item.get("ko", "")
                    code = item.get("code", "") or item.get("symbolCode", "")
                    if code and len(code) == 6 and code.isdigit():
                        print(f"  [자동완성] '{stock_name}' → '{name}'({code})")
                        return {"code": code, "name": name}
            elif isinstance(item_group, dict):
                name = item_group.get("name", "") or item_group.get("ko", "")
                code = item_group.get("code", "") or item_group.get("symbolCode", "")
                if code and len(code) == 6 and code.isdigit():
                    print(f"  [자동완성] '{stock_name}' → '{name}'({code})")
                    return {"code": code, "name": name}
    except Exception as e:
        print(f"  [자동완성 실패] {stock_name}: {e}")

    # 폴백: 네이버 검색 결과 크롤링
    try:
        url = f"https://finance.naver.com/search/searchResult.naver?query={requests.utils.quote(stock_name)}"
        resp = requests.get(url, headers=_NAVER_HEADERS, timeout=8)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")
        link = soup.select_one("table.type_1 td.tit a")
        if link and link.get("href"):
            m = re.search(r"code=(\d{6})", link["href"])
            if m:
                code = m.group(1)
                name = link.get_text(strip=True)
                print(f"  [검색 폴백] '{stock_name}' → '{name}'({code})")
                return {"code": code, "name": name}
    except Exception as e:
        print(f"  [검색 폴백 실패] {stock_name}: {e}")

    return {}


# ══════════════════════════════════════════════════════════════
#  6. 종목 존재 확인 (verify_stock_via_naver)
# ══════════════════════════════════════════════════════════════

def verify_stock_via_naver(stock_name: str) -> dict:
    """
    validation.py 에서 호출하는 함수.
    네이버 금융에서 종목명이 실제로 존재하는지 확인하고 코드 반환.
    반환: {"code": "000000", "name": "종목명", "verified": True} or {}
    """
    if not stock_name:
        return {}

    # 1차: 자동완성 API
    result = search_code_by_autocomplete(stock_name)
    if result.get("code"):
        return {
            "code":     result["code"],
            "name":     result.get("name", stock_name),
            "verified": True,
        }

    # 2차: 네이버 금융 검색 페이지
    try:
        from urllib.parse import quote
        url = f"https://finance.naver.com/search/searchResult.naver?query={quote(stock_name)}"
        resp = requests.get(url, headers=_NAVER_HEADERS, timeout=8)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")

        rows = soup.select("table.type_1 tbody tr")
        for row in rows:
            tit = row.select_one("td.tit a")
            if tit and tit.get("href"):
                m = re.search(r"code=(\d{6})", tit["href"])
                if m:
                    name = tit.get_text(strip=True)
                    code = m.group(1)
                    # 이름이 유사한지 확인
                    if stock_name in name or name in stock_name:
                        print(f"  [종목확인] '{stock_name}' → '{name}'({code})")
                        return {"code": code, "name": name, "verified": True}
    except Exception as e:
        print(f"  [종목확인 실패] {stock_name}: {e}")

    return {}


# ══════════════════════════════════════════════════════════════
#  7. 기업 정보 조회 (fetch_naver_company_info)
# ══════════════════════════════════════════════════════════════

def fetch_naver_company_info(stock_code: str) -> dict:
    """
    validation.py 에서 호출하는 함수.
    네이버 금융에서 업종·동종업체 정보 조회.
    반환: {"sector": "업종명", "peers": ["종목1", "종목2", ...]}
    """
    if not stock_code:
        return {}
    try:
        url = f"https://finance.naver.com/item/main.naver?code={stock_code}"
        resp = requests.get(url, headers=_NAVER_HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")

        # 업종 추출
        sector = ""
        sector_el = soup.select_one("div.section_company em.industry_type a")
        if not sector_el:
            sector_el = soup.select_one("div.trade_compare th")
        if sector_el:
            sector = sector_el.get_text(strip=True)

        # 동종업체 추출
        peers = []
        peer_links = soup.select("div.section_comp table a[href*='code=']")
        for link in peer_links[:8]:
            peer_name = link.get_text(strip=True)
            if peer_name and peer_name not in peers and len(peer_name) > 1:
                peers.append(peer_name)

        if sector or peers:
            return {"sector": sector, "peers": peers}

        # 폴백: 업종 섹션 다른 셀렉터 시도
        for sel in ["dl.corp_info dt", "table.tb_type1 td"]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if text:
                    return {"sector": text, "peers": []}

    except Exception as e:
        print(f"  [기업정보 조회 실패] {stock_code}: {e}")

    return {}


# ══════════════════════════════════════════════════════════════
#  8. 캔들스틱 차트 생성 (generate_candlestick_base64)
# ══════════════════════════════════════════════════════════════

def generate_candlestick_base64(history: list, stock_name: str = "") -> str:
    """
    validation.py 에서 호출하는 함수.
    일봉 히스토리 데이터로 캔들스틱 차트를 생성해 Base64 문자열로 반환.
    history: fetch_naver_daily_prices() 반환값 (최신 날짜 순)
    반환: base64 PNG 문자열 (헤더 없음) or ""
    """
    if not history or len(history) < 3:
        return ""

    try:
        import mplfinance as mpf
        import pandas as pd
        import matplotlib
        matplotlib.use("Agg")  # GUI 없는 환경

        # 오래된 날짜 순으로 정렬
        data = list(reversed(history))

        dates  = []
        opens  = []
        highs  = []
        lows   = []
        closes = []

        for row in data:
            date_str = row.get("date", "")
            try:
                dt = datetime.strptime(date_str, "%Y.%m.%d")
            except ValueError:
                continue
            o = int(row.get("open",  "0") or "0")
            h = int(row.get("high",  "0") or "0")
            l = int(row.get("low",   "0") or "0")
            c = int(row.get("close", "0") or "0")
            if not all([o, h, l, c]):
                continue
            dates.append(dt)
            opens.append(o)
            highs.append(h)
            lows.append(l)
            closes.append(c)

        if len(dates) < 3:
            return ""

        df = pd.DataFrame(
            {"Open": opens, "High": highs, "Low": lows, "Close": closes},
            index=pd.DatetimeIndex(dates),
        )

        buf = io.BytesIO()
        mc  = mpf.make_marketcolors(up="#ff6b6b", down="#339af0", inherit=True)
        s   = mpf.make_mpf_style(
            marketcolors=mc,
            base_mpf_style="nightclouds",
            facecolor="#0a0a14",
            edgecolor="#1e1e2e",
            gridcolor="#1e1e2e",
        )

        title = f"{stock_name} 최근 {len(dates)}일 주가"
        mpf.plot(
            df,
            type="candle",
            style=s,
            title=title,
            ylabel="주가 (원)",
            savefig=dict(fname=buf, dpi=100, bbox_inches="tight"),
            figsize=(8, 4),
        )

        buf.seek(0)
        return base64.b64encode(buf.read()).decode("utf-8")

    except ImportError:
        print(f"  [차트] mplfinance/pandas 미설치 → 차트 생략")
        return ""
    except Exception as e:
        print(f"  [차트 생성 실패] {stock_name}: {e}")
        return ""


# ══════════════════════════════════════════════════════════════
#  9. 폴백 종목 사전
# ══════════════════════════════════════════════════════════════

def _get_fallback_stocks() -> dict:
    """네이버 금융 크롤링 실패 시 사용하는 주요 종목 하드코딩 사전"""
    return {
        "삼성전자":        "005930", "SK하이닉스":        "000660",
        "LG에너지솔루션":  "373220", "삼성바이오로직스":  "207940",
        "현대차":          "005380", "기아":              "000270",
        "셀트리온":        "068270", "POSCO홀딩스":       "005490",
        "KB금융":          "105560", "신한지주":          "055550",
        "하나금융지주":    "086790", "우리금융지주":      "316140",
        "LG화학":          "051910", "삼성SDI":           "006400",
        "현대모비스":      "012330", "카카오":            "035720",
        "NAVER":           "035420", "LG전자":            "066570",
        "삼성물산":        "028260", "SK텔레콤":          "017670",
        "KT":              "030200", "한화에어로스페이스": "012450",
        "두산에너빌리티":  "034020", "HD현대중공업":      "329180",
        "HD한국조선해양":  "009540", "현대건설":          "000720",
        "LIG넥스원":       "079550", "한국항공우주":      "047810",
        "현대로템":        "064350", "한화시스템":        "272210",
        "한화오션":        "042660", "삼성전기":          "009150",
        "삼성SDS":         "018260", "LS ELECTRIC":       "010120",
        "HD현대일렉트릭":  "267260", "효성중공업":        "298040",
        "카카오페이":      "377300", "카카오뱅크":        "323410",
        "HMM":             "011200", "고려아연":          "010130",
        "대한항공":        "003490", "포스코퓨처엠":      "003670",
        "엘앤에프":        "066970", "SK이노베이션":      "096770",
        "삼성생명":        "032830", "삼성화재":          "000810",
        "한국전력":        "015760", "한국가스공사":      "036460",
        "현대미포조선":    "010620", "HL만도":            "204320",
        "현대위아":        "011210",
        "에코프로":        "086520", "에코프로비엠":      "247540",
        "HLB":             "028300", "유한양행":          "000100",
        "알테오젠":        "196170", "레인보우로보틱스":  "277810",
        "두산로보틱스":    "454910", "HPSP":              "403870",
        "피에스케이":      "319660", "엔씨소프트":        "036570",
        "크래프톤":        "259960", "하이브":            "352820",
        "SM":              "041510", "JYP Ent":           "035900",
        "이수페타시스":    "007660", "솔브레인":          "357780",
        "리노공업":        "058470", "이오테크닉스":      "039030",
        "주성엔지니어링":  "036930", "한미약품":          "128940",
        "종근당":          "185750", "셀트리온제약":      "068760",
        "메디톡스":        "086900", "휴젤":              "145020",
        "클래시스":        "214150", "덴티움":            "145720",
        "코스맥스":        "044820", "한국콜마":          "161890",
        "펄어비스":        "263750", "넷마블":            "251270",
        "카카오게임즈":    "293490", "파크시스템스":      "140860",
        "테크윙":          "089030", "포스코DX":          "022100",
        "포스코인터내셔널":"047050", "SKC":               "011790",
        "케이씨텍":        "064760",
    }
