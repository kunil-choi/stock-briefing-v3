# analyzer/naver_finance.py
# FIX-PRICE-1: HTML 파싱 → Naver JSON API 우선, sise_day 폴백
# FIX-PRICE-2: 주가 단위 오류 방지 (원 단위 정수 반환)
# FIX-SISE-1 : sise_day 정규식 그룹 인덱스 오류 수정
#              (m[2]전일비 스킵 → m[3]시가 올바르게 매핑)
# FIX-PRICE-5: 한국 주식시장 프리마켓 없음 반영
#              09:00 이전 → Naver API 반환값 = 전일 종가
#              price_label 결정은 ai_analyzer(호출부)에서 담당
#              이 함수는 가격 값만 정확하게 반환
# FIX-PRICE-6: API closePrice=0 또는 누락 시 추가 키 탐색 강화
#              prevClosePrice, stockEndPrice 순으로 폴백
#              prevClosePrice 폴백 시 change/change_pct는 0으로 강제
#              (의미 혼동 방지 — 어제 종가에 오늘 등락률 붙이지 않음)
# FIX-API-2  : Naver Stock API 응답 구조 변화 대응
#              stockPrice 중첩 객체 내 키도 탐색

import re
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}


def _get(url: str, timeout: int = 10) -> str:
    """공통 HTTP GET 헬퍼. 실패 시 빈 문자열 반환."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"[naver_finance] GET 실패 {url}: {e}")
        return ""


def _get_json(url: str, timeout: int = 10):
    """JSON GET 헬퍼. 실패 시 None 반환."""
    raw = _get(url, timeout)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _parse_int(value) -> int:
    """
    콤마·공백·부호 문자를 제거하고 정수로 변환.
    변환 실패 시 0 반환.
    """
    try:
        return int(
            str(value).replace(",", "").replace(" ", "").replace("+", "")
        )
    except (ValueError, TypeError):
        return 0


def _parse_float(value) -> float:
    """
    콤마·공백·%·부호 문자를 제거하고 float으로 변환.
    변환 실패 시 0.0 반환.
    """
    try:
        return float(
            str(value).replace("%", "").replace("+", "")
                      .replace(",", "").replace(" ", "")
        )
    except (ValueError, TypeError):
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 종목 코드 조회
# ─────────────────────────────────────────────────────────────────────────────

def search_code_by_autocomplete(stock_name: str) -> dict:
    """자동완성 API로 종목명 → 코드 변환. 실패 시 None 반환."""
    enc = urllib.parse.quote(stock_name)
    url = (
        f"https://ac.finance.naver.com/ac?"
        f"q={enc}&q_enc=UTF-8&st=111&sug=all&frm=stock"
    )
    raw = _get(url)
    try:
        data  = json.loads(raw)
        items = data.get("items", [[]])[0]
        for item in items:
            # item 형식: [name, code, ...]
            if len(item) >= 2:
                code = str(item[1])
                if re.match(r"^\d{6}$", code):
                    return {"name": item[0], "code": code}
    except Exception:
        pass
    return None


def verify_stock_via_naver(stock_name: str) -> dict:
    result = search_code_by_autocomplete(stock_name)
    if result:
        return {"verified": True, "code": result["code"], "name": result["name"]}
    return {"verified": False, "code": "", "name": stock_name}


# ─────────────────────────────────────────────────────────────────────────────
# API 응답 파싱 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def _extract_price_from_api(data: dict) -> tuple:
    """
    FIX-PRICE-6 / FIX-API-2:
    Naver Stock API JSON에서 price, change, change_pct를 추출한다.

    탐색 순서 (price):
      1. data["closePrice"]           → 정규장 현재가 / 장 전 전일종가
      2. data["stockPrice"]["closePrice"]
      3. data["stockEndPrice"]        → FIX-API-2: 구조 변화 대응
      4. data["stockPrice"]["stockEndPrice"]
      5. data["prevClosePrice"]       → FIX-PRICE-6: 위 모두 0일 때 폴백
      6. data["stockPrice"]["prevClosePrice"]

    탐색 순서 (change / change_pct):
      - 1~4번 경로로 price를 얻은 경우만 조회
      - prevClosePrice 폴백 시에는 change=0, change_pct=0.0 강제
        (어제 종가에 오늘 등락률을 붙이는 의미 혼동 방지)

    반환: (price: int, change: int, change_pct: float)
    price == 0 이면 조회 실패로 간주.
    """
    sp = data.get("stockPrice", {}) or {}

    # ── 1단계: closePrice / stockEndPrice 우선 탐색 ──────────────────────
    price         = 0
    used_fallback = False

    for key in ("closePrice", "stockEndPrice"):
        raw = data.get(key) or sp.get(key)
        if raw:
            price = _parse_int(raw)
            if price > 0:
                break

    # ── 2단계: 위 모두 0이면 prevClosePrice 폴백 ─────────────────────────
    if price == 0:
        for key in ("prevClosePrice",):
            raw = data.get(key) or sp.get(key)
            if raw:
                price = _parse_int(raw)
                if price > 0:
                    used_fallback = True
                    break

    # ── change / change_pct ───────────────────────────────────────────────
    # prevClosePrice 폴백 시에는 등락 정보가 의미 없으므로 0으로 강제
    change     = 0
    change_pct = 0.0

    if not used_fallback and price > 0:
        for key in ("compareToPreviousClosePrice",):
            raw = data.get(key) or sp.get(key)
            if raw is not None:
                change = _parse_int(raw)
                break
        for key in ("fluctuationsRatio",):
            raw = data.get(key) or sp.get(key)
            if raw is not None:
                change_pct = _parse_float(raw)
                break

    return price, change, change_pct


# ─────────────────────────────────────────────────────────────────────────────
# 현재가 조회
# ─────────────────────────────────────────────────────────────────────────────

def fetch_naver_stock_price(stock_name: str, code_override: str = "") -> dict:
    """
    종목 현재가(또는 전일종가)를 조회한다.

    FIX-PRICE-5:
      한국 주식시장은 프리마켓이 없다.
      - 09:00 이전: Naver API closePrice = 전일 종가
      - 09:00 이후: closePrice = 정규장 현재가
      price_label 결정은 ai_analyzer.py(호출부)에서 담당.
      이 함수는 가격 값만 올바르게 반환한다.

    우선순위:
      1) api.stock.naver.com/stock/{code}/basic  (JSON)
      2) sise_day 일별 데이터의 최신 종가          (폴백)

    반환:
      {"name": str, "code": str, "price": int,
       "change": int, "change_pct": float, "url": str}
      실패 시 None.
    """
    # 1. 코드 확보
    code = code_override.strip()
    if not code:
        result = search_code_by_autocomplete(stock_name)
        if not result:
            print(f"[naver_finance] 코드 조회 실패: {stock_name}")
            return None
        code       = result["code"]
        stock_name = result.get("name", stock_name)

    naver_url = f"https://finance.naver.com/item/main.naver?code={code}"

    # 2. [1순위] Naver Stock API (JSON)
    api_url = f"https://api.stock.naver.com/stock/{code}/basic"
    data    = _get_json(api_url)

    if data:
        try:
            price, change, change_pct = _extract_price_from_api(data)
            if price > 0:
                print(
                    f"[naver_finance] {stock_name}({code}): "
                    f"{price:,}원 ({change_pct:+.2f}%) [API]"
                )
                return {
                    "name":       stock_name,
                    "code":       code,
                    "price":      price,
                    "change":     change,
                    "change_pct": change_pct,
                    "url":        naver_url,
                }
        except Exception as e:
            print(f"[naver_finance] API 파싱 오류 ({stock_name}): {e}")

    # 3. [2순위] sise_day 일별 데이터 최신 종가
    print(f"[naver_finance] {stock_name}: API 폴백 → sise_day 사용")
    daily = fetch_naver_daily_prices(code, days=1)
    if daily:
        price = daily[0].get("close", 0)
        if price > 0:
            print(
                f"[naver_finance] {stock_name}({code}): "
                f"{price:,}원 [sise_day]"
            )
            return {
                "name":       stock_name,
                "code":       code,
                "price":      price,
                "change":     0,
                "change_pct": 0.0,
                "url":        naver_url,
            }

    print(f"[naver_finance] 현재가 조회 최종 실패: {stock_name}({code})")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 기업 정보 / 일별 시세
# ─────────────────────────────────────────────────────────────────────────────

def fetch_naver_company_info(code: str) -> dict:
    """섹터 및 동종업종 상위 5개 기업명 반환."""
    url  = f"https://finance.naver.com/item/main.naver?code={code}"
    html = _get(url)
    sector = ""
    peers  = []
    try:
        m = re.search(r'업종</th>\s*<td[^>]*>([^<]+)', html)
        if m:
            sector = m.group(1).strip()
        peers = re.findall(r'<a[^>]+etf_compare[^>]*>([^<]+)</a>', html)[:5]
    except Exception:
        pass
    return {"sector": sector, "peers": peers}


def fetch_naver_daily_prices(code: str, days: int = 14) -> list:
    """
    sise_day에서 일별 OHLCV 데이터 반환 (최신순).

    네이버 sise_day 컬럼 순서: 날짜 / 종가 / 전일비 / 시가 / 고가 / 저가 / 거래량
    정규식 그룹 인덱스:          [0]    [1]    [2]     [3]   [4]   [5]   [6]

    FIX-SISE-1: 전일비([2])가 부호 문자(▲▼)를 포함할 수 있어
                숫자만 추출하도록 느슨한 패턴 사용.
    """
    url  = f"https://finance.naver.com/item/sise_day.naver?code={code}&page=1"
    html = _get(url)
    rows = []
    try:
        pattern = (
            r'<td[^>]*>\s*(\d{4}\.\d{2}\.\d{2})\s*</td>'   # [0] 날짜
            r'.*?<td[^>]*>\s*([\d,]+)\s*</td>'              # [1] 종가
            r'.*?<td[^>]*>[^<]*?([\d,]+)[^<]*</td>'         # [2] 전일비 (숫자만)
            r'.*?<td[^>]*>\s*([\d,]+)\s*</td>'              # [3] 시가
            r'.*?<td[^>]*>\s*([\d,]+)\s*</td>'              # [4] 고가
            r'.*?<td[^>]*>\s*([\d,]+)\s*</td>'              # [5] 저가
            r'.*?<td[^>]*>\s*([\d,]+)\s*</td>'              # [6] 거래량
        )
        matches = re.findall(pattern, html, re.DOTALL)
        for m in matches[:days]:
            rows.append({
                "date":   m[0],
                "close":  int(m[1].replace(",", "")),
                "open":   int(m[3].replace(",", "")),
                "high":   int(m[4].replace(",", "")),
                "low":    int(m[5].replace(",", "")),
                "volume": int(m[6].replace(",", "")),
            })
    except Exception as e:
        print(f"[naver_finance] sise_day 파싱 오류 ({code}): {e}")
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# 캔들차트 생성
# ─────────────────────────────────────────────────────────────────────────────

def generate_candlestick_base64(daily_prices: list, stock_name: str = "") -> str:
    """캔들차트 PNG → base64 문자열. 실패 시 None 반환."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import base64
        from io import BytesIO
    except ImportError:
        return None

    if not daily_prices or len(daily_prices) < 2:
        return None

    try:
        prices = list(reversed(daily_prices))  # 오래된 날짜 → 최신 순
        fig, ax = plt.subplots(figsize=(8, 4))
        fig.patch.set_facecolor("#1e1e2e")
        ax.set_facecolor("#1e1e2e")

        for i, row in enumerate(prices):
            o, h, l, c = row["open"], row["high"], row["low"], row["close"]
            color = "#ef5350" if c >= o else "#26a69a"
            ax.plot([i, i], [l, h], color=color, linewidth=1)
            ax.add_patch(mpatches.FancyBboxPatch(
                (i - 0.3, min(o, c)), 0.6, abs(c - o),
                boxstyle="square,pad=0", color=color
            ))

        # 날짜 레이블 (최대 5개)
        step = max(1, len(prices) // 5)
        ax.set_xticks(range(0, len(prices), step))
        ax.set_xticklabels(
            [prices[i]["date"][5:] for i in range(0, len(prices), step)],
            color="#aaaaaa", fontsize=8
        )
        ax.tick_params(colors="#aaaaaa")
        for spine in ax.spines.values():
            spine.set_edgecolor("#444444")
        ax.set_title(stock_name, color="#ffffff", fontsize=10)
        plt.tight_layout()

        buf = BytesIO()
        plt.savefig(buf, format="png", dpi=100, facecolor=fig.get_facecolor())
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode()
    except Exception as e:
        print(f"[naver_finance] 캔들차트 생성 오류: {e}")
        return None
