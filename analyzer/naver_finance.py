# analyzer/naver_finance.py
"""
네이버 금융 주가 조회 - v3
종목 목록: 네이버 금융 시가총액 목록 (KRX 세션 이슈 우회)
현재가: 네이버 금융 개별 종목 페이지
"""
import requests
import json
import re
import time
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))

_NAVER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def load_stock_names() -> dict:
    """네이버 금융 시가총액 목록에서 종목명→코드 매핑 로드 (캐시 포함)"""
    import os

    cache_path = "data/stock_names_cache.json"
    today = datetime.now(KST).strftime("%Y-%m-%d")

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("date") == today and len(cache.get("stocks", {})) > 100:
                print(f"  [종목목록] 캐시 사용 ({len(cache['stocks'])}개, {today})")
                return cache["stocks"]
        except Exception:
            pass

    print("  [종목목록] 네이버 금융에서 종목 목록 로드 중...")
    stock_map = {}

    # 코스피(sosok=0) + 코스닥(sosok=1) 각 10페이지 (400개씩)
    for sosok, market_name in [(0, "코스피"), (1, "코스닥")]:
        count = _load_naver_market_stocks(sosok, stock_map, max_pages=10)
        print(f"  [{market_name}] {count}개 로드")

    if len(stock_map) < 50:
        print("  [종목목록] 네이버 금융 실패 → 주요 종목 폴백 사용")
        stock_map = _get_fallback_stocks()

    os.makedirs("data", exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"date": today, "stocks": stock_map}, f, ensure_ascii=False)

    print(f"  [종목목록] 총 {len(stock_map)}개 로드 완료")
    return stock_map


def _load_naver_market_stocks(sosok: int, stock_map: dict, max_pages: int = 10) -> int:
    """네이버 금융 시가총액 목록 수집 (sosok: 0=코스피, 1=코스닥)"""
    added = 0
    for page in range(1, max_pages + 1):
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        try:
            r = requests.get(url, headers=_NAVER_HEADERS, timeout=15)
            r.encoding = "euc-kr"
            soup = BeautifulSoup(r.text, "html.parser")

            rows = soup.select("table.type_2 tbody tr")
            page_count = 0
            for row in rows:
                link = row.select_one("a.tltle")
                if link and link.get("href"):
                    href = link["href"]
                    m = re.search(r"code=(\d{6})", href)
                    if m:
                        code = m.group(1)
                        name = link.get_text(strip=True)
                        if name and code and name not in stock_map:
                            stock_map[name] = code
                            page_count += 1
                            added += 1

            if page_count == 0:
                break  # 마지막 페이지

            time.sleep(0.2)  # 요청 간 짧은 대기

        except Exception as e:
            print(f"    [네이버 금융 오류] page={page}: {e}")
            break

    return added


