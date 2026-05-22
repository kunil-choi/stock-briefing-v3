"""
설정 파일 - v3
"""
import os
from dotenv import load_dotenv
import json

load_dotenv()

# API Keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
GH_TOKEN = os.getenv("GH_TOKEN", "")

# GitHub 설정
GITHUB_REPO = "kunil-choi/stock-briefing-v3"
CHANNELS_FILE = "channels.json"

# 관리자 비밀번호
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "stock2026!")

# 뉴스 RSS 피드
NEWS_RSS_FEEDS = {
    "매일경제": "https://www.mk.co.kr/rss/30000001/",
    "한국경제": "https://feeds.hankyung.com/apps/news.xml?category=economy",
    "서울경제": "https://www.sedaily.com/RSS/",
    "이데일리": "https://www.edaily.co.kr/rss/feed",
    "머니투데이": "https://www.mt.co.kr/rss/feed.xml",
    "연합뉴스 경제": "https://www.yna.co.kr/economy/rss.xml",
    "한국경제 전체": "https://feeds.hankyung.com/apps/news.xml",
    "조선비즈": "https://biz.chosun.com/RSS/",
}

# 증권TV 채널 목록 (config.py에서 직접 관리)
SECURITIES_TV_CHANNELS = [
    {
        "name": "한국경제TV",
        "id": "UCi1Fo6xR0DpBd1I-aEBKPHQ",
        "url": "https://www.youtube.com/@한국경제TV",
        "type": "증권TV"
    },
    {
        "name": "매일경제TV",
        "id": "UCx5Hb8WBZtxDW1-aBJi7okA",
        "url": "https://www.youtube.com/@매일경제TV",
        "type": "증권TV"
    },
    {
        "name": "머니투데이방송(MTN)",
        "id": "UCdajMQ3_XVMLN_r1g95FWSA",
        "url": "https://www.youtube.com/@MTN_MONEY",
        "type": "증권TV"
    },
    {
        "name": "이데일리TV",
        "id": "UC8Sv6O3Ux8ePVqorx8aOBMg",   # ← 수정: channels.json과 일치
        "url": "https://www.youtube.com/@edailytv",
        "type": "증권TV"
    },
    {
        "name": "SBS Biz",
        "id": "UCJLn8WjxtRJtlCFMpzOpAYg",
        "url": "https://www.youtube.com/@SBSBiz",
        "type": "증권TV"
    },
    {
        "name": "서울경제TV",
        "id": "",   # ID 미확인 → youtube_collector에서 handle로 resolve 시도
        "url": "https://www.youtube.com/@서울경제TV",
        "type": "증권TV"
    },
]

# 인기 패널리스트
POPULAR_PANELISTS = [
    "홍춘욱", "김영익", "박종훈", "전인구", "유동원", "이남우",
    "소수몽키", "박곰희", "강환국", "슈카", "이효석", "삼프로",
    "김작가", "신사임당", "와이스트릿", "815머니톡", "언더스탠딩",
    "달란트투자", "김단테", "부읽남", "선대인", "경제원탑",
    "머니인사이드", "테이버", "이세무사"
]

# 증권사 목록
BROKERS = [
    "삼성증권", "미래에셋", "키움증권", "한국투자증권", "NH투자증권",
    "KB증권", "신한투자증권", "하나증권", "대신증권", "한화투자증권",
    "LS증권", "교보증권", "유안타증권", "IBK투자증권", "DB금융투자",
    "메리츠증권", "이베스트투자증권", "SK증권", "BNK투자증권",
    "부국증권", "현대차증권", "신영증권", "다올투자증권", "케이프투자증권"
]

# 시간 범위 설정 (시간 단위)
BROADCAST_HOURS = 24
YOUTUBER_HOURS = 24
SECURITIES_TV_HOURS = 48
REPORT_DAYS = 1

# KRX 종목 목록 다운로드 URL
KRX_STOCK_LIST_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"


def load_channels():
    """channels.json 파일 로드"""
    try:
        with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 필수 키 보장
        for key in ["broadcast", "youtuber", "securities"]:
            if key not in data:
                data[key] = []
        return data
    except FileNotFoundError:
        return {"broadcast": [], "youtuber": [], "securities": []}
    except json.JSONDecodeError as e:
        print(f"channels.json 파싱 오류: {e}")
        return {"broadcast": [], "youtuber": [], "securities": []}
