"""报告层：生成 Markdown / HTML / PDF 日报（reports/YYYY-MM-DD.{md,html,pdf}）。

内容：汇总表（品种/状态/双分/关键读数）、相对上一报告日的状态切换
高亮区、每个状态的机会/风险提示语。只提示状态，不给买卖指令。
HTML 为带内联 CSS 的单文件，状态用语义色；PDF 由无头 Chrome 从 HTML
转换（A4、系统字体），Chrome 不存在时降级并在终端提示，不中断。
"""
import html as _html
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .state import STATE_TIPS
from . import state

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
SNAPSHOT = REPORTS_DIR / "latest_state.json"

# 八阶段语义色（HTML 徽标/提示语左边条），与 dashboard/index.html 保持一致
STATE_COLORS = {
    "底部盘整": "#64748b",   # 石板灰
    "启动": "#16a34a",       # 绿
    "趋势延续": "#15803d",   # 深绿
    "加速/过热": "#ea580c",  # 橙红
    "高位派筹": "#7c3aed",   # 紫
    "破位下跌": "#dc2626",   # 红橙
    "恐慌投降": "#991b1b",   # 深红
    "筑底修复": "#059669",   # 青绿
}

_CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome"),
    shutil.which("chrome"),
    shutil.which("chromium"),
]

DETAIL_ITEMS = [
    ("close", "收盘", 2), ("day_change", "日涨跌 %", 2),
    ("bias50", "BIAS50 %", 1), ("bias200", "BIAS200 %", 1),
    ("drawdown", "距52周高点回撤 %", 1), ("days_since_high", "距高点天数", 0),
    ("rsi14", "RSI(14)", 1), ("roc20", "ROC(20) %", 1),
    ("ret20", "近20日涨幅 %", 1), ("ret60", "近60日涨幅 %", 1),
    ("up_days", "连涨天数", 0), ("atr_pct", "ATR %", 2),
    ("hv20", "HV(20) %", 1), ("extreme60", "近60日|涨跌|>3σ次数", 0),
    ("vol_z", "成交量 z-score", 2),
]


def _fmt(x, nd=1, suffix=""):
    return "—" if x is None or x != x else f"{x:.{nd}f}{suffix}"


def snapshot_path(prefix=None):
    """按 watchlist 前缀区分快照，避免不同清单的状态切换提示串台。"""
    name = f"latest_state-{prefix}.json" if prefix else "latest_state.json"
    return REPORTS_DIR / name


def load_prev_snapshot(prefix=None):
    p = snapshot_path(prefix)
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_snapshot(date_str, results, prefix=None):
    REPORTS_DIR.mkdir(exist_ok=True)
    p = snapshot_path(prefix)
    data = {"date": date_str,
            "symbols": {c: {"state": r["state"], "overheat": round(r["overheat"], 1),
                            "capit": round(r["capit"], 1)}
                        for c, r in results.items() if r.get("ok")}}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _prepare(date_str, results, prev, detail_symbol=None):
    """把 results 整理成渲染无关的数据结构，供 MD/HTML 两个渲染器共用。"""
    prev_syms = prev.get("symbols", {})
    changes, rows, tips = [], [], {}
    for code, r in results.items():
        if not r.get("ok"):
            rows.append({"code": code, "ok": False, "name": r.get("name", code),
                         "category": r.get("category", ""),
                         "sector": r.get("sector", r.get("category", ""))})
            continue
        old = prev_syms.get(code, {}).get("state")
        if old and old != r["state"]:
            changes.append({"code": code, "name": r["name"], "old": old,
                            "new": r["state"], "overheat": r["overheat"],
                            "capit": r["capit"]})
        row = r["row"]
        fam = r["fam"]
        rows.append({"code": code, "ok": True, "name": r["name"],
                     "category": r["category"], "sector": r.get("sector", r["category"]),
                     "state": r["state"], "overheat": r["overheat"], "capit": r["capit"],
                     "f_position": _fmt(fam["position"], 0),
                     "f_momentum": _fmt(fam["momentum"], 0),
                     "f_volatility": _fmt(fam["volatility"], 0),
                     "f_volume": _fmt(fam["volume"], 0),
                     "day_change": _fmt(row.get("day_change"), 2),
                     "marks": " ".join(r["marks"]) or "—",
                     "tip": STATE_TIPS[r["state"]],
                     "bias50": _fmt(row.get("bias50")), "rsi14": _fmt(row.get("rsi14")),
                     "drawdown": _fmt(row.get("drawdown")),
                     "atr_pct": _fmt(row.get("atr_pct")),
                     "vol_z": _fmt(row.get("vol_z"), 1)})
        tips.setdefault(r["state"], []).append(r["name"])

    detail = None
    r = results.get(detail_symbol) if detail_symbol else None
    if r and r.get("ok"):
        row = r["row"]
        fam_tail = []
        for _, t in r["fam_tail"].iterrows():
            fam_tail.append({"date": t["date"].strftime("%m-%d"),
                             "position": _fmt(t["f_position"], 0),
                             "momentum": _fmt(t["f_momentum"], 0),
                             "volatility": _fmt(t["f_volatility"], 0),
                             "volume": _fmt(t["f_volume"], 0)})
        detail = {"code": detail_symbol, "name": r["name"],
                  "items": [(label, _fmt(row.get(k), nd)) for k, label, nd in DETAIL_ITEMS],
                  "marks": " ".join(r.get("marks", [])) or "—",
                  "div_lowvol_high": bool(row.get("div_lowvol_high")),
                  "div_vol_break": bool(row.get("div_vol_break")),
                  "fam_tail": fam_tail}

    return {"date": date_str, "prev_date": prev.get("date") or "无",
            "changes": changes, "rows": rows, "tips": tips, "detail": detail,
            "gen_time": f"{datetime.now():%Y-%m-%d %H:%M}"}


