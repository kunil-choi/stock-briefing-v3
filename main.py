# main.py
import os
import json
import shutil
import traceback
from collections import Counter
from datetime import datetime, timedelta, timezone

from config import (
    ANTHROPIC_API_KEY, YOUTUBE_API_KEY, GH_TOKEN, GITHUB_REPO,
    NEWS_RSS_FEEDS, BROADCAST_HOURS, YOUTUBER_HOURS, SECURITIES_TV_HOURS,
    load_channels,
)
from collectors.news_collector     import collect_news
from collectors.youtube_collector  import (
    get_youtube_client,
    collect_section1_youtube,
    collect_section2_securities_tv,
)
from collectors.analyst_collector  import collect_analyst
from collectors.market_collector   import collect_market_overview
from analyzer.ai_analyzer          import analyze_and_generate_html

# BUG-M-1: KST 상수 정의 — 모든 날짜 처리는 KST 기준
KST = timezone(timedelta(hours=9))


def _now_kst() -> datetime:
    return datetime.now(KST)


def _collect_safe(label: str, func, *args, **kwargs) -> list:
    """
    BUG-M-2: 수집기 개별 에러 격리 헬퍼.
    예외 발생 시 빈 리스트 반환 + 스택트레이스 출력으로
    한 수집기 실패가 전체 파이프라인을 중단하지 않도록 보장.
    """
    try:
        result = func(*args, **kwargs)
        if not isinstance(result, list):
            print(f"  [{label}] 반환값이 list가 아님 → 빈 리스트 처리")
            return []
        return result
    except Exception as e:
        print(f"  [{label}] 수집 실패: {e}")
        traceback.print_exc()
        return []


def _warn_empty_sources(all_data: list):
    """
    BUG-M-3: source_type 별 건수 출력 + 0건 소스 경고.
    수집 결과를 명확히 인식하고 다음 실행에서 원인을 파악하기 위함.
    """
    counter = Counter(d.get("source_type", "기타") for d in all_data)
    expected = ["뉴스", "경제방송", "경제방송TV", "유튜브", "애널리스트"]

    print("\n[수집 결과 요약]")
    for stype in expected:
        cnt = counter.get(stype, 0)
        flag = "⚠️ 0건" if cnt == 0 else f"{cnt}건"
        print(f"  {stype}: {flag}")

    for stype, cnt in counter.items():
        if stype not in expected:
            print(f"  {stype} (기타): {cnt}건")

    zero_types = [s for s in expected if counter.get(s, 0) == 0]
    if zero_types:
        print(f"\n  ⚠️ 경고: 다음 소스 수집 0건 → [{', '.join(zero_types)}]")
        print("     GitHub Actions 로그에서 해당 수집기 오류를 확인하세요.")


def _check_api_keys():
    """
    BUG-M-4: API 키 사전 검증 — 키 누락 시 조기 경고.
    실행은 계속하되 어떤 기능이 제한되는지 명확히 로깅.
    """
    issues = []
    if not ANTHROPIC_API_KEY:
        issues.append("ANTHROPIC_API_KEY 누락 → AI 분석 불가")
    if not YOUTUBE_API_KEY:
        issues.append("YOUTUBE_API_KEY 누락 → 유튜브 수집 스킵")
    if not GH_TOKEN:
        issues.append("GH_TOKEN 누락 → GitHub 커밋 불가 (로컬 실행은 무관)")

    if issues:
        print("\n[API 키 점검]")
        for issue in issues:
            print(f"  ⚠️ {issue}")
    else:
        print("[API 키 점검] 전체 정상")

    return bool(ANTHROPIC_API_KEY)  # AI 분석 가능 여부 반환


