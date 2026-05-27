# analyzer/naver_finance.py
"""
네이버 금융 데이터 수집 모듈
BUG-CR-2: 캐시 키를 "stock_map"으로 통일 (기존 "stocks" 키와의 하위 호환 유지)
"""

import os
import re
import json
import time
import base64
import io
import requests
import traceback
import pandas as pd
from datetime import datetime, timedelta, timezone

# ── 한국 표준시 ────────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))

# ── 상수 ──────────────────────────────────────────────────────────────────
CACHE_PATH = "data/stock_names_cache.json"   # BUG-CR-2: validation.py와 동일 경로 사용
CACHE_MAX_AGE_HOURS = 24                      # 캐시 유효 시간 (시간)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://finance.naver.com/",
}

NAVER_FINANCE_BASE = "https://finance.naver.com"
NAVER_SEARCH_URL   = "https://finance.naver.com/search/searchResult.naver"
NAVER_AUTO_URL     = "https://ac.finance.naver.com/ac"

# 해외 기업 판별 키워드
FOREIGN_KEYWORDS = ["엔비디아", "테슬라", "애플", "구글", "마이크로소프트",
                    "아마존", "메타", "넷플릭스", "NVIDIA", "TSMC", "인텔"]


# ─────────────────────────────────────────────────────────────────────────────
# 캐시 관련
# ─────────────────────────────────────────────────────────────────────────────

def _is_cache_valid() -> bool:
    """캐시 파일이 존재하고 24시간 이내인지 확인"""
    if not os.path.exists(CACHE_PATH):
        return False
    mtime = datetime.fromtimestamp(os.path.getmtime(CACHE_PATH), tz=KST)
    age = datetime.now(KST) - mtime
    return age.total_seconds() < CACHE_MAX_AGE_HOURS * 3600


def _read_cache() -> dict:
    """
    BUG-CR-2: 캐시 파일에서 stock_map 키 우선 읽기.
    구버전 "stocks" 키도 fallback으로 지원.
    """
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            cache = json.load(f)
        # stock_map 우선, 없으면 stocks(구버전) 사용
        result = cache.get("stock_map") or cache.get("stocks", {})
        return result if isinstance(result, dict) else {}
    except Exception as e:
        print(f"  [CACHE] 읽기 실패: {e}")
        return {}


def _write_cache(stock_dict: dict) -> None:
    """
    BUG-CR-2: 캐시 저장 시 "stock_map"과 "stocks" 두 키 모두 저장.
    → validation.py("stock_map")와 구버전 코드("stocks") 양쪽 호환.
    """
    os.makedirs("data", exist_ok=True)
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "stock_map": stock_dict,   # 신버전 키
                    "stocks": stock_dict,       # 구버전 호환 키
                    "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"  [CACHE저장] {len(stock_dict)}개 종목 → {CACHE_PATH}")
    except Exception as e:
        print(f"  [CACHE] 저장 실패: {e}")


def _save_single_to_cache(name: str, code: str) -> None:
    """단일 종목 코드를 캐시에 추가"""
    existing = _read_cache()
    existing[name] = code
    _write_cache(existing)


# ─────────────────────────────────────────────────────────────────────────────
# 종목 코드 조회
# ─────────────────────────────────────────────────────────────────────────────

def load_stock_names() -> dict:
    """
    종목명 → 코드 딕셔너리를 반환합니다.
    BUG-CR-2: 캐시 키 통일 (stock_map / stocks 양쪽 지원)

    우선순위:
    1. 유효한 캐시 파일 (24시간 이내)
    2. Naver 시장 크롤링
    3. 내장 fallback 종목 목록
    """
    # 1. 캐시
    if _is_cache_valid():
        cached = _read_cache()
        if cached:
            print(f"  [CACHE] 종목명 캐시 로드: {len(cached)}개")
            return cached

    # 2. Naver 크롤링
    print("  [NAVER] 시장 종목 목록 크롤링 시작...")
    stock_dict = _load_naver_market_stocks()

    # 3. Fallback
    if not stock_dict:
        print("  [FALLBACK] 내장 종목 목록 사용")
        stock_dict = _get_fallback_stocks()

    # 캐시 저장
    if stock_dict:
        _write_cache(stock_dict)

    return stock_dict


