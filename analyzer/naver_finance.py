# analyzer/naver_finance.py
"""
네이버 금융 데이터 수집 헬퍼
- fetch_naver_stock_price      : 현재가 조회
- fetch_naver_company_info     : 기업 정보 (업종, 동종업체)
- fetch_naver_daily_prices     : 일별 주가 데이터
- generate_candlestick_base64  : 캔들스틱 차트 생성
- search_code_by_autocomplete  : 자동완성으로 종목코드 검색
- verify_stock_via_naver       : 종목 존재 여부 확인
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
    네이버 금융에서 종목 현재가 조회.
    반환: {"name":str, "code":str, "price":int, "change":str,
           "change_pct":str, "naver_url":str}
    실패 시 None 반환.
    """
    import requests

    code = code_override.strip() if code_override else ""
    if not code:
        return None

    naver_url = f"https://finance.naver.com/item/main.naver?code={code}"

    try:
        resp = requests.get(naver_url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        text = resp.text

        # 현재가 파싱 (여러 패턴 순차 시도)
        price_int = None
        patterns = [
            r'<p[^>]+class="[^"]*no_today[^"]*"[^>]*>.*?<span[^>]+class="[^"]*blind[^"]*"[^>]*>([\d,]+)',
            r'<strong[^>]+id="stock_price"[^>]*>([\d,]+)',
            r'"현재가"\s*:\s*"?([\d,]+)',
            r'<dd[^>]*>\s*현재가\s*</dd>\s*<dd[^>]*>([\d,]+)',
            r'<strong[^>]*>([\d]{3,6}(?:,[\d]{3})*)</strong>',
        ]
        for pattern in patterns:
            m = re.search(pattern, text, re.DOTALL)
            if m:
                raw = m.group(1).replace(",", "")
                if raw.isdigit():
                    price_int = int(raw)
                    break

        if price_int is None:
            print(f"  [PRICE] {stock_name}({code}) 가격 파싱 실패")
            return None

        m_change   = re.search(r'<em[^>]+id="changeContents"[^>]*>.*?([\d,]+)', text, re.DOTALL)
        change_str = m_change.group(1).replace(",", "") if m_change else ""

        m_pct   = re.search(r'<span[^>]+class="[^"]*rate[^"]*"[^>]*>.*?([\d\.]+)%', text, re.DOTALL)
        pct_str = m_pct.group(1) if m_pct else ""

        print(f"  [PRICE] {stock_name}({code}): {price_int:,}원")
        return {
            "name":       stock_name,
            "code":       code,
            "price":      price_int,
            "change":     change_str,
            "change_pct": pct_str,
            "naver_url":  naver_url,
        }

    except Exception as e:
        print(f"  [PRICE] {stock_name}({code}) 예외: {e}")
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
            r'<tr[^>]*>\s*<td[^>]*>([\d\.]+)</td>'   # 날짜
            r'\s*<td[^>]*><span[^>]*>([\d,]+)</span></td>'  # 종가
            r'\s*<td[^>]*>.*?</td>'                  # 전일비
            r'\s*<td[^>]*>.*?</td>'                  # 등락률
            r'\s*<td[^>]*><span[^>]*>([\d,]+)</span></td>'  # 시가
            r'\s*<td[^>]*><span[^>]*>([\d,]+)</span></td>'  # 고가
            r'\s*<td[^>]*><span[^>]*>([\d,]+)</span></td>'  # 저가
            r'\s*<td[^>]*><span[^>]*>([\d,]+)</span></td>', # 거래량
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
        # 날짜 오름차순 정렬
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

            # 심지 (고가-저가)
            ax.plot([i, i], [l, h], color=color, linewidth=0.8, zorder=1)
            # 몸통 (시가-종가)
            body_bottom = min(o, c)
            body_height = abs(c - o) or 1
            rect = mpatches.Rectangle(
                (i - 0.35, body_bottom), 0.7, body_height,
                facecolor=color, edgecolor=color, linewidth=0.5, zorder=2
            )
            ax.add_patch(rect)

        # 날짜 레이블 (5개만)
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
        "q":    stock_name,
        "q_enc": "UTF-8",
        "st":   "111",
        "sug":  "all",
        "frm":  "stock",
    }
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=5)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items", [])
        if not items:
            return None

        # items[0] = [[code, name, ...], ...]
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
