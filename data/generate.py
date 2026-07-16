"""SAP CO 스타일 가상 경영 데이터 생성 → DuckDB 적재.

실제 기업 데이터를 쓸 수 없으므로, SAP CO(관리회계)의 전형적 구조
(코스트센터 × 계정 × 실적/계획 버전)를 본뜬 가상 데이터를 결정적(seed)으로
생성한다. 사업부별 성장률·계절성·노이즈를 넣어 '전년비', '계획대비 달성률'
같은 경영 질의가 의미 있게 성립하도록 설계했다.

    python data/generate.py   →  data/bizquery.duckdb
"""
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DB = ROOT / "bizquery.duckdb"
rng = np.random.default_rng(42)

MONTHS = pd.date_range("2024-01-01", "2026-06-01", freq="MS")

# ---------- 차원(Dimension) ----------
DIVISIONS = {  # 사업부: (매출 규모 계수, 연 성장률, 원가율)
    "반도체사업부": (100, 0.18, 0.58),
    "디스플레이사업부": (55, 0.05, 0.68),
    "가전사업부": (40, 0.02, 0.72),
}

COST_CENTERS = []  # (cc_id, cc_name, division, region)
_regions = ["국내", "미주", "유럽", "아시아"]
i = 0
for div in DIVISIONS:
    for region in ["국내", "미주", "아시아"]:
        COST_CENTERS.append((f"CC{1001+i}", f"{div[:-3]} {region}센터", div, region))
        i += 1
COST_CENTERS += [
    ("CC1901", "경영지원센터", "본사지원", "국내"),
    ("CC1902", "연구개발센터", "본사지원", "국내"),
    ("CC1903", "정보전략센터", "본사지원", "국내"),
]

ACCOUNTS = [  # (account_id, account_name, account_group)
    ("41010", "제품매출", "매출"),
    ("41020", "서비스매출", "매출"),
    ("51010", "재료비", "매출원가"),
    ("51020", "노무비", "매출원가"),
    ("51030", "제조경비", "매출원가"),
    ("61010", "급여", "판관비"),
    ("61020", "광고선전비", "판관비"),
    ("61030", "지급수수료", "판관비"),
    ("61040", "감가상각비", "판관비"),
    ("61050", "여비교통비", "판관비"),
]

PRODUCTS = [  # (product_id, product_name, category, 단가(원))
    ("P100", "DRAM 모듈", "메모리", 85_000),
    ("P110", "NAND SSD", "메모리", 120_000),
    ("P200", "이미지센서", "시스템반도체", 45_000),
    ("P210", "전력반도체", "시스템반도체", 30_000),
    ("P300", "OLED 패널", "디스플레이", 210_000),
    ("P310", "LCD 패널", "디스플레이", 90_000),
    ("P400", "시스템에어컨", "가전", 1_400_000),
    ("P410", "공기청정기", "가전", 450_000),
]
CHANNELS = ["온라인", "오프라인", "B2B"]


def month_factor(m: pd.Timestamp, start: pd.Timestamp, growth: float) -> float:
    """연 성장률 + 계절성(4분기 성수기)을 월 계수로 변환."""
    years = (m - start).days / 365.25
    seasonal = 1.0 + 0.12 * np.sin(2 * np.pi * (m.month - 3) / 12) + (0.08 if m.quarter == 4 else 0)
    return (1 + growth) ** years * seasonal


def build_financials() -> pd.DataFrame:
    """fact_financials: 월 × 코스트센터 × 계정 × 버전(PLAN/ACTUAL)."""
    rows = []
    start = MONTHS[0]
    for cc_id, _, div, region in COST_CENTERS:
        is_biz = div in DIVISIONS
        scale, growth, cogs_ratio = DIVISIONS.get(div, (0, 0.03, 0))
        region_w = {"국내": 0.45, "미주": 0.33, "아시아": 0.22}.get(region, 0.3)
        for m in MONTHS:
            f = month_factor(m, start, growth if is_biz else 0.03)
            # 매출 (사업부 CC만)
            plan_rev = scale * region_w * 1e8 * f if is_biz else 0
            for acc_id, _, grp in ACCOUNTS:
                if grp == "매출":
                    if not is_biz:
                        continue
                    share = 0.85 if acc_id == "41010" else 0.15
                    plan = plan_rev * share
                elif grp == "매출원가":
                    if not is_biz:
                        continue
                    share = {"51010": 0.6, "51020": 0.25, "51030": 0.15}[acc_id]
                    plan = plan_rev * cogs_ratio * share
                else:  # 판관비: 모든 CC
                    base = (scale if is_biz else 25) * 1e6
                    share = {"61010": 3.0, "61020": 1.2 if is_biz else 0.1,
                             "61030": 0.8, "61040": 1.0, "61050": 0.3}[acc_id]
                    plan = base * share * (1.03 ** ((m - start).days / 365.25))
                actual = plan * rng.normal(1.0, 0.07)
                rows.append((m.date(), cc_id, acc_id, "PLAN", round(plan)))
                rows.append((m.date(), cc_id, acc_id, "ACTUAL", round(max(actual, 0))))
    return pd.DataFrame(rows, columns=["month_date", "cc_id", "account_id", "version", "amount"])