# ---------------- Markdown 渲染 ----------------

def build(date_str, results, prev, detail_symbol=None):
    """results: {code: {ok, name, category, state, overheat, capit, row}}"""
    m = _prepare(date_str, results, prev, detail_symbol)
    L = [f"# 宏观及风险监控日报 {m['date']}", ""]
    L.append("> 只做状态提示，不构成任何买卖建议。指标口径见 README 指标词典。")
    L.append("")

    L.append("## 阶段顺序")
    L.append("")
    L.append("资产周期八阶段（按运行顺序）：")
    L.append("")
    for i, s in enumerate(state.STATES, 1):
        L.append(f"{i}. **{s}**：{STATE_TIPS[s]}")
    L.append("")

    L.append("## 状态切换（相对上一报告日 %s）" % m["prev_date"])
    L.append("")
    if m["changes"]:
        for c in m["changes"]:
            L.append(f"- **{c['name']}({c['code']})**：{c['old']} → **{c['new']}**"
                     f"（过热 {c['overheat']:.0f} / 恐慌 {c['capit']:.0f}）")
    else:
        L.append("- 无状态切换")
    L.append("")

    L.append("## 汇总")
    L.append("")
    L.append("按「类别 → 板块 → 阶段顺序」排列。同一板块内，阶段从早到晚（底部盘整 → 启动 → 趋势延续 → 加速/过热 → 高位派筹 → 破位下跌 → 恐慌投降 → 筑底修复）。")
    L.append("")

    CATEGORY_ORDER = {"A股": 0, "港股": 1, "ETF": 2, "LOF": 3,
                      "海外指数": 4, "加密": 5, "商品": 6, "贵金属": 7}
    phase_index = {s: i for i, s in enumerate(state.STATES)}

    def _row_sort_key(r):
        return (CATEGORY_ORDER.get(r["category"], 99),
                r.get("sector", ""),
                phase_index.get(r["state"], 99) if r.get("ok") else 99,
                r["name"])

    rows = sorted(m["rows"], key=_row_sort_key)
    current_group = None
    for r in rows:
        group = (r["category"], r.get("sector", r["category"]))
        if group != current_group:
            current_group = group
            L.append(f"### {group[0]} · {group[1]}")
            L.append("")
            L.append("| 品种 | 阶段 | 日涨跌 | 位置 | 动量 | 波动 | 量能 | 过热 | 恐慌 | 背离标记 | 行动提示 |")
            L.append("|---|---|---|---|---|---|---|---|---|---|---|")
        if not r["ok"]:
            L.append(f"| {r['name']}({r['code']}) | 数据缺失 | — | — | — | — | — | — | — | — | — |")
        else:
            L.append(f"| {r['name']}({r['code']}) | {r['state']} "
                     f"| {r['day_change']} | {r['f_position']} | {r['f_momentum']} | {r['f_volatility']} "
                     f"| {r['f_volume']} | {r['overheat']:.0f} | {r['capit']:.0f} "
                     f"| {r['marks']} | {r['tip']} |")
    L.append("")

    L.append("## 状态提示")
    L.append("")
    for s, names in m["tips"].items():
        L.append(f"- **{s}**（{'、'.join(names)}）：{STATE_TIPS[s]}")
    L.append("")

    if m["detail"]:
        d = m["detail"]
        L.append(f"## {d['name']}({d['code']}) 详细读数")
        L.append("")
        for label, v in d["items"]:
            L.append(f"- {label}：{v}")
        L.append(f"- 背离标记：{d['marks']}")
        L.append(f"- 缩量新高：{'是' if d['div_lowvol_high'] else '否'}；"
                 f"放量破位：{'是' if d['div_vol_break'] else '否'}")
        L.append("")
        L.append("四族子分近 20 日走势（位置/动量/波动/量能）：")
        L.append("")
        L.append("```")
        L.append("日期   位置  动量  波动  量能")
        for t in d["fam_tail"]:
            L.append(f"{t['date']}  {t['position']:>4}  {t['momentum']:>4}"
                     f"  {t['volatility']:>4}  {t['volume']:>4}")
        L.append("```")
        L.append("")

    L.append("")
    L.append("## 附录")
    L.append("")
    L.append("### 四族子分（位置 / 动量 / 波动 / 量能）")
    L.append("")
    L.append("四族子分都是把对应维度的多个指标换算成「自身过去 252 日滚动窗口的分位数」后合成的，")
    L.append("范围 0–100，含义如下：")
    L.append("")
    L.append("| 子分 | 含义 | 0 附近 | 50 附近 | 100 附近 |")
    L.append("|---|---|---|---|---|")
    L.append("| 位置 | 价格相对均线与 52 周高点的位置 | 深度回撤/严重超卖 | 中等位置 | 远离均线/接近或创新高 |")
    L.append("| 动量 | 涨跌动能与速度 | 极度弱势/快速下跌 | 常态波动 | 极强动量/加速上涨 |")
    L.append("| 波动 | 波动率与价格离散程度 | 极度平静/窄幅盘整 | 常态波动 | 波动剧烈/大阴大阳频出 |")
    L.append("| 量能 | 成交量异动与量价结构 | 极度缩量 | 常态成交 | 显著放量/放量滞涨或破位 |")
    L.append("")
    L.append("### 过热分与恐慌分")
    L.append("")
    L.append("两者都是 0–100 的综合分，用于判断当前处于风险端还是机会端：")
    L.append("")
    L.append(f"- **过热分**：由四族加权合成，权重为 位置 {state.FAMILY_W['position']:.0%}、动量 {state.FAMILY_W['momentum']:.0%}、波动 {state.FAMILY_W['volatility']:.0%}、量能 {state.FAMILY_W['volume']:.0%}。")
    L.append(f"  - ≥ {state.T_ACCEL_POSMOM} 分：进入「加速/过热」风险区；")
    L.append("  - 越高代表价格位置、动量、波动、量能同时处于自身历史极端，追高性价比差。")
    L.append("")
    L.append("- **恐慌分**：反映风险释放强度，由回撤深度、跌速、波动放大、放量下跌、RSI 超卖加权合成。")
    L.append(f"  - ≥ {state.T_BREAKDOWN} 分且仍在急跌：进入「破位下跌」；")
    L.append(f"  - ≥ {state.T_PANIC} 分：进入「恐慌投降」机会观察区；")
    L.append("  - 越高代表下跌速度、波动、卖压同时处于历史极端，但并不意味着立刻见底。")
    L.append("")
    L.append("### 背离标记说明")
    L.append("")
    L.append("背离是「价格走势」与「动能/量能信号」方向不一致的现象，常出现在转折附近。")
    L.append("本报告中的背离/结构标记含义：")
    L.append("")
    L.append("- **顶背离⚠**：价格创 60 日新高，但 RSI14 低于前一个 60 日新高点的 RSI。意味着上涨动能衰减，属于风险信号。")
    L.append("- **底背离✓**：价格创 60 日新低，但 RSI14 高于前一个 60 日新低点的 RSI。意味着下跌动能衰减，属于机会观察信号。")
    L.append("- **放量滞涨**：高位区成交量显著放大，但价格几乎没涨。疑似筹码派发，风险信号。")
    L.append("- **缩量企稳**：深度回撤后成交量萎缩、价格不再创新低。可能是抛压衰竭，机会观察信号。")
    L.append("- **放量破位**：价格跌破近期低点，同时成交量放大。下跌趋势确认，风险信号。")
    L.append("- **缩量新高**：价格创新高但成交量萎缩。上涨动能不足，风险信号。")
    L.append("")
    L.append("---")
    L.append(f"生成时间：{m['gen_time']}")
    return "\n".join(L)


