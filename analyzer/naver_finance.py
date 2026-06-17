# analyzer/naver_finance.py
"""
네이버 금융 데이터 수집 헬퍼
- fetch_naver_stock_price      : 현재가 조회
- fetch_naver_company_info     : 기업 정보 (업종, 동종업체)
- fetch_naver_daily_prices     : 일별 주가 데이터
- generate_candlestick_base64  : 캔들스틱 차트 생성
- search_code_by_autocomplete  : 자동완성으로 종목코드 검색
- verify_stock_via_naver       : 종목 존재 여부 확인

수정 이력:
- FIX-PRICE-2 : fetch_naver_stock_price HTML 파싱 → JSON API 방식으로 교체
                1차: 네이버 금융 실시간 API, 2차: 일별시세 API 폴백
"""

import re
import base64
import io
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}


# ── 1. 현재가 조회 ────────────────────────────────────────────────────────────

def fetch_naver_stock_price(stock_name: str, code_override: str = "") -> dict | None:
    """
    네이버 금융 JSON API로 종목 현재가 조회.
    1차: 네이버 금융 실시간 시세 API
    2차: 일별시세 API (폴백)
    반환: {"name":str, "code":str, "price":int, "change":str,
           "change_pct":str, "naver_url":str}
    실패 시 None 반환.
    """
    import requests

    code = code_override.strip() if code_override else ""
    if not code:
        return None

    naver_url = f"https://finance.naver.com/item/main.naver?code={code}"

    # ── 1차: 네이버 금융 실시간 시세 JSON API ─────────────────────────────────
    try:
        api_url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
        resp = requests.get(api_url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        datas = data.get("datas", [])
        if datas:
            item      = datas[0]
            price_str = item.get("closePrice", "").replace(",", "").replace("+", "")
            change_str = item.get("fluctuation", "").replace(",", "").replace("+", "")
            pct_str   = item.get("fluctuationsRatio", "").replace(",", "").replace("+", "")

            if price_str and price_str.lstrip("-").isdigit():
                price_int = int(price_str)
                print(f"  [PRICE-API] {stock_name}({code}): {price_int:,}원")
                return {
                    "name":       stock_name,
                    "code":       code,
                    "price":      price_int,
                    "change":     change_str,
                    "change_pct": pct_str,
                    "naver_url":  naver_url,
                }
    except Exception as e:
        print(f"  [PRICE-API] {stock_name}({code}) 실시간 API 실패: {e}")

    # ── 2차: 일별시세 API 폴백 ────────────────────────────────────────────────
    try:
        daily_url = f"https://finance.naver.com/item/sise_day.naver?code={code}&page=1"
        resp = requests.get(daily_url, headers=_HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        text = resp.text

        # 일별시세 첫 번째 행의 종가를 현재가로 사용
        m = re.search(
            r'<tr[^>]*>\s*<td[^>]*>[\d\.]+</td>'
            r'\s*<td[^>]*><span[^>]*>([\d,]+)</span></td>',
            text, re.DOTALL
        )
        if m:
            price_int = int(m.group(1).replace(",", ""))
            print(f"  [PRICE-DAILY] {stock_name}({code}): {price_int:,}원 (전일종가)")
            return {
                "name":       stock_name,
                "code":       code,
                "price":      price_int,
                "change":     "",
                "change_pct": "",
                "naver_url":  naver_url,
            }
    except Exception as e:
        print(f"  [PRICE-DAILY] {stock_name}({code}) 일별시세 폴백 실패: {e}")

    print(f"  [PRICE] {stock_name}({code}) 모든 조회 방법 실패")
    return None


# ── 2. 기업 정보 조회 ─────────────────────────────────────────────────────────

def fetch_naver_company_info(code: str) -> dict:
    """
    네이버 금융에서 기업 업종·동종업체 정보 조회.
    반환: {"sector": str, "peers": list[str]}
    실패 시 빈 dict 반환.
    """
    import requests

    url = f"https://finance.naver.com/item/main.naver?code={code}"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        text = resp.text

        # 업종 파싱
        sector = ""
        m_sector = re.search(
            r'업종[^<]*</[^>]+>\s*<[^>]+>\s*<a[^>]+>([^<]+)</a>', text
        )
        if not m_sector:
            m_sector = re.search(r'같은업종보기[^>]*>([^<]{2,20})</a>', text)
        if m_sector:
            sector = m_sector.group(1).strip()

        # 동종업체 파싱 (같은 업종 상위 종목)
        peers = []
        peer_matches = re.findall(
            r'<a[^>]+href="/item/main\.naver\?code=\d+"[^>]*>([^<]{2,15})</a>',
            text
        )
        seen = set()
        for p in peer_matches:
            p = p.strip()
            if p and p not in seen and len(p) >= 2:
                seen.add(p)
                peers.append(p)
                if len(peers) >= 5:
                    break

        return {"sector": sector, "peers": peers}

    except Exception as e:
        print(f"  [기업정보] {code} 조회 실패: {e}")
        return {}


# ── 3. 일별 주가 데이터 조회 ──────────────────────────────────────────────────

def fetch_naver_daily_prices(code: str, days: int = 14) -> list[dict]:
    """
    네이버 금융 일별시세에서 OHLCV 데이터 조회.
    반환: [{"date":str, "open":int, "high":int, "low":int,
            "close":int, "volume":int}, ...]
    최신순 정렬, 최대 days개.
    실패 시 빈 리스트 반환.
    """
    import requests

    url = (
        f"https://finance.naver.com/item/sise_day.naver"
        f"?code={code}&page=1"
    )
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        text = resp.text

        rows = re.findall(
            r'<tr[^>]*>\s*<td[^>]*>([\d\.]+)</td>'
            r'\s*<td[^>]*><span[^>]*>([\d,]+)</span></td>'
            r'\s*<td[^>]*>.*?</td>'
            r'\s*<td[^>]*>.*?</td>'
            r'\s*<td[^>]*><span[^>]*>([\d,]+)</span></td>'
            r'\s*<td[^>]*><span[^>]*>([\d,]+)</span></td>'
            r'\s*<td[^>]*><span[^>]*>([\d,]+)</span></td>'
            r'\s*<td[^>]*><span[^>]*>([\d,]+)</span></td>',
            text, re.DOTALL
        )

        result = []
        for row in rows[:days]:
            try:
                date_str = row[0].strip()
                close    = int(row[1].replace(",", ""))
                open_    = int(row[2].replace(",", ""))
                high     = int(row[3].replace(",", ""))
                low      = int(row[4].replace(",", ""))
                volume   = int(row[5].replace(",", ""))
                result.append({
                    "date":   date_str,
                    "open":   open_,
                    "high":   high,
                    "low":    low,
                    "close":  close,
                    "volume": volume,
                })
            except (ValueError, IndexError):
                continue

        return result

    except Exception as e:
        print(f"  [일별시세] {code} 조회 실패: {e}")
        return []


# ── 4. 캔들스틱 차트 생성 ─────────────────────────────────────────────────────

def generate_candlestick_base64(daily_prices: list[dict], stock_name: str = "") -> str | None:
    """
    일별 주가 데이터로 캔들스틱 차트를 생성하여 base64 문자열 반환.
    matplotlib 미설치 시 None 반환.
    """
    if not daily_prices or len(daily_prices) < 2:
        return None

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError:
        print("  [차트] matplotlib 미설치 → 차트 생성 스킵")
        return None

    try:
        prices = list(reversed(daily_prices))

        fig, ax = plt.subplots(figsize=(8, 4))
        fig.patch.set_facecolor("#0d1117")
        ax.set_facecolor("#161b22")

        for i, d in enumerate(prices):
            o = d["open"]
            h = d["high"]
            l = d["low"]
            c = d["close"]
            color = "#ff6b6b" if c >= o else "#74c0fc"

            ax.plot([i, i], [l, h], color=color, linewidth=0.8, zorder=1)
            body_bottom = min(o, c)
            body_height = abs(c - o) or 1
            rect = mpatches.Rectangle(
                (i - 0.35, body_bottom), 0.7, body_height,
                facecolor=color, edgecolor=color, linewidth=0.5, zorder=2
            )
            ax.add_patch(rect)

        tick_step  = max(1, len(prices) // 5)
        tick_pos   = list(range(0, len(prices), tick_step))
        tick_label = [prices[i]["date"][-5:] for i in tick_pos]
        ax.set_xticks(tick_pos)
        ax.set_xticklabels(tick_label, color="#8b949e", fontsize=7)
        ax.tick_params(axis="y", colors="#8b949e", labelsize=7)
        ax.set_xlim(-0.5, len(prices) - 0.5)

        for spine in ax.spines.values():
            spine.set_edgecolor("#30363d")
        ax.yaxis.grid(True, color="#21262d", linewidth=0.5)

        if stock_name:
            ax.set_title(stock_name, color="#e6edf3", fontsize=9, pad=6)

        plt.tight_layout(pad=0.5)

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=100,
                    facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode("utf-8")

    except Exception as e:
        print(f"  [차트] {stock_name} 생성 실패: {e}")
        return None


# ── 5. 자동완성 종목코드 검색 ─────────────────────────────────────────────────

def search_code_by_autocomplete(stock_name: str) -> dict | None:
    """
    네이버 금융 자동완성 API로 종목코드 검색.
    반환: {"name": str, "code": str} 또는 None
    """
    import requests

    url = "https://ac.finance.naver.com/ac"
    params = {
        "q":     stock_name,
        "q_enc": "UTF-8",
        "st":    "111",
        "sug":   "all",
        "frm":   "stock",
    }
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=5)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items", [])
        if not items:
            return None

        for group in items:
            for item in group:
                if not isinstance(item, list) or len(item) < 2:
                    continue
                code = str(item[0]).strip()
                name = str(item[1]).strip()
                if code.isdigit() and len(code) == 6:
                    print(f"  [자동완성] '{stock_name}' → '{name}' ({code})")
                    return {"name": name, "code": code}

        return None

    except Exception as e:
        print(f"  [자동완성] {stock_name} 검색 실패: {e}")
        return None


# ── 6. 종목 존재 여부 확인 ────────────────────────────────────────────────────

def verify_stock_via_naver(stock_name: str) -> dict:
    """
    네이버 금융 자동완성으로 종목 존재 여부 확인.
    반환: {"verified": bool, "code": str, "name": str}
    """
    result = search_code_by_autocomplete(stock_name)
    if result and result.get("code"):
        return {
            "verified": True,
            "code":     result["code"],
            "name":     result.get("name", stock_name),
        }
    return {"verified": False, "code": "", "name": stock_name}
