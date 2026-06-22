# main.py
"""
수정 이력:
- FIX-RPT-1   : collect_analyst에 api_key=ANTHROPIC_API_KEY 전달
- GEMINI-MAIN : Gemini 유튜브 영상 분석 파이프라인 추가
                수집 후 → Gemini 영상 분석 → 발언 확장 → Claude 분석 순서
- FIX-MAIN-1  : analyze_and_generate_html() 호출 시 gh_token 인자 누락 수정
                함수 시그니처: (all_data, channels_data, gh_repo, gh_token,
                               market_overview) 와 완전히 일치하도록 수정
- FIX-MAIN-2  : 애널리스트 수집 retry 루프 추가
                08:00 KST 이후 오늘 리포트 5건 이상 확보까지 5분 간격 재시도 (최대 90분)
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

    # ── 수동 실행 스킵 옵션 ────────────────────────────────────────────────
    SKIP_YOUTUBE = os.getenv("SKIP_YOUTUBE", "false").lower() == "true"
    SKIP_ANALYST = os.getenv("SKIP_ANALYST", "false").lower() == "true"
    if SKIP_YOUTUBE: print("  ⚡ SKIP_YOUTUBE=true → 유튜브 수집/분석 스킵")
    if SKIP_ANALYST: print("  ⚡ SKIP_ANALYST=true → 애널리스트 수집 스킵")

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
    yt_data = []
    panelist_data = []
    if SKIP_YOUTUBE:
        print("\n[2/5] 유튜브 수집 스킵 (SKIP_YOUTUBE=true)")
        print("\n[3/5] 패널리스트 검색 스킵 (SKIP_YOUTUBE=true)")
    else:
        print("\n[2/5] 유튜브 수집 (경제방송/유튜버/증권사 24h)...")
        if youtube:
            yt_data = safe_collect(
                collect_section1_youtube, youtube, channels, label="유튜브"
            )
            print(f"  → {len(yt_data)}건")
        else:
            print("  → YouTube 클라이언트 없음, 스킵")

        # ── 4. 패널리스트 이름 검색 수집 ──────────────────────────────────────
        print(f"\n[3/5] 패널리스트 이름 검색 수집 ({_PANELIST_HOURS}h)...")
        if youtube:
            panelist_data = safe_collect(
                collect_panelist_youtube, youtube, label="패널리스트검색"
            )
            print(f"  → {len(panelist_data)}건")
        else:
            print("  → YouTube 클라이언트 없음, 스킵")

    # ── GEMINI-MAIN: 유튜브 영상 Gemini 직접 분석 ─────────────────────────
    # 사전 수집된 youtube_mentions.json이 있으면 이미 분석된 영상은 건너뜀
    youtube_raw = yt_data + panelist_data
    if SKIP_YOUTUBE:
        print("\n[GEMINI] 유튜브 분석 스킵 (SKIP_YOUTUBE=true)")
        all_data.extend(youtube_raw)
    elif GEMINI_API_KEY and youtube_raw:
        import json as _json

        # 사전 수집에서 이미 분석된 video_url 로드
        _precollected_urls = set()
        _mentions_file = "data/youtube_mentions.json"
        if os.path.exists(_mentions_file):
            try:
                with open(_mentions_file, "r", encoding="utf-8") as _f:
                    _existing = _json.load(_f)
                # video_url과 timestamp_url 둘 다 포함 (필드명 불일치 방지)
                _precollected_urls = set()
                for m in _existing:
                    if m.get("video_url"): _precollected_urls.add(m["video_url"])
                    if m.get("timestamp_url"): _precollected_urls.add(m["timestamp_url"])
                    # &t= 파라미터 제거한 순수 video_url도 추가
                    _base_url = m.get("video_url","").split("&t=")[0]
                    if _base_url: _precollected_urls.add(_base_url)
                print(f"\n[GEMINI] 사전 수집 데이터: {len(_existing)}건 "
                      f"(분석된 영상 {len(_precollected_urls)}개)")
            except Exception as _e:
                print(f"  [GEMINI] 사전 수집 데이터 로드 실패: {_e}")

        # 신규 영상만 분석
        # link에서 &t= 파라미터 제거 후 비교 (timestamp_url과 불일치 방지)
        _new_items = [
            item for item in youtube_raw
            if item.get("link", "").split("&t=")[0] not in _precollected_urls
        ]
        _skip_count = len(youtube_raw) - len(_new_items)
        print(f"\n[GEMINI] 유튜브 영상 분석 시작 "
              f"({len(_new_items)}개 신규 / {_skip_count}개 사전수집으로 스킵)...")

        try:
            from collectors.gemini_youtube_analyzer import (
                analyze_youtube_items,
                expand_gemini_mentions,
            )
            if _new_items:
                enriched = analyze_youtube_items(_new_items, GEMINI_API_KEY)
                expanded = expand_gemini_mentions(enriched)
            else:
                expanded = []
                print("  → 신규 영상 없음, Gemini 분석 스킵")

            # 사전 수집 데이터를 all_data에 추가 (기존 mentions → gemini 필드로 변환)
            if _precollected_urls and os.path.exists(_mentions_file):
                try:
                    with open(_mentions_file, "r", encoding="utf-8") as _f:
                        _pre_mentions = _json.load(_f)
                    # 사전 수집 데이터를 all_data 형식으로 변환
                    for _m in _pre_mentions:
                        _pre_item = {
                            "source_type":      "유튜브",
                            "source_name":      _m.get("channel", ""),
                            "title":            f"{_m.get('speaker','')} : {_m.get('stock_name','')} 언급" if _m.get("speaker") else _m.get("video_title", ""),
                            "summary":          _m.get("fact", "") or _m.get("quote", ""),
                            "content":          _m.get("quote", ""),
                            "link":             _m.get("timestamp_url") or _m.get("video_url", ""),
                            "url":              _m.get("timestamp_url") or _m.get("video_url", ""),
                            "published":        _m.get("published", ""),
                            "stock_name":       _m.get("stock_name", ""),
                            "gemini_speaker":   _m.get("speaker", ""),
                            "gemini_fact":      _m.get("fact", ""),
                            "gemini_quote":     _m.get("quote", ""),
                            "gemini_sentiment": _m.get("sentiment", "중립"),
                            "gemini_confidence":_m.get("confidence", "보통"),
                            "_from_gemini":     True,
                            "_from_precollect": True,
                        }
                        if _pre_item["stock_name"]:
                            expanded.append(_pre_item)
                    print(f"  → 사전 수집 데이터 {len(_pre_mentions)}건 추가")
                except Exception as _e:
                    print(f"  [GEMINI] 사전 수집 데이터 변환 실패: {_e}")

            # 분석 안 된 원본 영상도 추가 (스킵된 것 제외)
            analyzed_urls = {item.get("link","") for item in (_new_items if _new_items else [])}
            for item in youtube_raw:
                if item.get("link","") not in analyzed_urls and item.get("link","") not in _precollected_urls:
                    expanded.append(item)

            all_data.extend(expanded)
            print(f"  → Gemini 분석 완료: {len(expanded)}건 (원본+발언 확장 포함)")
        except Exception as e:
            print(f"  [GEMINI] 유튜브 분석 실패 (기존 데이터로 계속 진행): {e}")
            all_data.extend(youtube_raw)
    else:
        all_data.extend(youtube_raw)
        if not GEMINI_API_KEY:
            print("\n[GEMINI] API 키 없음 → 유튜브 영상 분석 스킵")

    # ── 5. 애널리스트 리포트 (retry 루프: 08:00 KST 이후 5건 이상 확보까지 대기)
    import time as _time
    print("\n[5/5] 애널리스트 리포트 수집 (본문 크롤링 + Claude 요약 포함)...")
    if SKIP_ANALYST:
        print("  ⚡ 애널리스트 수집 스킵 (SKIP_ANALYST=true)")
        analyst_data = []
    else:
        _TARGET_HOUR   = 8
        _MIN_REPORTS   = 20
        _MAX_WAIT_MIN  = 120
        _RETRY_SEC     = 5 * 60

        analyst_data = []
        waited_min   = 0

        while waited_min <= _MAX_WAIT_MIN:
            _now = datetime.now(KST)

            if _now.hour < _TARGET_HOUR:
                _secs_to_target = (
                    (_TARGET_HOUR - _now.hour) * 3600
                    - _now.minute * 60
                    - _now.second
                )
                _wait = min(_secs_to_target, _RETRY_SEC)
                print(f"  ⏳ 08:00 KST 대기 중 (현재 {_now.strftime('%H:%M')}, {int(_wait//60)}분 후 재확인)...")
                _time.sleep(_wait)
                waited_min += _wait // 60
                continue

            _candidate  = safe_collect(collect_analyst, api_key=ANTHROPIC_API_KEY, label="애널리스트")
            _today_str  = datetime.now(KST).strftime("%y.%m.%d")
            _today_data = [d for d in _candidate if d.get("date", "") == _today_str]

            print(f"  → 오늘({_today_str}) 리포트: {len(_today_data)}건 / 전체 수집: {len(_candidate)}건")

            if len(_today_data) >= _MIN_REPORTS:
                analyst_data = _candidate
                print(f"  ✅ 기준({_MIN_REPORTS}건) 충족 → 진행")
                break

            _now2 = datetime.now(KST)
            if _now2.hour > 8 or (_now2.hour == 8 and _now2.minute >= 30):
                print(f"  ⚠️  08:30 KST 초과 → 건수 무관 강제 진행 ({len(_today_data)}건)")
                analyst_data = _candidate
                break

            if waited_min >= _MAX_WAIT_MIN:
                print(f"  ⚠️  최대 대기({_MAX_WAIT_MIN}분) 초과 → 수집된 데이터로 강제 진행")
                analyst_data = _candidate
                break

            print(f"  🔄 {_MIN_REPORTS}건 미달 → {_RETRY_SEC//60}분 후 재시도 (대기 누계: {waited_min}분)")
            _time.sleep(_RETRY_SEC)
            waited_min += _RETRY_SEC // 60

    all_data.extend(analyst_data)
    print(f"  → 최종 애널리스트 데이터: {len(analyst_data)}건")

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
        # KST 기준 날짜로 아카이브 (briefing_data 정의 전이라 now_kst 사용)
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
