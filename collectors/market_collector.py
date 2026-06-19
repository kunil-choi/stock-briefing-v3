def _fetch_naver_night_future(symbol: str):
    """
    네이버 증권 야간선물 페이지에서 현재가와 등락률을 파싱한다.
    symbol: "K2FA" (KOSPI200) 또는 "KSFA" (KOSDAQ150)
    """
    if not _REQUESTS_AVAILABLE:
        return None, None

    # 네이버 야간선물 페이지 URL
    url = f"https://finance.naver.com/item/coinfo.naver?code={symbol}"
    try:
        resp = requests.get(url, headers=_NAVER_HEADERS, timeout=10)
        resp.raise_for_status()
        resp.encoding = "euc-kr"
        text = resp.text

        # 현재가
        m_val = re.search(
            r'<p[^>]+class="[^"]*no_today[^"]*"[^>]*>.*?<em[^>]*>([\d,.]+)</em>',
            text, re.DOTALL
        )
        # 등락률
        m_pct = re.search(
            r'<p[^>]+class="[^"]*no_exday[^"]*"[^>]*>.*?'
            r'<span[^>]+class="[^"]*pct[^"]*"[^>]*>([\d.]+)%',
            text, re.DOTALL
        )
        # 방향 (상승/하락)
        m_dir = re.search(
            r'<p[^>]+class="[^"]*no_exday[^"]*"[^>]*>.*?'
            r'<span[^>]+class="(up|dn|down)"',
            text, re.DOTALL
        )

        if not m_val:
            return None, None

        value = float(m_val.group(1).replace(",", ""))
        pct   = float(m_pct.group(1)) if m_pct else 0.0

        raw_dir = (m_dir.group(1) if m_dir else "").lower()
        if "dn" in raw_dir or "down" in raw_dir:
            pct = -abs(pct)

        return value, pct

    except Exception as e:
        print(f"  [야간선물] {symbol} 파싱 실패: {e}")
        return None, None
