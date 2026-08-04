# 阶段特征表字段（v1 草案）

> 目的：每段完整生命周期沉淀一张结构化特征表，用于跨段共性比较，最终指导指标选取与权重/阈值调整。
> 粒度：一段生命周期 = 一行「段级摘要」+ 8 行「阶段窗口指纹」。

## 一、段级摘要（每段 1 行）

| 字段 | 说明 |
|---|---|
| `case_id` | 唯一标识：`{code}_{start_year}_{end_year}`，如 `BTC-USD_2020_2022` |
| `code` / `name` / `source` / `category` | 标的元信息 |
| `bottom_date` / `start_date` / `overheat_start_date` / `peak_date` / `breakdown_date` / `panic_trough_date` / `recovery_date` | 7 个锚点日 |
| `anchor_flags` | 锚点质量标记，逗号分隔：`no_overheat` / `breakdown_fallback` / `recovery_unconfirmed` / `suspect` |
| `cycle_days` | `bottom` → `recovery` 自然日数 |
| `bull_days` / `bear_days` | `bottom`→`peak` / `peak`→`panic_trough` 自然日数 |
| `max_gain_pct` | `bottom`→`peak` 涨幅 % |
| `max_dd_pct` | `peak`→`panic_trough` 回撤 %（负数） |
| `has_volume` | 该标的是否有成交量数据 |
| `reviewed` | 锚点是否经人工抽检确认（0/1） |

## 二、阶段窗口指纹（每段 8 行，每阶段 1 行）

| 字段 | 说明 |
|---|---|
| `case_id` | 关联段级摘要 |
| `stage` | 八阶段之一：底部盘整/启动/趋势延续/加速过热/高位派筹/破位下跌/恐慌投降/筑底修复 |
| `n_days` | 该阶段窗口交易日数 |
| **四族分位（中位数）** | |
| `f_position_med` | 位置族分位中位数 |
| `f_momentum_med` | 动量族分位中位数 |
| `f_volatility_med` | 波动族分位中位数 |
| `f_volume_med` | 量能族分位中位数（无成交量记 NaN） |
| **四族分位（IQR）** | |
| `f_position_iqr` / `f_momentum_iqr` / `f_volatility_iqr` / `f_volume_iqr` | 各族的 25–75 分位区间宽度，衡量阶段内稳定性 |
| **双分** | |
| `overheat_med` / `overheat_max` | 过热分中位数 / 最大值 |
| `capit_med` / `capit_max` | 恐慌分中位数 / 最大值 |
| **关键信号出现率**（该阶段窗口内出现该信号的交易日占比，0–1） | |
| `div_bear_rate` | 顶背离出现率 |
| `div_bull_rate` | 底背离出现率 |
| `vol_stall_rate` | 放量滞涨出现率 |
| `lowvol_stabil_rate` | 缩量企稳出现率 |
| `div_lowvol_high_rate` | 缩量新高出现率 |
| `div_vol_break_rate` | 放量破位出现率 |
| **原始指标（中位数，供回看）** | |
| `rsi14_med` / `roc20_med` / `atr_pct_med` / `vol_z_med` / `drawdown_med` / `bias50_med` | 关键原始指标中位数 |

## 三、跨段共性摘要（多段汇总后生成，单文件）

| 字段 | 说明 |
|---|---|
| `stage` | 八阶段之一 |
| `n_cases` | 该阶段有有效指纹的段数 |
| `f_position_med_of_med` | 各段该阶段 `f_position_med` 的中位数（跨段共性） |
| `f_momentum_med_of_med` / `f_volatility_med_of_med` / `f_volume_med_of_med` | 同上 |
| `overheat_med_of_med` / `capit_med_of_med` | 同上 |
| `div_bear_rate_med` / `vol_stall_rate_med` / ... | 各信号出现率的跨段中位数 |
| `by_category` | 按 `category`（A股/商品/ETF/BTC/港股/海外）分组的上述指标，用于看「因品种而异」的特征 |

## 四、输出文件约定

```text
case_library/
├── segments.csv          # 段级摘要（每段 1 行）
├── stage_fingerprints.csv# 阶段窗口指纹（每段 8 行）
└── cross_segment_summary.csv  # 跨段共性摘要（汇总后生成）
```

## 五、首期不做（按共识）

- 不存逐日全量指标轨迹（太重，锚点+窗口指纹够用）。
- 不在首期试加四族之外的新指标（第二期再评估）。
