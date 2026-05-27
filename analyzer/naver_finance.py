def fetch_naver_stock_price(stock_name: str, code_override: str = "") -> dict | None:
    """
    네이버 금융에서 종목 현재가 조회.

    반환 형태 (FIX-PRICE-2: price를 항상 int로 반환)
    ------------------------------------------------
    {
        "name":       "삼성전자",
        "code":       "005930",
        "price":      75400,        # ← 정수 (기존에 문자열 "75,400" 이던 것 수정)
        "change":     "+1200",
        "change_pct": "+1.62",
        "naver_url":  "https://finance.naver.com/item/main.naver?code=005930",
    }
    실패 시 None 반환.
    """
    import requests, re

    code = code_override.strip() if code_override else ""
    if not code:
        return None

    naver_url = f"https://finance.naver.com/item/main.naver?code={code}"
    api_url   = f"https://finance.naver.com/item/main.naver?code={code}"
    headers   = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://finance.naver.com/",
    }

    try:
        resp = requests.get(api_url, headers=headers, timeout=10)
        resp.raise_for_status()
        text = resp.text

        # 현재가 파싱
        m_price = re.search(
            r'<p[^>]+class="[^"]*no_today[^"]*"[^>]*>.*?<span[^>]+class="[^"]*blind[^"]*"[^>]*>([\d,]+)',
            text, re.DOTALL
        )
        # 대안 패턴
        if not m_price:
            m_price = re.search(r'"현재가"\s*:\s*"?([\d,]+)', text)
        if not m_price:
            m_price = re.search(r'<dd[^>]*>\s*현재가\s*</dd>\s*<dd[^>]*>([\d,]+)', text)
        if not m_price:
            # 최후 수단: 첫 번째 큰 숫자
            m_price = re.search(r'<strong[^>]*>([\d]{3,6}(?:,[\d]{3})*)</strong>', text)

        if not m_price:
            print(f"  [PRICE] {stock_name}({code}) 가격 파싱 실패")
            return None

        # FIX-PRICE-2: 쉼표 제거 후 정수로 변환
        price_str = m_price.group(1).replace(",", "")
        price_int = int(price_str)

        # 등락 파싱 (선택적)
        m_change = re.search(r'<span[^>]+class="[^"]*change[^"]*"[^>]*>([\d,]+)', text)
        change_str = m_change.group(1).replace(",", "") if m_change else ""

        m_pct = re.search(r'([\d\.]+)%', text)
        pct_str = m_pct.group(1) if m_pct else ""

        print(f"  [PRICE] {stock_name}({code}): {price_int:,}원")
        return {
            "name":       stock_name,
            "code":       code,
            "price":      price_int,   # ← int
            "change":     change_str,
            "change_pct": pct_str,
            "naver_url":  naver_url,
        }

    except Exception as e:
        print(f"  [PRICE] {stock_name}({code}) 예외: {e}")
        return None
