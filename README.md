# BizQuery — NL2SQL 경영계획 분석 시스템

자연어로 경영 데이터를 물으면, 시맨틱 카탈로그(메트릭/디멘션 정의)를 근거로
SQL을 생성하고, 3단계 검증(문법 → 바인딩 → 실행)을 통과한 결과만 보여주는
LLM 분석 시스템입니다. SAP CO 스타일 가상 데이터(코스트센터 × 계정 ×
실적/계획)로 실제 경영 질의 시나리오를 재현했습니다.

![BizQuery UI](docs/screenshot_ui.png)

## 왜 이렇게 설계했나

NL2SQL을 실무 분석 시스템으로 쓰려면 두 가지 문제를 풀어야 합니다.

1. 지표 정의의 일관성 — "영업이익률"을 물을 때마다 LLM이 다른 산식을 만들면
   분석 시스템으로 쓸 수 없습니다. 그래서 메트릭/디멘션 카탈로그
   (`semantic/catalog.yml`)를 지표 정의의 단일 출처로 두고, LLM은 카탈로그의
   SQL 산식을 그대로 사용하도록 강제했습니다. 예를 들어 매출액은 항상
   `account_group='매출' AND version='ACTUAL'` 조건으로만 계산됩니다.
2. 생성된 SQL의 신뢰성 — LLM이 만든 SQL을 그대로 실행하지 않습니다.
   SQL Verifier가 3단계로 검증하고, 실패하면 오류 메시지를 피드백으로
   재생성하는 자기교정 루프(최대 2회)를 돕니다.

```
질문 → [카탈로그 컨텍스트 주입] → LLM SQL 생성
     → Verifier ① 정적 검증 (sqlglot: 문법·SELECT-only·테이블 화이트리스트)
              ② 바인딩 검증 (EXPLAIN: 없는 컬럼/함수 적발)
              ③ 실행 + 정합성 (행수 상한·빈 결과·전부 NULL 경고)
     → 실패 시 오류를 피드백으로 재생성 (자기교정, 최대 2회)
     → 검증 통과한 SQL + 결과만 반환
```

## 평가 — Top-30 질의 시뮬레이션 (실측)

경영 질의 30개(easy 10 · medium 12 · hard 8)에 대해 손으로 작성한 정답 SQL의
실행 결과와 비교하는 실행 정확도(execution accuracy) 평가를 수행했습니다.
hard에는 전년비 성장률, 비중(윈도우 함수), 누적 합계, 사업부별 최대 계정
(PARTITION BY) 같은 실전 질의를 포함했습니다.

| 지표 | 결과 |
|---|---|
| 실행 정확도 | 96.7% (29/30) |
| 난이도별 | easy 9/10 · medium 12/12 · hard 8/8 |
| 첫 생성에 정답 | 29건 (재시도 없이 통과) |
| 검증 실패·자기교정 발생 | 0건 |
| 평균 응답 | 2.1초/질의 (gpt-4.1-mini) |

![평가 결과](results/eval_chart.png)

유일한 오답(#4 "2025년 반도체사업부의 매출액은?")은 값 자체는 정답과 동일한
1,562억원을 반환했지만, 요청하지 않은 사업부명 컬럼을 추가로 포함해 엄격
기준(컬럼 수 일치)에서 오답 처리된 케이스입니다. 즉 수치가 틀린 답은
30문항 중 0건이었습니다. 비교 기준은 행 순서·컬럼 순서 무시, 수치는 소수
1자리 반올림으로 완화했으며 이 기준은 `src/eval.py`에 명시되어 있습니다.

자기교정 루프가 이번 평가에서 발동하지 않은 것은 카탈로그 컨텍스트가 충분히
강해 첫 생성 품질이 높았기 때문입니다. 검증 루프는 스키마가 크고 지저분한
실환경에서 안전망 역할을 하도록 설계된 장치이며, 고의로 깨진 질의를 넣으면
정적/바인딩 단계에서 차단·재생성되는 것을 확인했습니다.

## 데이터

실제 기업 데이터를 쓸 수 없어, SAP CO(관리회계) 구조를 본뜬 가상 데이터를
결정적 시드로 생성합니다(`data/generate.py`) — 3개 사업부 × 12개 코스트센터
× 10개 계정 × 30개월 × 실적/계획 2버전(6,300행) + 제품 판매 마트(2,880행).
사업부별 성장률·계절성·노이즈를 넣어 전년비/달성률 질의가 의미 있게
성립합니다. 팩트+차원 조인과 시간 파생 컬럼은 마트 뷰(`mart_financials`,
`mart_sales`)로 자산화했습니다.

## 실행

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # OPENAI_API_KEY 입력

python data/generate.py                 # 가상 데이터 생성 → data/bizquery.duckdb
python src/nl2sql.py "2025년 사업부별 영업이익률을 알려줘"   # CLI 단건 질의
python src/eval.py                      # Top-30 평가 (LLM 호출 발생)

# Web UI
uvicorn api:app --app-dir src --host 127.0.0.1 --port 8021   # API
cd web && npm install && npm run dev                          # React (proxy → 8021)
```

## 구조

```
BizQuery/
├── semantic/catalog.yml    시맨틱 레이어 — 메트릭/디멘션/질의예시 자산화
├── data/generate.py        SAP CO 스타일 가상 데이터 → DuckDB (마트 뷰 포함)
├── src/
│   ├── catalog.py          카탈로그 → LLM 프롬프트 컨텍스트
│   ├── nl2sql.py           생성 + 자기교정 루프
│   ├── verifier.py         3단계 SQL 검증 (sqlglot·EXPLAIN·실행)
│   ├── eval.py             Top-30 실행 정확도 평가
│   └── api.py              FastAPI 엔드포인트
├── web/                    React UI (질의·SQL·검증 배지·결과 테이블·카탈로그)
├── eval/questions.yml      질의 30개 + 정답 SQL (질의 자산화)
└── results/                평가 리포트·차트
```

## 한계와 다음 단계

- 단일 마트 2개 기준의 평가입니다. 테이블 수십 개 실환경에서는 스키마 검색
  (RAG)과 카탈로그 분할 주입이 필요하며, 그때 자기교정 루프의 가치가 커집니다.
- 시맨틱 레이어는 자체 YAML 카탈로그입니다. dbt Semantic Layer / MetricFlow,
  Power BI 시맨틱 모델로의 포팅을 다음 단계로 설계해 두었습니다(구조 호환).
- 데이터 소스를 DuckDB에서 Microsoft Fabric(Lakehouse/Warehouse)으로 바꾸는
  것, Neo4j 기반 지표-조직 관계 그래프 고도화가 확장 로드맵입니다.

## 스택

Python · DuckDB · sqlglot · OpenAI API (gpt-4.1-mini) · FastAPI · React(Vite) · matplotlib
