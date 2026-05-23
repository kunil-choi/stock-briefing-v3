# collectors/market_collector.py - v3
"""
야간선물 및 미국 주식시장 데이터 수집기
- 야간 코스피200 선물 (네이버 증권 스크래핑)
- 미국 증시 (yfinance: S&P500, 나스닥, 다우)
- 환율 (달러/원)
"""

import os
import time
import requests
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
}


# ──────────────────────────────────────────────
# 1. 미국 증시 (yfinance)
# ──────────────────────────────────────────────
def get_us_market_data() -> dict:
    """yfinance로 S&P500·나스닥·다우 전일 종가·등락 수집"""
    result = {
        "sp500":  {"price": None, "change": None, "change_pct": None},
        "nasdaq": {"price": None, "change": None, "change_pct": None},
        "dow":    {"price": None, "change": None, "change_pct": None},
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
                ticker = yf.Ticker(symbol)
                info = ticker.fast_info
                price = round(float(info.last_price), 2)
                prev  = round(float(info.previous_close), 2)
                change = round(price - prev, 2)
                change_pct = round((change / prev) * 100, 2) if prev else 0.0
                result[key] = {
                    "price": price,
                    "change": change,
                    "change_pct": change_pct,
                }
                time.sleep(0.3)
            except Exception as e:
                print(f"  [미국증시] {key}({symbol}) 수집 실패: {e}")

        print(f"  [미국증시] S&P500={result['sp500']['price']} "
              f"나스닥={result['nasdaq']['price']} "
              f"다우={result['dow']['price']} "
              f"달러={result['usd_krw']['price']}")

    except ImportError:
        print("  [미국증시] yfinance 미설치 → 네이버 환율만 수집")
        result["usd_krw"] = _get_usd_krw_naver()

    return result


def _get_usd_krw_naver() -> dict:
    """네이버 증권에서 달러/원 환율 수집 (fallback)"""
    try:
        url = "https://finance.naver.com/marketindex/exchangeDetail.naver?marketindexCd=FX_USDKRW"
        resp = requests.get(url, headers=HEADERS, timeout=8)
        resp.encoding = "euc-kr"
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        price_el  = soup.select_one("div.today span.value")
        change_el = soup.select_one("div.today span.change")
        sign_el   = soup.select_one("div.today span.blind")

        price = float(price_el.text.replace(",", "")) if price_el else None
        change_raw = float(change_el.text.replace(",", "")) if change_el else None
        sign = -1 if (sign_el and "하락" in sign_el.text) else 1
        change = round(sign * change_raw, 2) if change_raw else None
        change_pct = round((change / (price - change)) * 100, 2) if change and price else None

        return {"price": price, "change": change, "change_pct": change_pct}
    except Exception as e:
        print(f"  [환율] 네이버 환율 수집 실패: {e}")
        return {"price": None, "change": None, "change_pct": None}


# ──────────────────────────────────────────────
# 2. 야간 선물 (네이버 증권 → 코스피200 선물)
# ──────────────────────────────────────────────
def get_night_futures_data() -> dict:
    """
    네이버 증권에서 코스피200 야간선물 데이터 수집
    야간시장: 18:00 ~ 익일 05:00 KST
    반환: {"price": float, "change": float, "change_pct": float,
           "volume": int, "direction": "call"|"put"|"neutral",
           "signal": str, "is_night_session": bool}
    """
    result = {
        "price": None,
        "change": None,
        "change_pct": None,
        "volume": None,
        "direction": "neutral",
        "signal": "데이터 없음",
        "is_night_session": False,
    }
    try:
        # 네이버 증권 선물 API (JSON)
        url = "https://api.stock.naver.com/index/FUT/basic"
        resp = requests.get(url, headers=HEADERS, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            price      = float(data.get("closePrice", 0) or 0)
            prev_price = float(data.get("compareToPreviousClosePrice", 0) or 0)
            change_pct = float(data.get("fluctuationsRatio", 0) or 0)
            volume     = int(data.get("accumulatedTradingVolume", 0) or 0)

            result["price"]      = round(price, 2)
            result["change"]     = round(prev_price, 2)
            result["change_pct"] = round(change_pct, 2)
            result["volume"]     = volume

            # 방향 판단
            if change_pct >= 0.3:
                result["direction"] = "call"
                result["signal"]    = f"상승 신호 (야간 선물 +{change_pct:.2f}%)"
            elif change_pct <= -0.3:
                result["direction"] = "put"
                result["signal"]    = f"하락 신호 (야간 선물 {change_pct:.2f}%)"
            else:
                result["direction"] = "neutral"
                result["signal"]    = f"보합/중립 (야간 선물 {change_pct:+.2f}%)"

            print(f"  [야간선물] 코스피200선물={price} ({change_pct:+.2f}%) "
                  f"방향={result['direction']}")
        else:
            # fallback: 네이버 증권 HTML
            result = _get_night_futures_html_fallback()

    except Exception as e:
        print(f"  [야간선물] 수집 실패: {e}")
        result = _get_night_futures_html_fallback()

    return result


def _get_night_futures_html_fallback() -> dict:
    """네이버 증권 HTML fallback - 코스피200 선물 페이지"""
    result = {
        "price": None, "change": None, "change_pct": None,
        "volume": None, "direction": "neutral",
        "signal": "데이터 수집 실패 (장중 참고 불가)", "is_night_session": False,
    }
    try:
        from bs4 import BeautifulSoup
        url = "https://finance.naver.com/item/main.naver?code=101S6000"
        resp = requests.get(url, headers=HEADERS, timeout=8)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")

        price_el  = soup.select_one("p.no_today span.blind")
        change_el = soup.select_one("p.no_exday em span.blind")

        if price_el:
            price = float(price_el.text.replace(",", ""))
            result["price"] = price
            result["signal"] = f"코스피200 선물 {price}"

    except Exception as e:
        print(f"  [야간선물 fallback] 실패: {e}")

    return result


# ──────────────────────────────────────────────
# 3. 전일 한국 증시 (코스피·코스닥)
# ──────────────────────────────────────────────
def get_korea_market_data() -> dict:
    """네이버 증권에서 코스피·코스닥 전일 종가·등락 수집"""
    result = {
        "kospi":  {"price": None, "change": None, "change_pct": None},
        "kosdaq": {"price": None, "change": None, "change_pct": None},
    }
    try:
        indices = {
            "kospi":  "KOSPI",
            "kosdaq": "KOSDAQ",
        }
        for key, code in indices.items():
            try:
                url = f"https://api.stock.naver.com/index/{code}/basic"
                resp = requests.get(url, headers=HEADERS, timeout=8)
                if resp.status_code == 200:
                    data = resp.json()
                    price      = float(data.get("closePrice", 0) or 0)
                    change     = float(data.get("compareToPreviousClosePrice", 0) or 0)
                    change_pct = float(data.get("fluctuationsRatio", 0) or 0)
                    result[key] = {
                        "price": round(price, 2),
                        "change": round(change, 2),
                        "change_pct": round(change_pct, 2),
                    }
                time.sleep(0.2)
            except Exception as e:
                print(f"  [한국증시] {key} 수집 실패: {e}")

        print(f"  [한국증시] 코스피={result['kospi']['price']} "
              f"코스닥={result['kosdaq']['price']}")

    except Exception as e:
        print(f"  [한국증시] 전체 수집 실패: {e}")

    return result


# ──────────────────────────────────────────────
# 4. 통합 수집
# ──────────────────────────────────────────────
def collect_market_overview() -> dict:
    """야간선물 + 미국증시 + 한국증시 통합 수집"""
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
