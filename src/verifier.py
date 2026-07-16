"""SQL Verifier — 생성된 SQL을 실행 전/후 3단계로 검증한다.

LLM이 만든 SQL을 그대로 믿지 않는 것이 이 시스템의 핵심 설계다.
 1) 정적 검증 (sqlglot): 문법 파싱, SELECT 단일문 강제(DML/DDL 차단),
    카탈로그에 없는 테이블 참조 차단
 2) 바인딩 검증 (EXPLAIN): 존재하지 않는 컬럼/함수 등 바인더 오류를
    실행 없이 적발
 3) 실행 + 정합성 검사: 행 수 상한, 빈 결과/전부 NULL 경고
실패 시 오류 메시지를 반환해 NL2SQL의 자기교정 루프 입력으로 쓴다.
"""
from dataclasses import dataclass, field

import duckdb
import sqlglot
from sqlglot import exp

MAX_ROWS = 500  # 분석 질의 가드레일


@dataclass
class VerifyResult:
    ok: bool
    stage: str            # static | binding | execution | passed
    error: str = ""
    warnings: list = field(default_factory=list)
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)


def static_check(sql: str, allowed: set) -> str | None:
    """1단계: 문법·안전성·테이블 화이트리스트. 문제 있으면 오류 문자열."""
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError as e:
        return f"SQL 문법 오류: {e}"
    if len(statements) != 1:
        return "단일 SELECT 문만 허용됩니다 (여러 문장이 감지됨)."
    stmt = statements[0]
    if not isinstance(stmt, (exp.Select, exp.Union)) and not (
        isinstance(stmt, exp.Query) and stmt.find(exp.Select)
    ):
        return f"SELECT 질의만 허용됩니다 (감지된 유형: {type(stmt).__name__})."
    # DML/DDL 노드가 어디에도 없어야 함
    for banned in (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter):
        if stmt.find(banned):
            return f"{banned.__name__} 구문은 허용되지 않습니다."
    # CTE 별칭은 테이블 화이트리스트 예외
    cte_names = {cte.alias_or_name for cte in stmt.find_all(exp.CTE)}
    for t in stmt.find_all(exp.Table):
        name = t.name
        if name and name not in allowed and name not in cte_names:
            return (f"허용되지 않은 테이블 '{name}' 참조. "
                    f"사용 가능: {', '.join(sorted(allowed))}")
    return None


def verify(sql: str, con: duckdb.DuckDBPyConnection, allowed: set) -> VerifyResult:
    """3단계 검증을 순차 수행하고 결과(또는 자기교정용 오류)를 돌려준다."""
    err = static_check(sql, allowed)
    if err:
        return VerifyResult(False, "static", err)

    try:
        con.execute(f"EXPLAIN {sql}")
    except Exception as e:
        return VerifyResult(False, "binding", f"바인딩 오류: {e}")

    try:
        cur = con.execute(sql)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchmany(MAX_ROWS + 1)
    except Exception as e:
        return VerifyResult(False, "execution", f"실행 오류: {e}")

    warnings = []
    if len(rows) > MAX_ROWS:
        rows = rows[:MAX_ROWS]
        warnings.append(f"결과가 {MAX_ROWS}행을 초과해 잘렸습니다.")
    if len(rows) == 0:
        warnings.append("결과가 0행입니다. 필터 조건(연도·이름 표기 등)을 확인하세요.")
    elif all(all(v is None for v in r) for r in rows):
        warnings.append("모든 값이 NULL입니다. 산식/조인 조건을 확인하세요.")

    return VerifyResult(True, "passed", warnings=warnings, columns=columns, rows=rows)
