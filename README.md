# 📊 AI 주식 브리핑 v3

한국 주식시장 관련 유튜브 채널 및 경제 미디어 콘텐츠를 자동 수집·분석하여 종목별 전문가 언급 내용을 정리해주는 웹 서비스입니다.

🔗 **라이브 페이지**: https://kunil-choi.github.io/stock-briefing-v3/

---

## 📌 v3 주요 변경사항 (v2 대비)

| 항목 | v2 | v3 |
|------|----|----|
| 섹션 구성 | 단일 섹션 | 4개 독립 섹션 |
| 증권TV 섹션 | ❌ | ✅ 섹션 2 (전일 기준) |
| 애널리스트 리포트 | 통합 | ✅ 섹션 3 독립 분리 |
| 리포트 분류 | ❌ | ✅ 동시언급/첫언급/신규커버리지 |
| 언급 횟수 표시 | 표시 | ❌ 미표시 (감성 레이블만) |
| 관리자 페이지 | ❌ | ✅ `/admin` |
| KRX 종목코드 메타 | ❌ | ✅ data-krx 속성 |

---

## 🗂 서비스 구조

```
섹션 1  유튜브·미디어 채널 언급 종목 분석
        - 방송사 채널 (한국경제TV, SBS Biz, 매일경제TV 등)
        - 개인 유튜브 채널 (슈카월드, 삼프로TV 등)
        - 증권사 유튜브 채널 (삼성증권, 키움증권 등)
        - 지난 24시간 기준

섹션 2  증권TV 전문가 출연 추천 종목
        - 한국경제TV, 매일경제TV, MTN, 이데일리TV 등
        - 전일(D-1) 방송 기준
        - 전문가 이름/코너명 함께 표시

섹션 3  증권사 애널리스트 리포트 분석
        ① 증권사 동시 언급 (24h 내 복수 증권사)
        ② 6개월 내 첫 언급 (단일 증권사)
        ③ 신규 커버리지 개시

종합    섹션 1~3 통합 교차 분석 기반 투자전략
```

---

## ⚙️ 설치 및 실행

### 1. 저장소 클론
```bash
git clone https://github.com/kunil-choi/stock-briefing-v3.git
cd stock-briefing-v3
```

### 2. 패키지 설치
```bash
pip install -r requirements.txt
```

### 3. 환경변수 설정
```bash
cp .env.example .env
# .env 파일을 열어 API 키 입력
```

### 4. 실행
```bash
python main.py
```

---

## 🔑 API 키 설정

| 키 | 설명 | 발급처 |
|----|------|--------|
| `ANTHROPIC_API_KEY` | Claude AI 분석 엔진 | https://console.anthropic.com/ |
| `YOUTUBE_API_KEY` | YouTube Data API v3 | https://console.cloud.google.com/ |
| `GH_TOKEN` | GitHub 푸시 권한 | https://github.com/settings/tokens |
| `ADMIN_PASSWORD` | 관리자 페이지 비밀번호 | 직접 설정 (기본: stock2026!) |

> ⚠️ **보안 주의**: `.env` 파일은 절대 git에 커밋하지 마세요.
> GitHub Actions Secrets에 등록하여 사용하세요.

---

## 🤖 자동 실행 (GitHub Actions)

매일 **오전 6시 KST** (평일)에 자동으로 브리핑이 생성됩니다.

```yaml
# .github/workflows/daily_briefing.yml
schedule:
  - cron: '0 21 * * 0-4'  # UTC 21:00 = KST 06:00
```

**수동 실행**: GitHub → Actions → Daily Stock Briefing v3 → Run workflow

---

## 📁 프로젝트 구조

```
stock-briefing-v3/
├── main.py                    # 메인 실행 파일
├── config.py                  # 설정 (API 키, 채널 목록 등)
├── channels.json              # 채널 관리 데이터
├── requirements.txt
├── .env.example               # 환경변수 템플릿
├── collectors/
│   ├── news_collector.py      # 뉴스 RSS 수집
│   ├── youtube_collector.py   # YouTube 수집 (섹션 1, 2)
│   └── analyst_collector.py   # 애널리스트 리포트 수집 (섹션 3)
├── analyzer/
│   ├── ai_analyzer.py         # Claude API 교차 분석
│   ├── html_generator.py      # HTML 생성
│   ├── naver_finance.py       # 주가 조회
│   └── api_client.py          # API 클라이언트 (재시도 로직)
├── docs/
│   ├── index.html             # 메인 브리핑 페이지 (GitHub Pages)
│   ├── admin/
│   │   └── index.html         # 관리자 페이지 (/admin)
│   └── archive/               # 날짜별 아카이브
├── data/                      # 원본 데이터 백업
└── .github/
    └── workflows/
        └── daily_briefing.yml
```

---

## 🔐 관리자 페이지

`/admin` 경로에서 접근 가능합니다.

**기능:**
- 채널 유형별 탭 구분 (방송사 / 개인유튜브 / 증권사유튜브)
- 채널 추가 / 수정 / 삭제
- 재검사 실행 및 경고 플래그 확인
- 비밀번호 기반 로그인 보호

**기본 비밀번호**: `stock2026!`  
(GitHub Secrets의 `ADMIN_PASSWORD`로 변경 가능)

---

## ⚠️ 면책 고지

본 서비스는 AI가 자동 생성한 참고 자료이며, 투자 권유가 아닙니다.  
투자 판단의 책임은 투자자 본인에게 있습니다.
