# analyzer/ai_analyzer.py
# 전체 파일

import json
import os
import re
import math
from datetime import datetime, timezone, timedelta
from anthropic_api import call_claude_with_retry

KST = timezone(timedelta(hours=9))
STOCK_CACHE_FILE = "data/stock_names_cache.json"
OUTPUT_FILE = "data/briefing_data.json"
CB = "```"

_SKIP_NAMES = {
    "삼성", "현대", "LG", "SK", "롯데", "한화", "포스코", "GS", "CJ",
    "KT", "LS", "DB", "OCI", "KG", "SG", "TG", "NH", "KB", "NH",
    "AI", "IT", "EV", "US", "EU", "UN", "M", "A", "S", "K",
    "전자", "화학", "건설", "증권", "은행", "보험", "자동차", "철강",
    "에너지", "바이오", "게임", "반도체", "배터리", "인터넷", "소프트웨어",
    "기업", "그룹", "홀딩스", "코리아", "코퍼레이션",
    "금리", "환율", "달러", "원화", "코스피", "코스닥", "나스닥",
    "매수", "매도", "상승", "하락", "급등", "급락", "시장", "투자",
    "주식", "펀드", "ETF", "채권", "선물", "옵션",
    "경제", "금융", "부동산", "인플레이션", "디플레이션",
    "중국", "미국", "유럽", "일본", "한국"
}
_MIN_NAME_LEN = 2


def _is_valid_stock_name(name: str) -> bool:
    """종목명 유효성 검사"""
    if len(name) < _MIN_NAME_LEN:
        return False
    if name in _SKIP_NAMES:
        return False
    # 2글자 영문 대문자만으로 이루어진 경우 제외
    if re.match(r'^[A-Z]{2,3}$', name):
        return False
    return True


# ── BUG-WEIGHT-1: 채널 가중치 함수 추가 ──────────────────────────────────
def _channel_weight(subscribers: int) -> float:
    """
    구독자 수 기반 채널 가중치 계산 (로그 스케일)
    - 기준: 100,000명 → 1.0
    - 1,000,000명 → ~2.0
    - 3,200,000명(최대) → ~2.85 (상한 3.0)
    - 0명(미확인) → 0.5
    """
    if subscribers <= 0:
        return 0.5
    base = math.log10(max(subscribers, 10000)) - math.log10(100000)
    weight = 1.0 + max(0.0, base)
    return min(weight, 3.0)


def _build_channel_weight_map(channels_data: dict) -> dict:
    """
    channels.json의 broadcast/youtuber/securities 채널명 → 가중치 매핑 딕셔너리 생성
    """
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
# ─────────────────────────────────────────────────────────────────────────────