# ---------------- HTML 渲染 ----------------

_CSS = """
@page { size: A4; margin: 15mm; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "PingFang SC", "Hiragino Sans GB",
  "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
  color: #1f2937; background: #f8fafc; margin: 0; padding: 16px; }
.wrap { max-width: 900px; margin: 0 auto; background: #fff; padding: 24px;
  border-radius: 12px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 24px 0 10px; padding-bottom: 6px;
  border-bottom: 2px solid #e5e7eb; }
.meta { color: #6b7280; font-size: 12px; }
.note { background: #fffbeb; border-left: 4px solid #f59e0b; padding: 8px 12px;
  font-size: 13px; color: #92400e; border-radius: 4px; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 999px;
  color: #fff; font-size: 12px; font-weight: 600; white-space: nowrap; }
.change { padding: 8px 12px; margin: 6px 0; background: #f9fafb;
  border-radius: 6px; border-left: 4px solid #9ca3af; font-size: 14px; }
.empty { color: #9ca3af; font-size: 14px; }
.tablebox { overflow-x: auto; -webkit-overflow-scrolling: touch; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { padding: 7px 8px; border-bottom: 1px solid #e5e7eb;
  text-align: right; white-space: nowrap; }
th { background: #f3f4f6; font-weight: 600; }
th:first-child, td:first-child, th:nth-child(2), td:nth-child(2),
th:nth-child(3), td:nth-child(3) { text-align: left; }
.tip { padding: 10px 12px; margin: 8px 0; border-radius: 6px;
  background: #f9fafb; border-left: 4px solid #9ca3af; font-size: 14px; }
.detail { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 6px 16px; font-size: 14px; }
.detail div { padding: 4px 0; border-bottom: 1px dashed #e5e7eb; }
.detail span { color: #6b7280; margin-right: 6px; }
.footer { margin-top: 24px; padding-top: 10px; border-top: 1px solid #e5e7eb;
  color: #9ca3af; font-size: 12px; }
@media print { body { background: #fff; padding: 0; }
  .wrap { box-shadow: none; border-radius: 0; max-width: none; } }
"""


