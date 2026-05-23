# config.py
import os
import json

# API Keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
YOUTUBE_API_KEY   = os.getenv("YOUTUBE_API_KEY", "")
GH_TOKEN          = os.getenv("GH_TOKEN", "")

GITHUB_REPO  = "kunil-choi/stock-briefing-v3"
CHANNELS_FILE = "channels.json"
PANEL_PASSWORD = "stock2026!"

NEWS_RSS_FEEDS = {
    "한국경제":    "https://www.hankyung.com/feed/finance",
    "매일경제":    "https://www.mk.co.kr/rss/30100041/",
    "연합뉴스 경제": "https://www.yna.co.kr/rss/economy.xml",
    "이데일리":    "https://rss.edaily.co.kr/edaily_stock.xml",
    "머니투데이":  "https://rss.mt.co.kr/mt_stock.xml",
    "서울경제 증권": "https://m.sedaily.com/rss/finance",
    "조선비즈":    "https://biz.chosun.com/site/data/rss/rss.xml",
    "한국경제 전체": "https://www.hankyung.com/feed/all-news",
}

POPULAR_PANELISTS = [
    "홍춘욱", "오건영", "박세익", "김학균", "이효석",
    "정용진", "강방천", "이채원", "최준철", "김경필",
    "염승환", "이선엽", "곽상준", "박문환", "허재환",
    "서영수", "김한진", "이경민", "김일구", "전종규",
    "이주열", "박종훈", "김현석", "신중호", "이창용",
]

BROADCAST_HOURS   = 24
YOUTUBER_HOURS    = 24
SECURITIES_TV_HOURS = 48
REPORT_DAYS       = 1

# 증권TV 전용 채널 (섹션2)
SECURITIES_TV_CHANNELS = {
    "한국경제TV": "UCp7vLUO-BI9UkVFJWFSJ-ig",
    "매일경제TV": "UCbSSCqKMmBOCR5KxFsVzmvA",
    "머니투데이TV": "UCjyp-3MIBaVxFkOhC0HY2mg",
    "이데일리TV": "UCuBrUcZjMnAk4vl91cHJcVg",
    "연합인포맥스": "UCuC2nQZ3nzK4cVT8j8ZXdZg",
}

def load_channels():
    try:
        with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            for cat in ["broadcast", "youtuber", "top50"]:
                if cat not in data:
                    data[cat] = {}
            return data
    except FileNotFoundError:
        print(f"[config] {CHANNELS_FILE} 없음. 빈 채널 목록 사용.")
        return {"broadcast": {}, "youtuber": {}, "top50": {}}
