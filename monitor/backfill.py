"""CLI 工具：批量拉取并缓存 watchlist 中所有品种的日线数据。

用法：
    python -m monitor.backfill --watchlist config/my_watchlist.yaml

逻辑：
- 对每个 symbol 调用 data.update 增量补全 data/<code>.csv
- 成功且日线 >= 80 条记为 success（满足 scan.py 与状态机最低样本要求）
- 拉取失败或数据不足 80 条记为 fail，写入 my_watchlist_unresolved.yaml
  并在终端输出失败原因
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

import yaml

from . import data

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WATCHLIST = ROOT / "config" / "my_watchlist.yaml"
DEFAULT_UNRESOLVED = ROOT / "config" / "my_watchlist_unresolved.yaml"
MIN_ROWS = 80


def _load_yaml(path):
    p = Path(path)
    if not p.exists():
        return {"symbols": []}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {"symbols": []}


def _save_yaml(path, doc):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(doc, f, allow_unicode=True, sort_keys=False)


def main():
    ap = argparse.ArgumentParser(description="批量补全 watchlist 日线数据")
    ap.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST),
                    help="要补全的 watchlist 文件")
    ap.add_argument("--unresolved", default=str(DEFAULT_UNRESOLVED),
                    help="失败品种写入的 unresolved 文件")
    ap.add_argument("--min-rows", type=int, default=MIN_ROWS,
                    help="判定成功的最少日线条数")
    ap.add_argument("--dry-run", action="store_true",
                    help="只检查，不修改 watchlist/unresolved 文件")
    args = ap.parse_args()

    wl = _load_yaml(args.watchlist)
    syms = wl.get("symbols", [])
    if not syms:
        print(f"watchlist 为空：{args.watchlist}")
        sys.exit(0)

    unresolved = _load_yaml(args.unresolved)
    existing_unresolved_names = {u.get("name") for u in unresolved.get("symbols", [])}

    success, failed = [], []
    for cfg in syms:
        code = cfg.get("code")
        name = cfg.get("name", code)
        try:
            df = data.update(cfg)
            n = len(df) if df is not None else 0
            if n >= args.min_rows:
                success.append({"code": code, "name": name, "rows": n,
                                "source": cfg.get("source"),
                                "from": df["date"].min().strftime("%Y-%m-%d"),
                                "to": df["date"].max().strftime("%Y-%m-%d")})
                print(f"[OK] {name}({code}): {n} 条 {df['date'].min().date()} ~ {df['date'].max().date()}")
            else:
                reason = f"数据不足：仅 {n} 条（要求 {args.min_rows} 条）"
                failed.append({"cfg": cfg, "reason": reason})
                print(f"[FAIL] {name}({code}): {reason}")
        except Exception as e:  # noqa: BLE001
            reason = f"拉取失败：{e}"
            failed.append({"cfg": cfg, "reason": reason})
            print(f"[FAIL] {name}({code}): {reason}")

    print(f"\n汇总：成功 {len(success)} / 失败 {len(failed)} / 总计 {len(syms)}")

    if failed and not args.dry_run:
        # 将失败品种从 watchlist 移到 unresolved
        failed_codes = {f["cfg"]["code"] for f in failed}
        wl["symbols"] = [s for s in syms if s.get("code") not in failed_codes]
        for f in failed:
            name = f["cfg"].get("name", f["cfg"].get("code"))
            if name not in existing_unresolved_names:
                unresolved.setdefault("symbols", []).append({
                    "name": name,
                    "reason": f["reason"],
                    "code": f["cfg"].get("code"),
                    "source": f["cfg"].get("source"),
                    "moved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
        _save_yaml(args.watchlist, wl)
        _save_yaml(args.unresolved, unresolved)
        print(f"已更新：{args.watchlist}（移除 {len(failed)} 个失败品种）")
        print(f"已更新：{args.unresolved}（追加 {len(failed)} 条失败记录）")
    elif failed:
        print("[dry-run] 以上失败品种未写入 unresolved 文件")


if __name__ == "__main__":
    main()
