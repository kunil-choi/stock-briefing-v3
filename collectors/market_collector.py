# collectors/market_collector.py
"""
시장 지표 수집기

수정 이력:
- FIX-MKT-1: 반환 딕셔너리 키를 html_generator._INDICATOR_DEFS와 일치하도록 통일
- FIX-MKT-2: FinanceDataReader 의존 제거, yfinance 우선 / 네이버 폴백
- FIX-MKT-3: collect_market_overview() 함수 내 들여쓰기 버그 수정
- FIX-MKT-4: KOSPI/KOSDAQ도 yfinance 우선으로 변경 (네이버 등락률 파싱 0.00% 버그 수정)
- BUG-8 FIX: 야간선물 수집 전면 재작성
             네이버 증권 realtime JSON API 사용
             KOSPI200 야간선물(K2FA001.N) + KOSDAQ150 야간선물(KSFA001.N) 추가
             야간 거래 시간(18:00~05:00) 외에는 None 반환 → 지표 미표시
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

_NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}

# 야간선물 네이버 realtime API
_NAVER_NIGHT_FUTURES_API = (
    "https://polling.finance.naver.com/api/realtime/domestic/index/{symbol}"
)


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
    """yfinance로 단일 티커 정보 조회. (value, change_pct) 반환."""
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

def _fetch_naver_index(symbol: str):
    """네이버 금융에서 국내 지수 조회 (폴백용)."""
    if not _REQUESTS_AVAILABLE:
        return None, None
    url = f"https://finance.naver.com/sise/sise_index.naver?code={symbol}"
    try:
        resp = requests.get(url, headers=_NAVER_HEADERS, timeout=10)
        resp.raise_for_status()
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


# ── BUG-8 FIX: 네이버 야간선물 realtime JSON API ─────────────────────────────

def _fetch_naver_night_future(symbol: str):
    """
    네이버 증권 realtime JSON API로 야간선물 조회.

    대상 심볼:
      K2FA001.N  → KOSPI200 야간선물
      KSFA001.N  → KOSDAQ150 야간선물

    야간 거래 시간(18:00 ~ 익일 05:00) 외에는 datas가 빈 배열로 반환됨.
    이 경우 (None, None) 반환 → 지표 미표시 처리.

    응답 예시 (거래 중):
    {
      "pollingInterval": 70000,
      "datas": [{
        "closePrice": "325.75",
        "compareToPreviousClosePrice": "+3.25",
        "fluctuationsRatio": "+1.01"
      }],
      "time": "20260615030000"
    }
    """
    if not _REQUESTS_AVAILABLE:
        return None, None

    url = _NAVER_NIGHT_FUTURES_API.format(symbol=symbol)
    try:
        resp = requests.get(url, headers=_NAVER_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        datas = data.get("datas", [])
        if not datas:
            # 야간 거래 시간 외 → 데이터 없음
            return None, None

        item = datas[0]
        close_str = item.get("closePrice", "").replace(",", "").replace("+", "")
        pct_str   = item.get("fluctuationsRatio", "0").replace(",", "")

        if not close_str:
            return None, None

        value = float(close_str)
        pct   = float(pct_str)
        return value, pct

    except Exception as e:
        print(f"  [Naver Night Future] {symbol} 조회 실패: {e}")
        return None, None


# ── 공개 API ──────────────────────────────────────────────────────────────────

def collect_market_overview() -> dict:
    """
    시장 지표를 수집하여 딕셔너리를 반환한다.

    반환 키:
      kospi, kosdaq, nasdaq, sp500, dow,
      kospi200_night  ← BUG-8 FIX: KOSPI200 야간선물 (신규)
      kosdaq150_night ← BUG-8 FIX: KOSDAQ150 야간선물 (신규)
      usd_krw
    """
    print("\n[시장수집] 지표 수집 시작...")
    result = {}

    # ── KOSPI ─────────────────────────────────────────────────────────────────
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

    # ── KOSPI200 야간선물 (BUG-8 FIX) ────────────────────────────────────────
    # 네이버 증권 realtime JSON API 사용 (계좌/API키 불필요)
    # 야간 거래 시간(18:00~05:00) 외에는 None → result에 키 미추가
    val, pct = _fetch_naver_night_future("K2FA001.N")
    if val is not None:
        result["kospi200_night"] = _make_indicator(val, pct)
        print(f"  KOSPI200 야간선물: {val:,.2f} ({pct:+.2f}%)")
    else:
        print("  KOSPI200 야간선물: 거래 시간 외 또는 데이터 없음 → 스킵")

    # ── KOSDAQ150 야간선물 (BUG-8 FIX) ───────────────────────────────────────
    val, pct = _fetch_naver_night_future("KSFA001.N")
    if val is not None:
        result["kosdaq150_night"] = _make_indicator(val, pct)
        print(f"  KOSDAQ150 야간선물: {val:,.2f} ({pct:+.2f}%)")
    else:
        print("  KOSDAQ150 야간선물: 거래 시간 외 또는 데이터 없음 → 스킵")

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
