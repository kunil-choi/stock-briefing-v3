# main.py
import os
import json
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

from config import (
    ANTHROPIC_API_KEY, YOUTUBE_API_KEY, GH_TOKEN, GITHUB_REPO,
    NEWS_RSS_FEEDS, REPORT_DAYS, load_channels,
)
from collectors.news_collector import collect_news
from collectors.youtube_collector import get_youtube_client, collect_section1_youtube
from collectors.analyst_collector import collect_analyst
from analyzer.ai_analyzer import analyze_and_generate_html

KST = ZoneInfo("Asia/Seoul")


def safe_collect(fn, *args, label="", **kwargs):
    try:
        result = fn(*args, **kwargs)
        return result if result else []
    except Exception as e:
        print(f"  [{label}] 수집 중 오류 발생: {e}")
        return []


def main():
    now_kst = datetime.now(KST)
    print(f"=== AI 증시 모닝브리핑 시작: {now_kst.strftime('%Y-%m-%d %H:%M:%S KST')} ===")
    start_time = now_kst.timestamp()

    # API 키 점검
    print("\n[API 키 점검]")
    keys = {
        "ANTHROPIC": ANTHROPIC_API_KEY,
        "YOUTUBE":   YOUTUBE_API_KEY,
        "GH_TOKEN":  GH_TOKEN,
    }
    all_ok = True
    for name, val in keys.items():
        if val:
            print(f"  {name}: ✅")
        else:
            print(f"  {name}: ❌ 없음")
            all_ok = False
    print(f"  {'전체 정상' if all_ok else '일부 키 누락'}")

    # 채널 로드
    print("\n[채널 로드]")
    channels = load_channels()
    for cat in ["broadcast", "youtuber", "securities"]:
        items = channels.get(cat, [])
        valid = [c for c in items if isinstance(c, dict) and c.get("id")]
        print(f"  {cat}: 전체 {len(items)}개 / 유효 ID {len(valid)}개")

    all_data = []

    # 1. 시장 데이터
    print("\n[시장 데이터 수집]")
    try:
        from collectors.market_collector import collect_market_overview
        market_overview = collect_market_overview()
    except Exception as e:
        print(f"  [시장수집 오류] {e}")
        market_overview = {}

    # 2. 뉴스
    print("\n[1/3] 뉴스 RSS 수집 중...")
    news_data = safe_collect(collect_news, NEWS_RSS_FEEDS, label="뉴스")
    all_data.extend(news_data)
    print(f"  → {len(news_data)}건")

    # 3. 유튜브 (섹션1만)
    print("\n[2/3] 유튜브 수집 중 (방송사/유튜버/증권사 24시간)...")
    youtube = get_youtube_client(YOUTUBE_API_KEY)
    if youtube:
        yt_data = safe_collect(
            collect_section1_youtube, youtube, channels, label="유튜브"
        )
        all_data.extend(yt_data)
        print(f"  → {len(yt_data)}건")
    else:
        print("  → YouTube 클라이언트 생성 실패, 스킵")

    # 4. 애널리스트
    print("\n[3/3] 애널리스트 리포트 수집 중...")
    analyst_data = safe_collect(collect_analyst, label="애널리스트")
    all_data.extend(analyst_data)
    print(f"  → {len(analyst_data)}건")

    # 수집 결과 요약
    print("\n" + "=" * 50)
    print(f"전체 수집: {len(all_data)}건")
    type_counts = {}
    for d in all_data:
        t = d.get("source_type", "기타")
        type_counts[t] = type_counts.get(t, 0) + 1
    print("\n[수집 결과 요약]")
    for t, c in type_counts.items():
        warn = "⚠️ " if c == 0 else "  "
        print(f"  {warn}{t}: {c}건")

    # 원본 백업
    os.makedirs("data", exist_ok=True)
    today_str = now_kst.strftime("%Y%m%d")
    with open(f"data/raw_{today_str}.json", "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[저장] data/raw_{today_str}.json 완료")

    # 아카이브
    os.makedirs("docs/archive", exist_ok=True)
    existing_index = "docs/index.html"
    if os.path.exists(existing_index):
        archive_date = now_kst.strftime("%Y-%m-%d")
        archive_path = f"docs/archive/{archive_date}.html"
        if not os.path.exists(archive_path):
            shutil.copy2(existing_index, archive_path)
            print(f"[아카이브] 저장 완료: {archive_path}")

    # AI 분석
    print("\n[AI 분석] Claude API로 교차분석 중...")
    try:
        html = analyze_and_generate_html(
            all_data,
            ANTHROPIC_API_KEY,
            channels_data=channels,
            gh_repo=GITHUB_REPO,
            market_overview=market_overview,
        )
    except Exception as e:
        print(f"[AI분석 오류] {e}")
        html = f"<html><body><h1>분석 오류</h1><p>{e}</p></body></html>"

    # HTML 저장
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    elapsed = datetime.now(KST).timestamp() - start_time
    print(f"\n✅ 브리핑 페이지 생성 완료: docs/index.html")
    print(f"=== 완료: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')} (소요: {elapsed:.0f}초) ===")


if __name__ == "__main__":
    main()