def _load_naver_market_stocks() -> dict:
    """네이버 금융 시가총액 상위 종목 크롤링"""
    from bs4 import BeautifulSoup

    stock_dict = {}
    markets = [
        ("KOSPI", "https://finance.naver.com/sise/sise_market_sum.naver?sosok=0"),
        ("KOSDAQ","https://finance.naver.com/sise/sise_market_sum.naver?sosok=1"),
    ]

    for market_name, base_url in markets:
        for page in range(1, 4):  # 1~3페이지
            url = f"{base_url}&page={page}"
            try:
                resp = requests.get(url, headers=HEADERS, timeout=10)
                resp.encoding = "euc-kr"
                soup = BeautifulSoup(resp.text, "lxml")
                rows = soup.select("table.type_2 tr")
                for row in rows:
                    link = row.select_one("a.tltle")
                    if not link:
                        continue
                    name = link.get_text(strip=True)
                    href = link.get("href", "")
                    code_match = re.search(r"code=(\d{6})", href)
                    if name and code_match:
                        stock_dict[name] = code_match.group(1)
                time.sleep(0.3)
            except Exception as e:
                print(f"  [NAVER] {market_name} p{page} 크롤링 실패: {e}")

    print(f"  [NAVER] 크롤링 완료: {len(stock_dict)}개 종목")
    return stock_dict


def search_code_by_autocomplete(name: str) -> str:
    """네이버 자동완성 API로 종목 코드 검색"""
    try:
        params = {"q": name, "q_enc": "UTF-8", "st": "111", "frq": "0",
                  "rc": "10", "r_format": "json", "r_enc": "UTF-8"}
        resp = requests.get(NAVER_AUTO_URL, params=params, headers=HEADERS, timeout=5)
        data = resp.json()
        items = data.get("items", [[]])[0]
        for item in items:
            if len(item) >= 3:
                candidate_name = item[0]
                candidate_code = item[1] if len(item[1]) == 6 and item[1].isdigit() else ""
                if candidate_name == name and candidate_code:
                    return candidate_code
        # 완전 일치 없으면 첫 번째 6자리 숫자 코드 반환
        for item in items:
            if len(item) >= 2 and len(item[1]) == 6 and item[1].isdigit():
                return item[1]
    except Exception as e:
        print(f"  [AUTO] {name} 자동완성 실패: {e}")
    return ""


def verify_stock_via_naver(name: str) -> tuple[bool, str]:
    """
    네이버 검색으로 종목 존재 여부 확인.
    Returns: (존재 여부, 종목 코드)
    """
    from bs4 import BeautifulSoup
    try:
        params = {"query": name}
        resp = requests.get(NAVER_SEARCH_URL, params=params, headers=HEADERS, timeout=8)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "lxml")
        # 종목 검색 결과 테이블
        rows = soup.select("table.tbl_search tr")
        for row in rows:
            cells = row.select("td")
            if len(cells) >= 2:
                stock_name = cells[0].get_text(strip=True)
                link = cells[0].select_one("a")
                if link and (stock_name == name or name in stock_name):
                    href = link.get("href", "")
                    code_match = re.search(r"code=(\d{6})", href)
                    if code_match:
                        return True, code_match.group(1)
    except Exception as e:
        print(f"  [VERIFY] {name} 검색 실패: {e}")
    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# 주가 데이터 조회
# ─────────────────────────────────────────────────────────────────────────────

def get_stock_price(code: str) -> dict:
    """현재가, 전일 대비 등 기본 가격 정보 반환"""
    return fetch_naver_stock_price(code)


def fetch_naver_stock_price(code: str) -> dict:
    """네이버 금융에서 현재가 정보 수집"""
    from bs4 import BeautifulSoup
    result = {"price": None, "change": None, "change_pct": None,
              "code": code, "naver_url": f"{NAVER_FINANCE_BASE}/item/main.naver?code={code}"}
    try:
        url = f"{NAVER_FINANCE_BASE}/item/main.naver?code={code}"
        resp = requests.get(url, headers=HEADERS, timeout=8)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "lxml")

        # 현재가
        price_el = soup.select_one("p.no_today span.blind")
        if price_el:
            price_text = price_el.get_text(strip=True).replace(",", "")
            result["price"] = int(price_text) if price_text.isdigit() else None

        # 전일 대비
        change_el = soup.select_one("p.no_exday em span.blind")
        if change_el:
            change_text = change_el.get_text(strip=True).replace(",", "").replace("+", "")
            try:
                result["change"] = int(change_text)
            except ValueError:
                pass

        # 등락률
        pct_el = soup.select_one("p.no_exday em.rate span.blind")
        if pct_el:
            pct_text = pct_el.get_text(strip=True).replace("%", "").replace("+", "")
            try:
                result["change_pct"] = float(pct_text)
            except ValueError:
                pass

    except Exception as e:
        print(f"  [PRICE] {code} 현재가 조회 실패: {e}")

    return result