def main():
    start_time = _now_kst()
    print(f"=== AI 증시 모닝브리핑 시작: {start_time.strftime('%Y-%m-%d %H:%M:%S KST')} ===")

    # BUG-M-4: API 키 사전 검증
    can_analyze = _check_api_keys()

    # 채널 목록 로드
    print("\n[채널 로드]")
    channels = load_channels()
    for cat, lst in channels.items():
        if isinstance(lst, list):
            confirmed = [c for c in lst if isinstance(c, dict) and c.get("id")]
            print(f"  {cat}: 전체 {len(lst)}개 / 유효 ID {len(confirmed)}개")

    # 시장 데이터 수집
    print("\n[시장 데이터 수집]")
    market_overview = _collect_safe("시장데이터", collect_market_overview)
    if not market_overview:
        market_overview = {}
        print("  ⚠️ 시장 데이터 수집 실패 → 빈 dict 사용")

    all_data: list = []

    # ── 1. 뉴스 RSS ───────────────────────────────────────────────────────────
    print(f"\n[1/4] 뉴스 RSS 수집 중...")
    news_data = _collect_safe("뉴스", collect_news, NEWS_RSS_FEEDS)
    all_data.extend(news_data)
    print(f"  → {len(news_data)}건")

    # ── 2. 유튜브 섹션1 ───────────────────────────────────────────────────────
    print(f"\n[2/4] 유튜브 수집 중 (섹션1: 방송사/유튜버/증권사 {BROADCAST_HOURS}시간)...")
    youtube_client = None
    if YOUTUBE_API_KEY:
        try:
            youtube_client = get_youtube_client(YOUTUBE_API_KEY)
        except Exception as e:
            print(f"  ⚠️ YouTube 클라이언트 생성 실패: {e}")

    if youtube_client:
        section1_data = _collect_safe(
            "유튜브섹션1", collect_section1_youtube, youtube_client, channels
        )
        all_data.extend(section1_data)
        print(f"  → {len(section1_data)}건")
    else:
        print("  ⚠️ YouTube API 비활성화 → 섹션1 스킵")

    # ── 3. 유튜브 섹션2 (경제방송TV) ──────────────────────────────────────────
    print(f"\n[3/4] 경제방송TV 수집 중 (섹션2: {SECURITIES_TV_HOURS}시간)...")
    if youtube_client:
        section2_data = _collect_safe(
            "유튜브섹션2", collect_section2_securities_tv, youtube_client
        )
        all_data.extend(section2_data)
        print(f"  → {len(section2_data)}건")
    else:
        print("  ⚠️ YouTube API 비활성화 → 섹션2 스킵")

    # ── 4. 애널리스트 리포트 ──────────────────────────────────────────────────
    print("\n[4/4] 애널리스트 리포트 수집 중...")
    analyst_data = _collect_safe("애널리스트", collect_analyst)
    all_data.extend(analyst_data)
    print(f"  → {len(analyst_data)}건")

    # ── 수집 결과 요약 ────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"전체 수집: {len(all_data)}건")
    _warn_empty_sources(all_data)

    # BUG-M-5: 수집 데이터가 전혀 없어도 최소 브리핑은 생성 (파이프라인 중단 방지)
    if len(all_data) == 0:
        print("\n  ⚠️ 수집 데이터 0건 → 최소 브리핑만 생성합니다.")

    # ── 원본 데이터 백업 ──────────────────────────────────────────────────────
    os.makedirs("data", exist_ok=True)
    # BUG-M-1: KST 기준 날짜 사용
    today_str    = _now_kst().strftime("%Y%m%d")
    save_payload = {
        "collected_at":   _now_kst().strftime("%Y-%m-%d %H:%M:%S KST"),
        "market_overview": market_overview,
        "items":           all_data,
        "source_counts":   dict(Counter(d.get("source_type", "기타") for d in all_data)),
    }
    raw_path = f"data/raw_{today_str}.json"
    try:
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(save_payload, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n[저장] {raw_path} 완료")
    except Exception as e:
        print(f"\n[저장] {raw_path} 실패: {e}")

    # ── 디렉토리 준비 ─────────────────────────────────────────────────────────
    os.makedirs("docs",         exist_ok=True)
    os.makedirs("docs/archive", exist_ok=True)

    # ── 아카이브 백업 (HTML 생성 전) ──────────────────────────────────────────
    existing_index = "docs/index.html"
    # BUG-M-1: KST 기준 날짜 사용
    archive_date = _now_kst().strftime("%Y-%m-%d")
    archive_path = f"docs/archive/{archive_date}.html"

    if os.path.exists(existing_index):
        if not os.path.exists(archive_path):
            try:
                shutil.copy2(existing_index, archive_path)
                print(f"[아카이브] 저장 완료: {archive_path}")
            except Exception as e:
                print(f"[아카이브] 저장 실패: {e}")
        else:
            print(f"[아카이브] 이미 존재: {archive_path} → 스킵")
    else:
        print("[아카이브] 기존 index.html 없음 → 스킵")

    # ── AI 분석 + HTML 생성 ───────────────────────────────────────────────────
    print("\n[AI 분석] Claude API로 교차분석 중...")

    html = None

    if can_analyze:
        try:
            html = analyze_and_generate_html(
                all_data,
                ANTHROPIC_API_KEY,
                channels_data=channels,
                gh_repo=GITHUB_REPO,
                market_overview=market_overview,
            )
        except Exception as e:
            print(f"  ⚠️ AI 분석 실패: {e}")
            traceback.print_exc()
    else:
        print("  ⚠️ ANTHROPIC_API_KEY 없음 → AI 분석 스킵")

    # BUG-M-6: AI 분석 실패 시 에러 페이지 생성 (빈 파일 방지)
    if not html:
        now_str = _now_kst().strftime("%Y-%m-%d %H:%M")
        html = (
            '<!DOCTYPE html><html lang="ko"><head>'
            '<meta charset="UTF-8">'
            '<title>AI 주식 브리핑 - 생성 실패</title>'
            '<style>body{font-family:sans-serif;background:#0a0a14;color:#e0e0e0;'
            'display:flex;justify-content:center;align-items:center;height:100vh;'
            'flex-direction:column;gap:16px;}'
            'h2{color:#ff6b6b;}p{color:#888;font-size:.9em;}</style>'
            '</head><body>'
            f'<h2>⚠️ 브리핑 생성 실패</h2>'
            f'<p>{now_str} KST 기준 데이터 수집 또는 AI 분석 중 오류가 발생했습니다.</p>'
            '<p>GitHub Actions 로그를 확인하세요.</p>'
            '</body></html>'
        )
        print("  ⚠️ 에러 페이지로 대체합니다.")

    # ── HTML 저장 ─────────────────────────────────────────────────────────────
    try:
        with open("docs/index.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\n✅ 브리핑 페이지 생성 완료: docs/index.html")
    except Exception as e:
        print(f"\n❌ docs/index.html 저장 실패: {e}")
        traceback.print_exc()
        raise  # 저장 실패는 치명적 오류 — GitHub Actions에서 실패로 감지되어야 함

    # ── 실행 시간 출력 ────────────────────────────────────────────────────────
    end_time    = _now_kst()
    elapsed_sec = (end_time - start_time).total_seconds()
    print(
        f"\n=== 완료: {end_time.strftime('%Y-%m-%d %H:%M:%S KST')} "
        f"(소요: {elapsed_sec:.0f}초) ==="
    )


if __name__ == "__main__":
    main()
