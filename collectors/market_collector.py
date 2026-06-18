# collectors/market_collector.py
"""
시장 지표 수집기

수정 이력:
- FIX-MKT-1 : 반환 딕셔너리 키를 html_generator._INDICATOR_DEFS와 일치하도록 통일
- FIX-MKT-2 : FinanceDataReader 의존 제거, yfinance 우선 / 네이버 폴백
- FIX-MKT-3 : collect_market_overview() 함수 내 들여쓰기 버그 수정
- FIX-MKT-4 : KOSPI/KOSDAQ도 yfinance 우선으로 변경
- BUG-8 FIX : 야간선물 수집 전면 재작성
- FIX-MKT-5 : 장 시작 전(09:00 KST 이전)에는 전일 종가 + "전일종가" 라벨 표시
- FIX-MKT-6 : _is_premarket() 주말(토·일) 처리 추가 (BUG-M1)
- FIX-MKT-7 : 나스닥/S&P500/다우존스/달러원은 is_premarket=False 고정
              (당일 오전 마감 지표 또는 실시간 환율 — (전일) 라벨 불필요)
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

_NAVER_NIGHT_FUTURES_API = (
    "https://polling.finance.naver.com/api/realtime/domestic/index/{symbol}"
)


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _is_premarket() -> bool:
    """
    현재 시각이 장 시작(09:00 KST) 이전인지 확인.
    FIX-MKT-6: 토·일요일은 항상 True 반환 (장 없음).
    """
    now = datetime.now(KST)
    if now.weekday() >= 5:   # 5=토, 6=일
        return True
    return now.hour < 9


def _make_indicator(value, change_pct, direction: str = "",
                    is_premarket: bool = False) -> dict:
    try:
        pct_num = float(change_pct) if change_pct is not None else 0.0
    except (TypeError, ValueError):
        pct_num = 0.0
    if not direction:
        direction = "up" if pct_num > 0 else "down" if pct_num < 0 else "flat"
    return {
        "value":        value,
        "change_pct":   pct_num,
        "direction":    direction,
        "is_premarket": is_premarket,
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

        m_val = re.search(r'id="now_value"[^>]*>([\d,.]+)', text)
        m_pct = re.search(r'id="change_percent"[^>]*>([\d.]+)', text)
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
            r'USD/KRW.*?value[\s"]+>([\d,.]+)', text, re.DOTALL
        )
        if not m_val:
            m_val = re.search(r'"exchangeRate"[^>]*>([\d,.]+)', text)
        if not m_val:
            return None, None
        value = float(m_val.group(1).replace(",", ""))
        return value, 0.0
    except Exception as e:
        print(f"  [Naver] USD/KRW 조회 실패: {e}")
        return None, None


# ── 야간선물 realtime JSON API ────────────────────────────────────────────────

def _fetch_naver_night_future(symbol: str):
    if not _REQUESTS_AVAILABLE:
        return None, None
    url = _NAVER_NIGHT_FUTURES_API.format(symbol=symbol)
    try:
        resp  = requests.get(url, headers=_NAVER_HEADERS, timeout=10)
        resp.raise_for_status()
        data  = resp.json()
        datas = data.get("datas", [])
        if not datas:
            return None, None
        item      = datas[0]
        close_str = item.get("closePrice", "").replace(",", "").replace("+", "")
        pct_str   = item.get("fluctuationsRatio", "0").replace(",", "")
        if not close_str:
            return None, None
        return float(close_str), float(pct_str)
    except Exception as e:
        print(f"  [Naver Night Future] {symbol} 조회 실패: {e}")
        return None, None


# ── 공개 API ──────────────────────────────────────────────────────────────────

def collect_market_overview() -> dict:
    print("\n[시장수집] 지표 수집 시작...")
    result    = {}
    premarket = _is_premarket()

    if premarket:
        print("  [장전/주말] 전일 종가 기준으로 표시")

    # ── KOSPI ─────────────────────────────────────────────────────────────────
    # FIX-MKT-7: KOSPI/KOSDAQ만 is_premarket 플래그 적용
    val, pct = _fetch_yf("^KS11")
    if val is None:
        val, pct = _fetch_naver_index("KOSPI")
    if val is not None:
        result["kospi"] = _make_indicator(val, pct, is_premarket=premarket)
        print(f"  KOSPI: {val:,.2f} ({pct:+.2f}%)" + (" [전일종가]" if premarket else ""))

    # ── KOSDAQ ────────────────────────────────────────────────────────────────
    # FIX-MKT-7: KOSPI/KOSDAQ만 is_premarket 플래그 적용
    val, pct = _fetch_yf("^KQ11")
    if val is None:
        val, pct = _fetch_naver_index("KOSDAQ")
    if val is not None:
        result["kosdaq"] = _make_indicator(val, pct, is_premarket=premarket)
        print(f"  KOSDAQ: {val:,.2f} ({pct:+.2f}%)" + (" [전일종가]" if premarket else ""))

    # ── NASDAQ ────────────────────────────────────────────────────────────────
    # FIX-MKT-7: 당일 오전 마감 지표 — is_premarket=False 고정
    val, pct = _fetch_yf("^IXIC")
    if val is not None:
        result["nasdaq"] = _make_indicator(val, pct, is_premarket=False)
        print(f"  NASDAQ: {val:,.2f} ({pct:+.2f}%)")

    # ── S&P 500 ───────────────────────────────────────────────────────────────
    # FIX-MKT-7: 당일 오전 마감 지표 — is_premarket=False 고정
    val, pct = _fetch_yf("^GSPC")
    if val is not None:
        result["sp500"] = _make_indicator(val, pct, is_premarket=False)
        print(f"  S&P500: {val:,.2f} ({pct:+.2f}%)")

    # ── 다우존스 ──────────────────────────────────────────────────────────────
    # FIX-MKT-7: 당일 오전 마감 지표 — is_premarket=False 고정
    val, pct = _fetch_yf("^DJI")
    if val is not None:
        result["dow"] = _make_indicator(val, pct, is_premarket=False)
        print(f"  DOW: {val:,.2f} ({pct:+.2f}%)")

    # ── KOSPI200 야간선물 ──────────────────────────────────────────────────────
    val, pct = _fetch_naver_night_future("K2FA001.N")
    if val is not None:
        result["kospi200_night"] = _make_indicator(val, pct)
        print(f"  KOSPI200 야간선물: {val:,.2f} ({pct:+.2f}%)")
    else:
        print("  KOSPI200 야간선물: 거래 시간 외 또는 데이터 없음 → 스킵")

    # ── KOSDAQ150 야간선물 ─────────────────────────────────────────────────────
    val, pct = _fetch_naver_night_future("KSFA001.N")
    if val is not None:
        result["kosdaq150_night"] = _make_indicator(val, pct)
        print(f"  KOSDAQ150 야간선물: {val:,.2f} ({pct:+.2f}%)")
    else:
        print("  KOSDAQ150 야간선물: 거래 시간 외 또는 데이터 없음 → 스킵")

    # ── USD/KRW ───────────────────────────────────────────────────────────────
    # FIX-MKT-7: 생성 시점 환율 — is_premarket=False 고정
    val, pct = _fetch_yf("KRW=X")
    if val is None:
        val, pct = _fetch_naver_forex()
    if val is not None:
        result["usd_krw"] = _make_indicator(val, pct, is_premarket=False)
        print(f"  USD/KRW: {val:,.2f} ({pct:+.2f}%)")

    if not result:
        print("  [경고] 모든 시장 지표 수집 실패")
    else:
        print(f"[시장수집] 완료 ({len(result)}개 지표)")

    return result
