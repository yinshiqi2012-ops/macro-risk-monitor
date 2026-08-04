"""CLI 工具：管理观察清单 config/my_watchlist.yaml。

用法：
    python -m monitor.watchlist add 000300.SH ak_stock 沪深300 A股
    python -m monitor.watchlist list
    python -m monitor.watchlist remove 000300.SH
    python -m monitor.watchlist --watchlist config/my_watchlist.yaml list
"""
import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WATCHLIST = ROOT / "config" / "my_watchlist.yaml"


VALID_SOURCES = [
    "okx", "nasdaq", "naver", "em_index", "ak_index", "ak_sge", "ak_futures",
    "ak_stock", "ak_etf", "ak_hk", "ak_global_index",
]
VALID_CATEGORIES = ["A股", "港股", "ETF", "LOF", "商品", "海外指数", "加密",
                    "美股", "韩股", "A股指数", "贵金属"]


def _load(path):
    p = Path(path)
    if not p.exists():
        return {"symbols": []}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {"symbols": []}


def _save(path, doc):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(doc, f, allow_unicode=True, sort_keys=False)


def cmd_add(args):
    if args.source not in VALID_SOURCES:
        print(f"不支持的 source：{args.source}，可选：{', '.join(VALID_SOURCES)}")
        return 1
    if args.category not in VALID_CATEGORIES:
        print(f"不支持的 category：{args.category}，可选：{', '.join(VALID_CATEGORIES)}")
        return 1
    doc = _load(args.watchlist)
    syms = doc.setdefault("symbols", [])
    for s in syms:
        if s.get("code") == args.code:
            print(f"已存在 {args.code}，请先 remove 再 add")
            return 1
    syms.append({
        "code": args.code,
        "name": args.name,
        "source": args.source,
        "category": args.category,
    })
    _save(args.watchlist, doc)
    print(f"已添加 {args.code} ({args.name}) -> {args.watchlist}")
    return 0


def cmd_list(args):
    doc = _load(args.watchlist)
    syms = doc.get("symbols", [])
    if not syms:
        print(f"{args.watchlist} 暂无品种")
        return 0
    print(f"{'code':<12} {'source':<16} {'category':<8} name")
    print("-" * 60)
    for s in syms:
        print(f"{s.get('code',''):<12} {s.get('source',''):<16} {s.get('category',''):<8} {s.get('name','')}")
    print(f"\n共 {len(syms)} 条")
    return 0


def cmd_remove(args):
    doc = _load(args.watchlist)
    syms = doc.get("symbols", [])
    before = len(syms)
    syms = [s for s in syms if s.get("code") != args.code]
    if len(syms) == before:
        print(f"未找到 {args.code}")
        return 1
    doc["symbols"] = syms
    _save(args.watchlist, doc)
    print(f"已移除 {args.code} -> {args.watchlist}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="管理观察清单")
    ap.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST),
                    help="watchlist 文件路径")
    sub = ap.add_subparsers(dest="cmd", required=True)

    add_p = sub.add_parser("add", help="添加品种")
    add_p.add_argument("code", help="品种代码，如 000300.SH")
    add_p.add_argument("source", help="数据源，如 ak_stock")
    add_p.add_argument("name", help="品种名称")
    add_p.add_argument("category", help="分类，如 A股")
    add_p.set_defaults(func=cmd_add)

    list_p = sub.add_parser("list", help="列出品种")
    list_p.set_defaults(func=cmd_list)

    rm_p = sub.add_parser("remove", help="移除品种")
    rm_p.add_argument("code", help="要移除的代码")
    rm_p.set_defaults(func=cmd_remove)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
