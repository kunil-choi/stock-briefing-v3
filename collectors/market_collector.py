# collectors/market_collector.py
"""
시장 지표 수집기

수정 이력:
- FIX-MKT-1  : 반환 딕셔너리 키를 html_generator._INDICATOR_DEFS와 일치하도록 통일
- FIX-MKT-2  : FinanceDataReader 의존 제거, yfinance 우선 / 네이버 폴백
- FIX-MKT-3  : collect_market_overview() 함수 내 들여쓰기 버그 수정
- FIX-MKT-4  : KOSPI/KOSDAQ도 yfinance 우선으로 변경
- BUG-8 FIX  : 야간선물 수집 전면 재작성
- FIX-MKT-5  : 장 시작 전(09:00 KST 이전)에는 전일 종가 + "전일종가" 라벨 표시
- FIX-MKT-6  : _is_premarket() 주말(토·일) 처리 추가
- FIX-MKT-7  : 나스닥/S&P500/다우존스/달러원은 is_premarket=False 고정
- FIX-MKT-8  : 야간선물 수집 재수정 (페이지 파싱 + API 폴백)
- FIX-MKT-9  : 야간선물 이전 종가 캐시 추가
               06:00 KST 워크플로우 실행 시 KRX 야간시장(18:00~05:00)이
               이미 종료되어 있으므로, 전일 야간선물 최종가를 캐시 파일로
               보관하여 새벽시장 섹션에 표시
               캐시 파일: data/night_future_cache.json
               유효 기간: 당일(KST 날짜 기준)
"""

