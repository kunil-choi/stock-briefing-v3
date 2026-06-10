# analyzer/ai_analyzer.py
"""
AI 주식 브리핑 분석 엔진

수정 이력:
- BUG-CR-1   : 상대 임포트로 전환
- BUG-HIDDEN : 히든픽 로직 정비
- BUG-CACHE  : stock_map 캐시 키 우선순위 정비
- BUG-KEY-1  : ai_strategy 키 통일
- V2-PROMPT  : Claude 프롬프트를 V2 수준으로 개선
               (summary / catalyst / risk / channel_mentions 필드 추가)
- V2-SYNC    : channel_mentions → reasons 동기화 블록 추가
- FIX-ANA-2  : generate_market_summary 데드코드 제거
               (market_summary는 메인 분석 JSON에서 수신하므로 별도 호출 불필요)
"""

import json
import os
import re
import math
from datetime import datetime, timezone, timedelta

from .api_client import call_claude_with_retry

KST              = timezone(timedelta(hours=9))
STOCK_CACHE_FILE = "data/stock_names_cache.json"
OUTPUT_FILE      = "data/briefing_data.json"
CB               = "```"

_SKIP_NAMES = {
    "삼성", "현대", "LG", "SK", "롯데", "한화", "포스코", "GS", "CJ",
    "KT", "LS", "DB", "OCI", "KG", "SG", "TG", "NH", "KB",
    "AI", "IT", "EV", "US", "EU", "UN", "M", "A", "S", "K",
    "전자", "화학", "건설", "증권", "은행", "보험", "자동차", "철강",
    "에너지", "바이오", "게임", "반도체", "배터리", "인터넷", "소프트웨어",
    "기업", "그룹", "홀딩스", "코리아", "코퍼레이션",
    "금리", "환율", "달러", "원화", "코스피", "코스닥", "나스닥",
    "매수", "매도", "상승", "하락", "급등", "급락", "시장", "투자",
    "주식", "펀드", "ETF", "채권", "선물", "옵션",
    "경제", "금융", "부동산", "인플레이션", "디플레이션",
    "중국", "미국", "유럽", "일본", "한국",
}
_MIN_NAME_LEN       = 2
_HIGH_QUALITY_TYPES = {"애널리스트", "경제방송TV", "경제방송"}


# ── 종목명 유효성 검사 ─────────────────────────────────────────────────────────

def _is_valid_stock_name(name: str) -> bool:
    if len(name) < _MIN_NAME_LEN:
        return False
    if name in _SKIP_NAMES:
        return False
    if re.match(r'^[A-Z]{2,3}$', name):
        return False
    return True


# ── 채널 가중치 ───────────────────────────────────────────────────────────────

def _channel_weight(subscribers: int) -> float:
    """구독자 수 기반 채널 가중치 (로그 스케일, 0.5~3.0)"""
    if not subscribers or subscribers <= 0:
        return 0.5
    base = math.log10(max(subscribers, 10000)) - math.log10(100000)
    return min(1.0 + max(0.0, base), 3.0)


def _build_channel_weight_map(channels_data: dict) -> dict:
    weight_map = {}
    if not channels_data:
        return weight_map
    for section in ["broadcast", "youtuber", "securities"]:
        for ch in channels_data.get(section, []):
            name = ch.get("name", "")
            subs = ch.get("subscribers", 0)
            if name:
                weight_map[name] = _channel_weight(subs)
    return weight_map


# ── 종목 코드 로드 ─────────────────────────────────────────────────────────────

