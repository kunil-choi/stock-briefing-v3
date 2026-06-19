# main.py
"""
수정 이력:
- FIX-RPT-1   : collect_analyst에 api_key=ANTHROPIC_API_KEY 전달
- GEMINI-MAIN : Gemini 유튜브 영상 분석 파이프라인 추가
                수집 후 → Gemini 영상 분석 → 발언 확장 → Claude 분석 순서
- FIX-MAIN-1  : analyze_and_generate_html() 호출 시 gh_token 인자 누락 수정
                함수 시그니처: (all_data, channels_data, gh_repo, gh_token,
                               market_overview) 와 완전히 일치하도록 수정
"""
import os
import json
import shutil
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from config import (
    ANTHROPIC_API_KEY, YOUTUBE_API_KEY, GH_TOKEN, GITHUB_REPO,
    GEMINI_API_KEY,
    NEWS_RSS_FEEDS, REPORT_DAYS, load_channels,
)
from collectors.news_collector    import collect_news
from collectors.youtube_collector import (
    get_youtube_client,
    collect_section1_youtube,
    collect_panelist_youtube,
    _PANELIST_HOURS,
)
from collectors.analyst_collector import collect_analyst
from analyzer.ai_analyzer         import analyze_and_generate_html

KST = ZoneInfo("Asia/Seoul")


def safe_collect(fn, *args, label="", **kwargs):
    try:
        result = fn(*args, **kwargs)
        return result if result else []
    except Exception as e:
        print(f"  [{label}] 수집 중 오류: {e}")
        return []


