"""시맨틱 카탈로그 로더 — YAML 카탈로그를 NL2SQL 프롬프트 컨텍스트로 변환.

카탈로그가 지표 정의의 단일 출처(single source of truth)이므로,
LLM에게 "지표는 반드시 이 산식으로 계산하라"는 계약을 프롬프트로 전달한다.
"""
from pathlib import Path

import yaml

CATALOG_PATH = Path(__file__).resolve().parent.parent / "semantic" / "catalog.yml"


def load_catalog() -> dict:
    with open(CATALOG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def allowed_tables(catalog: dict) -> set:
    return set(catalog["tables"].keys())


def to_prompt_context(catalog: dict) -> str:
    """카탈로그 → LLM 프롬프트에 넣을 스키마/지표/예시 텍스트."""
    parts = ["[테이블 스키마]"]
    for tname, t in catalog["tables"].items():
        parts.append(f"\n테이블 {tname}: {t['description']}")
        for col, desc in t["columns"].items():
            parts.append(f"  - {col}: {desc}")

    parts.append("\n[지표 정의 — 아래 지표를 물으면 반드시 이 SQL 산식을 그대로 사용]")
    for m in catalog["metrics"]:
        parts.append(f"\n{m['label']}({m['name']}) — {m['description']}")
        parts.append(f"  테이블: {m['table']}")
        parts.append(f"  산식: {m['expression']}")

    parts.append("\n[차원(GROUP BY 후보)]")
    for d in catalog["dimensions"]:
        parts.append(f"  - {d['name']} ({d['label']}) — {', '.join(d['tables'])}")

    parts.append("\n[질의 예시]")
    for ex in catalog["examples"]:
        parts.append(f"\nQ: {ex['question']}\nSQL:\n{ex['sql'].strip()}")

    return "\n".join(parts)


if __name__ == "__main__":
    print(to_prompt_context(load_catalog())[:1500])
