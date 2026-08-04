"""案例库构建：事件锚定 + 阶段窗口指纹（价格层）。

用法：
  .venv/bin/python scripts/build_case_library.py --only BTC-USD_2020_2022 300750.SZ_2020_2024
  .venv/bin/python scripts/build_case_library.py --skip-fetch   # 用已有缓存，不拉新数据

输入：config/case_library.yaml
输出：case_library/segments.csv（每段 1 行）
      case_library/stage_fingerprints.csv（每段 8 行）
      case_library/cross_segment_summary.csv（跨段共性）

锚点只用价格客观事件（复用 indicators/state 的指标，不读状态机输出）。
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor import data, indicators, state  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CFG_PATH = ROOT / "config" / "case_library.yaml"
OUT_DIR = ROOT / "case_library"

STAGES = ["底部盘整", "启动", "趋势延续", "加速/过热",
          "高位派筹", "破位下跌", "恐慌投降", "筑底修复"]

# 锚点阈值与 state.py 对齐（价格层，不读状态机结果）
T_POSMOM = 80
DD_OVERHEAT = -25
ROC_PCTL_BREAK = 30
T_CAPIT_BREAK = 60
DD_BREAK_FALLBACK = -15


def _load_segments(path=CFG_PATH):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["segments"]


def _window_bounds(win):
    start = pd.Timestamp(f"{win[0]}-01")
    end = pd.Timestamp(f"{win[1]}-01") + pd.offsets.MonthEnd(1)
    return start, end


def fetch_yahoo_deep(code, win):
    """nasdaq 源历史不足时，用 Yahoo v8 chart 拉窗口深历史（含熔断）。"""
    start, end = _window_bounds(win)
    # 往前多取 1 年，给 252 日分位留预热
    start_ext = start - pd.Timedelta(days=366)
    p1, p2 = int(start_ext.timestamp()), int(end.timestamp())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    last = None
    for i in range(6):
        try:
            r = requests.get(url, params={"period1": p1, "period2": p2, "interval": "1d"},
                             headers=headers, timeout=20)
            if r.status_code == 200:
                res = r.json()["chart"]["result"][0]
                ts = res["timestamp"]
                q = res["indicators"]["quote"][0]
                adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose")
                df = pd.DataFrame({
                    "date": pd.to_datetime(ts, unit="s"),
                    "open": q.get("open"), "high": q.get("high"),
                    "low": q.get("low"), "close": q.get("close"),
                    "volume": q.get("volume")})
                if adj:
                    df["close"] = adj  # 用调整后收盘价，对齐前复权
                return df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"Yahoo 深历史拉取失败 {code}: {last}")


def fetch_full_history(cfg, win, skip_fetch=False):
    """取该 code 历史并覆盖窗口；nasdaq 历史不足时回退 Yahoo 深历史。"""
    start, end = _window_bounds(win)
    df = None
    if not skip_fetch:
        try:
            df = data.update(cfg)
        except Exception as e:  # noqa: BLE001
            print(f"  [拉取失败] {cfg['code']}: {e}（继续用已有缓存）")
    if df is None:
        df = data.load(cfg["code"])

    # 检查窗口覆盖：缓存必须包含窗口内数据（给 252 日分位留预热）
    covers = (df is not None and len(df) >= 60
              and df["date"].min() <= start and df["date"].max() >= end)
    # nasdaq 或显式需要深历史的品种（如 BTC-USD / 带 yahoo_code）走 Yahoo
    yahoo_code = cfg.get("yahoo_code") or (
        cfg["code"] if (cfg["source"] == "nasdaq" or cfg["code"] in {"BTC-USD"}) else None)
    if not covers and yahoo_code:
        print(f"  [回退] {cfg['code']} 缓存不覆盖窗口，改用 Yahoo 深历史 ({yahoo_code})")
        df = fetch_yahoo_deep(yahoo_code, win)
        # 缓存为同 code 文件，便于复跑；列与 data._std 对齐
        cache = data.cache_path(cfg["code"])
        old = data.load(cfg["code"])
        if old is not None and len(old):
            df = pd.concat([df, old]).drop_duplicates("date", keep="first")
            df = df.sort_values("date").reset_index(drop=True)
        df.to_csv(cache, index=False)

    if df is None or len(df) < 60:
        raise RuntimeError(f"{cfg['code']} 缓存不足（{0 if df is None else len(df)} 行）")
    return df


def compute_scores(df):
    ind = indicators.compute(df)
    return state.score(ind)  # 不调用 run_state_machine


def anchor(sc, win, category=""):
    """在 window 内找 7 个锚点，返回 dict（缺失为 None）+ flags。"""
    start, end = _window_bounds(win)
    w = sc[(sc["date"] >= start) & (sc["date"] <= end)].copy()
    flags = []
    if len(w) < 60:
        raise RuntimeError(f"窗口内数据不足（{len(w)} 行）")

    a = {"bottom": None, "start": None, "overheat_start": None,
         "peak": None, "breakdown": None, "panic_trough": None, "recovery": None}

    # 1 顶部：窗口内最高收盘价
    peak_i = w["close"].idxmax()
    a["peak"] = w.loc[peak_i, "date"]
    # 周期底：peak 之前窗口内最低收盘价
    before_peak = w[w["date"] <= a["peak"]]
    bot_i = before_peak["close"].idxmin()
    a["bottom"] = w.loc[bot_i, "date"]

    # 2 启动：bottom 之后首次站上 MA50 且 bias50 转正且此前30日曾为负
    after_bot = w[w["date"] > a["bottom"]]
    bias_min30 = w["bias50"].rolling(30, min_periods=1).min()
    cand = after_bot[(after_bot["close"] > after_bot["ma50"])
                     & (after_bot["bias50"] > 0)
                     & (bias_min30.loc[after_bot.index] < 0)]
    if len(cand):
        a["start"] = cand.iloc[0]["date"]

    # 3 过热起点：start 之后首次 posmom>=80 且回撤不深
    base = w[w["date"] > (a["start"] or a["bottom"])]
    cand = base[(base["posmom"] >= T_POSMOM) & (base["drawdown"] >= DD_OVERHEAT)]
    if len(cand):
        a["overheat_start"] = cand.iloc[0]["date"]
    else:
        flags.append("no_overheat")

    # 4 顶部：窗口内最高收盘价（已在前面锚定）
    # 5 破位：peak 之后首次 close<MA50 且 roc 分位低 且 capit 高；缺则降级
    after_peak = w[w["date"] > a["peak"]]
    cand = after_peak[(after_peak["close"] < after_peak["ma50"])
                      & (after_peak["p_roc"] <= ROC_PCTL_BREAK)
                      & (after_peak["capit"] >= T_CAPIT_BREAK)]
    if len(cand):
        a["breakdown"] = cand.iloc[0]["date"]
    else:
        cand2 = after_peak[(after_peak["close"] < after_peak["ma50"])
                           & (after_peak["drawdown"] <= DD_BREAK_FALLBACK)]
        if len(cand2):
            a["breakdown"] = cand2.iloc[0]["date"]
            flags.append("breakdown_fallback")

    # 6 恐慌谷：peak（或 breakdown）之后最低收盘价
    ref = a["breakdown"] or a["peak"]
    after_ref = w[w["date"] >= ref]
    if len(after_ref):
        trough_i = after_ref["close"].idxmin()
        a["panic_trough"] = w.loc[trough_i, "date"]

    # 7 修复确认：谷后首次站上 MA50；或企稳/底背离后 10 日未创新低
    if a["panic_trough"] is not None:
        after_tr = w[w["date"] > a["panic_trough"]]
        cand = after_tr[after_tr["close"] > after_tr["ma50"]]
        if len(cand):
            a["recovery"] = cand.iloc[0]["date"]
        else:
            sig = after_tr[(after_tr["lowvol_stabil"]) | (after_tr["div_bull"])]
            if len(sig):
                a["recovery"] = sig.iloc[0]["date"]
                flags.append("recovery_unconfirmed")

    # 可疑检查
    if a["panic_trough"] is not None:
        if (a["panic_trough"] - a["peak"]).days < 5:
            flags.append("suspect_short_bear")
        seg = w[(w["date"] >= a["peak"]) & (w["date"] <= a["panic_trough"])]
        # 商品/贵金属完整轮回撤常浅于股票；浅熊判定用 -15%，其余仍用 -20%
        dd_gate = -15 if category in ("商品", "贵金属") else -20
        if len(seg) and (seg["close"].min() / seg["close"].max() - 1) * 100 > dd_gate:
            flags.append("suspect_shallow_bear")
    if (a["peak"] - a["bottom"]).days < 60:
        flags.append("suspect_short_cycle")
    if a["overheat_start"] is not None and a["overheat_start"] > a["peak"]:
        flags.append("suspect_overheat_after_peak")

    return a, flags, w


def stage_bounds(a):
    """由锚点切 8 个阶段窗口（日期半开区间 [lo, hi)），缺失给 None。"""
    b = {s: (None, None) for s in STAGES}
    bot, st, oh, pk = a["bottom"], a["start"], a["overheat_start"], a["peak"]
    bd, tr, rc = a["breakdown"], a["panic_trough"], a["recovery"]

    bull_start = bot
    bull_end = pk
    b["底部盘整"] = (bull_start, st or bull_end)
    if st and oh and oh > st:
        cut = st + (oh - st) / 3
        b["启动"] = (st, cut)
        b["趋势延续"] = (cut, oh)
    elif st:
        b["启动"] = (st, bull_end)
    oh_eff = oh if oh and oh <= pk else None
    dist_lo = pk - pd.Timedelta(days=30)
    if oh_eff:
        b["加速/过热"] = (oh_eff, dist_lo if dist_lo > oh_eff else pk)
        b["高位派筹"] = (dist_lo if dist_lo > oh_eff else pk, pk)
    else:
        b["高位派筹"] = (dist_lo, pk)
    bear_end = tr or pk
    panic_lo = (tr - pd.Timedelta(days=20)) if tr else pk
    if bd and tr and tr > bd:
        b["破位下跌"] = (bd, panic_lo if panic_lo > bd else tr)
    elif tr:
        b["破位下跌"] = (pk, panic_lo if panic_lo > pk else tr)
    if tr:
        b["恐慌投降"] = (max(panic_lo, b["破位下跌"][0] or pk), tr)
        b["筑底修复"] = (tr, rc)
    return b


def _iqr(s):
    q = s.quantile([0.25, 0.75])
    return float(q.iloc[1] - q.iloc[0]) if len(s.dropna()) else np.nan


def fingerprint(sc, stage, lo, hi):
    if lo is None:
        return None
    seg = sc[(sc["date"] >= lo) & (sc["date"] < hi)] if hi else sc[sc["date"] >= lo]
    if len(seg) == 0:
        return None
    row = {"stage": stage, "n_days": len(seg)}
    for fam in ["position", "momentum", "volatility", "volume"]:
        row[f"f_{fam}_med"] = float(seg[f"f_{fam}"].median())
        row[f"f_{fam}_iqr"] = _iqr(seg[f"f_{fam}"])
    row["overheat_med"] = float(seg["overheat"].median())
    row["overheat_max"] = float(seg["overheat"].max())
    row["capit_med"] = float(seg["capit"].median())
    row["capit_max"] = float(seg["capit"].max())
    for sig in ["div_bear", "div_bull", "vol_stall", "lowvol_stabil",
                "div_lowvol_high", "div_vol_break"]:
        row[f"{sig}_rate"] = float(seg[sig].astype(float).mean())
    for m in ["rsi14", "roc20", "atr_pct", "vol_z", "drawdown", "bias50"]:
        row[f"{m}_med"] = float(seg[m].median())
    return row


def process_segment(seg_cfg, skip_fetch=False):
    cfg = {"code": seg_cfg["code"], "name": seg_cfg["name"],
           "source": seg_cfg["source"], "category": seg_cfg["category"]}
    if seg_cfg.get("ak_symbol"):
        cfg["ak_symbol"] = seg_cfg["ak_symbol"]
    if seg_cfg.get("yahoo_code"):
        cfg["yahoo_code"] = seg_cfg["yahoo_code"]

    df = fetch_full_history(cfg, seg_cfg["window"], skip_fetch=skip_fetch)
    sc = compute_scores(df)
    a, flags, w = anchor(sc, seg_cfg["window"], category=cfg.get("category", ""))

    seg_row = {"case_id": seg_cfg["case_id"], "code": cfg["code"],
               "name": cfg["name"], "source": cfg["source"],
               "category": cfg["category"], "bubble": seg_cfg.get("bubble", ""),
               "anchor_flags": ",".join(flags) if flags else "",
               "reviewed": 0}
    for k in ["bottom", "start", "overheat_start", "peak",
              "breakdown", "panic_trough", "recovery"]:
        seg_row[f"{k}_date"] = a[k].date() if a[k] is not None else None

    if a["bottom"] is not None and a["recovery"] is not None:
        seg_row["cycle_days"] = (a["recovery"] - a["bottom"]).days
    if a["bottom"] is not None and a["peak"] is not None:
        seg_row["bull_days"] = (a["peak"] - a["bottom"]).days
        seg_row["max_gain_pct"] = round((w.loc[w["date"] == a["peak"], "close"].iloc[0]
                                         / w.loc[w["date"] == a["bottom"], "close"].iloc[0]
                                         - 1) * 100, 1)
    if a["peak"] is not None and a["panic_trough"] is not None:
        seg_row["bear_days"] = (a["panic_trough"] - a["peak"]).days
        seg_row["max_dd_pct"] = round((w.loc[w["date"] == a["panic_trough"], "close"].iloc[0]
                                       / w.loc[w["date"] == a["peak"], "close"].iloc[0]
                                       - 1) * 100, 1)
    seg_row["has_volume"] = int(sc["vol_z"].notna().sum() > 0)

    bounds = stage_bounds(a)
    fps = []
    for s in STAGES:
        lo, hi = bounds[s]
        fp = fingerprint(sc, s, lo, hi)
        if fp:
            fp["case_id"] = seg_cfg["case_id"]
            fps.append(fp)
    return seg_row, fps


def cross_segment_summary(fp_df, seg_df):
    df = fp_df.merge(seg_df[["case_id", "category", "bubble"]], on="case_id", how="left")
    num_cols = [c for c in df.columns
                if c.endswith(("_med", "_max", "_rate", "_iqr")) and c != "stage"]
    rows = []
    for s in STAGES:
        sub = df[df["stage"] == s]
        if len(sub) == 0:
            continue
        row = {"stage": s, "n_cases": sub["case_id"].nunique()}
        for c in num_cols:
            row[f"{c}_med_of_med"] = float(sub[c].median())
        rows.append(row)
    by_stage = pd.DataFrame(rows)

    cat_rows = []
    for (s, cat), sub in df.groupby(["stage", "category"]):
        row = {"stage": s, "category": cat, "n_cases": sub["case_id"].nunique()}
        for c in num_cols:
            row[f"{c}_med_of_med"] = float(sub[c].median())
        cat_rows.append(row)
    by_category = pd.DataFrame(cat_rows)
    return by_stage, by_category


def main():
    ap = argparse.ArgumentParser(description="案例库构建（价格层锚定+指纹）")
    ap.add_argument("--config", default=str(CFG_PATH))
    ap.add_argument("--only", nargs="*", help="只跑这些 case_id")
    ap.add_argument("--skip-fetch", action="store_true", help="不拉新数据，只用缓存")
    ap.add_argument("--summary-only", action="store_true",
                    help="不重建段，仅由已有 CSV 重新生成跨段摘要")
    args = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    seg_csv = OUT_DIR / "segments.csv"
    fp_csv = OUT_DIR / "stage_fingerprints.csv"

    if args.summary_only:
        seg_df = pd.read_csv(seg_csv)
        fp_df = pd.read_csv(fp_csv)
    else:
        segments = _load_segments(Path(args.config))
        if args.only:
            segments = [s for s in segments if s["case_id"] in set(args.only)]
        seg_rows, fp_rows = [], []
        for s in segments:
            try:
                seg_row, fps = process_segment(s, skip_fetch=args.skip_fetch)
                seg_rows.append(seg_row)
                fp_rows.extend(fps)
                print(f"[OK] {s['case_id']} 锚点flags={seg_row['anchor_flags'] or '-'} "
                      f"指纹阶段数={len(fps)}")
            except Exception as e:  # noqa: BLE001
                print(f"[FAIL] {s['case_id']}: {e}")
        seg_df = pd.DataFrame(seg_rows)
        fp_df = pd.DataFrame(fp_rows)
        seg_df.to_csv(seg_csv, index=False)
        fp_df.to_csv(fp_csv, index=False)

    if len(fp_df):
        by_stage, by_category = cross_segment_summary(fp_df, seg_df)
        by_stage.to_csv(OUT_DIR / "cross_segment_summary.csv", index=False)
        by_category.to_csv(OUT_DIR / "cross_segment_by_category.csv", index=False)
        print(f"\n输出：{seg_csv}\n      {fp_csv}\n      "
              f"{OUT_DIR / 'cross_segment_summary.csv'}\n      "
              f"{OUT_DIR / 'cross_segment_by_category.csv'}")
    else:
        print("无有效指纹，未生成摘要。")


if __name__ == "__main__":
    main()
