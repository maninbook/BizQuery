"""NL2SQL 엔진 — 자연어 질의 → 카탈로그 기반 SQL 생성 → 검증 → 자기교정.

흐름: generate → verify(정적/바인딩/실행) → 실패 시 오류를 피드백으로
      재생성(최대 MAX_RETRIES회) → 검증 통과한 SQL과 결과만 반환.
DataMaster의 grade(검증)→rewrite 루프와 같은 패턴을 정형 데이터에 적용했다.
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
from dotenv import load_dotenv
from openai import OpenAI

from catalog import load_catalog, to_prompt_context, allowed_tables
from verifier import verify, VerifyResult

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MODEL = os.getenv("BIZQUERY_MODEL", "gpt-4.1-mini")
MAX_RETRIES = 2

SYSTEM_PROMPT = """당신은 경영 데이터 분석용 SQL 생성기입니다. DuckDB 방언의 SQL을 작성합니다.

규칙:
1. 아래 카탈로그에 정의된 테이블·컬럼만 사용합니다.
2. 지표(매출액, 영업이익, 계획달성률 등)를 계산할 때는 카탈로그의 산식을 그대로 사용합니다. 임의 산식 금지.
3. 재무 지표는 mart_financials, 제품/지역/채널 판매 분석은 mart_sales를 사용합니다.
4. mart_financials 금액 질의에는 반드시 version 조건을 명시합니다(실적=ACTUAL, 계획=PLAN). 별도 언급 없으면 실적(ACTUAL) 기준.
5. SELECT 문 하나만 작성합니다. 설명 없이 ```sql 코드블록으로만 답합니다.
6. 결과 컬럼은 질문이 요구한 것만 간결하게 (그룹 컬럼 + 지표).
7. 정렬이 자연스러운 질의(추이·순위)는 ORDER BY를 포함합니다."""


@dataclass
class NL2SQLResult:
    question: str
    sql: str = ""
    ok: bool = False
    attempts: int = 0
    recovered: bool = False      # 자기교정으로 살아났는지
    verify: VerifyResult | None = None
    history: list = field(default_factory=list)  # (sql, stage, error) 실패 기록


def extract_sql(text: str) -> str:
    m = re.search(r"```sql\s*(.+?)```", text, re.S | re.I)
    if m:
        return m.group(1).strip().rstrip(";")
    m = re.search(r"```\s*(.+?)```", text, re.S)
    return (m.group(1) if m else text).strip().rstrip(";")


class NL2SQL:
    def __init__(self, db_path: str | Path | None = None):
        self.catalog = load_catalog()
        self.context = to_prompt_context(self.catalog)
        self.allowed = allowed_tables(self.catalog)
        self.con = duckdb.connect(str(db_path or ROOT / "data" / "bizquery.duckdb"),
                                  read_only=True)
        self.client = OpenAI()

    def _generate(self, question: str, feedback: list) -> str:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{self.context}\n\n[질문]\n{question}"},
        ]
        for sql, stage, error in feedback:  # 자기교정: 이전 실패를 컨텍스트로
            messages.append({"role": "assistant", "content": f"```sql\n{sql}\n```"})
            messages.append({"role": "user",
                             "content": f"위 SQL이 {stage} 단계 검증에 실패했습니다.\n"
                                        f"오류: {error}\n수정된 SQL을 다시 작성하세요."})
        resp = self.client.chat.completions.create(
            model=MODEL, messages=messages, temperature=0)
        return extract_sql(resp.choices[0].message.content)

    def ask(self, question: str) -> NL2SQLResult:
        res = NL2SQLResult(question=question)
        feedback = []
        for attempt in range(1 + MAX_RETRIES):
            res.attempts = attempt + 1
            sql = self._generate(question, feedback)
            v = verify(sql, self.con, self.allowed)
            if v.ok:
                res.sql, res.ok, res.verify = sql, True, v
                res.recovered = attempt > 0
                return res
            feedback.append((sql, v.stage, v.error))
            res.history.append({"sql": sql, "stage": v.stage, "error": v.error})
        res.sql = feedback[-1][0]
        res.verify = verify(res.sql, self.con, self.allowed)
        return res


if __name__ == "__main__":
    import sys
    engine = NL2SQL()
    q = sys.argv[1] if len(sys.argv) > 1 else "2025년 사업부별 영업이익률을 알려줘"
    r = engine.ask(q)
    print(f"Q: {q}\n시도: {r.attempts}회 (자기교정: {r.recovered})\n\n{r.sql}\n")
    if r.ok:
        print(r.verify.columns)
        for row in r.verify.rows[:10]:
            print(row)
        for w in r.verify.warnings:
            print("⚠", w)
    else:
        print("실패:", r.verify.error)
