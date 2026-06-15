# config.py
"""
환경변수 및 전역 설정

수정 이력:
- INIT      : 초기 설정
- FIX-PAN-1 : POPULAR_PANELISTS 업데이트 (2026년 6월 기준, 최근 2개월 내 5만뷰+ 기준)
"""

import os
import json

# ── API 키 및 토큰 ──────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
YOUTUBE_API_KEY   = os.getenv("YOUTUBE_API_KEY", "")
GH_TOKEN          = os.getenv("GH_TOKEN", "")

# ── GitHub 저장소 설정 ──────────────────────────────────────────
GITHUB_REPO   = "kunil-choi/stock-briefing-v3"
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

# ── 파일 경로 ───────────────────────────────────────────────────
CHANNELS_FILE = "channels.json"
OUTPUT_FILE   = "data/briefing_data.json"

# ── 관리자 패널 비밀번호 ────────────────────────────────────────
PANEL_PASSWORD = os.getenv("ADMIN_PASSWORD", "stock2026!")

# ── 수집 시간 범위 (시간 단위) ──────────────────────────────────
BROADCAST_HOURS  = 24
YOUTUBER_HOURS   = 24
SECURITIES_HOURS = 24
REPORT_DAYS      = 1   # 애널리스트 리포트 수집 기간 (일)

# ── 패널리스트 YouTube 검색 기준 ────────────────────────────────
PANELIST_SEARCH_HOURS   = 48    # 최근 몇 시간 이내 영상 검색
PANELIST_MIN_VIEWS      = 50000 # 최소 조회수 (5만)
PANELIST_MAX_RESULTS    = 10    # 이름당 최대 검색 결과 수

# ── RSS 뉴스 피드 ───────────────────────────────────────────────
NEWS_RSS_FEEDS = {
    "한국경제":     "https://www.hankyung.com/feed/finance",
    "매일경제":     "https://www.mk.co.kr/rss/30100041/",
    "머니투데이":   "https://rss.mt.co.kr/news/rss.xml",
    "이데일리":     "https://rss.edaily.co.kr/edaily/stock.xml",
    "연합인포맥스": "https://news.einfomax.co.kr/rss/subList/2.xml",
    "뉴스핌":       "https://www.newspim.com/rss/",
    "파이낸셜뉴스": "https://www.fnnews.com/rss/fn_realestate_stock.xml",
    "아시아경제":   "https://www.asiae.co.kr/rss/all.htm",
}

# ── 주요 패널리스트 이름 목록 ────────────────────────────────────
# 기준: 2026년 4~6월 기준, 최근 2개월 내 조회수 5만+ 콘텐츠에
#       출연하거나 직접 진행한 전문가
# 이 목록에 포함된 이름이 제목/본문에 등장하는 YouTube 영상은
# 등록된 채널 외부 콘텐츠라도 동일 분석 대상으로 처리됨.
POPULAR_PANELISTS = [
    # ── 매크로 / 채권 / 환율 ────────────────────────────
    "오건영",   # 신한은행 WM, 채권·매크로 전문
    "홍춘욱",   # 프리즘투자자문, 글로벌 자산배분
    "한상춘",   # 한국경제TV 논설위원, 글로벌 경제
    "이진우",   # MBC 라디오 경제 진행자

    # ── 국내 주식 / 리서치 ──────────────────────────────
    "염승환",   # LS증권 이사, 개인투자자 대상 주식 강의
    "박세익",   # 체슬리투자자문, 성장주 전문
    "이효석",   # 이효석아카데미, 기술적 분석
    "이선엽",   # 신한투자증권 이사
    "박병창",   # 유튜브 '박병창TV', 수급 분석
    "이승우",   # 유진투자증권 리서치센터장
    "윤지호",   # 이베스트투자증권 리서치센터장
    "신형관",   # 미래에셋증권 수석 애널리스트
    "장우석",   # 현대차증권 리서치
    "임형록",   # 한화투자증권 투자전략팀장

    # ── 가치투자 / 자산운용 ─────────────────────────────
    "강방천",   # 에셋플러스자산운용 회장
    "최준철",   # VIP자산운용 대표
    "이채원",   # 한국투자밸류자산운용 대표
    "곽상준",   # 파인에셋자산운용

    # ── 방송 / 미디어 ───────────────────────────────────
    "김동환",   # 삼프로TV 진행자
    "박종훈",   # KBS 기자, 경제 콘텐츠

    # ── 경제 / 시황 ─────────────────────────────────────
    "김학균",   # 신영증권 리서치센터장
    "김한진",   # 키움증권 수석 연구위원
    "이채원",   # (가치투자 섹션 중복 제거됨)
]

# 중복 제거 (이채원 중복 방지용 안전장치)
POPULAR_PANELISTS = list(dict.fromkeys(POPULAR_PANELISTS))


# ── 채널 데이터 로드 함수 ────────────────────────────────────────
def load_channels() -> dict:
    """
    channels.json을 읽어 broadcast / youtuber / securities 섹션을 반환.
    파일이 없으면 빈 딕셔너리 반환.
    """
    try:
        with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for cat in ["broadcast", "youtuber", "securities"]:
            data.setdefault(cat, [])
        return data
    except FileNotFoundError:
        print(f"[설정] {CHANNELS_FILE} 없음 → 빈 채널 목록 사용")
        return {"broadcast": [], "youtuber": [], "securities": []}
    except json.JSONDecodeError as e:
        print(f"[설정] {CHANNELS_FILE} JSON 파싱 실패: {e}")
        return {"broadcast": [], "youtuber": [], "securities": []}