def main():
    now_kst    = datetime.now(KST)
    print(f"=== AI 주식 브리핑 시작: {now_kst.strftime('%Y-%m-%d %H:%M:%S KST')} ===")
    start_time = now_kst.timestamp()

    # ── API 키 확인 ────────────────────────────────────────────────────────
    print("\n[API 키 확인]")
    keys = {
        "ANTHROPIC": ANTHROPIC_API_KEY,
        "YOUTUBE":   YOUTUBE_API_KEY,
        "GH_TOKEN":  GH_TOKEN,
        "GEMINI":    GEMINI_API_KEY,
    }
    all_ok = True
    for name, val in keys.items():
        if val:
            print(f"  {name}: ✅")
        else:
            print(f"  {name}: ❌ 없음")
            if name not in ("GEMINI",):   # Gemini는 선택적 — 없어도 중단하지 않음
                all_ok = False
    print(f"  {'정상 동작' if all_ok else '일부 키 없음'}")

    # ── 채널 로드 ──────────────────────────────────────────────────────────
    print("\n[채널 로드]")
    channels = load_channels()
    for cat in ["broadcast", "youtuber", "securities"]:
        items = channels.get(cat, [])
        valid = [c for c in items if isinstance(c, dict) and c.get("id")]
        print(f"  {cat}: 전체 {len(items)}개 / 유효 ID {len(valid)}개")

    all_data = []

    # ── 1. 시장 데이터 ─────────────────────────────────────────────────────
    print("\n[시장 데이터 수집]")
    try:
        from collectors.market_collector import collect_market_overview
        market_overview = collect_market_overview()
    except Exception as e:
        print(f"  [시장데이터 수집 실패] {e}")
        market_overview = {}

    # ── 2. 뉴스 RSS ────────────────────────────────────────────────────────
    print("\n[1/5] 뉴스 RSS 수집...")
    news_data = safe_collect(collect_news, NEWS_RSS_FEEDS, label="뉴스")
    all_data.extend(news_data)
    print(f"  → {len(news_data)}건")

    # ── YouTube 클라이언트 ─────────────────────────────────────────────────
    youtube = get_youtube_client(YOUTUBE_API_KEY)

    # ── 3. 등록 채널 플레이리스트 수집 ────────────────────────────────────
    print("\n[2/5] 유튜브 수집 (경제방송/유튜버/증권사 24h)...")
    yt_data = []
    if youtube:
        yt_data = safe_collect(
            collect_section1_youtube, youtube, channels, label="유튜브"
        )
        print(f"  → {len(yt_data)}건")
    else:
        print("  → YouTube 클라이언트 없음, 스킵")

    # ── 4. 패널리스트 이름 검색 수집 ──────────────────────────────────────
    print(f"\n[3/5] 패널리스트 이름 검색 수집 ({_PANELIST_HOURS}h)...")
    panelist_data = []
    if youtube:
        panelist_data = safe_collect(
            collect_panelist_youtube, youtube, label="패널리스트검색"
        )
        print(f"  → {len(panelist_data)}건")
    else:
        print("  → YouTube 클라이언트 없음, 스킵")

    # ── GEMINI-MAIN: 유튜브 영상 Gemini 직접 분석 ─────────────────────────
    youtube_raw = yt_data + panelist_data
    if GEMINI_API_KEY and youtube_raw:
        print(f"\n[GEMINI] 유튜브 영상 분석 시작 ({len(youtube_raw)}개 영상)...")
        try:
            from collectors.gemini_youtube_analyzer import (
                analyze_youtube_items,
                expand_gemini_mentions,
            )
            enriched = analyze_youtube_items(youtube_raw, GEMINI_API_KEY)
            expanded = expand_gemini_mentions(enriched)
            all_data.extend(expanded)
            print(f"  → Gemini 분석 완료: {len(expanded)}건 (원본+발언 확장 포함)")
        except Exception as e:
            print(f"  [GEMINI] 유튜브 분석 실패 (기존 데이터로 계속 진행): {e}")
            all_data.extend(youtube_raw)
    else:
        all_data.extend(youtube_raw)
        if not GEMINI_API_KEY:
            print("\n[GEMINI] API 키 없음 → 유튜브 영상 분석 스킵")

    # ── 5. 애널리스트 리포트 ───────────────────────────────────────────────
    print("\n[5/5] 애널리스트 리포트 수집 (본문 크롤링 + Claude 요약 포함)...")
    analyst_data = safe_collect(
        collect_analyst,
        api_key=ANTHROPIC_API_KEY,
        label="애널리스트",
    )
    all_data.extend(analyst_data)
    print(f"  → {len(analyst_data)}건")

    # ── 수집 요약 ──────────────────────────────────────────────────────────
    print("\n" + "=" * 50)
    print(f"총 수집: {len(all_data)}건")
    type_counts = {}
    for d in all_data:
        t = d.get("source_type", "기타")
        type_counts[t] = type_counts.get(t, 0) + 1
    print("\n[수집 유형 요약]")
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        warn = "⚠️ " if c == 0 else "  "
        print(f"  {warn}{t}: {c}건")

    # ── 원본 저장 ──────────────────────────────────────────────────────────
    os.makedirs("data", exist_ok=True)
    today_str = now_kst.strftime("%Y%m%d")
    with open(f"data/raw_{today_str}.json", "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[저장] data/raw_{today_str}.json 저장")

    # ── 아카이브 ───────────────────────────────────────────────────────────
    os.makedirs("docs/archive", exist_ok=True)
    existing_index = "docs/index.html"
    if os.path.exists(existing_index):
        archive_date = now_kst.strftime("%Y-%m-%d")
        archive_path = f"docs/archive/{archive_date}.html"
        if not os.path.exists(archive_path):
            shutil.copy2(existing_index, archive_path)
            print(f"[아카이브] 저장: {archive_path}")

    # ── AI 분석 (Claude + Gemini 검수) ────────────────────────────────────
    print("\n[AI 분석] Claude 분석 + Gemini 검수 시작...")
    try:
        # FIX-MAIN-1: gh_token 인자 명시적으로 전달
        # ai_analyzer.analyze_and_generate_html 시그니처:
        #   (all_data, channels_data, gh_repo, gh_token, market_overview)
        html = analyze_and_generate_html(
            all_data,
            channels_data=channels,
            gh_repo=GITHUB_REPO,
            gh_token=GH_TOKEN,           # ← FIX-MAIN-1: 누락된 인자 추가
            market_overview=market_overview,
        )
    except Exception as e:
        print(f"[AI 분석 실패] {e}")
        print(traceback.format_exc())
        html = f"<html><body><h1>분석 실패</h1><p>{e}</p></body></html>"

    # ── HTML 저장 ──────────────────────────────────────────────────────────
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    elapsed = datetime.now(KST).timestamp() - start_time
    print(f"\n✅ 브리핑 완성 → docs/index.html")
    print(f"=== 완료: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')} "
          f"(소요: {elapsed:.0f}초) ===")


if __name__ == "__main__":
    main()
