# analyzer/naver_finance.py
# FIX-PRICE-1: HTML 파싱 → Naver JSON API 우선, sise_day 폴백
# FIX-PRICE-2: 주가 단위 오류 방지 (원 단위 정수 반환)

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


# ─────────────────────────────────────────────────────────────────────────────
# 종목 코드 조회
# ─────────────────────────────────────────────────────────────────────────────

def search_code_by_autocomplete(stock_name: str) -> dict:
    """자동완성 API로 종목명 → 코드 변환. 실패 시 None."""
    enc = urllib.parse.quote(stock_name)
    url = (
        f"https://ac.finance.naver.com/ac?"
        f"q={enc}&q_enc=UTF-8&st=111&sug=all&frm=stock"
    )
    raw = _get(url)
    try:
        data = json.loads(raw)
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
# 현재가 조회  ★ 핵심 수정
# ─────────────────────────────────────────────────────────────────────────────

def fetch_naver_stock_price(stock_name: str, code_override: str = "") -> dict:
    """
    종목 현재가를 조회한다.

    우선순위:
      1) api.stock.naver.com/stock/{code}/basic  (JSON, 장중/장후 모두 동작)
      2) sise_day 일별 데이터의 최신 종가 (폴백)

    반환:
      {"name": str, "code": str, "price": int, "change": int,
       "change_pct": float, "url": str}
      실패 시 None.
    """
    # 1. 코드 확보
    code = code_override.strip()
    if not code:
        result = search_code_by_autocomplete(stock_name)
        if not result:
            print(f"[naver_finance] 코드 조회 실패: {stock_name}")
            return None
        code = result["code"]
        stock_name = result.get("name", stock_name)

    naver_url = f"https://finance.naver.com/item/main.naver?code={code}"

    # 2. [1순위] Naver Stock API (JSON)
    api_url = f"https://api.stock.naver.com/stock/{code}/basic"
    data = _get_json(api_url)
    if data:
        try:
            # closePrice / compareToPreviousClosePrice / fluctuationsRatio
            price_raw = data.get("closePrice") or data.get("stockPrice", {}).get("closePrice", "")
            change_raw = (
                data.get("compareToPreviousClosePrice")
                or data.get("stockPrice", {}).get("compareToPreviousClosePrice", "0")
            )
            pct_raw = (
                data.get("fluctuationsRatio")
                or data.get("stockPrice", {}).get("fluctuationsRatio", "0.00")
            )
            # 콤마 제거 후 정수 변환
            price = int(str(price_raw).replace(",", "").replace(" ", ""))
            change = int(str(change_raw).replace(",", "").replace(" ", "").replace("+", ""))
            pct = float(str(pct_raw).replace("%", "").replace("+", "").replace(",", ""))

            if price > 0:
                print(f"[naver_finance] {stock_name}({code}): {price:,}원 ({pct:+.2f}%) [API]")
                return {
                    "name": stock_name,
                    "code": code,
                    "price": price,
                    "change": change,
                    "change_pct": pct,
                    "url": naver_url,
                }
        except Exception as e:
            print(f"[naver_finance] API 파싱 오류 ({stock_name}): {e}")

    # 3. [2순위] sise_day 일별 데이터 최신 종가
    print(f"[naver_finance] {stock_name}: API 폴백 → sise_day 사용")
    daily = fetch_naver_daily_prices(code, days=1)
    if daily:
        row = daily[0]
        price = row.get("close", 0)
        if price > 0:
            print(f"[naver_finance] {stock_name}({code}): {price:,}원 [sise_day]")
            return {
                "name": stock_name,
                "code": code,
                "price": price,
                "change": 0,
                "change_pct": 0.0,
                "url": naver_url,
            }

    print(f"[naver_finance] 현재가 조회 최종 실패: {stock_name}({code})")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 기업 정보 / 일별 시세
# ─────────────────────────────────────────────────────────────────────────────

def fetch_naver_company_info(code: str) -> dict:
    """섹터 및 동종업종 상위 5개 기업명 반환."""
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    html = _get(url)
    sector = ""
    peers = []
    try:
        m = re.search(r'업종</th>\s*<td[^>]*>([^<]+)', html)
        if m:
            sector = m.group(1).strip()
        peers = re.findall(r'<a[^>]+etf_compare[^>]*>([^<]+)</a>', html)[:5]
    except Exception:
        pass
    return {"sector": sector, "peers": peers}


def fetch_naver_daily_prices(code: str, days: int = 14) -> list:
    """sise_day에서 일별 OHLCV 데이터 반환 (최신순)."""
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}&page=1"
    html = _get(url)
    rows = []
    try:
        pattern = (
            r'<td[^>]*>\s*(\d{4}\.\d{2}\.\d{2})\s*</td>'
            r'.*?<td[^>]*>\s*([\d,]+)\s*</td>'   # 종가
            r'.*?<td[^>]*>\s*([\d,]+)\s*</td>'   # 전일비
            r'.*?<td[^>]*>\s*([\d,]+)\s*</td>'   # 시가
            r'.*?<td[^>]*>\s*([\d,]+)\s*</td>'   # 고가
            r'.*?<td[^>]*>\s*([\d,]+)\s*</td>'   # 저가
            r'.*?<td[^>]*>\s*([\d,]+)\s*</td>'   # 거래량
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
    """캔들차트 PNG → base64 문자열. 실패 시 None."""
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
