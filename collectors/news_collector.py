# collectors/news_collector.py
"""
뉴스 RSS 수집기 - v3
매일경제, 한국경제, 서울경제, 이데일리, 머니투데이 등 주요 경제신문사 RSS 수집

수정 이력:
- BUG-N-1: 날짜 파싱 실패 시 현재 시각 반환 (24시간 필터 통과)
- BUG-N-2: feedparser bozo 오류 감지, CharacterEncodingOverride는 무해 처리
- BUG-N-3: 피드별 독립 try/except
- BUG-N-4: link 기준 중복 제거
- BUG-7 FIX: link 없는 항목의 title 기반 중복 제거 추가
             link URL 정규화(쿼리 파라미터 제거) 후 비교
"""
import re
import feedparser
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urlunparse

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


def _normalize_link(link: str) -> str:
    """
    BUG-7 FIX: URL 정규화 — 쿼리 파라미터·프래그먼트 제거 후 소문자 변환.
    동일 기사가 트래킹 파라미터만 다르게 여러 피드에 배포되는 경우를 잡기 위함.

    예) https://news.example.com/article/123?utm_source=rss&ref=main
     → https://news.example.com/article/123
    """
    if not link:
        return ""
    try:
        parsed = urlparse(link.strip())
        # scheme + netloc + path만 유지, query·fragment 제거
        normalized = urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            "",   # params
            "",   # query  ← 제거
            "",   # fragment ← 제거
        ))
        return normalized
    except Exception:
        return link.strip().lower()


def _normalize_title(title: str) -> str:
    """
    BUG-7 FIX: 제목 정규화 — 공백·특수문자 제거 후 소문자 변환.
    링크가 없는 항목의 중복 감지에 사용.

    예) "[속보] 삼성전자, 2분기 실적 발표"
     → "속보삼성전자2분기실적발표"
    """
    if not title:
        return ""
    # 한글·영문·숫자만 남기고 나머지 제거
    normalized = re.sub(r"[^\w가-힣]", "", title).lower()
    return normalized


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

    # ── BUG-7 FIX: 중복 제거 강화 ────────────────────────────────────────────
    # 기존: link 있는 항목만 중복 제거, link 없는 항목은 모두 통과
    # 수정: ① link가 있으면 정규화된 URL로 비교
    #       ② link가 없으면 정규화된 title로 비교
    #       → 두 기준 모두 적용해 중복 원천 차단
    seen_links:  set[str] = set()   # 정규화된 link 추적
    seen_titles: set[str] = set()   # 정규화된 title 추적 (link 없는 항목용)
    deduped: list[dict]   = []

    for item in results:
        link  = item.get("link", "")
        title = item.get("title", "")

        norm_link  = _normalize_link(link)
        norm_title = _normalize_title(title)

        if norm_link:
            # link가 있는 항목: 정규화된 link 기준으로 중복 판단
            if norm_link in seen_links:
                continue
            seen_links.add(norm_link)
        else:
            # BUG-7 FIX: link가 없는 항목: 정규화된 title 기준으로 중복 판단
            if norm_title and norm_title in seen_titles:
                continue

        # title은 link 유무와 관계없이 항상 seen_titles에 등록
        # (link가 다르더라도 제목이 완전히 같으면 중복으로 볼 수 있음)
        if norm_title:
            seen_titles.add(norm_title)

        deduped.append(item)

    if failed_feeds:
        print(f"\n  [뉴스] 실패 피드: {', '.join(failed_feeds)}")

    print(f"\n[뉴스 합계] {len(deduped)}건 (중복 제거 전: {len(results)}건)")
    return deduped
