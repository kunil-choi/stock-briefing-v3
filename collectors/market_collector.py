# collectors/market_collector.py
"""
야간선물 · 미국증시 · 한국증시 데이터 수집
- 야간 코스피200 선물 (네이버 증권 모바일 API)
- 미국 증시 (yfinance: S&P500, 나스닥, 다우, 달러/원)
- 한국 증시 (코스피, 코스닥)
"""

import time
import requests
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    "Accept": "application/json, text/plain, */*",
}


# ─── 공통 유틸 ────────────────────────────────────────────────────────────────

def _safe_float(value) -> float:
    """쉼표 포함 문자열·None·비숫자를 안전하게 float 변환"""
    if value is None:
        return 0.0
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _fmt(value, suffix="%") -> str:
    """등락률을 +/- 부호 포함 문자열로 안전 변환"""
    if value is None:
        return "N/A"
    try:
        return f"{float(value):+.2f}{suffix}"
    except (TypeError, ValueError):
        return "N/A"


# ─── 1. 미국 증시 ─────────────────────────────────────────────────────────────

def get_us_market_data() -> dict:
    """yfinance로 S&P500·나스닥·다우·달러/원 직전 종가 수집"""
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
                info  = yf.Ticker(symbol).fast_info
                price = round(_safe_float(info.last_price), 2)
                prev  = round(_safe_float(info.previous_close), 2)
                chg   = round(price - prev, 2)
                pct   = round((chg / prev) * 100, 2) if prev else 0.0
                result[key] = {"price": price, "change": chg, "change_pct": pct}
                time.sleep(0.3)
            except Exception as e:
                print(f"  [미국증시] {key}({symbol}) 수집 실패: {e}")

        sp = result["sp500"]
        nq = result["nasdaq"]
        dw = result["dow"]
        fx = result["usd_krw"]
        print(
            f"  [미국증시] S&P500={sp['price']}({_fmt(sp['change_pct'])}) "
            f"나스닥={nq['price']}({_fmt(nq['change_pct'])}) "
            f"다우={dw['price']}({_fmt(dw['change_pct'])}) "
            f"달러/원={fx['price']}({_fmt(fx['change_pct'])})"
        )
    except ImportError:
        print("  [미국증시] yfinance 미설치 → 네이버 환율만 수집")
        result["usd_krw"] = _get_usd_krw_naver()
    return result


def _get_usd_krw_naver() -> dict:
    """네이버 환율 페이지에서 달러/원 스크래핑 (yfinance 폴백)"""
    try:
        from bs4 import BeautifulSoup
        url = "https://finance.naver.com/marketindex/exchangeDetail.naver?marketindexCd=FX_USDKRW"
        resp = requests.get(url, headers=HEADERS, timeout=8)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")
        price_el  = soup.select_one("div.today span.value")
        change_el = soup.select_one("div.today span.change")
        sign_el   = soup.select_one("div.today span.blind")
        price      = _safe_float(price_el.text)  if price_el  else None
        change_raw = _safe_float(change_el.text) if change_el else None
        sign       = -1 if (sign_el and "하락" in sign_el.text) else 1
        change     = round(sign * change_raw, 2) if change_raw else None
        change_pct = round((change / (price - change)) * 100, 2) if (change and price) else None
        return {"price": price, "change": change, "change_pct": change_pct}
    except Exception as e:
        print(f"  [환율] 네이버 환율 수집 실패: {e}")
        return {"price": None, "change": None, "change_pct": None}


# ─── 2. 야간선물 ──────────────────────────────────────────────────────────────

def get_night_futures_data() -> dict:
    """네이버 모바일 API로 코스피200 야간선물 수집"""
    result = {
        "price": None, "change": None, "change_pct": None,
        "volume": None, "direction": "neutral", "signal": "데이터 없음",
    }
    try:
        url  = "https://m.stock.naver.com/api/index/FUT/basic"
        resp = requests.get(url, headers=HEADERS, timeout=8)
        if resp.status_code == 200:
            data       = resp.json()
            price      = _safe_float(data.get("closePrice"))
            change     = _safe_float(data.get("compareToPreviousClosePrice"))
            change_pct = _safe_float(data.get("fluctuationsRatio"))
            volume     = int(_safe_float(data.get("accumulatedTradingVolume")))
            result.update({
                "price": round(price, 2),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
                "volume": volume,
            })
            if change_pct >= 0.3:
                result["direction"] = "call"
                result["signal"]    = f"상승 신호 (야간선물 +{change_pct:.2f}%)"
            elif change_pct <= -0.3:
                result["direction"] = "put"
                result["signal"]    = f"하락 신호 (야간선물 {change_pct:.2f}%)"
            else:
                result["signal"] = f"보합/중립 (야간선물 {change_pct:+.2f}%)"
            print(f"  [야간선물] 코스피200={price} ({_fmt(change_pct)}) 방향={result['direction']}")
            return result
        else:
            print(f"  [야간선물] API 오류 {resp.status_code} → HTML 폴백")
    except Exception as e:
        print(f"  [야간선물] 1차 실패: {e} → HTML 폴백")
    return _get_night_futures_fallback(result)


