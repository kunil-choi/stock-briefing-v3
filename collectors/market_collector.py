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
- FIX-MKT-8 : 야간선물 수집 재수정
               polling API 심볼/파싱 오류 수정
               네이버 야간선물 페이지 직접 파싱으로 변경
               _fetch_naver_index() 방향 정규식 개선 (숫자 suffix 대응)
               _fetch_naver_forex() 파싱 정규식 개선
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
    """
    네이버 금융에서 국내 지수 조회 (폴백용).
    FIX-MKT-8: 방향 클래스 정규식에 숫자 suffix 대응 (up1, down2 등)
    """
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
        # FIX-MKT-8: 숫자 suffix 대응
        m_dir = re.search(r'class="(up\d*|down\d*|dn\d*|no\d*)"', text)

        if not m_val:
            return None, None
        value     = float(m_val.group(1).replace(",", ""))
        pct       = float(m_pct.group(1)) if m_pct else 0.0
        raw_dir   = (m_dir.group(1) if m_dir else "").lower()
        direction = ("up"   if "up"   in raw_dir else
                     "down" if ("down" in raw_dir or "dn" in raw_dir) else
                     "flat")
        if direction == "down":
            pct = -abs(pct)
        return value, pct
    except Exception as e:
        print(f"  [Naver] {symbol} 조회 실패: {e}")
        return None, None


def _fetch_naver_forex():
    """
    네이버 금융에서 USD/KRW 환율 조회 (yfinance 실패 시 폴백).
    FIX-MKT-8: 파싱 정규식 개선
    """
    if not _REQUESTS_AVAILABLE:
        return None, None
    url = "https://finance.naver.com/marketindex/"
    try:
        resp = requests.get(url, headers=_NAVER_HEADERS, timeout=10)
        resp.raise_for_status()
        resp.encoding = "euc-kr"
        text = resp.text

        # 네이버 마켓인덱스 USD/KRW 구조
        # <span class="value">1,325.50</span>
        m_val = re.search(
            r'USD.*?<span[^>]+class="[^"]*value[^"]*"[^>]*>([\d,.]+)</span>',
            text, re.DOTALL
        )
        if not m_val:
            # 대안 패턴: 1,000~2,000 사이 환율 직접 탐색
            m_val = re.search(r'\b(1[,.]?\d{3}[.,]\d{2})\b', text)
        if not m_val:
            return None, None

        value = float(m_val.group(1).replace(",", ""))
        if not (900 < value < 2000):   # 환율 합리성 검증
            return None, None

        # 등락률
        m_pct = re.search(
            r'USD.*?<span[^>]+class="[^"]*rate[^"]*"[^>]*>.*?([\d.]+)%',
            text, re.DOTALL
        )
        pct = float(m_pct.group(1)) if m_pct else 0.0

        # 방향 (FIX-MKT-8: 숫자 suffix 대응)
        m_dir = re.search(r'USD.*?class="(up\d*|dn\d*|down\d*)"', text, re.DOTALL)
        raw_dir = (m_dir.group(1) if m_dir else "").lower()
        if "dn" in raw_dir or "down" in raw_dir:
            pct = -abs(pct)

        return value, pct
    except Exception as e:
        print(f"  [Naver] USD/KRW 조회 실패: {e}")
        return None, None


# ── 야간선물 수집 ─────────────────────────────────────────────────────────────

def _fetch_naver_night_future(symbol: str):
    """
    FIX-MKT-8: 야간선물 수집 전면 재작성.

    기존 polling API (심볼 K2FA001.N / KSFA001.N) 가 실제로 동작하지 않아
    네이버 증권 야간선물 개별 종목 페이지를 직접 파싱하는 방식으로 변경.

    symbol:
      "K2FA" → KOSPI200 야간선물
      "KSFA" → KOSDAQ150 야간선물

    야간선물은 KRX 야간시장(18:00~익일 05:00 KST)에만 거래됨.
    거래 시간 외에는 직전 거래 종가를 반환하거나 None 반환.
    """
    if not _REQUESTS_AVAILABLE:
        return None, None

    url = f"https://finance.naver.com/item/coinfo.naver?code={symbol}"
    try:
        resp = requests.get(url, headers=_NAVER_HEADERS, timeout=10)
        resp.raise_for_status()
        resp.encoding = "euc-kr"
        text = resp.text

        # 현재가: <em class="no_today"> 또는 <strong id="nowVal">
        m_val = re.search(
            r'<strong[^>]+id="[^"]*nowVal[^"]*"[^>]*>([\d,.]+)</strong>',
            text
        )
        if not m_val:
            m_val = re.search(
                r'class="[^"]*no_today[^"]*"[^>]*>.*?<em[^>]*>([\d,.]+)</em>',
                text, re.DOTALL
            )
        if not m_val:
            # 추가 패턴: 현재가 span
            m_val = re.search(
                r'<span[^>]+id="[^"]*today[^"]*"[^>]*>([\d,.]+)</span>',
                text
            )

        if not m_val:
            return None, None

        value = float(m_val.group(1).replace(",", ""))
        if value <= 0:
            return None, None

        # 등락률
        m_pct = re.search(
            r'<em[^>]+class="[^"]*em_[\w]+"[^>]*>.*?([\d.]+)%',
            text, re.DOTALL
        )
        if not m_pct:
            m_pct = re.search(r'([\d.]+)%', text)
        pct = float(m_pct.group(1)) if m_pct else 0.0

        # 방향
        m_dir = re.search(r'class="(up\d*|dn\d*|down\d*|blind)"', text)
        raw_dir = (m_dir.group(1) if m_dir else "").lower()
        if "dn" in raw_dir or "down" in raw_dir:
            pct = -abs(pct)

        return value, pct

    except Exception as e:
        print(f"  [야간선물] {symbol} 파싱 실패: {e}")
        return None, None