def _esc(s):
    return _html.escape(str(s))


def _badge(state):
    color = STATE_COLORS.get(state, "#9ca3af")
    return f'<span class="badge" style="background:{color}">{_esc(state)}</span>'


def build_html(date_str, results, prev, detail_symbol=None):
    m = _prepare(date_str, results, prev, detail_symbol)
    P = ["<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">",
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         f"<title>宏观及风险监控日报 {_esc(m['date'])}</title>",
         f"<style>{_CSS}</style></head><body><div class=\"wrap\">",
         f"<h1>宏观及风险监控日报 {_esc(m['date'])}</h1>",
         f"<div class=\"meta\">生成时间：{m['gen_time']}</div>",
         '<p class="note">只做状态提示，不构成任何买卖建议。指标口径见 README 指标词典。</p>']

    P.append("<h2>阶段顺序</h2>")
    P.append('<ol style="line-height:1.8;margin-top:6px;">')
    for s in state.STATES:
        P.append(f"<li><b>{_esc(s)}</b>：{_esc(STATE_TIPS[s])}</li>")
    P.append("</ol>")

    P.append(f"<h2>状态切换（相对上一报告日 {_esc(m['prev_date'])}）</h2>")
    if m["changes"]:
        for c in m["changes"]:
            color = STATE_COLORS.get(c["new"], "#9ca3af")
            P.append(f'<div class="change" style="border-left-color:{color}">'
                     f"<b>{_esc(c['name'])}({_esc(c['code'])})</b>："
                     f"{_badge(c['old'])} → {_badge(c['new'])} "
                     f"（过热 {c['overheat']:.0f} / 恐慌 {c['capit']:.0f}）</div>")
    else:
        P.append('<div class="empty">无状态切换</div>')

    P.append("<h2>汇总</h2>")
    P.append('<p style="font-size:13px;color:#6b7280;">按「类别 → 板块 → 阶段顺序」排列。同一板块内，阶段从早到晚（底部盘整 → 启动 → 趋势延续 → 加速/过热 → 高位派筹 → 破位下跌 → 恐慌投降 → 筑底修复）。</p>')

    CATEGORY_ORDER = {"A股": 0, "港股": 1, "ETF": 2, "LOF": 3,
                      "海外指数": 4, "加密": 5, "商品": 6, "贵金属": 7}
    phase_index = {s: i for i, s in enumerate(state.STATES)}

    def _row_sort_key(r):
        return (CATEGORY_ORDER.get(r["category"], 99),
                r.get("sector", ""),
                phase_index.get(r["state"], 99) if r.get("ok") else 99,
                r["name"])

    rows = sorted(m["rows"], key=_row_sort_key)
    current_group = None
    for r in rows:
        group = (r["category"], r.get("sector", r["category"]))
        if group != current_group:
            if current_group is not None:
                P.append("</tbody></table></div>")
            current_group = group
            P.append(f"<h3>{_esc(group[0])} · {_esc(group[1])}</h3>")
            P.append('<div class="tablebox"><table><thead><tr>'
                     "<th>品种</th><th>阶段</th><th>日涨跌</th><th>位置</th><th>动量</th>"
                     "<th>波动</th><th>量能</th><th>过热</th><th>恐慌</th>"
                     "<th>背离标记</th><th>行动提示</th>"
                     "</tr></thead><tbody>")
        if not r["ok"]:
            P.append(f"<tr><td>{_esc(r['name'])}({_esc(r['code'])})</td>"
                     f"<td>数据缺失</td>"
                     + "<td>—</td>" * 10 + "</tr>")
        else:
            P.append(f"<tr><td>{_esc(r['name'])}({_esc(r['code'])})</td>"
                     f"<td>{_badge(r['state'])}</td>"
                     f"<td>{r['day_change']}</td>"
                     f"<td>{r['f_position']}</td><td>{r['f_momentum']}</td>"
                     f"<td>{r['f_volatility']}</td><td>{r['f_volume']}</td>"
                     f"<td>{r['overheat']:.0f}</td><td>{r['capit']:.0f}</td>"
                     f"<td>{_esc(r['marks'])}</td><td>{_esc(r['tip'])}</td></tr>")
    if current_group is not None:
        P.append("</tbody></table></div>")

    P.append("<h2>状态提示</h2>")
    for s, names in m["tips"].items():
        color = STATE_COLORS.get(s, "#9ca3af")
        P.append(f'<div class="tip" style="border-left-color:{color}">'
                 f"{_badge(s)}（{_esc('、'.join(names))}）<br>{_esc(STATE_TIPS[s])}</div>")

    if m["detail"]:
        d = m["detail"]
        P.append(f"<h2>{_esc(d['name'])}({_esc(d['code'])}) 详细读数</h2>")
        P.append('<div class="detail">')
        for label, v in d["items"]:
            P.append(f"<div><span>{_esc(label)}</span>{_esc(v)}</div>")
        P.append(f"<div><span>背离标记</span>{_esc(d['marks'])}</div>")
        P.append(f"<div><span>缩量新高</span>{'是' if d['div_lowvol_high'] else '否'}</div>")
        P.append(f"<div><span>放量破位</span>{'是' if d['div_vol_break'] else '否'}</div>")
        P.append("</div>")
        P.append("<h2>四族子分近 20 日走势</h2>")
        P.append('<div class="tablebox"><table><thead><tr><th>日期</th>'
                 "<th>位置</th><th>动量</th><th>波动</th><th>量能</th>"
                 "</tr></thead><tbody>")
        for t in d["fam_tail"]:
            P.append(f"<tr><td>{t['date']}</td><td>{t['position']}</td>"
                     f"<td>{t['momentum']}</td><td>{t['volatility']}</td>"
                     f"<td>{t['volume']}</td></tr>")
        P.append("</tbody></table></div>")

    P.append("<h2>附录</h2>")
    P.append("<h3>四族子分（位置 / 动量 / 波动 / 量能）</h3>")
    P.append('<p>四族子分都是把对应维度的多个指标换算成「自身过去 252 日滚动窗口的分位数」后合成的，范围 0–100：</p>')
    P.append('<div class="tablebox"><table><thead><tr>'
             '<th>子分</th><th>含义</th><th>0 附近</th><th>50 附近</th><th>100 附近</th>'
             '</tr></thead><tbody>')
    rows = [
        ("位置", "价格相对均线与 52 周高点的位置", "深度回撤/严重超卖", "中等位置", "远离均线/接近或创新高"),
        ("动量", "涨跌动能与速度", "极度弱势/快速下跌", "常态波动", "极强动量/加速上涨"),
        ("波动", "波动率与价格离散程度", "极度平静/窄幅盘整", "常态波动", "波动剧烈/大阴大阳频出"),
        ("量能", "成交量异动与量价结构", "极度缩量", "常态成交", "显著放量/放量滞涨或破位"),
    ]
    for cells in rows:
        P.append("<tr><td>" + "</td><td>".join(_esc(c) for c in cells) + "</td></tr>")
    P.append("</tbody></table></div>")

    P.append("<h3>过热分与恐慌分</h3>")
    P.append("<p>两者都是 0–100 的综合分：</p>")
    P.append(f'<p><b>过热分</b>由四族加权合成，权重为 位置 {state.FAMILY_W["position"]:.0%}、动量 {state.FAMILY_W["momentum"]:.0%}、波动 {state.FAMILY_W["volatility"]:.0%}、量能 {state.FAMILY_W["volume"]:.0%}。≥ {state.T_ACCEL_POSMOM} 分进入「加速/过热」风险区；越高代表价格位置、动量、波动、量能同时处于自身历史极端，追高性价比差。</p>')
    P.append(f'<p><b>恐慌分</b>反映风险释放强度，由回撤深度、跌速、波动放大、放量下跌、RSI 超卖加权合成。≥ {state.T_BREAKDOWN} 分且仍在急跌进入「破位下跌」；≥ {state.T_PANIC} 分进入「恐慌投降」机会观察区；越高代表下跌速度、波动、卖压同时处于历史极端，但并不意味着立刻见底。</p>')

    P.append("<h3>背离标记说明</h3>")
    P.append('<p>背离是「价格走势」与「动能/量能信号」方向不一致的现象，常出现在转折附近。</p>')
    P.append('<ul style="line-height:1.8;">')
    marks = [
        ("顶背离⚠", "价格创 60 日新高，但 RSI14 低于前一个 60 日新高点的 RSI。意味着上涨动能衰减，属于风险信号。"),
        ("底背离✓", "价格创 60 日新低，但 RSI14 高于前一个 60 日新低点的 RSI。意味着下跌动能衰减，属于机会观察信号。"),
        ("放量滞涨", "高位区成交量显著放大，但价格几乎没涨。疑似筹码派发，风险信号。"),
        ("缩量企稳", "深度回撤后成交量萎缩、价格不再创新低。可能是抛压衰竭，机会观察信号。"),
        ("放量破位", "价格跌破近期低点，同时成交量放大。下跌趋势确认，风险信号。"),
        ("缩量新高", "价格创新高但成交量萎缩。上涨动能不足，风险信号。"),
    ]
    for title, desc in marks:
        P.append(f"<li><b>{_esc(title)}</b>：{_esc(desc)}</li>")
    P.append("</ul>")

    P.append('<div class="footer">宏观及风险监控系统 · 只做状态提示，不做买卖建议</div>')
    P.append("</div></body></html>")
    return "\n".join(P)