import re
import json
import os
import math
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# 야간선물 캐시 파일 경로
_NIGHT_FUTURE_CACHE = "data/night_future_cache.json"

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
    """현재 시각이 장 시작(09:00 KST) 이전인지 확인. 토·일은 항상 True."""
    now = datetime.now(KST)
    if now.weekday() >= 5:
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
        c, p = float(current), float(previous)
        if p == 0:
            return 0.0
        return round((c - p) / p * 100, 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


# ── yfinance 기반 조회 ────────────────────────────────────────────────────────

def _fetch_yf(ticker: str):
    """
    FIX-MKT-11: yfinance가 당일 미개장(특히 미국장 개장 전) 구간에
    NaN 종가를 포함한 placeholder 행을 반환하는 경우가 있음.
    NaN을 그대로 통과시키면 화면에 "nan"으로 표출되므로,
    NaN인 경우 수집 실패(None, None)로 명확히 처리한다.
    """
    if not _YF_AVAILABLE:
        return None, None
    try:
        tk   = yf.Ticker(ticker)
        hist = tk.history(period="5d")
        if hist.empty or len(hist) < 2:
            return None, None
        close_prev = float(hist["Close"].iloc[-2])
        close_now  = float(hist["Close"].iloc[-1])
        if math.isnan(close_prev) or math.isnan(close_now):
            return None, None
        return close_now, _pct(close_now, close_prev)
    except Exception as e:
        print(f"  [yfinance] {ticker} 조회 실패: {e}")
        return None, None


# ── 네이버 금융 폴백 ──────────────────────────────────────────────────────────

def _fetch_naver_index(symbol: str):
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
    if not _REQUESTS_AVAILABLE:
        return None, None
    url = "https://finance.naver.com/marketindex/"
    try:
        resp = requests.get(url, headers=_NAVER_HEADERS, timeout=10)
        resp.raise_for_status()
        resp.encoding = "euc-kr"
        text = resp.text

        m_val = re.search(
            r'USD.*?<span[^>]+class="[^"]*value[^"]*"[^>]*>([\d,.]+)</span>',
            text, re.DOTALL
        )
        if not m_val:
            m_val = re.search(r'\b(1[,.]?\d{3}[.,]\d{2})\b', text)
        if not m_val:
            return None, None

        value = float(m_val.group(1).replace(",", ""))
        if not (900 < value < 2000):
            return None, None

        m_pct = re.search(
            r'USD.*?<span[^>]+class="[^"]*rate[^"]*"[^>]*>.*?([\d.]+)%',
            text, re.DOTALL
        )
        pct = float(m_pct.group(1)) if m_pct else 0.0

        m_dir   = re.search(r'USD.*?class="(up\d*|dn\d*|down\d*)"', text, re.DOTALL)
        raw_dir = (m_dir.group(1) if m_dir else "").lower()
        if "dn" in raw_dir or "down" in raw_dir:
            pct = -abs(pct)

        return value, pct
    except Exception as e:
        print(f"  [Naver] USD/KRW 조회 실패: {e}")
        return None, None


# ── 야간선물 캐시 (FIX-MKT-9) ────────────────────────────────────────────────

def _load_night_future_cache() -> dict:
    """
    야간선물 캐시 파일을 로드한다.
    당일(KST) 데이터가 있으면 반환, 없으면 빈 dict.
    """
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    if not os.path.exists(_NIGHT_FUTURE_CACHE):
        return {}
    try:
        with open(_NIGHT_FUTURE_CACHE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        if cache.get("date") == today_str:
            return cache.get("data", {})
    except Exception:
        pass
    return {}


def _save_night_future_cache(data: dict) -> None:
    """야간선물 데이터를 오늘 날짜와 함께 캐시 파일에 저장."""
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    os.makedirs("data", exist_ok=True)
    try:
        with open(_NIGHT_FUTURE_CACHE, "w", encoding="utf-8") as f:
            json.dump({"date": today_str, "data": data}, f, ensure_ascii=False)
    except Exception as e:
        print(f"  [야간선물캐시] 저장 실패: {e}")


# ── 야간선물 수집 ─────────────────────────────────────────────────────────────

def _fetch_naver_night_future(symbol: str):
    """
    네이버 증권 야간선물 개별 종목 페이지를 파싱하여 (value, pct)를 반환.
    야간시장 거래 시간(18:00~05:00 KST) 외에는 None, None 반환.
    """
    if not _REQUESTS_AVAILABLE:
        return None, None

    url = f"https://finance.naver.com/item/coinfo.naver?code={symbol}"
    try:
        resp = requests.get(url, headers=_NAVER_HEADERS, timeout=10)
        resp.raise_for_status()
        resp.encoding = "euc-kr"
        text = resp.text

        # 현재가 탐색
        m_val = re.search(
            r'<strong[^>]+id="[^"]*nowVal[^"]*"[^>]*>([\d,.]+)</strong>', text
        )
        if not m_val:
            m_val = re.search(
                r'class="[^"]*no_today[^"]*"[^>]*>.*?<em[^>]*>([\d,.]+)</em>',
                text, re.DOTALL
            )
        if not m_val:
            m_val = re.search(
                r'<span[^>]+id="[^"]*today[^"]*"[^>]*>([\d,.]+)</span>', text
            )
        if not m_val:
            return None, None

        value = float(m_val.group(1).replace(",", ""))
        if value <= 0:
            return None, None

        # 등락률
        m_pct = re.search(
            r'<em[^>]+class="[^"]*em_[\w]+"[^>]*>.*?([\d.]+)%', text, re.DOTALL
        )
        if not m_pct:
            m_pct = re.search(r'([\d.]+)%', text)
        pct = float(m_pct.group(1)) if m_pct else 0.0

        # 방향
        m_dir   = re.search(r'class="(up\d*|dn\d*|down\d*|blind)"', text)
        raw_dir = (m_dir.group(1) if m_dir else "").lower()
        if "dn" in raw_dir or "down" in raw_dir:
            pct = -abs(pct)

        return value, pct
    except Exception as e:
        print(f"  [야간선물] {symbol} 파싱 실패: {e}")
        return None, None


def _fetch_night_future_via_api(symbol: str):
    """네이버 실시간 API 방식으로 야간선물 조회 (보조 수단)."""
    if not _REQUESTS_AVAILABLE:
        return None, None

    api_url = (
        f"https://polling.finance.naver.com/api/realtime/domestic/futures/{symbol}"
    )
    try:
        resp = requests.get(api_url, headers=_NAVER_HEADERS, timeout=8)
        resp.raise_for_status()
        data  = resp.json()
        items = (data.get("result", {}).get("areas", [{}])[0].get("datas", [])
                 or data.get("datas", []))
        if not items:
            return None, None

        item      = items[0]
        close_raw = (item.get("nv") or item.get("closePrice") or
                     item.get("price") or "")
        pct_raw   = (item.get("cr") or item.get("fluctuationsRatio") or
                     item.get("changeRate") or "0")

        close_str = str(close_raw).replace(",", "").replace("+", "").strip()
        pct_str   = str(pct_raw).replace(",", "").replace("+", "").strip()

        if not close_str or close_str in ("", "0", "-"):
            return None, None

        return float(close_str), float(pct_str) if pct_str else 0.0
    except Exception as e:
        print(f"  [야간선물API] {symbol} 조회 실패: {e}")
        return None, None


def _get_night_future(symbol: str, label: str):
    """
    야간선물 수집 통합 함수.
    1순위: 페이지 직접 파싱
    2순위: API 방식
    실패 시 None, None 반환.
    """
    val, pct = _fetch_naver_night_future(symbol)
    if val is not None:
        return val, pct

    print(f"  [{label}] 페이지 파싱 실패 → API 방식 시도")
    val, pct = _fetch_night_future_via_api(symbol)
    return val, pct


# ── 공개 API ──────────────────────────────────────────────────────────────────

def collect_market_overview() -> dict:
    """
    시장 지표 수집.

    FIX-MKT-9  : 야간선물 캐시 로직 추가.
                 KRX 야간선물 거래시간: 18:00~익일 05:00 KST
                 06:00 워크플로우 실행 시 이미 거래 종료 → 전일 캐시 데이터 사용.
                 캐시 저장 시점: 야간선물 실시간 데이터 수집 성공 시 즉시 저장.
                 캐시 유효 기간: 당일(KST 날짜 기준).
    FIX-MKT-10 : 모든 지표를 항상 result에 포함 (수집 실패 시 value=None).
                 → html_generator가 "데이터 없음" 카드로라도 항상 표출하도록 함.
                 (기존엔 수집 실패 시 키 자체가 없어 카드가 통째로 사라졌음)
    """
    print("\n[시장수집] 지표 수집 시작...")
    result    = {}
    premarket = _is_premarket()

    if premarket:
        print("  [장전/주말] 전일 종가 기준으로 표시")

    def _set(key: str, label: str, val, pct, is_pre: bool = False) -> None:
        """FIX-MKT-10: 성공/실패 모두 result[key]를 채운다 (실패 시 value=None)."""
        if val is not None:
            result[key] = _make_indicator(val, pct, is_premarket=is_pre)
            suffix = " [전일종가]" if is_pre else ""
            print(f"  {label}: {val:,.2f} ({pct:+.2f}%){suffix}")
        else:
            result[key] = _make_indicator(None, 0.0, direction="flat", is_premarket=is_pre)
            print(f"  {label}: 데이터 없음")

    # ── KOSPI ─────────────────────────────────────────────────────────────────
    val, pct = _fetch_yf("^KS11")
    if val is None:
        val, pct = _fetch_naver_index("KOSPI")
    _set("kospi", "KOSPI", val, pct, premarket)

    # ── KOSDAQ ────────────────────────────────────────────────────────────────
    val, pct = _fetch_yf("^KQ11")
    if val is None:
        val, pct = _fetch_naver_index("KOSDAQ")
    _set("kosdaq", "KOSDAQ", val, pct, premarket)

    # ── NASDAQ ────────────────────────────────────────────────────────────────
    val, pct = _fetch_yf("^IXIC")
    _set("nasdaq", "NASDAQ", val, pct, False)

    # ── S&P 500 ───────────────────────────────────────────────────────────────
    val, pct = _fetch_yf("^GSPC")
    _set("sp500", "S&P500", val, pct, False)

    # ── 다우존스 ──────────────────────────────────────────────────────────────
    val, pct = _fetch_yf("^DJI")
    _set("dow", "DOW", val, pct, False)

    # ── KOSPI200 야간선물 (FIX-MKT-9/10: 캐시 + 항상 표출) ───────────────────
    night_cache = _load_night_future_cache()

    val, pct = _get_night_future("K2FA", "KOSPI200야간선물")
    if val is not None:
        result["kospi200_night"] = _make_indicator(val, pct)
        print(f"  KOSPI200 야간선물: {val:,.2f} ({pct:+.2f}%) [실시간]")
        night_cache["kospi200_night"] = {"value": val, "change_pct": pct}
        _save_night_future_cache(night_cache)
    elif night_cache.get("kospi200_night"):
        cached = night_cache["kospi200_night"]
        c_val  = cached.get("value", 0)
        c_pct  = cached.get("change_pct", 0.0)
        result["kospi200_night"] = _make_indicator(c_val, c_pct)
        print(f"  KOSPI200 야간선물: {c_val:,.2f} ({c_pct:+.2f}%) [캐시]")
    else:
        result["kospi200_night"] = _make_indicator(None, 0.0, direction="flat")
        print("  KOSPI200 야간선물: 데이터 없음 (거래 시간 외 + 캐시 없음)")

    # ── KOSDAQ150 야간선물 (FIX-MKT-9/10: 캐시 + 항상 표출) ──────────────────
    val, pct = _get_night_future("KSFA", "KOSDAQ150야간선물")
    if val is not None:
        result["kosdaq150_night"] = _make_indicator(val, pct)
        print(f"  KOSDAQ150 야간선물: {val:,.2f} ({pct:+.2f}%) [실시간]")
        night_cache["kosdaq150_night"] = {"value": val, "change_pct": pct}
        _save_night_future_cache(night_cache)
    elif night_cache.get("kosdaq150_night"):
        cached = night_cache["kosdaq150_night"]
        c_val  = cached.get("value", 0)
        c_pct  = cached.get("change_pct", 0.0)
        result["kosdaq150_night"] = _make_indicator(c_val, c_pct)
        print(f"  KOSDAQ150 야간선물: {c_val:,.2f} ({c_pct:+.2f}%) [캐시]")
    else:
        result["kosdaq150_night"] = _make_indicator(None, 0.0, direction="flat")
        print("  KOSDAQ150 야간선물: 데이터 없음 (거래 시간 외 + 캐시 없음)")

    # ── USD/KRW ───────────────────────────────────────────────────────────────
    val, pct = _fetch_yf("KRW=X")
    if val is None:
        val, pct = _fetch_naver_forex()
    _set("usd_krw", "USD/KRW", val, pct, False)

    available = sum(1 for v in result.values() if v.get("value") is not None)
    print(f"[시장수집] 완료 (전체 {len(result)}개 지표 / 실제 수집 {available}개)")

    return result