def get_stock_price(stock_code: str) -> dict:
    """네이버 금융에서 현재가 및 등락 정보 조회"""
    if not stock_code:
        return {}
    try:
        url = f"https://finance.naver.com/item/main.naver?code={stock_code}"
        resp = requests.get(url, headers=_NAVER_HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")

        # 현재가 추출 (여러 셀렉터 시도)
        price = ""
        for sel in [
            "#chart_area .today .blind",
            "p.no_today em span.blind",
            ".today .p11 em",
            "div.today strong span.blind",
        ]:
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

        # 상승/하락 방향 확인
        today_block = soup.select_one("#chart_area .today")
        if today_block:
            today_html = str(today_block)
            if "ico_up" in today_html or "상승" in today_html:
                change = "+" + change if change and not change.startswith("+") else change
                rate = "+" + rate if rate and not rate.startswith("+") else rate
            elif "ico_down" in today_html or "하락" in today_html:
                change = "-" + change if change and not change.startswith("-") else change
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
    """네이버 금융 실패 시 주요 종목 폴백 목록 (250개+)"""
    return {
        # 코스피 대형주
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
        "삼성전기": "009150", "삼성SDS": "018260",
        "LS ELECTRIC": "010120", "HD현대일렉트릭": "267260",
        "효성중공업": "298040", "산일전기": "062040",
        "카카오페이": "377300", "카카오뱅크": "323410",
        "HMM": "011200", "고려아연": "010130",
        "대한항공": "003490", "팬오션": "028670",
        "포스코퓨처엠": "003670", "엘앤에프": "066970",
        "씨에스윈드": "112610", "원익IPS": "240810",
        "SK이노베이션": "096770", "GS": "078930",
        "삼성생명": "032830", "삼성화재": "000810",
        "SK": "034730", "LG": "003550", "CJ": "001040",
        "롯데케미칼": "011170", "OCI": "010060",
        "현대제철": "004020", "POSCO": "005490",
        "S-Oil": "010950", "GS칼텍스": "078930",
        "KT&G": "033780", "한전KPS": "051600",
        "한국전력": "015760", "한국가스공사": "036460",
        "한국조선해양": "009540", "현대미포조선": "010620",
        "기아": "000270", "만도": "204320",
        "HL만도": "204320", "현대위아": "011210",
        "S&T모티브": "064960",
        # 코스닥 주요종목
        "에코프로": "086520", "에코프로비엠": "247540",
        "HLB": "028300", "유한양행": "000100", "알테오젠": "196170",
        "레인보우로보틱스": "277810", "두산로보틱스": "454910",
        "HPSP": "403870", "피에스케이": "319660",
        "엔씨소프트": "036570", "크래프톤": "259960",
        "하이브": "352820", "SM": "041510", "JYP Ent": "035900",
        "이수페타시스": "007660", "솔브레인": "357780",
        "리노공업": "058470", "이오테크닉스": "039030",
        "주성엔지니어링": "036930", "원텍": "336570",
        "HLB생명과학": "067630", "HLB제약": "047920",
        "오스코텍": "039200", "유바이오로직스": "206650",
        "셀트리온제약": "068760", "셀트리온헬스케어": "091990",
        "메디톡스": "086900", "휴젤": "145020",
        "클래시스": "214150", "덴티움": "145720",
        "코스맥스": "044820", "한국콜마": "161890",
        "뷰티스킨": "NONE",
        "펄어비스": "263750", "넷마블": "251270",
        "컴투스": "078340", "위메이드": "112040",
        "카카오게임즈": "293490",
        "네오위즈": "095660", "엔씨소프트": "036570",
        "CJ ENM": "035760", "스튜디오드래곤": "253450",
        "에이스침대": "003440",
        "파크시스템스": "140860", "테크윙": "089030",
        "피에스케이홀딩스": "031980",
        "실리콘투": "257720", "에스엠코어": "007820",
        "한화솔루션": "009830", "OCI홀딩스": "456040",
        "케이씨텍": "064760", "원익홀딩스": "030530",
        "SKC": "011790", "SK피아이씨글로벌": "011790",
        "포스코DX": "022100", "포스코인터내셔널": "047050",
        "NAVER웹툰": "NONE",
        "카카오엔터테인먼트": "NONE",
        "하이닉스": "000660",
        "삼성바이오에피스": "NONE",
        "셀트리온": "068270",
        "한미약품": "128940", "종근당": "185750",
        "보령": "003850", "광동제약": "009290",
        "제일약품": "271980",
        "에스티팜": "237690", "동아에스티": "170900",
        "녹십자": "006280", "일양약품": "007570",
        "메지온": "140410",
        # 방산 테마
        "LIG넥스원": "079550", "한화에어로스페이스": "012450",
        "한국항공우주": "047810", "현대로템": "064350",
        "한화시스템": "272210", "한화오션": "042660",
        "LIG넥스원": "079550",
        # AI/반도체
        "삼성전자": "005930", "SK하이닉스": "000660",
        "DB하이텍": "000990", "원익IPS": "240810",
        "테스": "095610", "유진테크": "084370",
        "피에스케이": "319660", "HPSP": "403870",
        "케이씨텍": "064760",
        # 2차전지
        "LG에너지솔루션": "373220", "삼성SDI": "006400",
        "SK이노베이션": "096770", "에코프로": "086520",
        "에코프로비엠": "247540", "포스코퓨처엠": "003670",
        "엘앤에프": "066970", "코스모화학": "005420",
        "씨아이에스": "222080", "천보": "278280",
    }