def build_sales() -> pd.DataFrame:
    """fact_sales: 월 × 제품 × 지역 × 채널 판매 수량/매출."""
    rows = []
    start = MONTHS[0]
    cat_growth = {"메모리": 0.20, "시스템반도체": 0.10, "디스플레이": 0.04, "가전": 0.01}
    for pid, _, cat, price in PRODUCTS:
        base_qty = {"메모리": 90_000, "시스템반도체": 60_000,
                    "디스플레이": 25_000, "가전": 3_500}[cat]
        for m in MONTHS:
            f = month_factor(m, start, cat_growth[cat])
            for region in _regions:
                rw = {"국내": 0.35, "미주": 0.28, "유럽": 0.17, "아시아": 0.20}[region]
                for ch in CHANNELS:
                    cw = {"온라인": 0.25, "오프라인": 0.30, "B2B": 0.45}[ch]
                    qty = max(int(base_qty * f * rw * cw * rng.normal(1.0, 0.10)), 0)
                    rev = round(qty * price * rng.normal(1.0, 0.03))
                    rows.append((m.date(), pid, region, ch, qty, rev))
    return pd.DataFrame(rows, columns=["month_date", "product_id", "region", "channel",
                                       "quantity", "revenue"])


def main():
    fin = build_financials()
    sales = build_sales()
    dim_cc = pd.DataFrame(COST_CENTERS, columns=["cc_id", "cc_name", "division", "region"])
    dim_acc = pd.DataFrame(ACCOUNTS, columns=["account_id", "account_name", "account_group"])
    dim_prod = pd.DataFrame(PRODUCTS, columns=["product_id", "product_name", "category", "unit_price"])

    con = duckdb.connect(str(DB))
    for name, df in [("dim_cost_center", dim_cc), ("dim_account", dim_acc),
                     ("dim_product", dim_prod), ("fact_financials", fin), ("fact_sales", sales)]:
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM df")

    # ---- 마트 뷰: 팩트+차원 조인 + 시간 차원 파생 (시맨틱 레이어의 물리적 기반) ----
    con.execute("""
        CREATE OR REPLACE VIEW mart_financials AS
        SELECT f.month_date,
               year(f.month_date)    AS year,
               quarter(f.month_date) AS quarter,
               month(f.month_date)   AS month,
               c.cc_id, c.cc_name, c.division, c.region,
               a.account_id, a.account_name, a.account_group,
               f.version, f.amount
        FROM fact_financials f
        JOIN dim_cost_center c USING (cc_id)
        JOIN dim_account a USING (account_id)
    """)
    con.execute("""
        CREATE OR REPLACE VIEW mart_sales AS
        SELECT s.month_date,
               year(s.month_date)    AS year,
               quarter(s.month_date) AS quarter,
               month(s.month_date)   AS month,
               p.product_id, p.product_name, p.category,
               s.region, s.channel, s.quantity, s.revenue
        FROM fact_sales s
        JOIN dim_product p USING (product_id)
    """)

    print(f"적재 완료 → {DB.name}")
    for t in ["dim_cost_center", "dim_account", "dim_product", "fact_financials", "fact_sales"]:
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {n:,} rows")
    # sanity: 2025년 전사 손익
    r = con.execute("""
        SELECT account_group, round(sum(amount)/1e8,1) AS 억원
        FROM mart_financials WHERE version='ACTUAL' AND year=2025
        GROUP BY 1 ORDER BY 1
    """).fetchdf()
    print("\n2025년 실적(억원):"); print(r.to_string(index=False))
    con.close()


if __name__ == "__main__":
    main()
