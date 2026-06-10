# collectors/market_collector.py
"""
시장 지표 수집기

수정 이력:
- FIX-MKT-1: 반환 딕셔너리 키를 html_generator._INDICATOR_DEFS와 일치하도록 통일
- FIX-MKT-2: FinanceDataReader 의존 제거, yfinance 우선 / 네이버 폴백
- FIX-MKT-3: collect_market_overview() 함수 내 들여쓰기 버그 수정
- FIX-MKT-4: KOSPI/KOSDAQ도 yfinance 우선으로 변경 (네이버 등락률 파싱 0.00% 버그 수정)
"""

import re
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False


# ── 내부 헬퍼 ────────────────────────────────────────────────────────────────

def _make_indicator(value, change_pct, direction: str = "") -> dict:
    try:
        pct_num = float(change_pct) if change_pct is not None else 0.0
    except (TypeError, ValueError):
        pct_num = 0.0
    if not direction:
        direction = "up" if pct_num > 0 else "down" if pct_num < 0 else "flat"
    return {
        "value":      value,
        "change_pct": pct_num,
        "direction":  direction,
    }


def _pct(current, previous) -> float:
    try:
        c = float(current)
        p = float(previous)
        if p == 0:
            return 0.0
        return round((c - p) / p * 100, 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


# ── yfinance 기반 조회 ────────────────────────────────────────────────────────

def _fetch_yf(ticker: str):
    """yfinance로 단일 티커 정보 조회. (close, change_pct) 반환."""
    if not _YF_AVAILABLE:
        return None, None
    try:
        tk   = yf.Ticker(ticker)
        hist = tk.history(period="5d")
        if hist.empty or len(hist) < 2:
            return None, None
        close_prev = float(hist["Close"].iloc[-2])
        close_now  = float(hist["Close"].iloc[-1])
        pct        = _pct(close_now, close_prev)
        return close_now, pct
    except Exception as e:
        print(f"  [yfinance] {ticker} 조회 실패: {e}")
        return None, None


# ── 네이버 금융 폴백 ──────────────────────────────────────────────────────────

_NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}


def _fetch_naver_index(symbol: str):
    """네이버 금융에서 국내 지수 조회 (폴백용)."""
    if not _REQUESTS_AVAILABLE:
        return None, None
    url = f"https://finance.naver.com/sise/sise_index.naver?code={symbol}"
    try:
        import requests
        resp = requests.get(url, headers=_NAVER_HEADERS, timeout=10)
        resp.raise_for_status()
        # EUC-KR 인코딩 명시
        resp.encoding = "euc-kr"
        text = resp.text

        m_val = re.search(r'id="now_value"[^>]*>([\d,\.]+)', text)
        m_pct = re.search(r'id="change_percent"[^>]*>([\d\.]+)', text)
        m_dir = re.search(r'class="(up|down|dn|no)"', text)

        if not m_val:
            return None, None
        value     = float(m_val.group(1).replace(",", ""))
        pct       = float(m_pct.group(1)) if m_pct else 0.0
        raw_dir   = (m_dir.group(1) if m_dir else "").lower()
        direction = "up" if "up" in raw_dir else "down" if ("down" in raw_dir or "dn" in raw_dir) else "flat"
        if direction == "down":
            pct = -abs(pct)
        return value, pct
    except Exception as e:
        print(f"  [Naver] {symbol} 조회 실패: {e}")
        return None, None


def _fetch_naver_forex():
    """네이버 금융에서 USD/KRW 환율 조회."""
    if not _REQUESTS_AVAILABLE:
        return None, None
    url = "https://finance.naver.com/marketindex/"
    try:
        import requests
        resp = requests.get(url, headers=_NAVER_HEADERS, timeout=10)
        resp.raise_for_status()
        resp.encoding = "euc-kr"
        text = resp.text
        m_val = re.search(
            r'USD/KRW.*?value["\s]+>([\d,\.]+)', text, re.DOTALL
        )
        if not m_val:
            m_val = re.search(r'"exchangeRate"[^>]*>([\d,\.]+)', text)
        if not m_val:
            return None, None
        value = float(m_val.group(1).replace(",", ""))
        return value, 0.0
    except Exception as e:
        print(f"  [Naver] USD/KRW 조회 실패: {e}")
        return None, None


# ── 공개 API ──────────────────────────────────────────────────────────────────

def collect_market_overview() -> dict:
    """
    시장 지표를 수집하여 딕셔너리를 반환한다.
    FIX-MKT-4: KOSPI/KOSDAQ도 yfinance 우선, 네이버는 값이 없을 때만 폴백
    """
    print("\n[시장수집] 지표 수집 시작...")
    result = {}

    # ── KOSPI ─────────────────────────────────────────────────────────────────
    # FIX-MKT-4: yfinance 우선 (등락률 정확), 네이버는 폴백
    val, pct = _fetch_yf("^KS11")
    if val is None:
        val, pct = _fetch_naver_index("KOSPI")
    if val is not None:
        result["kospi"] = _make_indicator(val, pct)
        print(f"  KOSPI: {val:,.2f} ({pct:+.2f}%)")

    # ── KOSDAQ ────────────────────────────────────────────────────────────────
    val, pct = _fetch_yf("^KQ11")
    if val is None:
        val, pct = _fetch_naver_index("KOSDAQ")
    if val is not None:
        result["kosdaq"] = _make_indicator(val, pct)
        print(f"  KOSDAQ: {val:,.2f} ({pct:+.2f}%)")

    # ── NASDAQ ────────────────────────────────────────────────────────────────
    val, pct = _fetch_yf("^IXIC")
    if val is not None:
        result["nasdaq"] = _make_indicator(val, pct)
        print(f"  NASDAQ: {val:,.2f} ({pct:+.2f}%)")

    # ── S&P 500 ───────────────────────────────────────────────────────────────
    val, pct = _fetch_yf("^GSPC")
    if val is not None:
        result["sp500"] = _make_indicator(val, pct)
        print(f"  S&P500: {val:,.2f} ({pct:+.2f}%)")

    # ── 다우존스 ──────────────────────────────────────────────────────────────
    val, pct = _fetch_yf("^DJI")
    if val is not None:
        result["dow"] = _make_indicator(val, pct)
        print(f"  DOW: {val:,.2f} ({pct:+.2f}%)")

    # ── 야간선물 ──────────────────────────────────────────────────────────────
    val, pct = _fetch_yf("^KS200")
    if val is None:
        val, pct = _fetch_naver_index("KOSPI200")
    if val is not None:
        result["night_future"] = _make_indicator(val, pct)
        print(f"  야간선물: {val:,.2f} ({pct:+.2f}%)")

    # ── USD/KRW ───────────────────────────────────────────────────────────────
    val, pct = _fetch_yf("KRW=X")
    if val is None:
        val, pct = _fetch_naver_forex()
    if val is not None:
        result["usd_krw"] = _make_indicator(val, pct)
        print(f"  USD/KRW: {val:,.2f} ({pct:+.2f}%)")

    if not result:
        print("  [경고] 모든 시장 지표 수집 실패")
    else:
        print(f"[시장수집] 완료 ({len(result)}개 지표)")

    return result