def load_stock_names() -> dict:
    """KRX 종목 목록 로드 (캐시 우선, 실패 시 하드코딩 fallback)"""
    today_kst = datetime.now(KST).strftime("%Y-%m-%d")
    
    # 캐시 확인
    if os.path.exists(STOCK_CACHE_FILE):
        try:
            with open(STOCK_CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("date") == today_kst and cache.get("stock_map"):
                print(f"[종목캐시] {len(cache['stock_map'])}개 로드 (캐시)")
                return cache["stock_map"]
        except Exception:
            pass

    # KRX API 요청
    stock_map = {}
    try:
        import requests
        for market_id in ["STK", "KSQ"]:  # KOSPI, KOSDAQ
            url = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
            payload = {
                "bld": "dbms/MDC/STAT/standard/MDCSTAT01901",
                "mktId": market_id,
                "share": "1",
                "csvxls_isNo": "false"
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
                json.dump({"date": today_kst, "stock_map": stock_map}, f, ensure_ascii=False)
            print(f"[종목로드] KRX에서 {len(stock_map)}개 로드")
            return stock_map
    except Exception as e:
        print(f"[종목로드] KRX 요청 실패: {e}, fallback 사용")

    # Fallback 하드코딩 목록
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


def extract_mentions(all_data: list, stock_map: dict, channels_data: dict = None) -> dict:
    """
    수집된 데이터에서 종목 언급을 추출하고 채널 가중치를 적용.
    
    반환 구조:
    {
      "종목명": {
        "code": "종목코드",
        "total_count": int,          # 원시 언급 횟수
        "weighted_score": float,     # 가중치 적용 점수 (정렬 기준)
        "channel_types": set,        # 채널 유형 집합
        "channels": {
          "채널유형": [
            {"source_name": ..., "snippet": ..., "link": ...,
             "content_id": ..., "weight": float}
          ]
        }
      }
    }
    """
    # 채널 가중치 맵 구성
    # BUG-WEIGHT-2: channels_data가 없을 때 빈 딕셔너리로 안전하게 처리
    weight_map = _build_channel_weight_map(channels_data) if channels_data else {}

    # source_type 정규화 매핑
    type_map = {
        "뉴스":     "뉴스",
        "경제방송":  "경제방송",
        "경제방송TV": "경제방송TV",
        "유튜브":   "유튜브",
        "증권사":   "유튜브",      # BUG-M4 유지
        "애널리스트": "애널리스트",
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

        # 채널 가중치 결정
        # BUG-WEIGHT-3: source_name으로 매핑, 없으면 source_type 기반 기본값
        weight = weight_map.get(src_name)
        if weight is None:
            # 유형별 기본 가중치 (채널 목록에 없는 뉴스 등)
            default_weights = {
                "뉴스":      1.5,   # 언론은 중간 이상 신뢰도
                "경제방송":  1.8,
                "경제방송TV": 1.8,
                "애널리스트": 2.5,  # 애널리스트 리포트는 높은 가중치
                "유튜브":    1.0,
            }
            weight = default_weights.get(ch_type, 1.0)

        for name, code in stock_map.items():
            if not _is_valid_stock_name(name):
                continue
            if name not in text:
                continue

            # 중복 방지용 content_id
            content_id = f"{src_name}_{link}_{name}"

            if name not in mentions:
                mentions[name] = {
                    "code": code,
                    "total_count": 0,
                    "weighted_score": 0.0,
                    "channel_types": set(),
                    "channels": {}
                }

            entry = mentions[name]

            # 이미 동일 content로 추가된 경우 skip
            existing_ids = [
                m["content_id"]
                for ch_items in entry["channels"].values()
                for m in ch_items
            ]
            if content_id in existing_ids:
                continue

            # 채널 유형 등록
            entry["channel_types"].add(ch_type)
            if ch_type not in entry["channels"]:
                entry["channels"][ch_type] = []

            # 스니펫 (200자)
            idx = text.find(name)
            snippet = text[max(0, idx - 50): idx + 150].strip()

            entry["channels"][ch_type].append({
                "source_name": src_name,
                "snippet": snippet,
                "link": link,
                "content_id": content_id,
                "weight": round(weight, 2)   # BUG-WEIGHT-4: 가중치 저장
            })

            entry["total_count"] += 1
            entry["weighted_score"] += weight  # BUG-WEIGHT-5: 가중치 누적

    # set → list 변환 (JSON 직렬화 대비)
    for name in mentions:
        mentions[name]["channel_types"] = list(mentions[name]["channel_types"])

    print(f"[언급추출] {len(mentions)}개 종목 발견")
    return mentions


def filter_mentions(mentions: dict, min_channel_types: int = 2) -> list:
    """
    2개 이상 채널 유형에서 언급된 종목만 필터링.
    정렬 기준: weighted_score 내림차순 (구독자 가중치 반영)
    """
    filtered = []
    for name, data in mentions.items():
        if len(data["channel_types"]) >= min_channel_types:
            filtered.append((name, data))

    # BUG-WEIGHT-6: weighted_score 기준 정렬
    filtered.sort(key=lambda x: x[1]["weighted_score"], reverse=True)
    print(f"[필터링] {len(filtered)}개 종목 선택 (채널 유형 ≥{min_channel_types})")
    return filtered


def generate_market_summary(market_overview: dict, recent_headlines: list, api_key: str) -> str:
    """Claude를 이용한 시장 요약 생성"""
    if not market_overview:
        return "시장 데이터를 불러오지 못했습니다."

    market_text = json.dumps(market_overview, ensure_ascii=False, indent=2)
    headlines_text = "\n".join(f"- {h}" for h in recent_headlines[:10])

    prompt = f"""다음 시장 데이터와 주요 뉴스를 바탕으로 오늘 한국 주식시장에 대한 브리핑을 5단락으로 작성하세요.
각 단락은 3~4문장으로 구성하고, 투자자가 오늘 알아야 할 핵심 정보를 포함하세요.

[시장 데이터]
{market_text}

[주요 뉴스 헤드라인]
{headlines_text}

형식: 단락만 출력 (번호나 제목 없이)"""

    try:
        response = call_claude_with_retry(prompt, api_key, max_tokens=1500)
        return response.strip()
    except Exception as e:
        print(f"[시장요약] Claude 호출 실패: {e}")
        kospi = market_overview.get("kospi", {})
        return f"오늘 코스피는 {kospi.get('value', 'N/A')} ({kospi.get('change_pct', 'N/A')}%)로 마감했습니다."


def build_analysis_prompt(filtered_mentions: list, all_data: list,
                          today_date: str, now_kst: str) -> str:
    """Claude 분석 프롬프트 생성 (상위 15개 종목)"""
    # 뉴스 헤드라인 추출
    headlines = []
    for item in all_data:
        if item.get("source_type") == "뉴스":
            t = item.get("title", "").strip()
            if t:
                headlines.append(t)
    headlines = list(dict.fromkeys(headlines))[:30]  # BUG-H3: 최대 30개

    # 상위 15개 종목 정보 구성
    top_stocks = filtered_mentions[:15]
    stocks_info = []
    for rank, (name, data) in enumerate(top_stocks, 1):
        # BUG-WEIGHT-7: weighted_score 함께 표시
        line = (f"{rank}. {name} (코드:{data['code']}, "
                f"언급:{data['total_count']}회, "
                f"가중점수:{data['weighted_score']:.1f}, "
                f"채널유형:{','.join(data['channel_types'])})")
        stocks_info.append(line)

        # 채널별 예시 (각 최대 5개)
        for ch_type, items in data["channels"].items():
            for item in items[:5]:
                w_str = f"[가중치:{item.get('weight', 1.0):.1f}]"
                stocks_info.append(
                    f"   [{ch_type}]{w_str} {item['source_name']}: {item['snippet'][:150]}"
                )

    stocks_text = "\n".join(stocks_info)
    headlines_text = "\n".join(f"- {h}" for h in headlines)

    prompt = f"""당신은 15년 경력의 한국 주식시장 전문 애널리스트입니다.
아래 데이터를 분석하여 오늘의 주식 브리핑을 JSON 형식으로 작성하세요.

[분석 날짜] {today_date} ({now_kst} KST)

[종목별 미디어 언급 현황 - 가중치 점수 기준 정렬]
{stocks_text}

[오늘의 뉴스 헤드라인]
{headlines_text}

[출력 형식] 반드시 아래 JSON 구조만 출력하세요:
{CB}json
{{
  "briefing_date": "{today_date}",
  "market_summary": "시장 요약 (5단락, 각 3~4문장)",
  "hot_sectors": ["섹터1", "섹터2", "섹터3"],
  "stocks": [
    {{
      "rank": 1,
      "name": "종목명",
      "code": "종목코드",
      "signal": "positive|negative|neutral",
      "description": "종목 설명 (3~4문장)",
      "price_trend": "현재 주가 흐름 설명",
      "catalyst": "상승/하락 촉매",
      "risk": "주요 리스크",
      "channel_counts": {{}},
      "total_count": 0,
      "weighted_score": 0.0,
      "overlap_count": 0,
      "reasons": [
        {{
          "source_type": "채널유형",
          "source_name": "출처명",
          "reason": "언급 이유/내용 요약",
          "source_url": ""
        }}
      ]
    }}
  ],
  "hidden_picks": [
    {{
      "name": "종목명",
      "code": "종목코드",
      "signal": "positive",
      "description": "숨겨진 유망 종목 설명",
      "catalyst": "기회 요인",
      "risk": "리스크",
      "reasons": []
    }}
  ],
  "investment_strategy": "오늘의 투자 전략 (3~5문장)"
}}
{CB}

[작성 지침]
- stocks는 가중치 점수가 높은 순서로 최대 5개
- hidden_picks는 단일 채널에서만 언급됐지만 고품질 소스(애널리스트/경제방송TV)에서 언급된 종목 최대 3개
- signal은 언급 맥락을 분석해 결정 (단순 언급은 neutral)
- 각 종목의 reasons는 실제 언급된 채널 정보를 반영
- weighted_score는 위 데이터의 값을 그대로 사용
- 국내 상장 종목만 포함 (해외 주식, ETF 제외)"""

    return prompt


def _restore_source_url(reason: dict, real_channel_data: list) -> dict:
    """reason 딕셔너리의 source_url이 없을 때 원본 데이터에서 복원"""
    if reason.get("source_url"):
        return reason

    src_name = reason.get("source_name", "")
    link     = reason.get("link", "")

    # BUG-M4: "증권사" 타입도 유튜브 버킷에서 검색
    for item in real_channel_data:
        if item.get("source_name") == src_name:
            url = item.get("link") or item.get("url", "")
            if url:
                reason["source_url"] = url
                return reason
        if link and (item.get("link") == link or item.get("url") == link):
            reason["source_url"] = link
            return reason

    return reason


def _try_parse_json(text: str) -> dict | None:
    """Claude 응답에서 JSON 추출 (다단계 클리닝)"""
    if not text:
        return None

    # 1단계: ```json 블록 추출
    match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    if match:
        candidate = match.group(1)
    else:
        # 2단계: 첫 { 부터 마지막 } 까지
        start = text.find('{')
        end   = text.rfind('}')
        if start == -1 or end == -1:
            return None
        candidate = text[start:end + 1]

    # 3단계: 제어문자 제거, 줄바꿈 정리
    candidate = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', candidate)

    # 4단계: trailing comma 제거
    candidate = re.sub(r',\s*([}\]])', r'\1', candidate)

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # 5단계: 줄별로 파싱 재시도
        lines = [l for l in candidate.split('\n') if l.strip()]
        cleaned = '\n'.join(lines)
        try:
            return json.loads(cleaned)
        except Exception:
            return None


def analyze_and_generate_html(all_data: list, api_key: str,
                               channels_data: dict = None,
                               gh_repo: str = "",
                               market_overview: dict = None) -> str:
    """메인 분석 오케스트레이터"""
    print("=" * 60)
    print("[AI분석] 시작")
    now_kst    = datetime.now(KST)
    today_date = now_kst.strftime("%Y-%m-%d")
    now_str    = now_kst.strftime("%H:%M")

    os.makedirs("data", exist_ok=True)

    # 1. 종목 목록 로드
    stock_map = load_stock_names()
    if not stock_map:
        print("[AI분석] 종목 목록 로드 실패 → 최소 브리핑 반환")
        from analyzer.html_generator import generate_html
        return generate_html({
            "briefing_date": today_date,
            "market_summary": "종목 데이터를 불러오지 못했습니다.",
            "hot_sectors": [], "stocks": [], "hidden_picks": [],
            "investment_strategy": ""
        }, all_data, channels_data, gh_repo, market_overview)

    # 2. 언급 추출 (채널 가중치 적용)
    # BUG-WEIGHT-8: channels_data 전달
    mentions = extract_mentions(all_data, stock_map, channels_data)

    # 3. 필터링
    filtered = filter_mentions(mentions)
    if not filtered:
        print("[AI분석] 필터링 후 종목 없음 → 최소 브리핑")
        from analyzer.html_generator import generate_html
        return generate_html({
            "briefing_date": today_date,
            "market_summary": "오늘 복수 채널에서 언급된 종목이 없습니다.",
            "hot_sectors": [], "stocks": [], "hidden_picks": [],
            "investment_strategy": ""
        }, all_data, channels_data, gh_repo, market_overview)

    # 4. 분석 프롬프트 생성 및 Claude 호출
    prompt   = build_analysis_prompt(filtered, all_data, today_date, now_str)
    print(f"[AI분석] Claude 호출 (종목 {len(filtered)}개, 상위 15개 분석)")
    response = call_claude_with_retry(prompt, api_key, max_tokens=16000)

    # 5. JSON 파싱
    result = _try_parse_json(response)
    if not result:
        print("[AI분석] JSON 파싱 실패")
        from analyzer.html_generator import generate_html
        return generate_html({
            "briefing_date": today_date,
            "market_summary": "AI 분석 결과를 파싱하지 못했습니다.",
            "hot_sectors": [], "stocks": [], "hidden_picks": [],
            "investment_strategy": ""
        }, all_data, channels_data, gh_repo, market_overview)

    print(f"[AI분석] JSON 파싱 성공: 종목 {len(result.get('stocks', []))}개")

    # 6. 종목별 정확한 카운트 및 가중치 점수 보정
    # BUG-WEIGHT-9: Claude가 반환한 값보다 실제 집계값이 더 정확
    mention_dict = dict(filtered)
    for stock in result.get("stocks", []):
        name = stock.get("name", "")
        if name in mention_dict:
            d = mention_dict[name]
            stock["channel_counts"]  = {k: len(v) for k, v in d["channels"].items()}
            stock["total_count"]     = d["total_count"]
            stock["weighted_score"]  = round(d["weighted_score"], 2)
            stock["overlap_count"]   = len(d["channel_types"])

    # 7. source_url 복원
    for stock in result.get("stocks", []):
        for reason in stock.get("reasons", []):
            _restore_source_url(reason, all_data)

    for pick in result.get("hidden_picks", []):
        for reason in pick.get("reasons", []):
            _restore_source_url(reason, all_data)

    # 8. 검증
    from analyzer.validation import validate_stocks
    result = validate_stocks(result, all_data, api_key)

    # 9. 저장 (차트 base64 제외)
    save_data = json.loads(json.dumps(result))
    for stock in save_data.get("stocks", []):
        stock.pop("chart_base64", None)
    for pick in save_data.get("hidden_picks", []):
        pick.pop("chart_base64", None)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    print(f"[AI분석] 결과 저장: {OUTPUT_FILE}")

    # 10. HTML 생성
    gh_token = os.environ.get("GH_TOKEN", "")
    from analyzer.html_generator import generate_html
    html = generate_html(result, all_data, channels_data, gh_repo,
                         market_overview, gh_token)
    print("[AI분석] 완료")
    return html
