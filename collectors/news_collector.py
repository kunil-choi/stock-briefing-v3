# collectors/news_collector.py
"""
뉴스 RSS 수집기 - v3
매일경제, 한국경제, 서울경제, 이데일리, 머니투데이 등 주요 경제신문사 RSS 수집
"""
import re
import feedparser
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

KST = timezone(timedelta(hours=9))


def _parse_published(entry) -> datetime:
    """RSS 엔트리의 published 날짜를 datetime으로 파싱"""
    for attr in ("published", "updated", "created"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return parsedate_to_datetime(val).astimezone(KST)
            except Exception:
                pass
    # BUG-N-1: 날짜 파싱 실패 시 현재 시각 반환 (24시간 필터 통과)
    return datetime.now(KST)


def _is_valid_feed(feed) -> bool:
    """
    BUG-N-2: feedparser가 에러 없이 빈 결과를 반환하는 경우 감지.
    bozo=1 은 피드 파싱 오류(잘못된 XML 등)를 의미.
    """
    if getattr(feed, "bozo", False):
        exc = getattr(feed, "bozo_exception", None)
        # CharacterEncodingOverride는 무해한 경고 — 정상 처리
        if exc and "CharacterEncodingOverride" in type(exc).__name__:
            return True
        return False
    return True


def collect_news(rss_feeds: dict, hours: int = 24) -> list:
    """
    RSS 피드에서 최근 N시간 이내 뉴스를 수집합니다.

    반환: [{"source_type":"뉴스", "source_name":str, "title":str,
            "summary":str, "link":str, "published":str}]

    BUG-N-3: 피드별 독립 try/except — 한 피드 실패가 전체 수집을 중단하지 않음
    """
    cutoff  = datetime.now(KST) - timedelta(hours=hours)
    results = []
    failed_feeds: list[str] = []

    for source_name, feed_url in rss_feeds.items():
        try:
            feed = feedparser.parse(feed_url)

            # BUG-N-2: 피드 유효성 검사
            if not _is_valid_feed(feed):
                print(f"  [뉴스] {source_name} 피드 파싱 오류 → 스킵")
                failed_feeds.append(source_name)
                continue

            if not feed.entries:
                print(f"  [뉴스] {source_name} 엔트리 없음 → 스킵")
                failed_feeds.append(source_name)
                continue

            count = 0
            for entry in feed.entries:
                published_dt = _parse_published(entry)
                if published_dt < cutoff:
                    continue

                title   = (getattr(entry, "title",   "") or "").strip()
                summary = (getattr(entry, "summary", "") or
                           getattr(entry, "description", "") or "").strip()
                link    = (getattr(entry, "link", "") or "").strip()

                # HTML 태그 제거 및 길이 제한
                summary = re.sub(r"<[^>]+>", "", summary)[:800]

                if not title:
                    continue

                # BUG-N-4: 중복 링크 방지 (같은 link가 여러 피드에 배포되는 경우)
                results.append({
                    "source_type": "뉴스",
                    "source_name": source_name,
                    "title":       title,
                    "summary":     summary,
                    "link":        link,
                    "published":   published_dt.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
                })
                count += 1

            print(f"  [뉴스] {source_name}: {count}건")

        except Exception as e:
            print(f"  [뉴스] {source_name} 수집 실패: {e}")
            failed_feeds.append(source_name)

    # BUG-N-4: 전체 결과 중 link 기준 중복 제거
    seen_links: set[str] = set()
    deduped: list[dict]  = []
    for item in results:
        link = item.get("link", "")
        if link and link in seen_links:
            continue
        if link:
            seen_links.add(link)
        deduped.append(item)

    if failed_feeds:
        print(f"\n  [뉴스] 실패 피드: {', '.join(failed_feeds)}")

    print(f"\n[뉴스 합계] {len(deduped)}건 (중복 제거 전: {len(results)}건)")
    return deduped