# ---------------- 写文件 / PDF ----------------

def write(date_str, content, prefix=None):
    REPORTS_DIR.mkdir(exist_ok=True)
    name = f"{prefix}-{date_str}" if prefix else date_str
    p = REPORTS_DIR / f"{name}.md"
    p.write_text(content, encoding="utf-8")
    return p


def write_html(date_str, content, prefix=None):
    REPORTS_DIR.mkdir(exist_ok=True)
    name = f"{prefix}-{date_str}" if prefix else date_str
    p = REPORTS_DIR / f"{name}.html"
    p.write_text(content, encoding="utf-8")
    return p


def find_chrome():
    for c in _CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    return None


def write_pdf(html_path, date_str, prefix=None):
    """无头 Chrome 从 HTML 转 PDF（A4 由 CSS @page 控制）。返回 (路径|None, 提示信息)。"""
    chrome = find_chrome()
    name = f"{prefix}-{date_str}" if prefix else date_str
    pdf_path = REPORTS_DIR / f"{name}.pdf"
    if not chrome:
        return None, "未找到 Chrome，已跳过 PDF（仅生成 HTML+MD）"
    try:
        proc = subprocess.run(
            [chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
             f"--print-to-pdf={pdf_path}", str(html_path)],
            capture_output=True, text=True, timeout=90)
        if proc.returncode == 0 and pdf_path.exists() and pdf_path.stat().st_size > 0:
            return pdf_path, ""
        return None, f"Chrome PDF 转换失败：{(proc.stderr or proc.stdout).strip()[:200]}"
    except Exception as e:  # noqa: BLE001
        return None, f"Chrome PDF 转换失败：{e}"
