import { useEffect, useState } from "react";

const SAMPLES = [
  "2025년 사업부별 영업이익률을 알려줘",
  "2025년 매출액 상위 3개 코스트센터를 보여줘",
  "2025년 전사 매출액의 2024년 대비 성장률(%)은?",
  "2025년 제품 카테고리별 판매금액 비중을 보여줘",
  "2025년 사업부별 매출 계획달성률(%)을 보여줘",
];

const fmt = (v) => {
  if (v === null || v === undefined) return "–";
  const n = Number(v);
  if (!Number.isNaN(n) && v !== "" && Math.abs(n) >= 1000) return n.toLocaleString("ko-KR");
  return String(v);
};

export default function App() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [res, setRes] = useState(null);
  const [catalog, setCatalog] = useState(null);

  useEffect(() => {
    fetch("/api/catalog").then((r) => r.json()).then(setCatalog).catch(() => {});
    // 딥링크 자동 질의: /?q=2025년+사업부별+영업이익률
    const q = new URLSearchParams(window.location.search).get("q");
    if (q) ask(q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const ask = async (q) => {
    const query = (q ?? question).trim();
    if (!query || loading) return;
    setQuestion(query);
    setLoading(true);
    setRes(null);
    try {
      const r = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: query }),
      });
      setRes(await r.json());
    } catch (e) {
      setRes({ ok: false, error: String(e) });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="wrap">
      <header>
        <div className="kicker">NL2SQL · SEMANTIC LAYER · SQL VERIFIER</div>
        <h1>BizQuery</h1>
        <p className="sub">
          자연어로 경영 데이터를 물으면, 시맨틱 카탈로그 기반으로 SQL을 생성하고
          3단계 검증(문법·바인딩·실행)을 통과한 결과만 보여줍니다.
        </p>
      </header>

      <div className="askbox">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && ask()}
          placeholder="예: 2025년 사업부별 영업이익률을 알려줘"
        />
        <button onClick={() => ask()} disabled={loading}>
          {loading ? "생성·검증 중…" : "질의"}
        </button>
      </div>

      <div className="samples">
        {SAMPLES.map((s) => (
          <button key={s} className="chip" onClick={() => ask(s)} disabled={loading}>
            {s}
          </button>
        ))}
      </div>

      {res && (
        <section className="result">
          <div className="badges">
            <span className={`badge ${res.ok ? "ok" : "fail"}`}>
              {res.ok ? "검증 통과" : "검증 실패"}
            </span>
            <span className="badge neutral">시도 {res.attempts}회</span>
            {res.recovered && <span className="badge warn">자기교정으로 회복</span>}
            {res.elapsed && <span className="badge neutral">{res.elapsed}s</span>}
          </div>

          <h3>생성된 SQL</h3>
          <pre>{res.sql}</pre>

          {res.ok ? (
            <>
              {res.warnings?.map((w) => (
                <div key={w} className="warnline">⚠ {w}</div>
              ))}
              <h3>결과 ({res.rows.length}행)</h3>
              <div className="tablewrap">
                <table>
                  <thead>
                    <tr>{res.columns.map((c) => <th key={c}>{c}</th>)}</tr>
                  </thead>
                  <tbody>
                    {res.rows.slice(0, 50).map((row, i) => (
                      <tr key={i}>{row.map((v, j) => <td key={j}>{fmt(v)}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="errline">{res.error}</div>
          )}
        </section>
      )}

      {catalog && (
        <section className="catalog">
          <h3>시맨틱 카탈로그 — 지표는 항상 같은 산식으로 계산됩니다</h3>
          <div className="metricgrid">
            {catalog.metrics.map((m) => (
              <div key={m.name} className="metric">
                <div className="mname">{m.label}</div>
                <div className="mdesc">{m.description}</div>
              </div>
            ))}
          </div>
          <div className="dims">
            차원: {catalog.dimensions.map((d) => d.label).join(" · ")}
          </div>
        </section>
      )}

      <footer>BizQuery — LLM 경영계획 분석 데모 · DuckDB + 시맨틱 레이어 + SQL Verifier</footer>
    </div>
  );
}
