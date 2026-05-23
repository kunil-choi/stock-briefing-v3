# main.py - v3
"""
AI 주식 브리핑 v3 메인 실행 파일
"""
import os
import json
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

from config import (
    ANTHROPIC_API_KEY, YOUTUBE_API_KEY, GH_TOKEN, GITHUB_REPO,
    NEWS_RSS_FEEDS, load_channels
)
from collectors.news_collector import collect_news
from collectors.youtube_collector import (
    get_youtube_client, collect_section1_youtube, collect_section2_securities_tv
)
from collectors.analyst_collector import collect_analyst
from collectors.market_collector import collect_market_overview   # ← 추가
from analyzer.ai_analyzer import analyze_and_generate_html

KST = ZoneInfo("Asia/Seoul")

def main():
    now = datetime.now(KST)
    print(f"\n{'='*60}")
    print(f"  AI 주식 브리핑 v3 시작: {now.strftime('%Y-%m-%d %H:%M:%S KST')}")
    print(f"{'='*60}\n")

    # 채널 로드
    channels = load_channels()
    print(f"[채널] 방송:{len(channels.get('broadcast',[]))} "
          f"유튜버:{len(channels.get('youtuber',[]))} "
          f"증권:{len(channels.get('securities',[]))}")

    all_data = []

    # ── 시장 데이터 수집 (야간선물, 미국증시, 한국증시) ──────────────
    market_overview = collect_market_overview()

    # ── 뉴스 RSS ──────────────────────────────────────────────────────
    print("\n[뉴스 수집]")
    news_items = collect_news(NEWS_RSS_FEEDS)
    all_data.extend(news_items)
    print(f"  뉴스 {len(news_items)}건 수집")

    # ── 유튜브 섹션1·섹션2 ───────────────────────────────────────────
    youtube_client = None
    if YOUTUBE_API_KEY:
        youtube_client = get_youtube_client(YOUTUBE_API_KEY)

    section1_items = []
    section2_items = []

    if youtube_client:
        print("\n[유튜브 섹션1 수집]")
        section1_items = collect_section1_youtube(youtube_client, channels)
        all_data.extend(section1_items)
        print(f"  섹션1 {len(section1_items)}건")

        print("\n[유튜브 섹션2 수집]")
        section2_items = collect_section2_securities_tv(youtube_client)
        all_data.extend(section2_items)
        print(f"  섹션2 {len(section2_items)}건")
    else:
        print("\n[유튜브] YOUTUBE_API_KEY 없음 → 스킵")

    # ── 애널리스트 리포트 ─────────────────────────────────────────────
    print("\n[애널리스트 리포트 수집]")
    analyst_items = collect_analyst()
    all_data.extend(analyst_items)
    print(f"  리포트 {len(analyst_items)}건")

    print(f"\n[전체] 수집 완료: {len(all_data)}건 "
          f"(뉴스 {len(news_items)} | 섹션1 {len(section1_items)} "
          f"| 섹션2 {len(section2_items)} | 리포트 {len(analyst_items)})")

    # ── Raw 데이터 저장 ───────────────────────────────────────────────
    os.makedirs("data", exist_ok=True)
    raw_path = f"data/raw_{now.strftime('%Y%m%d')}.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump({
            "collected_at": now.isoformat(),
            "market_overview": market_overview,
            "items": all_data,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n[저장] {raw_path}")

    # ── 아카이브 백업 ─────────────────────────────────────────────────
    os.makedirs("docs/archive", exist_ok=True)
    archive_path = f"docs/archive/{now.strftime('%Y-%m-%d')}.html"
    if os.path.exists("docs/index.html") and not os.path.exists(archive_path):
        shutil.copy("docs/index.html", archive_path)
        print(f"[아카이브] {archive_path}")

    # ── AI 분석 + HTML 생성 ───────────────────────────────────────────
    print("\n[AI 분석 시작]")
    html = analyze_and_generate_html(
        all_data=all_data,
        api_key=ANTHROPIC_API_KEY,
        channels=channels,
        github_repo=GITHUB_REPO,
        market_overview=market_overview,        # ← 추가
    )

    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    end = datetime.now(KST)
    print(f"\n{'='*60}")
    print(f"  완료: {end.strftime('%Y-%m-%d %H:%M:%S KST')}")
    print(f"  소요: {int((end-now).total_seconds())}초")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
