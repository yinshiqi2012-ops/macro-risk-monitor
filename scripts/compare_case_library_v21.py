"""对比 v2.1 改动对案例库的影响（不只看 6 个硬核对）。

1) 价格层指纹：重跑前后 cross_segment_summary 数值漂移
2) 状态机：同一套锚点窗口内，旧/新「筑底修复」规则命中差异

用法（先保存 before CSV，再 build_case_library，再本脚本）：
  .venv/bin/python scripts/compare_case_library_v21.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from monitor import state  # noqa: E402
import build_case_library as bcl  # noqa: E402

OUT = ROOT / "reports" / "case-library-v21-compare.md"


def fingerprint_drift(before_path, after_path, tol=2.0):
    b = pd.read_csv(before_path)
    a = pd.read_csv(after_path)
    key = "stage"
    cols = [c for c in a.columns if c.endswith("_med_of_med")]
    m = b.merge(a, on=key, suffixes=("_before", "_after"))
    rows = []
    for _, r in m.iterrows():
        for c in cols:
            bv, av = r[f"{c}_before"], r[f"{c}_after"]
            if pd.isna(bv) and pd.isna(av):
                continue
            d = float(av - bv) if pd.notna(bv) and pd.notna(av) else np.nan
            rows.append({"stage": r[key], "metric": c, "before": bv,
                         "after": av, "delta": d,
                         "drift": abs(d) > tol if pd.notna(d) else True})
    return pd.DataFrame(rows)


def repair_hit_old(row):
    return bool(row["recent_panic"] >= state.T_PANIC
                and (row["lowvol_stabil_n"] or row["div_bull_n"]
                     or row["f_vol_chg5"] < 0))


def repair_hit_new(row):
    return bool(
        (row["recent_panic"] >= state.T_PANIC
         and (row["lowvol_stabil_n"] or row["div_bull_n"]
              or row["f_vol_chg5"] < 0))
        or (row["recent_panic"] >= state.T_REPAIR_VIA_STABIL
            and row["lowvol_stabil_n"])
    )


def analyze_segment(seg_cfg):
    cfg = {"code": seg_cfg["code"], "name": seg_cfg["name"],
           "source": seg_cfg["source"], "category": seg_cfg["category"]}
    if seg_cfg.get("ak_symbol"):
        cfg["ak_symbol"] = seg_cfg["ak_symbol"]
    if seg_cfg.get("yahoo_code"):
        cfg["yahoo_code"] = seg_cfg["yahoo_code"]
    df = bcl.fetch_full_history(cfg, seg_cfg["window"], skip_fetch=True)
    sc = bcl.compute_scores(df)
    sc = state.run_state_machine(sc)
    # 复用锚点：从已落库 segments.csv 读，避免重锚漂移
    return sc


def main():
    before_sum = ROOT / "reports" / "cross_segment_summary_before_v21.csv"
    after_sum = ROOT / "case_library" / "cross_segment_summary.csv"
    segs = pd.read_csv(ROOT / "case_library" / "segments.csv")
    cfg_by_id = {s["case_id"]: s for s in bcl._load_segments()}

    drift = fingerprint_drift(before_sum, after_sum)
    big = drift[drift["drift"]].sort_values("delta", key=abs, ascending=False)

    # 状态机：在峰后→修复窗口统计筑底修复候选日（旧/新规则，不依赖状态机确认）
    repair_rows = []
    stage_occ = []
    for _, seg in segs.iterrows():
        cid = seg["case_id"]
        if cid not in cfg_by_id:
            continue
        try:
            sc = analyze_segment(cfg_by_id[cid])
        except Exception as e:  # noqa: BLE001
            repair_rows.append({"case_id": cid, "error": str(e)})
            continue
        # 辅列（与 run_state_machine 一致）
        sc = sc.copy()
        sc["recent_panic"] = sc["capit"].rolling(state.RECENT_PANIC_N, min_periods=1).max().shift()
        sc["f_vol_chg5"] = sc["f_volatility"] - sc["f_volatility"].shift(5)
        for sig in ["div_bear", "div_bull", "vol_stall", "lowvol_stabil"]:
            sc[sig + "_n"] = sc[sig].rolling(state.DIVERGENCE_N, min_periods=1).max().fillna(False)

        peak = pd.Timestamp(seg["peak_date"])
        trough = pd.Timestamp(seg["panic_trough_date"]) if pd.notna(seg["panic_trough_date"]) else None
        if trough is None:
            continue
        win = sc[(sc["date"] >= peak) & (sc["date"] <= trough + pd.Timedelta(days=60))]
        if len(win) == 0:
            continue
        old_n = int(win.apply(repair_hit_old, axis=1).sum())
        new_n = int(win.apply(repair_hit_new, axis=1).sum())
        sm_repair = int((win["state"] == "筑底修复").sum())
        repair_rows.append({
            "case_id": cid, "category": seg["category"], "bubble": seg.get("bubble", ""),
            "bear_days": seg.get("bear_days"),
            "old_repair_days": old_n, "new_repair_days": new_n,
            "delta_days": new_n - old_n,
            "state_machine_repair_days": sm_repair,
            "gained": new_n > old_n,
        })
        # 全样本状态占比（峰前 90 日 + 峰到谷后 60 日）
        start_w = peak - pd.Timedelta(days=120)
        end_w = trough + pd.Timedelta(days=60)
        sw = sc[(sc["date"] >= start_w) & (sc["date"] <= end_w)]
        if len(sw):
            dist = sw["state"].value_counts(normalize=True)
            row = {"case_id": cid, "category": seg["category"]}
            for st in state.STATES:
                row[st] = float(dist.get(st, 0.0))
            stage_occ.append(row)

    repair_df = pd.DataFrame([r for r in repair_rows if "error" not in r])
    err_df = pd.DataFrame([r for r in repair_rows if "error" in r])
    occ_df = pd.DataFrame(stage_occ)

    lines = []
    lines.append("# 案例库 v2.1 对比报告\n")
    lines.append("> 目的：确认改动不只贴合 6 个硬核对案例；价格层指纹与状态机辅路径在 48 段上的表现。\n")
    lines.append(f"- 段数：{len(segs)}")
    lines.append(f"- 指纹漂移阈值：|Δ| > 2.0 分位点记为漂移\n")

    lines.append("## 1. 跨段指纹漂移（score 层，与状态机无关）\n")
    if len(big) == 0:
        lines.append("**无显著漂移**（所有阶段 × 指标 |Δ| ≤ 2）。价格层共性稳定。\n")
    else:
        lines.append(f"显著漂移 {len(big)} 项：\n")
        lines.append("| stage | metric | before | after | delta |")
        lines.append("|---|---|---|---|---|")
        for _, r in big.head(30).iterrows():
            lines.append(f"| {r['stage']} | {r['metric']} | {r['before']:.2f} | {r['after']:.2f} | {r['delta']:+.2f} |")
        lines.append("")

    # 关键阶段指纹快照（after）
    after = pd.read_csv(after_sum)
    lines.append("## 2. 重跑后跨段共性快照（位置/动量/过热/恐慌）\n")
    lines.append("| 阶段 | n | 位置族 | 动量族 | 过热分 | 恐慌分 |")
    lines.append("|---|---|---|---|---|---|")
    for _, r in after.iterrows():
        lines.append(
            f"| {r['stage']} | {int(r['n_cases'])} | "
            f"{r['f_position_med_med_of_med']:.1f} | {r['f_momentum_med_med_of_med']:.1f} | "
            f"{r['overheat_med_med_of_med']:.1f} | {r['capit_med_med_of_med']:.1f} |"
        )
    lines.append("")

    lines.append("## 3. 筑底修复辅路径：旧规则 vs 新规则（峰→谷+60日）\n")
    if len(repair_df):
        gained = repair_df[repair_df["gained"]]
        lines.append(f"- 可分析段数：{len(repair_df)}")
        lines.append(f"- 新规则增加修复命中日的段数：{len(gained)} / {len(repair_df)}")
        lines.append(f"- 旧规则命中日合计：{repair_df['old_repair_days'].sum()}")
        lines.append(f"- 新规则命中日合计：{repair_df['new_repair_days'].sum()}")
        lines.append(f"- 净增天数：{repair_df['delta_days'].sum()}\n")
        if len(gained):
            lines.append("### 受益段（新规则多出命中日）\n")
            lines.append("| case_id | category | old | new | Δ |")
            lines.append("|---|---|---|---|---|")
            for _, r in gained.sort_values("delta_days", ascending=False).iterrows():
                lines.append(
                    f"| {r['case_id']} | {r['category']} | {r['old_repair_days']} | "
                    f"{r['new_repair_days']} | +{r['delta_days']} |"
                )
            lines.append("")
        # 按类别
        by_cat = repair_df.groupby("category").agg(
            n=("case_id", "count"),
            gained=("gained", "sum"),
            delta=("delta_days", "sum"),
        ).reset_index()
        lines.append("### 按类别\n")
        lines.append("| category | n | 受益段数 | Δ天数 |")
        lines.append("|---|---|---|---|")
        for _, r in by_cat.iterrows():
            lines.append(f"| {r['category']} | {r['n']} | {int(r['gained'])} | {int(r['delta'])} |")
        lines.append("")
    if len(err_df):
        lines.append(f"数据失败 {len(err_df)} 段：{', '.join(err_df['case_id'])}\n")

    lines.append("## 4. 状态机阶段占比（跨段中位数，峰前120日～谷后60日）\n")
    if len(occ_df):
        lines.append("| 阶段 | 跨段中位占比 |")
        lines.append("|---|---|")
        for st in state.STATES:
            med = occ_df[st].median() * 100
            lines.append(f"| {st} | {med:.1f}% |")
        lines.append("")
        # 牛市假阳性粗检：加速/过热占比中位数
        lines.append(
            f"- 「加速/过热」跨段中位占比：{occ_df['加速/过热'].median()*100:.1f}% "
            f"（NDX 硬门禁要求 ≤20%；此处为跨泡沫均值，仅作参考）\n"
        )

    lines.append("## 5. 结论\n")
    lines.append("- 价格层跨段指纹：见第 1 节；若无显著漂移，说明重跑未扭曲共性。")
    lines.append("- 筑底修复辅路径：若受益段分散在多类别而非仅硬核对 6 案例，则改动具有跨库证据。")
    lines.append("- 泡沫标签层：仍后置，本报告不涉及。\n")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    repair_df.to_csv(ROOT / "reports" / "case-library-v21-repair-delta.csv", index=False)
    if len(occ_df):
        occ_df.to_csv(ROOT / "reports" / "case-library-v21-state-occupancy.csv", index=False)
    print(OUT)
    print("repair gained:", int(repair_df["gained"].sum()) if len(repair_df) else 0,
          "/", len(repair_df))


if __name__ == "__main__":
    main()
