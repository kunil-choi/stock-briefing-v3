# config.py
import os
import json

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
YOUTUBE_API_KEY   = os.getenv("YOUTUBE_API_KEY", "")
GH_TOKEN          = os.getenv("GH_TOKEN", "")

GITHUB_REPO   = "kunil-choi/stock-briefing-v3"
CHANNELS_FILE = "channels.json"
PANEL_PASSWORD = os.getenv("ADMIN_PASSWORD", "stock2026!")

NEWS_RSS_FEEDS = {
    "한국경제":      "https://www.hankyung.com/feed/finance",
    "매일경제":      "https://www.mk.co.kr/rss/30100041/",
    "연합뉴스 경제": "https://www.yna.co.kr/rss/economy.xml",
    "이데일리":      "https://rss.edaily.co.kr/edaily_stock.xml",
    "머니투데이":    "https://rss.mt.co.kr/mt_stock.xml",
    "서울경제 증권": "https://m.sedaily.com/rss/finance",
    "조선비즈":      "https://biz.chosun.com/site/data/rss/rss.xml",
    "한국경제 전체": "https://www.hankyung.com/feed/all-news",
}

POPULAR_PANELISTS = [
    "홍춘욱", "오건영", "박세익", "김학균", "이효석",
    "정용진", "강방천", "이채원", "최준철", "김경필",
    "염승환", "이선엽", "곽상준", "박문환", "허재환",
    "서영수", "김한진", "이경민", "김일구", "전종규",
    "이주열", "박종훈", "김현석", "신중호", "이창용",
]

# ── 수집 시간 설정 ──────────────────────────────────────────────
BROADCAST_HOURS  = 24   # 방송사 유튜브 채널
YOUTUBER_HOURS   = 24   # 개인 유튜버 채널
SECURITIES_HOURS = 24   # 증권사 유튜브 채널

REPORT_DAYS = 1


def load_channels():
    try:
        with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for cat in ["broadcast", "youtuber", "securities"]:
            if cat not in data:
                data[cat] = []
                print(f"[config] '{cat}' 키 없음 → 빈 목록으로 초기화")
        return data
    except FileNotFoundError:
        print(f"[config] {CHANNELS_FILE} 없음. 빈 채널 목록 사용.")
        return {"broadcast": [], "youtuber": [], "securities": []}
