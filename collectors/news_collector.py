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
    return datetime.now(KST)


def collect_news(rss_feeds: dict, hours: int = 24) -> list:
    """
    RSS 피드에서 최근 N시간 이내 뉴스를 수집합니다.
    반환: [{"source_type":"뉴스", "source_name":str, "title":str,
            "summary":str, "link":str, "published":str}]
    """
    cutoff  = datetime.now(KST) - timedelta(hours=hours)
    results = []

    for source_name, feed_url in rss_feeds.items():
        print(f"  [뉴스] {source_name} RSS 수집 중...")
        try:
            feed  = feedparser.parse(feed_url)
            count = 0
            for entry in feed.entries:
                published_dt = _parse_published(entry)
                if published_dt < cutoff:
                    continue

                title   = entry.get("title",   "").strip()
                summary = entry.get("summary", entry.get("description", "")).strip()
                link    = entry.get("link",    "")

                # HTML 태그 제거 및 길이 제한
                summary = re.sub(r"<[^>]+>", "", summary)[:800]

                if not title:
                    continue

                results.append({
                    "source_type": "뉴스",
                    "source_name": source_name,
                    "title":       title,
                    "summary":     summary,
                    "link":        link,
                    "published":   published_dt.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
                })
                count += 1

            print(f"    → {count}건 수집")

        except Exception as e:
            print(f"    → 오류: {e}")

    print(f"\n[뉴스 합계] {len(results)}건")
    return results