def _fetch_night_future_via_api(symbol: str):
    """
    네이버 실시간 API 방식으로 야간선물 조회 (보조 수단).
    symbol: "K2FA" 또는 "KSFA"
    """
    if not _REQUESTS_AVAILABLE:
        return None, None

    # 네이버 실시간 지수 API (국내 선물)
    api_url = (
        f"https://polling.finance.naver.com/api/realtime/domestic/futures/{symbol}"
    )
    try:
        resp = requests.get(api_url, headers=_NAVER_HEADERS, timeout=8)
        resp.raise_for_status()
        data = resp.json()

        # 응답 구조 탐색: result 또는 datas
        items = (data.get("result", {}).get("areas", [{}])[0].get("datas", [])
                 or data.get("datas", []))
        if not items:
            return None, None

        item = items[0]
        # 필드명 후보 탐색
        close_raw = (item.get("nv") or item.get("closePrice") or
                     item.get("price") or "")
        pct_raw   = (item.get("cr") or item.get("fluctuationsRatio") or
                     item.get("changeRate") or "0")

        close_str = str(close_raw).replace(",", "").replace("+", "").strip()
        pct_str   = str(pct_raw).replace(",", "").replace("+", "").strip()

        if not close_str or close_str in ("", "0", "-"):
            return None, None

        value = float(close_str)
        pct   = float(pct_str) if pct_str else 0.0
        return value, pct

    except Exception as e:
        print(f"  [야간선물API] {symbol} 조회 실패: {e}")
        return None, None


def _get_night_future(symbol: str, label: str):
    """
    야간선물 수집 통합 함수.
    1순위: 페이지 직접 파싱
    2순위: API 방식
    """
    val, pct = _fetch_naver_night_future(symbol)
    if val is not None:
        return val, pct

    print(f"  [{label}] 페이지 파싱 실패 → API 방식 시도")
    val, pct = _fetch_night_future_via_api(symbol)
    return val, pct


# ── 공개 API ──────────────────────────────────────────────────────────────────

def collect_market_overview() -> dict:
    print("\n[시장수집] 지표 수집 시작...")
    result    = {}
    premarket = _is_premarket()

    if premarket:
        print("  [장전/주말] 전일 종가 기준으로 표시")

    # ── KOSPI ─────────────────────────────────────────────────────────────────
    val, pct = _fetch_yf("^KS11")
    if val is None:
        val, pct = _fetch_naver_index("KOSPI")
    if val is not None:
        result["kospi"] = _make_indicator(val, pct, is_premarket=premarket)
        print(f"  KOSPI: {val:,.2f} ({pct:+.2f}%)"
              + (" [전일종가]" if premarket else ""))

    # ── KOSDAQ ────────────────────────────────────────────────────────────────
    val, pct = _fetch_yf("^KQ11")
    if val is None:
        val, pct = _fetch_naver_index("KOSDAQ")
    if val is not None:
        result["kosdaq"] = _make_indicator(val, pct, is_premarket=premarket)
        print(f"  KOSDAQ: {val:,.2f} ({pct:+.2f}%)"
              + (" [전일종가]" if premarket else ""))

    # ── NASDAQ ────────────────────────────────────────────────────────────────
    val, pct = _fetch_yf("^IXIC")
    if val is not None:
        result["nasdaq"] = _make_indicator(val, pct, is_premarket=False)
        print(f"  NASDAQ: {val:,.2f} ({pct:+.2f}%)")

    # ── S&P 500 ───────────────────────────────────────────────────────────────
    val, pct = _fetch_yf("^GSPC")
    if val is not None:
        result["sp500"] = _make_indicator(val, pct, is_premarket=False)
        print(f"  S&P500: {val:,.2f} ({pct:+.2f}%)")

    # ── 다우존스 ──────────────────────────────────────────────────────────────
    val, pct = _fetch_yf("^DJI")
    if val is not None:
        result["dow"] = _make_indicator(val, pct, is_premarket=False)
        print(f"  DOW: {val:,.2f} ({pct:+.2f}%)")

    # ── KOSPI200 야간선물 ──────────────────────────────────────────────────────
    # FIX-MKT-8: 페이지 파싱 우선, API 방식 폴백
    val, pct = _get_night_future("K2FA", "KOSPI200야간선물")
    if val is not None:
        result["kospi200_night"] = _make_indicator(val, pct)
        print(f"  KOSPI200 야간선물: {val:,.2f} ({pct:+.2f}%)")
    else:
        print("  KOSPI200 야간선물: 거래 시간 외 또는 데이터 없음 → 스킵")

    # ── KOSDAQ150 야간선물 ─────────────────────────────────────────────────────
    # FIX-MKT-8: 페이지 파싱 우선, API 방식 폴백
    val, pct = _get_night_future("KSFA", "KOSDAQ150야간선물")
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
        result["usd_krw"] = _make_indicator(val, pct, is_premarket=False)
        print(f"  USD/KRW: {val:,.2f} ({pct:+.2f}%)")

    if not result:
        print("  [경고] 모든 시장 지표 수집 실패")
    else:
        print(f"[시장수집] 완료 ({len(result)}개 지표)")

    return result
