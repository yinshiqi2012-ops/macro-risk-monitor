"""CLI 工具：按状态扫描观察清单，输出处于指定阶段的品种摘要。

用法：
    python -m monitor.scan --watchlist config/my_watchlist.yaml --state 启动 --state 高位派筹
    python -m monitor.scan --watchlist config/my_watchlist.yaml --state 破位下跌 --state 恐慌投降
"""
import argparse
import sys
from pathlib import Path

import yaml

from . import data, indicators, state
from .state import STATE_TIPS

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WATCHLIST = ROOT / "config" / "my_watchlist.yaml"


def load_watchlist(path):
    with open(Path(path), encoding="utf-8") as f:
        return yaml.safe_load(f).get("symbols", [])


def analyze(cfg):
    df = data.update(cfg)
    if df is None or len(df) < 80:
        raise RuntimeError(f"数据量不足（{0 if df is None else len(df)} 行）")
    ind = indicators.compute(df)
    sc = state.score(ind)
    return state.run_state_machine(sc)


def main():
    ap = argparse.ArgumentParser(description="按状态扫描观察清单")
    ap.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST),
                    help="watchlist 文件路径")
    ap.add_argument("--state", action="append", required=True,
                    help="要扫描的状态（可多次指定）")
    ap.add_argument("--asof", help="使用截至该日的数据")
    args = ap.parse_args()

    target_states = set(args.state)
    cfgs = load_watchlist(args.watchlist)
    if not cfgs:
        print(f"watchlist 为空：{args.watchlist}")
        sys.exit(0)

    matches = []
    for cfg in cfgs:
        code = cfg.get("code")
        try:
            sc = analyze(cfg)
            last = sc.iloc[-1]
            st = last["state"]
            if st in target_states:
                matches.append({
                    "code": code,
                    "name": cfg.get("name", code),
                    "category": cfg.get("category", ""),
                    "state": st,
                    "overheat": last["overheat"],
                    "capit": last["capit"],
                    "tip": STATE_TIPS.get(st, ""),
                })
        except Exception as e:  # noqa: BLE001
            print(f"[跳过] {cfg.get('name', code)}({code}): {e}")

    if not matches:
        print(f"未找到处于 {target_states} 的品种。")
        sys.exit(0)

    print(f"扫描状态：{', '.join(target_states)}  | 共命中 {len(matches)} 个品种\n")
    print(f"{'阶段':<10} {'过热':>6} {'恐慌':>6} {'品种':<20} {'行动提示'}")
    print("-" * 80)
    for m in matches:
        print(f"{m['state']:<10} {m['overheat']:>6.0f} {m['capit']:>6.0f} "
              f"{m['name']}({m['code']}) {m['tip']}")


if __name__ == "__main__":
    main()
