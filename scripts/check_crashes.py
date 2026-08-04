"""周期雷达 v2 历史核对脚本（八阶段状态机）。

用法：.venv/bin/python scripts/check_crashes.py

1) 四大崩盘案例回归：顶部前出现「加速/过热」或「高位派筹」（过热分 > 80）；
   急跌段出现「破位下跌」→「恐慌投降」（恐慌分 > 80）；闪迪/海力士顶部
   区间必须识别出「高位派筹」（横盘滞涨特征）。
2) 牛市上半场案例：NDX 2023–2024 应主要在「趋势延续」、不频繁误报过热；
   BTC 2024 年初主升浪应依次经过「启动→趋势延续→加速/过热」。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.run import load_watchlist, analyze  # noqa: E402

TOP_WINDOW = 90  # 顶部区域：峰前 90 行（约自然日）
CRASH_CASES = ["BTC-USD", "XAG", "SNDK", "000660.KS"]
DIST_REQUIRED = {"SNDK", "000660.KS"}  # 顶部必须识别出高位派筹的案例


def check_crash(code, cfg):
    sc = analyze(cfg)
    recent = sc[sc["date"] >= "2024-01-01"].reset_index(drop=True)
    dd = recent["close"] / recent["close"].cummax() - 1
    trough_i = dd.idxmin()
    peak_i = recent.loc[:trough_i, "close"].idxmax()
    p, t = recent.loc[peak_i], recent.loc[trough_i]
    top = recent.loc[max(0, peak_i - TOP_WINDOW):peak_i]
    dist_win = recent.loc[max(0, peak_i - TOP_WINDOW):peak_i + 10]  # 派筹可滞后峰顶数日确认
    seg = recent.loc[peak_i:trough_i]

    top_states = set(top["state"]) - {None}
    seg_states = [s for s in dict.fromkeys(seg["state"]) if s]
    r = {
        "code": code, "peak": p["date"].date(), "trough": t["date"].date(),
        "dd": dd.min() * 100, "days": (t["date"] - p["date"]).days,
        "top_states": top_states, "top_max_oh": top["overheat"].max(),
        "seg_states": seg_states, "seg_max_cap": seg["capit"].max(),
        "has_dist": "高位派筹" in (set(dist_win["state"]) - {None}),
    }
    r["top_ok"] = (("加速/过热" in top_states and r["top_max_oh"] > 80)
                   or "高位派筹" in top_states)
    r["crash_ok"] = ("破位下跌" in seg_states and "恐慌投降" in seg_states
                     and r["seg_max_cap"] > 80)
    r["dist_ok"] = (code not in DIST_REQUIRED) or r["has_dist"]
    r["pass"] = r["top_ok"] and r["crash_ok"] and r["dist_ok"]
    return r


def check_bull_ndx(cfg):
    """NDX 2023–2024：多数时间应在趋势延续，加速/过热不应频繁误报。"""
    sc = analyze(cfg)
    w = sc[(sc["date"] >= "2023-01-01") & (sc["date"] <= "2024-12-31")]
    w = w[w["state"].notna()]
    share = w["state"].value_counts(normalize=True)
    trend_share = share.get("趋势延续", 0)
    accel_share = share.get("加速/过热", 0)
    ok = trend_share >= 0.4 and accel_share <= 0.2
    return {"code": "NDX 2023-2024", "trend_share": trend_share,
            "accel_share": accel_share, "dist": share.to_dict(), "pass": ok}


def check_bull_btc(cfg):
    """BTC 2024 年初主升浪：应依次经过 启动→趋势延续→加速/过热。"""
    sc = analyze(cfg)
    w = sc[(sc["date"] >= "2023-10-01") & (sc["date"] <= "2024-04-30")]
    w = w[w["state"].notna()]
    seq = [s for s in dict.fromkeys(w["state"])]
    order = ["启动", "趋势延续", "加速/过热"]
    idx = []
    for s in order:
        hits = [i for i, x in enumerate(seq) if x == s]
        idx.append(hits[0] if hits else None)
    ordered = all(i is not None for i in idx) and idx == sorted(idx)
    return {"code": "BTC 2024 主升浪", "seq": seq, "idx": idx, "pass": ordered}


def main():
    import sys
    from pathlib import Path
    path = sys.argv[1] if len(sys.argv) > 1 else "config/watchlist.yaml"
    cfgs = {c["code"]: c for c in load_watchlist(path)}
    results = []
    for code in CRASH_CASES:
        r = check_crash(code, cfgs[code])
        results.append(r)
        print(f"\n=== {r['code']}: {r['peak']} → {r['trough']}  {r['dd']:.1f}% / {r['days']}天")
        print(f"  顶部: 窗口最高过热分={r['top_max_oh']:.0f} 窗口状态={sorted(r['top_states'])}"
              f" → {'✓' if r['top_ok'] else '✗'}")
        print(f"  急跌: 状态序列={'/'.join(r['seg_states'])} 最高恐慌分={r['seg_max_cap']:.0f}"
              f" → {'✓' if r['crash_ok'] else '✗'}")
        if code in DIST_REQUIRED:
            print(f"  派筹识别: {'✓ 高位派筹已捕捉' if r['has_dist'] else '✗ 未捕捉到高位派筹'}")
        print(f"  结论: {'验证通过' if r['pass'] else '未完全触发'}")

    b1 = check_bull_ndx(cfgs["NDX"])
    results.append(b1)
    print(f"\n=== NDX 2023-2024（牛市上半场）")
    print(f"  趋势延续占比={b1['trend_share']:.0%} 加速/过热占比={b1['accel_share']:.0%}")
    print(f"  状态分布={ {k: f'{v:.0%}' for k, v in b1['dist'].items()} }")
    print(f"  结论: {'验证通过' if b1['pass'] else '未通过（误报或识别不足）'}")

    b2 = check_bull_btc(cfgs["BTC-USD"])
    results.append(b2)
    print(f"\n=== BTC 2024 年初主升浪")
    print(f"  状态序列={'/'.join(b2['seq'])}")
    print(f"  启动→趋势延续→加速/过热 依次出现: {'✓' if b2['pass'] else '✗'}")

    n = sum(r["pass"] for r in results)
    print(f"\n{n}/{len(results)} 个案例验证通过")
    return results


if __name__ == "__main__":
    main()
