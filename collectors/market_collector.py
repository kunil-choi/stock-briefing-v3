# collectors/market_collector.py
"""
시장 데이터 수집기 - v3
야간선물 / 미국증시 / 한국증시
"""
import time
import requests
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer":         "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


# ── 유틸리티 ───────────────────────────────────────────────────────────────────

def _safe_float(val) -> float | None:
    """
    문자열·숫자 혼용 값을 float으로 안전 변환.
    콤마, %, +, 공백 제거 후 변환. 실패 시 None 반환.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        cleaned = (
            str(val)
            .replace(",", "")
            .replace("%", "")
            .replace("+", "")
            .replace(" ", "")
            .strip()
        )
        if not cleaned or cleaned in ("-", "N/A", "n/a", "--"):
            return None
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _fmt(change_pct) -> str:
    """등락률 포맷 (+1.23% / -0.45%)"""
    v = _safe_float(change_pct)
    if v is None:
        return "N/A"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


# ── 미국 증시 ─────────────────────────────────────────────────────────────────

def _get_usd_krw_naver() -> dict:
    """네이버 환율 스크래핑 폴백"""
    try:
        url  = "https://finance.naver.com/marketindex/exchangeDetail.naver?marketindexCd=FX_USDKRW"
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")
        price_el  = soup.select_one("p.no_today em.no_up, p.no_today em.no_down, p.no_today em")
        change_el = soup.select_one("span.change")
        price  = price_el.get_text(strip=True).replace(",", "")  if price_el  else ""
        change = change_el.get_text(strip=True).replace(",", "") if change_el else ""
        return {
            "price":      _safe_float(price),
            "change":     _safe_float(change),
            "change_pct": None,
        }
    except Exception as e:
        print(f"  [환율 폴백 오류] {e}")
        return {"price": None, "change": None, "change_pct": None}


def get_us_market_data() -> dict:
    """yfinance로 미국 주요 지수 및 환율 수집"""
    result = {
        "sp500":   {"price": None, "change": None, "change_pct": None},
        "nasdaq":  {"price": None, "change": None, "change_pct": None},
        "dow":     {"price": None, "change": None, "change_pct": None},
        "usd_krw": {"price": None, "change": None, "change_pct": None},
    }
    try:
        import yfinance as yf
        tickers = {
            "sp500":   "^GSPC",
            "nasdaq":  "^IXIC",
            "dow":     "^DJI",
            "usd_krw": "USDKRW=X",
        }
        for key, symbol in tickers.items():
            try:
                t         = yf.Ticker(symbol)
                info      = t.fast_info
                price     = _safe_float(getattr(info, "last_price",     None))
                prev      = _safe_float(getattr(info, "previous_close", None))
                change    = round(price - prev, 2) if (price and prev) else None
                chg_pct   = round((change / prev) * 100, 2) if (change and prev) else None
                result[key] = {
                    "price":      round(price, 2) if price else None,
                    "change":     change,
                    "change_pct": chg_pct,
                }
                print(f"  [{key}] {price} ({_fmt(chg_pct)})")
            except Exception as e:
                print(f"  [{key}] yfinance 오류: {e}")
    except ImportError:
        print("  [미국증시] yfinance 미설치 → 환율만 네이버 폴백")
        result["usd_krw"] = _get_usd_krw_naver()
    return result


# ── 야간선물 ──────────────────────────────────────────────────────────────────

def _get_night_futures_fallback() -> dict:
    """
    네이버 금융 야간선물 HTML 폴백.
    코스피200 야간선물 종목코드: 101S6000
    """
    try:
        url  = "https://finance.naver.com/item/main.naver?code=101S6000"
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")

        price_el      = soup.select_one("p.no_today em")
        change_el     = soup.select_one("p.no_exday em")
        change_pct_el = soup.select_one("p.no_exday span.no_up, p.no_exday span.no_down")

        price     = _safe_float(price_el.get_text(strip=True))      if price_el      else None
        change    = _safe_float(change_el.get_text(strip=True))     if change_el     else None
        chg_pct   = _safe_float(change_pct_el.get_text(strip=True)) if change_pct_el else None

        if chg_pct is not None:
            direction = "call" if chg_pct > 0 else ("put" if chg_pct < 0 else "neutral")
        elif change is not None:
            direction = "call" if change > 0  else ("put" if change  < 0 else "neutral")
        else:
            direction = "neutral"

        return {
            "price":      price,
            "change":     change,
            "change_pct": chg_pct,
            "volume":     None,
            "direction":  direction,
        }
    except Exception as e:
        print(f"  [야간선물 폴백 오류] {e}")
        return {
            "price": None, "change": None, "change_pct": None,
            "volume": None, "direction": "neutral",
        }


def get_night_futures_data() -> dict:
    """
    코스피200 야간선물 수집.
    1순위: 네이버 모바일 API (KOSPI200FUT)
    2순위: HTML 폴백 (code=101S6000)
    """
    # BUG-M3 수정: 정확한 코스피200 야간선물 API 엔드포인트 사용
    endpoints = [
        "https://m.stock.naver.com/api/index/KOSPI200FUT/basic",
        "https://m.stock.naver.com/api/index/FUT/basic",
    ]

    for url in endpoints:
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=10)
            if resp.status_code != 200:
                continue

            d       = resp.json()
            price   = _safe_float(d.get("closePrice")    or d.get("price"))
            change  = _safe_float(d.get("compareToPreviousClosePrice") or d.get("change"))
            chg_pct = _safe_float(d.get("fluctuationsRatio")           or d.get("change_pct"))
            volume  = _safe_float(d.get("accumulatedTradingVolume")    or d.get("volume"))

            if price is None:
                continue

            if chg_pct is not None:
                direction = "call" if chg_pct > 0 else ("put" if chg_pct < 0 else "neutral")
            elif change is not None:
                direction = "call" if change > 0  else ("put" if change  < 0 else "neutral")
            else:
                direction = "neutral"

            result = {
                "price":      price,
                "change":     change,
                "change_pct": chg_pct,
                "volume":     volume,
                "direction":  direction,
            }
            print(f"  [야간선물] {price} ({_fmt(chg_pct)}) → {direction}")
            return result

        except Exception as e:
            print(f"  [야간선물 API 오류] {url}: {e}")

    print("  [야간선물] API 모두 실패 → HTML 폴백 시도")
    return _get_night_futures_fallback()


# ── 한국 증시 ─────────────────────────────────────────────────────────────────

def _get_korea_index_fallback(code: str) -> dict:
    """네이버 코스피/코스닥 HTML 폴백"""
    try:
        url  = f"https://finance.naver.com/sise/sise_index.naver?code={code}"
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")
        price_el  = soup.select_one("strong#now_value")
        change_el = soup.select_one("strong#change_value")
        price   = _safe_float(price_el.get_text(strip=True))  if price_el  else None
        change  = _safe_float(change_el.get_text(strip=True)) if change_el else None
        chg_pct = (
            round((change / (price - change)) * 100, 2)
            if (change is not None and price and price != change)
            else None
        )
        return {"price": price, "change": change, "change_pct": chg_pct}
    except Exception as e:
        print(f"  [{code} 폴백 오류] {e}")
        return {"price": None, "change": None, "change_pct": None}


def get_korea_market_data() -> dict:
    """네이버 모바일 API에서 코스피/코스닥 수집"""
    result = {
        "kospi":  {"price": None, "change": None, "change_pct": None},
        "kosdaq": {"price": None, "change": None, "change_pct": None},
    }
    indices = [
        ("kospi",  "KOSPI",  "KOSPI"),
        ("kosdaq", "KOSDAQ", "KOSDAQ"),
    ]
    for key, api_code, fallback_code in indices:
        try:
            url  = f"https://m.stock.naver.com/api/index/{api_code}/basic"
            resp = requests.get(url, headers=_HEADERS, timeout=10)
            if resp.status_code != 200:
                result[key] = _get_korea_index_fallback(fallback_code)
                continue
            d       = resp.json()
            price   = _safe_float(d.get("closePrice")    or d.get("price"))
            change  = _safe_float(d.get("compareToPreviousClosePrice") or d.get("change"))
            chg_pct = _safe_float(d.get("fluctuationsRatio")           or d.get("change_pct"))
            result[key] = {"price": price, "change": change, "change_pct": chg_pct}
            print(f"  [{key.upper()}] {price} ({_fmt(chg_pct)})")
            time.sleep(0.2)
        except Exception as e:
            print(f"  [{key} 오류] {e} → 폴백")
            result[key] = _get_korea_index_fallback(fallback_code)
    return result


# ── 통합 수집 ─────────────────────────────────────────────────────────────────

def collect_market_overview() -> dict:
    """야간선물 + 미국증시 + 한국증시 통합 수집"""
    print("  [시장] 야간선물 수집...")
    night_futures = get_night_futures_data()

    print("  [시장] 미국 증시 수집...")
    us_market = get_us_market_data()

    print("  [시장] 한국 증시 수집...")
    korea_market = get_korea_market_data()

    overview = {
        "night_futures": night_futures,
        "us_market":     us_market,
        "korea_market":  korea_market,
        "timestamp":     datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
    }
    print("  [시장] 수집 완료")
    return overview