def load_stock_names() -> dict:
    """
    종목명 → 코드 딕셔너리 반환.
    우선순위: 오늘 날짜 캐시 → KRX API → fallback 내장 목록
    """
    today_kst = datetime.now(KST).strftime("%Y-%m-%d")

    if os.path.exists(STOCK_CACHE_FILE):
        try:
            with open(STOCK_CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            cached_map = cache.get("stock_map") or cache.get("stocks", {})
            if cache.get("date") == today_kst and cached_map:
                print(f"[종목캐시] {len(cached_map)}개 로드 (캐시)")
                return cached_map
        except Exception:
            pass

    stock_map = {}
    try:
        import requests
        for market_id in ["STK", "KSQ"]:
            url = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
            payload = {
                "bld":         "dbms/MDC/STAT/standard/MDCSTAT01901",
                "mktId":       market_id,
                "share":       "1",
                "csvxls_isNo": "false",
            }
            headers = {"Referer": "http://data.krx.co.kr/"}
            resp = requests.post(url, data=payload, headers=headers, timeout=10)
            data = resp.json()
            for item in data.get("OutBlock_1", []):
                name = item.get("ISU_ABBRV", "").strip()
                code = item.get("ISU_SRT_CD", "").strip()
                if name and code:
                    stock_map[name] = code
        if stock_map:
            os.makedirs("data", exist_ok=True)
            with open(STOCK_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {"date": today_kst, "stock_map": stock_map, "stocks": stock_map},
                    f, ensure_ascii=False,
                )
            print(f"[종목로드] KRX에서 {len(stock_map)}개 로드")
            return stock_map
    except Exception as e:
        print(f"[종목로드] KRX 요청 실패: {e}, fallback 사용")

    stock_map = {
        "삼성전자": "005930", "SK하이닉스": "000660", "LG에너지솔루션": "373220",
        "삼성바이오로직스": "207940", "현대차": "005380", "NAVER": "035420",
        "카카오": "035720", "셀트리온": "068270", "삼성SDI": "006400",
        "LG화학": "051910", "KB금융": "105560", "신한지주": "055550",
        "하나금융지주": "086790", "현대모비스": "012330", "LG전자": "066570",
        "포스코홀딩스": "005490", "삼성물산": "028260", "SK이노베이션": "096770",
        "기아": "000270", "카카오뱅크": "323410", "크래프톤": "259960",
        "HMM": "011200", "한국전력": "015760", "삼성생명": "032830",
        "SK텔레콤": "017670", "KT": "030200", "롯데케미칼": "011170",
        "CJ제일제당": "097950", "아모레퍼시픽": "090430", "엔씨소프트": "036570",
        "넷마블": "251270", "두산에너빌리티": "034020", "현대건설": "000720",
        "GS건설": "006360", "삼성전기": "009150", "SK바이오사이언스": "302440",
        "카카오페이": "377300", "LG이노텍": "011070", "고려아연": "010130",
        "OCI": "010060", "한화솔루션": "009830", "한화에어로스페이스": "012450",
        "현대제철": "004020", "HD현대중공업": "329180", "삼성증권": "016360",
        "미래에셋증권": "006800", "한국항공우주": "047810", "에코프로비엠": "247540",
        "에코프로": "086520", "포스코퓨처엠": "003670", "엘앤에프": "066970",
        "레인보우로보틱스": "277810", "두산로보틱스": "454910", "HD현대": "267250",
        "KT&G": "033780", "SKC": "011790", "한미반도체": "042700",
        "이오테크닉스": "039030", "솔브레인": "357780", "피에스케이": "319660",
        "클래시스": "214150", "코스메카코리아": "241710", "오스코텍": "039200",
        "알테오젠": "196170", "유한양행": "000100", "종근당": "185750",
        "HLB": "028300", "리가켐바이오": "141080", "메드팩토": "235980",
        "카나리아바이오": "016150", "현대바이오": "048410", "HPSP": "403870",
        "신성델타테크": "065350", "DB하이텍": "000990", "제우스": "079170",
        "심텍": "036710", "원익IPS": "240810", "테스": "095610",
        "동진쎄미켐": "005290", "SK스퀘어": "402340", "LG디스플레이": "034220",
    }
    print(f"[종목로드] fallback {len(stock_map)}개 사용")
    return stock_map


# ── 언급 추출 ─────────────────────────────────────────────────────────────────

def extract_mentions(all_data: list, stock_map: dict,
                     channels_data: dict = None) -> dict:
    weight_map = _build_channel_weight_map(channels_data) if channels_data else {}

    type_map = {
        "뉴스":       "뉴스",
        "경제방송":   "경제방송",
        "경제방송TV": "경제방송TV",
        "유튜브":     "유튜브",
        "증권사":     "유튜브",
        "애널리스트": "애널리스트",
    }
    default_weights = {
        "뉴스": 1.5, "경제방송": 1.8, "경제방송TV": 1.8,
        "애널리스트": 2.5, "유튜브": 1.0,
    }

    mentions = {}

    for item in all_data:
        raw_type = item.get("source_type", "유튜브")
        ch_type  = type_map.get(raw_type, "유튜브")
        src_name = item.get("source_name", "")
        title    = item.get("title", "")
        summary  = item.get("summary", "") or item.get("content", "")
        link     = item.get("link", "") or item.get("url", "")
        text     = f"{title} {summary}"
        weight   = weight_map.get(src_name, default_weights.get(ch_type, 1.0))

        for name, code in stock_map.items():
            if not _is_valid_stock_name(name):
                continue
            if name not in text:
                continue

            content_id = f"{src_name}_{link}_{name}"

            if name not in mentions:
                mentions[name] = {
                    "code":           code,
                    "total_count":    0,
                    "weighted_score": 0.0,
                    "channel_types":  set(),
                    "channels":       {},
                }

            entry = mentions[name]
            existing_ids = [
                m["content_id"]
                for ch_items in entry["channels"].values()
                for m in ch_items
            ]
            if content_id in existing_ids:
                continue

            entry["channel_types"].add(ch_type)
            if ch_type not in entry["channels"]:
                entry["channels"][ch_type] = []

            idx     = text.find(name)
            snippet = text[max(0, idx - 50): idx + 150].strip()

            entry["channels"][ch_type].append({
                "source_name": src_name,
                "snippet":     snippet,
                "link":        link,
                "content_id":  content_id,
                "weight":      round(weight, 2),
            })
            entry["total_count"]    += 1
            entry["weighted_score"] += weight

    for name in mentions:
        mentions[name]["channel_types"] = list(mentions[name]["channel_types"])

    print(f"[언급추출] {len(mentions)}개 종목 발견")
    return mentions


def filter_mentions(mentions: dict, min_channel_types: int = 2) -> list:
    filtered = [
        (name, data) for name, data in mentions.items()
        if len(data["channel_types"]) >= min_channel_types
    ]
    filtered.sort(key=lambda x: x[1]["weighted_score"], reverse=True)
    print(f"[필터링] {len(filtered)}개 종목 선택 (채널 유형 ≥{min_channel_types})")
    return filtered


# ── 히든픽 후보 추출 ──────────────────────────────────────────────────────────

def extract_hidden_picks(mentions: dict, filtered_names: set,
                          max_picks: int = 3) -> list:
    candidates = []
    for name, data in mentions.items():
        if name in filtered_names:
            continue
        ch_types = set(data["channel_types"])
        if len(ch_types) != 1:
            continue
        sole_type = list(ch_types)[0]
        if sole_type not in _HIGH_QUALITY_TYPES:
            continue
        candidates.append({
            "name":           name,
            "code":           data["code"],
            "channel_type":   sole_type,
            "channels":       data["channels"],
            "weighted_score": round(data["weighted_score"], 2),
            "total_count":    data["total_count"],
        })
    candidates.sort(key=lambda x: x["weighted_score"], reverse=True)
    result = candidates[:max_picks]
    print(f"[히든픽] 후보 {len(candidates)}개 중 {len(result)}개 선택")
    return result


# ── Claude 프롬프트 생성 (V2 수준) ───────────────────────────────────────────

def build_analysis_prompt(filtered_mentions: list, hidden_candidates: list,
                           all_data: list, today_date: str,
                           now_kst: str) -> str:
    """
    V2 수준의 Claude 분석 프롬프트.
    - 종목별 summary / catalyst / risk / channel_mentions 필드 요청
    - reasons 배열은 source_type / source_name / detail / source_url 구조
    - hidden_picks는 반드시 hidden_candidates 목록에서만 선택
    """

    # 헤드라인 수집 (소스별 최대 60건)
    headlines = []
    for item in all_data[:150]:
        title   = (item.get("title") or "").strip()
        src     = item.get("source_name", "")
        stype   = item.get("source_type", "")
        stock   = item.get("stock_name", "")
        url     = item.get("link") or item.get("url", "")
        summary = (item.get("summary") or item.get("content") or "")[:120]
        if title:
            line = f"[{stype}/{src}] {title}"
            if stock:
                line += f" (종목: {stock})"
            if summary:
                line += f" → {summary}"
            if url:
                line += f" [URL: {url}]"
            headlines.append(line)
    headlines = headlines[:60]
    headlines_text = "\n".join(headlines)

    # 관심종목 (상위 15개) — 채널별 원문 snippet 포함
    top_stocks  = filtered_mentions[:15]
    stocks_info = []
    for rank, (name, data) in enumerate(top_stocks, 1):
        line = (f"{rank}. {name} (코드:{data['code']}, "
                f"언급:{data['total_count']}회, "
                f"가중점수:{data['weighted_score']:.1f}, "
                f"채널유형:{','.join(data['channel_types'])})")
        stocks_info.append(line)
        for ch_type, items in data["channels"].items():
            for item in items[:5]:
                w_str = f"[가중치:{item.get('weight', 1.0):.1f}]"
                link  = item.get("link", "")
                url_str = f" [URL: {link}]" if link else ""
                stocks_info.append(
                    f"   [{ch_type}]{w_str} {item['source_name']}: "
                    f"{item['snippet'][:200]}{url_str}"
                )

    # 히든픽 후보
    hidden_info = []
    for i, pick in enumerate(hidden_candidates, 1):
        name    = pick["name"]
        ch_type = pick["channel_type"]
        line = (f"{i}. {name} (코드:{pick['code']}, "
                f"채널:{ch_type}, 가중점수:{pick['weighted_score']:.1f})")
        hidden_info.append(line)
        for item in pick["channels"].get(ch_type, [])[:3]:
            link    = item.get("link", "")
            url_str = f" [URL: {link}]" if link else ""
            hidden_info.append(
                f"   [{ch_type}] {item['source_name']}: "
                f"{item['snippet'][:200]}{url_str}"
            )

    stocks_text = "\n".join(stocks_info)
    hidden_text = "\n".join(hidden_info) if hidden_info else "해당 없음"

    return f"""당신은 15년 경력의 한국 주식시장 전문 애널리스트입니다.
아래 데이터를 분석하여 오늘의 주식 브리핑 JSON을 작성하세요.

[분석 날짜] {today_date} ({now_kst} KST)

[수집된 원문 데이터 - 채널별 언급 내용]
{headlines_text}

[관심종목 후보 - 2개 이상 채널 유형에서 언급, 가중치 점수 기준 정렬]
{stocks_text}

[히든픽 후보 - 전문가 소스(애널리스트/경제방송TV/경제방송)에서만 단독 언급]
{hidden_text}

[출력 형식] 반드시 아래 JSON 구조만 출력:
{CB}json
{{
  "briefing_date": "{today_date}",
  "market_summary": "시장 전체 요약 (5개 단락, \\n\\n 구분, 각 단락 3~4문장. 시장개요/주요이슈/투자포인트/리스크/전망 순서)",
  "hot_sectors": [
    {{"name": "섹터명", "reason": "주목 이유 1문장"}}
  ],
  "stocks": [
    {{
      "rank": 1,
      "name": "종목명",
      "code": "종목코드",
      "signal": "긍정|부정|중립 중 하나",
      "summary": "기업 소개 2~3문장 (업종, 사업내용, 시장 내 위치)",
      "catalyst": "상승 촉매 2~3문장 (구체적 수치/이벤트/목표가 포함)",
      "risk": "핵심 리스크 1~2문장",
      "channel_mentions": [
        {{
          "source_type": "뉴스|경제방송|경제방송TV|유튜브|애널리스트 중 하나",
          "source_name": "채널명 또는 증권사명",
          "content": "이 채널에서 이 종목을 언급한 핵심 내용 1~2문장",
          "url": "원문 URL (없으면 빈 문자열)"
        }}
      ],
      "channel_counts": {{}},
      "total_count": 0,
      "weighted_score": 0.0,
      "overlap_count": 0,
      "reasons": [
        {{
          "source_type": "채널유형",
          "source_name": "출처명",
          "detail": "언급 내용 요약 1문장",
          "source_url": "URL 또는 빈 문자열"
        }}
      ]
    }}
  ],
  "hidden_picks": [
    {{
      "rank": 1,
      "name": "종목명",
      "code": "종목코드",
      "signal": "positive",
      "summary": "기업 소개 2~3문장",
      "catalyst": "전문가가 주목한 이유 2~3문장 (구체적 근거 포함)",
      "risk": "핵심 리스크 1문장",
      "channel_type": "애널리스트|경제방송TV|경제방송 중 하나",
      "channel_name": "채널명 또는 증권사명",
      "reasons": [
        {{
          "source_type": "채널유형",
          "source_name": "출처명",
          "detail": "언급 내용 요약",
          "source_url": "URL 또는 빈 문자열"
        }}
      ]
    }}
  ],
  "ai_strategy": "오늘의 AI 투자 전략 (300자 이상, 구체적 매수/비중/리스크관리 액션 포함)"
}}
{CB}

[작성 규칙]
1. stocks: 관심종목 후보에서 가중치 점수 높은 순 최대 5개 선택
2. signal: 언급 맥락 분석 — 긍정적=긍정, 부정적=부정, 단순언급=중립
3. summary / catalyst / risk: 반드시 작성, 빈 문자열 절대 금지
4. channel_mentions: 위 원문 데이터에서 해당 종목을 실제로 언급한 채널만 기재 (최대 4개)
5. hidden_picks: 반드시 위 [히든픽 후보] 목록에서만 선택, 임의 추가 절대 금지
6. hidden_picks 후보가 없으면 빈 배열 [] 반환
7. market_summary: 5단락, \\n\\n으로 구분, 각 단락 3~4문장, 400자 이상
8. ai_strategy: 구체적 종목/비중/매수전략/리스크 관리 포함, 300자 이상
9. channel_counts / total_count / weighted_score / overlap_count: 위 데이터 값 그대로
10. reasons의 텍스트 키는 반드시 "detail" 사용 ("reason" 사용 금지)
11. URL은 원문 데이터에 있는 것만 사용, 없으면 빈 문자열
12. 국내 상장 종목만 포함, 해외 주식/지수/ETF 제외"""


# ── source_url 복원 ───────────────────────────────────────────────────────────

def _restore_source_url(reason: dict, real_channel_data: list) -> dict:
    if reason.get("source_url"):
        return reason
    src_name = reason.get("source_name", "")
    for item in real_channel_data:
        if item.get("source_name") == src_name:
            url = item.get("link") or item.get("url", "")
            if url:
                reason["source_url"] = url
                return reason
    return reason


# ── JSON 파싱 ─────────────────────────────────────────────────────────────────

def _try_parse_json(text: str):
    if not text:
        return None
    match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    if match:
        candidate = match.group(1)
    else:
        start = text.find('{')
        end   = text.rfind('}')
        if start == -1 or end == -1:
            return None
        candidate = text[start:end + 1]

    candidate = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', candidate)
    candidate = re.sub(r',\s*([}\]])', r'\1', candidate)

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        lines   = [ln for ln in candidate.split('\n') if ln.strip()]
        cleaned = '\n'.join(lines)
        try:
            return json.loads(cleaned)
        except Exception:
            return None


# ── fallback HTML ─────────────────────────────────────────────────────────────

def _fallback_html(channels_data, gh_repo, market_overview,
                   all_data, briefing_date, message):
    from .html_generator import generate_html
    return generate_html(
        {
            "briefing_date":  briefing_date,
            "market_summary": message,
            "hot_sectors":    [],
            "stocks":         [],
            "hidden_picks":   [],
            "ai_strategy":    "",
        },
        channels_data, gh_repo, "", market_overview, all_data,
    )


# ── 메인 분석 함수 ────────────────────────────────────────────────────────────

def analyze_and_generate_html(
    all_data: list,
    api_key: str,
    channels_data: dict = None,
    gh_repo: str = "",
    market_overview: dict = None,
) -> str:
    print("=" * 60)
    print("[AI분석] 시작")
    now_kst    = datetime.now(KST)
    today_date = now_kst.strftime("%Y-%m-%d")
    now_str    = now_kst.strftime("%H:%M")

    os.makedirs("data", exist_ok=True)

    # ── 1. 종목 목록 로드 ────────────────────────────────────────────────────
    stock_map = load_stock_names()
    if not stock_map:
        print("[AI분석] 종목 목록 로드 실패")
        return _fallback_html(
            channels_data, gh_repo, market_overview,
            all_data, today_date, "종목 데이터를 불러오지 못했습니다.",
        )

    # ── 2. 언급 추출 ─────────────────────────────────────────────────────────
    mentions = extract_mentions(all_data, stock_map, channels_data)

    # ── 3. 관심종목 필터링 ───────────────────────────────────────────────────
    filtered       = filter_mentions(mentions)
    filtered_names = {name for name, _ in filtered}

    # ── 4. 히든픽 후보 추출 ──────────────────────────────────────────────────
    hidden_candidates = extract_hidden_picks(mentions, filtered_names)

    if not filtered and not hidden_candidates:
        print("[AI분석] 관심종목/히든픽 모두 없음")
        return _fallback_html(
            channels_data, gh_repo, market_overview,
            all_data, today_date, "오늘 분석 가능한 종목이 없습니다.",
        )

    # ── 5. Claude 호출 ───────────────────────────────────────────────────────
    prompt = build_analysis_prompt(
        filtered, hidden_candidates, all_data, today_date, now_str
    )
    print(f"[AI분석] Claude 호출 "
          f"(관심종목 {len(filtered)}개, 히든픽 후보 {len(hidden_candidates)}개)")

    response = call_claude_with_retry(prompt, api_key, max_tokens=16000)

    # ── 6. JSON 파싱 ─────────────────────────────────────────────────────────
    result = _try_parse_json(response)
    if not result:
        print("[AI분석] JSON 파싱 실패 → fallback HTML 반환")
        return _fallback_html(
            channels_data, gh_repo, market_overview,
            all_data, today_date, "AI 분석 결과를 파싱하지 못했습니다.",
        )

    print(f"[AI분석] 파싱 성공: "
          f"관심종목 {len(result.get('stocks', []))}개, "
          f"히든픽 {len(result.get('hidden_picks', []))}개")

    # ── 7. 실제 집계값으로 카운트 보정 ──────────────────────────────────────
    mention_dict = dict(filtered)
    for stock in result.get("stocks", []):
        name = stock.get("name", "")
        if name in mention_dict:
            d = mention_dict[name]
            stock["channel_counts"] = {k: len(v) for k, v in d["channels"].items()}
            stock["total_count"]    = d["total_count"]
            stock["weighted_score"] = round(d["weighted_score"], 2)
            stock["overlap_count"]  = len(d["channel_types"])

    # ── 8. 히든픽 보정 및 중복 제거 ─────────────────────────────────────────
    hidden_dict = {p["name"]: p for p in hidden_candidates}
    for hp in result.get("hidden_picks", []):
        name = hp.get("name", "")
        if name in filtered_names:
            print(f"  [히든픽중복제거] {name} → 관심종목과 중복")
            hp["_remove"] = True
            continue
        if name in hidden_dict:
            d = hidden_dict[name]
            hp["channel_type"]   = d["channel_type"]
            hp["total_count"]    = d["total_count"]
            hp["weighted_score"] = d["weighted_score"]

    result["hidden_picks"] = [
        hp for hp in result.get("hidden_picks", [])
        if not hp.get("_remove")
    ]

    # ── 9. source_url 복원 ───────────────────────────────────────────────────
    for stock in result.get("stocks", []):
        for reason in stock.get("reasons", []):
            _restore_source_url(reason, all_data)
    for pick in result.get("hidden_picks", []):
        for reason in pick.get("reasons", []):
            _restore_source_url(reason, all_data)

    # ── 10. V2-SYNC: channel_mentions → reasons 동기화 ───────────────────────
    for stock in result.get("stocks", []) + result.get("hidden_picks", []):
        cm = stock.get("channel_mentions", [])
        if cm and not stock.get("reasons"):
            stock["reasons"] = [
                {
                    "source_type": m.get("source_type", ""),
                    "source_name": m.get("source_name", ""),
                    "detail":      m.get("content", ""),
                    "source_url":  m.get("url", ""),
                }
                for m in cm
            ]

    # ── 11. 검증 ─────────────────────────────────────────────────────────────
    from .validation import validate_stocks
    result = validate_stocks(result, all_data, api_key, stock_map)

    # ── 12. 결과 저장 (chart_base64 제외) ────────────────────────────────────
    save_data = json.loads(json.dumps(result))
    for stock in save_data.get("stocks", []):
        stock.pop("chart_base64", None)
    for pick in save_data.get("hidden_picks", []):
        pick.pop("chart_base64", None)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    print(f"[AI분석] 결과 저장: {OUTPUT_FILE}")

    # ── 13. HTML 생성 ────────────────────────────────────────────────────────
    gh_token = os.environ.get("GH_TOKEN", "")
    from .html_generator import generate_html
    html = generate_html(
        result, channels_data, gh_repo, gh_token,
        market_overview, all_data,
    )
    print("[AI분석] 완료")
    return html