def get_stock_price_history(code: str, days: int = 14) -> list:
    """최근 N일 일봉 데이터 반환"""
    return fetch_naver_daily_prices(code, days)


def fetch_naver_daily_prices(code: str, days: int = 14) -> list:
    """네이버 금융 일봉 데이터 수집"""
    from bs4 import BeautifulSoup
    prices = []
    try:
        url = f"{NAVER_FINANCE_BASE}/item/sise_day.naver?code={code}"
        resp = requests.get(url, headers=HEADERS, timeout=8)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "lxml")

        rows = soup.select("table.type2 tr")
        for row in rows:
            cells = row.select("td span")
            if len(cells) >= 6:
                try:
                    date_str = cells[0].get_text(strip=True)
                    close  = int(cells[1].get_text(strip=True).replace(",", ""))
                    open_  = int(cells[3].get_text(strip=True).replace(",", ""))
                    high   = int(cells[4].get_text(strip=True).replace(",", ""))
                    low    = int(cells[5].get_text(strip=True).replace(",", ""))
                    if date_str and close > 0:
                        prices.append({
                            "date": date_str, "open": open_, "high": high,
                            "low": low, "close": close
                        })
                except (ValueError, IndexError):
                    continue
            if len(prices) >= days:
                break
    except Exception as e:
        print(f"  [HISTORY] {code} 히스토리 조회 실패: {e}")
    return prices


def get_stock_full_info(code: str, name: str = "") -> dict:
    """현재가 + 히스토리 + 등락 요약 통합 반환"""
    info = fetch_naver_stock_price(code)
    history = fetch_naver_daily_prices(code, days=14)

    if history:
        closes = [h["close"] for h in history]
        info["history"] = history
        info["period_high"]   = max(h["high"]  for h in history)
        info["period_low"]    = min(h["low"]   for h in history)
        info["period_change"] = round((closes[0] - closes[-1]) / closes[-1] * 100, 2) if closes[-1] else 0
    else:
        info["history"] = []
        info["period_high"] = info["period_low"] = info["period_change"] = None

    info["name"] = name
    _fill_price_display(info)
    return info


def _fill_price_display(info: dict) -> None:
    """가격 표시 문자열 보완 (None 처리)"""
    price = info.get("price")
    change_pct = info.get("change_pct")
    info["price_display"] = f"{price:,}원" if isinstance(price, int) else "N/A"
    info["change_pct_display"] = f"{change_pct:+.2f}%" if isinstance(change_pct, float) else "N/A"


# ─────────────────────────────────────────────────────────────────────────────
# 차트 생성
# ─────────────────────────────────────────────────────────────────────────────

def generate_candlestick_base64(code: str, name: str = "") -> str:
    """
    캔들스틱 차트를 생성하고 Base64 문자열로 반환.
    실패 시 빈 문자열 반환.
    BUG-CHART: 14 → 7 → 3일 순으로 데이터 재시도
    """
    import matplotlib
    matplotlib.use("Agg")  # GitHub Actions 환경 (화면 없음)
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm

    # 한글 폰트 설정
    font_candidates = [
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    ]
    for fp in font_candidates:
        if os.path.exists(fp):
            fm.fontManager.addfont(fp)
            plt.rcParams["font.family"] = fm.FontProperties(fname=fp).get_name()
            break
    plt.rcParams["axes.unicode_minus"] = False

    # 데이터 수집 (재시도)
    history = []
    for days in [14, 7, 3]:
        history = fetch_naver_daily_prices(code, days=days)
        if len(history) >= 3:
            break

    if len(history) < 3:
        print(f"  [CHART] {code} 데이터 부족 ({len(history)}일) → 차트 생성 스킵")
        return ""

    try:
        import mplfinance as mpf

        # 데이터프레임 준비 (날짜 역순 → 오름차순)
        history_rev = list(reversed(history))
        df = pd.DataFrame(history_rev)
        df["Date"] = pd.to_datetime(df["date"], format="%Y.%m.%d", errors="coerce")
        df = df.dropna(subset=["Date"])
        df = df.set_index("Date")
        df = df.rename(columns={"open": "Open", "high": "High",
                                 "low": "Low", "close": "Close"})
        df["Volume"] = 0  # 거래량 없으면 0

        # 차트 스타일
        mc = mpf.make_marketcolors(up="#ff6b6b", down="#74c0fc",
                                    edge="inherit", wick="inherit")
        style = mpf.make_mpf_style(base_mpf_style="nightclouds",
                                    marketcolors=mc,
                                    facecolor="#1a1a2e",
                                    figcolor="#1a1a2e",
                                    gridcolor="#2d2d44")

        fig, axes = mpf.plot(
            df, type="candle", style=style,
            title=f"\n{name or code}",
            ylabel="", volume=False,
            returnfig=True,
            figsize=(6, 3),
        )
        axes[0].tick_params(colors="#cccccc", labelsize=7)
        axes[0].title.set_color("#e0e0e0")
        fig.patch.set_facecolor("#1a1a2e")

        # Base64 인코딩
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100,
                    bbox_inches="tight", facecolor="#1a1a2e")
        plt.close(fig)
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode("utf-8")
        print(f"  [CHART] {code} 차트 생성 완료 ({len(b64)} bytes)")
        return b64

    except ImportError:
        print("  [CHART] mplfinance 미설치 → 차트 생성 스킵")
        return ""
    except Exception as e:
        print(f"  [CHART] {code} 차트 생성 실패: {e}")
        traceback.print_exc()
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# 기업 정보
# ─────────────────────────────────────────────────────────────────────────────

