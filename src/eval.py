"""Top-30 질의 시뮬레이션 — 실행 정확도(execution accuracy) 평가.

각 질의에 대해 NL2SQL이 생성·검증한 SQL의 실행 결과를, 손으로 작성한
정답(gold) SQL의 실행 결과와 비교한다. 비교는 값 기준(컬럼명 무관,
행 순서 무관, 수치는 소수 1자리 반올림)으로 하며, 이 완화 기준은
README에 명시한다. Verifier의 자기교정으로 회복된 건수도 따로 센다.

    python src/eval.py
"""
import json
import time
from collections import Counter
from pathlib import Path

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.family"] = "AppleGothic"
plt.rcParams["axes.unicode_minus"] = False
import numpy as np
import yaml

from nl2sql import NL2SQL

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results"
OUT.mkdir(exist_ok=True)


def norm_val(v):
    """값 정규화: 수치는 소수 1자리, 날짜 등은 문자열로."""
    if v is None:
        return "∅"
    if isinstance(v, bool):
        return str(v)
    try:
        return f"{round(float(v), 1):.1f}"
    except (TypeError, ValueError):
        return str(v)


def result_multiset(columns, rows):
    """행 순서·컬럼 순서에 무관한 결과 시그니처(멀티셋)."""
    return Counter(tuple(sorted(norm_val(v) for v in row)) for row in rows), len(columns)


def compare(gen_cols, gen_rows, gold_cols, gold_rows) -> bool:
    g1, n1 = result_multiset(gen_cols, gen_rows)
    g2, n2 = result_multiset(gold_cols, gold_rows)
    return n1 == n2 and g1 == g2


def main():
    with open(ROOT / "eval" / "questions.yml", encoding="utf-8") as f:
        questions = yaml.safe_load(f)["questions"]

    engine = NL2SQL()
    gold_con = duckdb.connect(str(ROOT / "data" / "bizquery.duckdb"), read_only=True)

    records = []
    t0 = time.time()
    for q in questions:
        r = engine.ask(q["question"])
        gold_cur = gold_con.execute(q["gold"])
        gold_cols = [d[0] for d in gold_cur.description]
        gold_rows = gold_cur.fetchall()

        correct = bool(r.ok and compare(r.verify.columns, r.verify.rows, gold_cols, gold_rows))
        records.append({
            "id": q["id"], "difficulty": q["difficulty"], "question": q["question"],
            "sql": r.sql, "verified": r.ok, "attempts": r.attempts,
            "recovered": r.recovered, "correct": correct,
            "fail_history": r.history,
        })
        mark = "O" if correct else ("v" if r.ok else "X")
        print(f"[{mark}] #{q['id']:>2} ({q['difficulty']:<6}) 시도{r.attempts} "
              f"{'(자기교정)' if r.recovered else '':<7} {q['question'][:44]}")
    elapsed = time.time() - t0

    # ---- 집계 ----
    n = len(records)
    acc = sum(r["correct"] for r in records) / n
    by_diff = {}
    for d in ["easy", "medium", "hard"]:
        sub = [r for r in records if r["difficulty"] == d]
        by_diff[d] = (sum(r["correct"] for r in sub), len(sub))
    first_shot = sum(r["correct"] and not r["recovered"] for r in records)
    recovered_ok = sum(r["correct"] and r["recovered"] for r in records)
    retried = sum(r["attempts"] > 1 for r in records)
    verify_fail = sum(not r["verified"] for r in records)

    lines = [
        "=== BizQuery Top-30 질의 시뮬레이션 결과 ===",
        f"모델: gpt-4.1-mini · 검증: sqlglot 정적 + EXPLAIN 바인딩 + 실행 · 자기교정 최대 2회",
        "",
        f"실행 정확도(execution accuracy): {acc*100:.1f}%  ({sum(r['correct'] for r in records)}/{n})",
        *[f"  - {d:<6}: {c}/{t}  ({c/t*100:.0f}%)" for d, (c, t) in by_diff.items()],
        "",
        f"첫 생성에 정답: {first_shot}건 · 자기교정 후 정답: {recovered_ok}건",
        f"검증 실패로 재시도 발생: {retried}건 · 최종 검증 실패: {verify_fail}건",
        f"총 소요: {elapsed:.0f}초 ({elapsed/n:.1f}초/질의)",
    ]
    report = "\n".join(lines)
    print("\n" + report)
    (OUT / "eval_report.txt").write_text(report + "\n", encoding="utf-8")
    (OUT / "eval_results.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ---- 차트 ----
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    fig.patch.set_facecolor("#F6F4EF")
    for ax in axes:
        ax.set_facecolor("#F6F4EF")

    ax = axes[0]
    ds = list(by_diff.keys())
    vals = [c / t * 100 for c, t in by_diff.values()]
    ax.bar(ds, vals, color="#31655F", width=0.55)
    for i, (v, (c, t)) in enumerate(zip(vals, by_diff.values())):
        ax.text(i, v + 2, f"{v:.0f}%  ({c}/{t})", ha="center", fontsize=10, fontweight="bold")
    ax.set_ylim(0, 112)
    ax.set_ylabel("Execution accuracy (%)")
    ax.set_title("난이도별 실행 정확도", loc="left", fontweight="bold")
    ax.grid(axis="y", alpha=0.25)

    ax = axes[1]
    parts = [first_shot, recovered_ok, n - first_shot - recovered_ok]
    labels = [f"첫 생성 정답 {parts[0]}", f"자기교정 후 정답 {parts[1]}", f"오답 {parts[2]}"]
    colors = ["#31655F", "#B08A3E", "#B0574A"]
    left = 0
    for p, l, c in zip(parts, labels, colors):
        ax.barh([0], [p], left=left, color=c, height=0.45, label=l)
        if p:
            ax.text(left + p / 2, 0, str(p), ha="center", va="center",
                    color="white", fontweight="bold")
        left += p
    ax.set_xlim(0, n)
    ax.set_yticks([])
    ax.set_title("Top-30 결과 구성 (Verifier 자기교정 효과)", loc="left", fontweight="bold")
    ax.legend(loc="lower right", fontsize=8, ncols=3)
    fig.tight_layout()
    fig.savefig(OUT / "eval_chart.png", dpi=140)
    print(f"\n저장: {OUT}/eval_report.txt, eval_results.json, eval_chart.png")


if __name__ == "__main__":
    main()