def _get_night_futures_fallback(result: dict) -> dict:
    """네이버 야간선물 HTML 페이지 폴백"""
    try:
        from bs4 import BeautifulSoup
        url  = "https://finance.naver.com/item/main.naver?code=101S6000"
        resp = requests.get(url, headers=HEADERS, timeout=8)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")
        el   = soup.select_one("p.no_today span.blind")
        if el:
            price = _safe_float(el.text)
            result["price"]  = price
            result["signal"] = f"코스피200선물 {price:,.2f} (등락 미확인)"
        print(f"  [야간선물 폴백] {result['signal']}")
    except Exception as e:
        print(f"  [야간선물 폴백] 실패: {e}")
    return result


# ─── 3. 한국 증시 ─────────────────────────────────────────────────────────────

def get_korea_market_data() -> dict:
    """네이버 모바일 API로 코스피·코스닥 수집"""
    result = {
        "kospi":  {"price": None, "change": None, "change_pct": None},
        "kosdaq": {"price": None, "change": None, "change_pct": None},
    }
    for key, code in [("kospi", "KOSPI"), ("kosdaq", "KOSDAQ")]:
        try:
            url  = f"https://m.stock.naver.com/api/index/{code}/basic"
            resp = requests.get(url, headers=HEADERS, timeout=8)
            if resp.status_code == 200:
                data       = resp.json()
                price      = _safe_float(data.get("closePrice"))
                change     = _safe_float(data.get("compareToPreviousClosePrice"))
                change_pct = _safe_float(data.get("fluctuationsRatio"))
                result[key] = {
                    "price": round(price, 2),
                    "change": round(change, 2),
                    "change_pct": round(change_pct, 2),
                }
            else:
                print(f"  [한국증시] {key} API 오류 {resp.status_code} → HTML 폴백")
                result[key] = _get_korea_index_fallback(code)
            time.sleep(0.2)
        except Exception as e:
            print(f"  [한국증시] {key} 실패: {e} → HTML 폴백")
            result[key] = _get_korea_index_fallback(code)

    kp = result["kospi"]
    kd = result["kosdaq"]
    print(
        f"  [한국증시] 코스피={kp['price']}({_fmt(kp['change_pct'])}) "
        f"코스닥={kd['price']}({_fmt(kd['change_pct'])})"
    )
    return result


def _get_korea_index_fallback(code: str) -> dict:
    """네이버 증시 지수 HTML 폴백"""
    try:
        from bs4 import BeautifulSoup
        url  = f"https://finance.naver.com/sise/sise_index.naver?code={code}"
        resp = requests.get(url, headers=HEADERS, timeout=8)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")
        el   = soup.select_one("#now_value")
        if el:
            return {"price": _safe_float(el.text), "change": None, "change_pct": None}
    except Exception as e:
        print(f"  [한국증시 폴백] {code} 실패: {e}")
    return {"price": None, "change": None, "change_pct": None}


# ─── 4. 통합 수집 ─────────────────────────────────────────────────────────────

def collect_market_overview() -> dict:
    """야간선물 · 미국증시 · 한국증시 데이터를 통합 수집해 딕셔너리로 반환"""
    print("\n[시장 데이터 수집]")
    print("  야간선물 수집 중...")
    night_futures = get_night_futures_data()
    print("  미국 증시 수집 중...")
    us_market = get_us_market_data()
    print("  한국 증시 수집 중...")
    korea_market = get_korea_market_data()
    overview = {
        "night_futures": night_futures,
        "us_market":     us_market,
        "korea_market":  korea_market,
        "collected_at":  datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
    }
    print(f"  [시장 데이터] 수집 완료 ({overview['collected_at']})")
    return overview