def fetch_naver_company_info(code: str) -> dict:
    """업종, 동종업계 종목 등 기업 기본 정보 반환"""
    from bs4 import BeautifulSoup
    info = {"industry": "", "peers": []}
    try:
        url = f"{NAVER_FINANCE_BASE}/item/main.naver?code={code}"
        resp = requests.get(url, headers=HEADERS, timeout=8)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "lxml")

        # 업종
        industry_el = soup.select_one("div.section_trade div.trade_compare dt a")
        if industry_el:
            info["industry"] = industry_el.get_text(strip=True)

        # 동종업계
        peer_els = soup.select("div.section_trade ul.lst_related_stock li a")
        info["peers"] = [p.get_text(strip=True) for p in peer_els[:5]]

    except Exception as e:
        print(f"  [INFO] {code} 기업 정보 조회 실패: {e}")
    return info


# ─────────────────────────────────────────────────────────────────────────────
# 내장 Fallback 종목 목록
# ─────────────────────────────────────────────────────────────────────────────

def _get_fallback_stocks() -> dict:
    """네이버 크롤링 실패 시 사용하는 주요 종목 내장 목록"""
    return {
        # KOSPI 대형주
        "삼성전자": "005930", "SK하이닉스": "000660", "LG에너지솔루션": "373220",
        "삼성바이오로직스": "207940", "현대차": "005380", "기아": "000270",
        "POSCO홀딩스": "005490", "삼성SDI": "006400", "LG화학": "051910",
        "현대모비스": "012330", "카카오": "035720", "NAVER": "035420",
        "셀트리온": "068270", "KB금융": "105560", "신한지주": "055550",
        "하나금융지주": "086790", "우리금융지주": "316140", "삼성물산": "028260",
        "LG전자": "066570", "SK이노베이션": "096770", "SK텔레콤": "017670",
        "KT": "030200", "롯데케미칼": "011170", "한국전력": "015760",
        "두산에너빌리티": "034020", "고려아연": "010130", "삼성전기": "009150",
        "에스오일": "010950", "한화에어로스페이스": "012450", "크래프톤": "259960",
        # KOSDAQ 주요 종목
        "에코프로비엠": "247540", "에코프로": "086520", "셀트리온헬스케어": "091990",
        "카카오게임즈": "293490", "HLB": "028300", "펄어비스": "263750",
        "엔씨소프트": "036570", "넷마블": "251270", "더블유게임즈": "192080",
        "CJ ENM": "035760", "스튜디오드래곤": "253450", "와이지엔터테인먼트": "122870",
        "에스엠": "041510", "JYP Ent.": "035900", "하이브": "352820",
        "리노공업": "058470", "레이크머티리얼즈": "281740", "알테오젠": "196170",
        "유한양행": "000100", "한미약품": "128940", "종근당": "185750",
        "대웅제약": "069620", "동화약품": "000020",
        # ETF
        "KODEX 200": "069500", "KODEX 코스닥150": "229200",
        "TIGER 미국S&P500": "360750", "KODEX 나스닥100": "379810",
    }
