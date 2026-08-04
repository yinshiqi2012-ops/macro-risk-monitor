# 事件锚定规则（v1 草案）

> 目的：用价格客观事件为每段完整生命周期定出 7 个骨干锚点，作为阶段窗口划分与特征提取的基准。
> 原则：规则可程序化、可复现；人工只抽检/修改可疑锚点，不逐日手工标注。

## 输入

- 标准六列日线：`date, open, high, low, close, volume`（来自 `monitor/data.py`）。
- 一段完整生命周期的候选窗口（起止年份），由候选名单给出。

## 7 个骨干锚点定义

| # | 锚点 | 客观判定规则（可程序化） |
|---|---|---|
| 1 | **周期底** `bottom` | 窗口内 `close` 的最低值日；若有多个，取最早一个。 |
| 2 | **启动** `start` | `bottom` 之后，首次同时满足：`close > MA50` 且 `bias50 > 0` 且此前 30 日内 `bias50` 曾 < 0。 |
| 3 | **过热起点** `overheat_start` | `start` 之后，首次 `posmom = (f_position + f_momentum)/2 >= 80` 且 `drawdown >= -25%`。 |
| 4 | **顶部** `peak` | `overheat_start` 之后、`bottom` 之后窗口内 `close` 的最高值日。 |
| 5 | **破位** `breakdown` | `peak` 之后，首次 `close < MA50` 且 `roc20` 分位 ≤ 30 且 `capit >= 60`。 |
| 6 | **恐慌谷** `panic_trough` | `breakdown` 之后窗口内 `close` 的最低值日（即本轮最大回撤谷底）。 |
| 7 | **修复确认** `recovery` | `panic_trough` 之后，首次满足以下任一：`close` 重新站上 MA50；或出现 `lowvol_stabil` / `div_bull` 信号后 10 日内未创新低。 |

> 说明：`f_position/f_momentum/posmom/capit` 等由 `monitor/state.py` 的 `score()` 计算；`lowvol_stabil/div_bull` 由 `monitor/indicators.py` 计算。锚定规则复用现有指标，但**不直接使用状态机输出**作为锚点。

## 锚点缺失的降级处理

| 情况 | 处理 |
|---|---|
| 窗口内找不到 `overheat_start` | 该段标记为「无明确过热段」，仍可保留作对照，但不计入「完整生命周期」段数 |
| 找不到 `breakdown` | 用 `peak` 之后首次 `close < MA50` 且 `drawdown < -15%` 降级判定 |
| 找不到 `recovery` | 用 `panic_trough` 之后 60 日内 `close` 最高点作为「修复观察点」，并标注「修复未确认」 |
| 历史数据不足（< 252 交易日） | 整段降级为半程对照，不进完整生命周期库 |
| **窗口终点仍在历史高位区**（`peak` 距窗口末日 < 60 交易日，且峰后最大回撤浅于 -20%） | **禁止当作完整生命周期**：这是「上涨中有回调」的半程样本（例：黄金 2016→今）。应改用上一轮已走完的峰谷（黄金应为 2011 顶→2015 底） |

## 阶段窗口划分

7 个锚点把一段完整生命周期切成 8 个阶段窗口（对应八阶段状态机）：

| 阶段窗口 | 区间 |
|---|---|
| 底部盘整 | 窗口起点 → `start` |
| 启动 | `start` → `overheat_start` 前半段（`start` 到 `overheat_start` 的前 1/3） |
| 趋势延续 | `start` → `overheat_start` 后半段（后 2/3） |
| 加速/过热 | `overheat_start` → `peak` |
| 高位派筹 | `peak` 前 30 交易日 → `peak`（若 `overheat_start` 距 `peak` < 30 日，则并入加速/过热） |
| 破位下跌 | `peak` → `panic_trough` 前半段 |
| 恐慌投降 | `panic_trough` 前 20 交易日 → `panic_trough` |
| 筑底修复 | `panic_trough` → `recovery` |

> 「高位派筹」窗口刻意取峰前 30 日，与 `check_crashes.py` 的 `TOP_WINDOW = 90` 和派筹确认滞后逻辑对齐。

## 人工抽检规则

- 每段只审 7 个锚点日，不逐日审。
- 抽检比例：首期约 20%（每 5 段抽 1 段全审 7 锚点）；其余段只在程序标记「可疑」时审。
- 「可疑」触发条件（任一）：
  - `peak` 与 `panic_trough` 间隔 < 5 交易日（急跌段过短，可能锚错）；
  - `bottom` 与 `peak` 间隔 < 60 交易日（周期过短）；
  - `drawdown`（`peak`→`panic_trough`）浅于 -20%（不像一轮完整熊市）；
  - `overheat_start` 晚于 `peak`（逻辑倒置）。
