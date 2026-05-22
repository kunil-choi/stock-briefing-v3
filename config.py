import os
import json

# === API Keys ===
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
YOUTUBE_API_KEY   = os.getenv("YOUTUBE_API_KEY", "")
GH_TOKEN          = os.getenv("GH_TOKEN", "")

# === GitHub 저장소 정보 ===
GITHUB_REPO   = "kunil-choi/stock-briefing-v3"
CHANNELS_FILE = "channels.json"

# === 관리자 페이지 인증 ===
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "stock2026!")

# === 뉴스 RSS 피드 ===
NEWS_RSS_FEEDS = {
    "매일경제":     "https://www.mk.co.kr/rss/30100041/",
    "한국경제":     "https://www.hankyung.com/feed/finance",
    "서울경제":     "https://m.sedaily.com/rss/finance",
    "이데일리":     "https://rss.edaily.co.kr/edaily_stock.xml",
    "머니투데이":   "https://rss.mt.co.kr/mt_stock.xml",
    "연합뉴스 경제":"https://www.yna.co.kr/rss/economy.xml",
    "한국경제 전체":"https://www.hankyung.com/feed/all-news",
    "조선비즈":     "https://biz.chosun.com/site/data/rss/rss.xml",
}

# === 증권TV 채널 — list 구조 (섹션 2 전용) ===
# ✅ 수정: dict → list 로 변경 (youtube_collector.py 와 타입 일치)
SECURITIES_TV_CHANNELS = [
    {
        "name": "한국경제TV",
        "id":   "UCF8AeLlUbEpKju6v1H6p8Eg",
        "url":  "https://www.youtube.com/@hkwowtv",
        "type": "증권TV",
    },
    {
        "name": "매일경제TV",
        "id":   "UCnMtEMnsGFjQgLJEEkMSHhQ",
        "url":  "https://www.youtube.com/@MKeconomy_TV",
        "type": "증권TV",
    },
    {
        "name": "머니투데이방송(MTN)",
        "id":   "UClErHbdZKUnD1NyIUeQWvuQ",
        "url":  "https://www.youtube.com/@mtn",
        "type": "증권TV",
    },
    {
        "name": "이데일리TV",
        "id":   "UCXopJfBhGH2sWl-4e8k5uOg",
        "url":  "https://www.youtube.com/@edailytv",
        "type": "증권TV",
    },
    {
        "name": "SBS Biz",
        "id":   "UCbMjg2EvXs_RUGW-KrdM3pw",
        "url":  "https://www.youtube.com/@SBSBiz2021",
        "type": "증권TV",
    },
    {
        "name": "서울경제TV",
        "id":   "UCZKBS37Y0TmrFBfYBuBibtQ",
        "url":  "https://www.youtube.com/@sentv",
        "type": "증권TV",
    },
]

# === 인기 패널리스트 목록 ===
POPULAR_PANELISTS = [
    "홍춘욱", "오건영", "박세익", "김학균", "이효석",
    "정용진", "강방천", "이채원", "최준철", "김경필",
    "염승환", "이선엽", "곽상준", "박문환", "허재환",
    "서영수", "김한진", "이경민", "김일구", "전종규",
    "이주열", "박종훈", "김현석", "신중호", "이창용",
]

# === 증권사 목록 ===
BROKERS = [
    "NH투자증권", "삼성증권", "KB증권", "미래에셋증권",
    "한국투자증권", "신한투자증권", "하나증권", "키움증권",
    "대신증권", "메리츠증권", "한화투자증권", "유진투자증권",
    "LS증권", "IBK투자증권", "DB금융투자", "SK증권",
    "현대차증권", "BNK투자증권", "iM증권", "교보증권",
    "다올투자증권", "한양증권", "흥국증권", "토스증권",
]

# === 수집 시간 범위 설정 ===
BROADCAST_HOURS    = 24   # 방송 채널: 최근 24시간
YOUTUBER_HOURS     = 24   # 유튜버: 최근 24시간
SECURITIES_TV_HOURS = 48  # 증권TV: 전일 기준 (48시간으로 여유 확보)
REPORT_DAYS        = 1    # 애널리스트 리포트: 최근 1일

# === KRX 종목 목록 URL ===
KRX_STOCK_LIST_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"


def load_channels():
    """channels.json 로드 (v3 구조: broadcast / youtuber / securities)"""
    try:
        with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for cat in ["broadcast", "youtuber", "securities"]:
            if cat not in data:
                data[cat] = {}
        return data
    except FileNotFoundError:
        print(f"[config] {CHANNELS_FILE} 파일을 찾을 수 없습니다.")
        return {"broadcast": {}, "youtuber": {}, "securities": {}}
