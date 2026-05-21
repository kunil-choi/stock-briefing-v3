# main.py - v3
"""
AI 주식 브리핑 v3 메인 실행 파일
섹션 1: 유튜브·미디어 채널 언급 종목 분석
섹션 2: 증권TV 전문가 출연 추천 종목 (전일 방송 기준)
섹션 3: 증권사 애널리스트 리포트 분석
종합 투자전략: 섹션 1~3 통합 분석
"""
import os
import json
import shutil
from datetime import datetime, timedelta, timezone

from config import (
    ANTHROPIC_API_KEY,
    YOUTUBE_API_KEY,
    GH_TOKEN,
    GITHUB_REPO,
    NEWS_RSS_FEEDS,
    load_channels,
)
from collectors.news_collector import collect_news
from collectors.youtube_collector import collect_section1_youtube, collect_section2_securities_tv
from collectors.analyst_collector import collect_analyst
from analyzer.ai_analyzer import analyze_and_generate_html

KST = timezone(timedelta(hours=9))


def main():
    now = datetime.now(KST)
    print(f"=== AI 주식 브리핑 v3 시작: {now.strftime('%Y-%m-%d %H:%M:%S')} ===\n")

    # 채널 목록 로드
    print("[채널 로드]")
    channels = load_channels()
    print(f"  방송사: {len(channels.get('broadcast', {}))}개")
    print(f"  개인유튜브: {len(channels.get('youtuber', {}))}개")
    print(f"  증권사유튜브: {len(channels.get('securities', {}))}개")

    all_data = []

    # 1. 뉴스 (섹션 1 및 시장요약용)
    print("\n[1/4] 뉴스 RSS 수집 중...")
    news_data = collect_news(NEWS_RSS_FEEDS)
    all_data.extend(news_data)
    print(f"  → 총 {len(news_data)}건")

    # 2. 섹션 1: 유튜브·미디어 채널
    print(f"\n[2/4] 섹션 1 유튜브·미디어 채널 수집 중...")
    if YOUTUBE_API_KEY:
        section1_yt = collect_section1_youtube()
        all_data.extend(section1_yt)
        print(f"  → 총 {len(section1_yt)}건")
    else:
        print("  → YouTube API 키 없음, 건너뜀")

    # 3. 섹션 2: 증권TV (전일 기준)
    print(f"\n[3/4] 섹션 2 증권TV 전문가 채널 수집 중 (전일 기준)...")
    if YOUTUBE_API_KEY:
        section2_yt = collect_section2_securities_tv()
        all_data.extend(section2_yt)
        print(f"  → 총 {len(section2_yt)}건")
    else:
        print("  → YouTube API 키 없음, 건너뜀")

    # 4. 섹션 3: 애널리스트 리포트
    print("\n[4/4] 섹션 3 애널리스트 리포트 수집 중...")
    analyst_data = collect_analyst()
    all_data.extend(analyst_data)
    print(f"  → 총 {len(analyst_data)}건")

    print(f"\n========== 전체 {len(all_data)}건 수집 완료 ==========")

    # 원본 데이터 백업
    os.makedirs("data", exist_ok=True)
    today_str = now.strftime("%Y%m%d")
    with open(f"data/raw_{today_str}.json", "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  원본 데이터 저장: data/raw_{today_str}.json")

    # docs 디렉토리 준비
    os.makedirs("docs", exist_ok=True)
    os.makedirs("docs/archive", exist_ok=True)

    # 기존 index.html을 아카이브에 백업 (HTML 생성 전)
    existing_index = "docs/index.html"
    if os.path.exists(existing_index):
        archive_date = now.strftime("%Y-%m-%d")
        archive_path = f"docs/archive/{archive_date}.html"
        if not os.path.exists(archive_path):
            shutil.copy2(existing_index, archive_path)
            print(f"  아카이브 저장: {archive_path}")

    # AI 분석 + HTML 생성
    print("\n[AI 분석] Claude API로 교차분석 중...")
    html = analyze_and_generate_html(
        all_data,
        ANTHROPIC_API_KEY,
        channels_data=channels,
        gh_repo=GITHUB_REPO,
    )

    # index.html 저장
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  브리핑 페이지 저장: docs/index.html")

    print(f"\n=== 완료: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')} ===")


if __name__ == "__main__":
    main()
