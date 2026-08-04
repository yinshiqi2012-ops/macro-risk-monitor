"""CLI 入口：python -m monitor.run [--watchlist PATH] [--asof YYYY-MM-DD] [--symbol CODE] [--format md|html|pdf]

逐品种增量更新数据 → 计算指标 → 打分 → 状态机 → 生成日报。
默认同时输出 reports/{watchlist}-{date}.{md,html,pdf} 三份；--format 可单选。
单品种失败不中断整体，报告中标注「数据缺失」。
"""
import argparse
from datetime import datetime
from pathlib import Path

import yaml

from . import data, indicators, report, state

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WATCHLIST = ROOT / "config" / "my_watchlist.yaml"
if not DEFAULT_WATCHLIST.exists():
    DEFAULT_WATCHLIST = ROOT / "config" / "watchlist.yaml"


def load_watchlist(path):
    with open(Path(path), encoding="utf-8") as f:
        return yaml.safe_load(f)["symbols"]


def watchlist_prefix(path):
    """从 watchlist 路径提取清单名，用于报告文件名前缀。"""
    return Path(path).stem


def analyze(cfg, asof=None):
    df = data.update(cfg, asof=asof)
    if df is None or len(df) < 60:
        raise RuntimeError(f"数据量不足（{0 if df is None else len(df)} 行）")
    ind = indicators.compute(df)
    sc = state.score(ind)
    return state.run_state_machine(sc)


def main():
    ap = argparse.ArgumentParser(description="宏观及风险监控系统")
    ap.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST),
                    help="watchlist 文件路径；默认 config/my_watchlist.yaml（不存在时回退到 config/watchlist.yaml）")
    ap.add_argument("--asof", help="用截至该日（含）的数据出报告，用于历史回放")
    ap.add_argument("--symbol", help="单品种详细输出（watchlist 中的 code）")
    ap.add_argument("--format", choices=["md", "html", "pdf"],
                    help="只输出单一格式；缺省同时生成 md/html/pdf 三份")
    args = ap.parse_args()

    asof = args.asof
    date_str = asof or datetime.now().strftime("%Y-%m-%d")
    prefix = watchlist_prefix(args.watchlist)
    if prefix == "watchlist":
        prefix = None  # 默认清单保持原 reports/YYYY-MM-DD.{md,html,pdf}
    cfgs = load_watchlist(args.watchlist)
    if args.symbol:
        matched = [c for c in cfgs if c["code"] == args.symbol]
        if not matched:
            raise RuntimeError(f"watchlist 中未找到品种：{args.symbol}")
        cfgs = matched

    results = {}
    for cfg in cfgs:
        code = cfg["code"]
        try:
            sc = analyze(cfg, asof=asof)
            last = sc.iloc[-1]
            marks = [label for flag, label in [
                ("div_bear", "顶背离⚠"), ("div_bull", "底背离✓"),
                ("vol_stall", "放量滞涨"), ("lowvol_stabil", "缩量企稳"),
                ("div_vol_break", "放量破位"), ("div_lowvol_high", "缩量新高"),
            ] if bool(last.get(flag))]
            results[code] = {"ok": True, "code": code, "name": cfg["name"],
                             "category": cfg["category"],
                             "sector": cfg.get("sector", cfg["category"]),
                             "state": last["state"], "overheat": last["overheat"],
                             "capit": last["capit"], "row": last, "marks": marks,
                             "tip": state.STATE_TIPS[last["state"]],
                             "fam": {"position": last["f_position"],
                                     "momentum": last["f_momentum"],
                                     "volatility": last["f_volatility"],
                                     "volume": last["f_volume"]},
                             "fam_tail": sc[["date", "f_position", "f_momentum",
                                             "f_volatility", "f_volume"]].tail(20),
                             "last_date": last["date"].strftime("%Y-%m-%d")}
            print(f"[OK] {cfg['name']}({code}) 截至 {results[code]['last_date']} "
                  f"阶段={last['state']} 过热={last['overheat']:.0f} 恐慌={last['capit']:.0f}")
        except Exception as e:  # noqa: BLE001
            results[code] = {"ok": False, "name": cfg["name"],
                             "category": cfg["category"], "error": str(e)}
            print(f"[数据缺失] {cfg['name']}({code}): {e}")

    prev = report.load_prev_snapshot(prefix=prefix)
    fmts = [args.format] if args.format else ["md", "html", "pdf"]
    paths = []
    if "md" in fmts:
        paths.append(report.write(date_str,
                                  report.build(date_str, results, prev,
                                               detail_symbol=args.symbol),
                                  prefix=prefix))
    html_path = None
    if "html" in fmts or "pdf" in fmts:  # PDF 由 HTML 转换，需要先生成 HTML
        html_path = report.write_html(
            date_str, report.build_html(date_str, results, prev,
                                        detail_symbol=args.symbol),
            prefix=prefix)
        if "html" in fmts:
            paths.append(html_path)
    if "pdf" in fmts:
        pdf_path, msg = report.write_pdf(html_path, date_str, prefix=prefix)
        if pdf_path:
            paths.append(pdf_path)
        else:
            print(f"[提示] {msg}")
    report.save_snapshot(date_str, results, prefix=prefix)
    dashboard_path = write_dashboard_data(date_str, results)
    print("\n报告已生成：")
    for p in paths:
        print(f"  {p}")
    print(f"\n桌面看板已更新：\n  {dashboard_path}")


def write_dashboard_data(date_str, results, dashboard_dir=None):
    """把最新结果写成 dashboard/data.js，供 dashboard/index.html 展示。"""
    import json
    from datetime import datetime
    if dashboard_dir is None:
        dashboard_dir = ROOT / "dashboard"
    dashboard_dir.mkdir(exist_ok=True)

    CATEGORY_ORDER = {"A股": 0, "港股": 1, "ETF": 2, "LOF": 3,
                      "海外指数": 4, "加密": 5, "商品": 6, "贵金属": 7}
    STATES = ["底部盘整", "启动", "趋势延续", "加速/过热",
              "高位派筹", "破位下跌", "恐慌投降", "筑底修复"]
    phase_index = {s: i for i, s in enumerate(STATES)}

    ok_items = [r for r in results.values() if r.get("ok")]
    groups = {}
    for r in ok_items:
        key = (r["category"], r.get("sector", r["category"]))
        groups.setdefault(key, []).append({
            "code": r.get("code", ""),
            "name": r["name"],
            "state": r["state"],
            "day_change": round(r["row"].get("day_change", 0), 2),
            "overheat": round(r["overheat"]),
            "capit": round(r["capit"]),
            "marks": " ".join(r.get("marks", [])),
            "tip": r.get("tip", ""),
        })

    sorted_groups = []
    for (cat, sec), items in sorted(groups.items(),
                                    key=lambda kv: (CATEGORY_ORDER.get(kv[0][0], 99),
                                                    kv[0][1],
                                                    min(phase_index.get(i["state"], 99) for i in kv[1]))):
        sorted_groups.append({
            "category": cat,
            "sector": sec,
            "items": sorted(items, key=lambda i: (phase_index.get(i["state"], 99), i["name"]))
        })

    data = {
        "date": date_str,
        "gen_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": len(ok_items),
        "groups": sorted_groups,
    }
    js = "const DASHBOARD_DATA = " + json.dumps(data, ensure_ascii=False, indent=2) + ";\n"
    path = dashboard_dir / "data.js"
    path.write_text(js, encoding="utf-8")
    return path


if __name__ == "__main__":
    main()
