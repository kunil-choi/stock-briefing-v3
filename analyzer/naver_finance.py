def fetch_naver_stock_price(stock_name: str, code_override: str = "") -> dict | None:
    """
    네이버 금융에서 종목 현재가 조회.
    반환: {"name":str, "code":str, "price":int, "change":str, "change_pct":str, "naver_url":str}
    실패 시 None 반환.
    """
    import requests, re

    code = code_override.strip() if code_override else ""
    if not code:
        return None

    naver_url = f"https://finance.naver.com/item/main.naver?code={code}"
    headers   = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://finance.naver.com/",
    }

    try:
        resp = requests.get(naver_url, headers=headers, timeout=10)
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

        # 등락 파싱 (선택적, 실패해도 무관)
        m_change = re.search(r'<em[^>]+id="changeContents"[^>]*>.*?([\d,]+)', text, re.DOTALL)
        change_str = m_change.group(1).replace(",", "") if m_change else ""

        m_pct = re.search(r'<span[^>]+class="[^"]*rate[^"]*"[^>]*>.*?([\d\.]+)%', text, re.DOTALL)
        pct_str = m_pct.group(1) if m_pct else ""

        print(f"  [PRICE] {stock_name}({code}): {price_int:,}원")
        return {
            "name":       stock_name,
            "code":       code,
            "price":      price_int,   # 항상 int
            "change":     change_str,
            "change_pct": pct_str,
            "naver_url":  naver_url,
        }

    except Exception as e:
        print(f"  [PRICE] {stock_name}({code}) 예외: {e}")
        return None
