# analyzer/naver_finance.py
"""
네이버 금융 주가 조회 - v3
종목 코드 확인 및 현재가 조회
"""
import requests
import json
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def load_stock_names() -> dict:
    """KRX에서 전체 종목 목록 로드 (캐시 포함)"""
    import os

    cache_path = "data/stock_names_cache.json"
    today = datetime.now(KST).strftime("%Y-%m-%d")

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("date") == today and len(cache.get("stocks", {})) > 0:
                print(f"  [종목목록] 캐시 사용 ({len(cache['stocks'])}개, {today})")
                return cache["stocks"]
        except Exception:
            pass

    print("  [종목목록] KRX 종목 목록 로드 중...")
    stock_map = {}
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "http://data.krx.co.kr/",
    }

    for market_id, market_name in [("STK", "코스피"), ("KSQ", "코스닥")]:
        try:
            url = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
            params = {
                "bld": "dbms/MDC/STAT/standard/MDCSTAT01901",
                "mktId": market_id,
                "share": "1",
                "money": "1",
                "csvxls_isNo": "false",
            }
            res = requests.post(url, data=params, headers=headers, timeout=15)
            data = res.json()
            items = data.get("OutBlock_1", [])
            for item in items:
                name = item.get("ISU_ABBRV", "").strip()
                code = item.get("ISU_SRT_CD", "").strip()
                if name and code:
                    stock_map[name] = code
            print(f"  [{market_name}] {len(items)}개 로드")
        except Exception as e:
            print(f"  [{market_name}] KRX 오류: {e}")

    if not stock_map:
        print("  [종목목록] KRX 실패 -> 주요 종목 폴백 사용")
        stock_map = _get_fallback_stocks()

    os.makedirs("data", exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"date": today, "stocks": stock_map}, f, ensure_ascii=False)

    print(f"  [종목목록] 총 {len(stock_map)}개 로드 완료")
    return stock_map


def get_stock_price(stock_code: str) -> dict:
    """네이버 금융에서 현재가 및 등락 정보 조회"""
    if not stock_code:
        return {}
    try:
        url = f"https://finance.naver.com/item/main.naver?code={stock_code}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = "euc-kr"

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        # 현재가 추출 (여러 셀렉터 시도)
        price = ""
        for sel in ["#chart_area .today .blind", "p.no_today em span.blind", ".today .p11 em"]:
            el = soup.select_one(sel)
            if el:
                price = el.get_text(strip=True).replace(",", "")
                break

        if not price:
            return {"code": stock_code, "price": "", "change": "", "change_pct": ""}

        # 등락 추출
        change = ""
        rate = ""
        change_el = soup.select_one("#chart_area .today .change .blind")
        rate_el = soup.select_one("#chart_area .today .rate .blind")

        if change_el:
            change = change_el.get_text(strip=True).replace(",", "")
        if rate_el:
            rate = rate_el.get_text(strip=True)

        # 상승/하락 방향 확인 (아이콘 클래스)
        up_el = soup.select_one("#chart_area .today .ico_up, .blind + .up")
        down_el = soup.select_one("#chart_area .today .ico_down, .blind + .dn")

        if up_el or (change and not change.startswith(("-", "+"))):
            # 방향성 확인이 어려우면 HTML 텍스트에서 추출
            today_block = soup.select_one("#chart_area .today")
            if today_block:
                today_text = today_block.get_text()
                if "상승" in today_text or "ico_up" in str(today_block):
                    change = "+" + change if not change.startswith("+") else change
                    rate = "+" + rate if rate and not rate.startswith("+") else rate
                elif "하락" in today_text or "ico_down" in str(today_block):
                    change = "-" + change if not change.startswith("-") else change
                    rate = "-" + rate if rate and not rate.startswith("-") else rate
        elif up_el:
            change = "+" + change if not change.startswith("+") else change
            rate = "+" + rate if rate and not rate.startswith("+") else rate
        elif down_el:
            change = "-" + change if not change.startswith("-") else change
            rate = "-" + rate if rate and not rate.startswith("-") else rate

        return {
            "code": stock_code,
            "price": price,
            "change": change,
            "change_pct": rate,
        }
    except Exception as e:
        print(f"  [주가조회 실패] {stock_code}: {e}")

    return {"code": stock_code, "price": "", "change": "", "change_pct": ""}


def _get_fallback_stocks() -> dict:
    """KRX 실패 시 사용할 주요 종목 폴백 목록"""
    return {
        "삼성전자": "005930", "SK하이닉스": "000660", "LG에너지솔루션": "373220",
        "삼성바이오로직스": "207940", "현대차": "005380", "기아": "000270",
        "셀트리온": "068270", "POSCO홀딩스": "005490", "KB금융": "105560",
        "신한지주": "055550", "하나금융지주": "086790", "우리금융지주": "316140",
        "LG화학": "051910", "삼성SDI": "006400", "현대모비스": "012330",
        "카카오": "035720", "NAVER": "035420", "LG전자": "066570",
        "삼성물산": "028260", "SK텔레콤": "017670", "KT": "030200",
        "한화에어로스페이스": "012450", "두산에너빌리티": "034020",
        "HD현대중공업": "329180", "HD한국조선해양": "009540",
        "현대건설": "000720", "LIG넥스원": "079550",
        "한국항공우주": "047810", "현대로템": "064350",
        "한화시스템": "272210", "한화오션": "042660",
        "에코프로": "086520", "에코프로비엠": "247540",
        "HLB": "028300", "유한양행": "000100", "알테오젠": "196170",
        "레인보우로보틱스": "277810", "두산로보틱스": "454910",
        "삼성전기": "009150", "삼성SDS": "018260",
        "LS ELECTRIC": "010120", "HD현대일렉트릭": "267260",
        "효성중공업": "298040", "산일전기": "062040",
        "카카오페이": "377300", "카카오뱅크": "323410",
        "HMM": "011200", "고려아연": "010130",
        "대한항공": "003490", "팬오션": "028670",
        "포스코퓨처엠": "003670", "엘앤에프": "066970",
        "씨에스윈드": "112610", "원익IPS": "240810",
        "HPSP": "403870", "피에스케이": "319660",
        "엔씨소프트": "036570", "크래프톤": "259960",
        "하이브": "352820", "SM": "041510", "JYP Ent": "035900",
        "이수페타시스": "007660", "솔브레인": "357780",
        "SK이노베이션": "096770", "GS": "078930",
    }
