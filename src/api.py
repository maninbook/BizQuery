"""BizQuery API — NL2SQL 질의 엔드포인트 (FastAPI).

    uvicorn api:app --app-dir src --host 127.0.0.1 --port 8021
"""
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from catalog import load_catalog
from nl2sql import NL2SQL

app = FastAPI(title="BizQuery", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

_engine: NL2SQL | None = None


def engine() -> NL2SQL:
    global _engine
    if _engine is None:
        _engine = NL2SQL()
    return _engine


class AskRequest(BaseModel):
    question: str


@app.get("/api/catalog")
def get_catalog():
    """UI 표시용 지표/차원 카탈로그."""
    c = load_catalog()
    return {
        "metrics": [{"label": m["label"], "name": m["name"], "description": m["description"]}
                    for m in c["metrics"]],
        "dimensions": [{"label": d["label"], "name": d["name"]} for d in c["dimensions"]],
    }


@app.post("/api/ask")
def ask(req: AskRequest):
    t0 = time.time()
    r = engine().ask(req.question)
    body = {
        "question": r.question,
        "sql": r.sql,
        "ok": r.ok,
        "attempts": r.attempts,
        "recovered": r.recovered,
        "elapsed": round(time.time() - t0, 2),
        "history": r.history,
    }
    if r.ok:
        body["columns"] = r.verify.columns
        body["rows"] = [[str(v) if v is not None else None for v in row]
                        for row in r.verify.rows]
        body["warnings"] = r.verify.warnings
    else:
        body["error"] = r.verify.error if r.verify else "알 수 없는 오류"
    return body
