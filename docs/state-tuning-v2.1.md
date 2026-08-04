# 状态机小步调参 v2.1（案例库证据）

> 日期：2026-08-02  
> 前提：抽检第 1 轮已落库；证券 ETF 顶取 2020-07；碳酸锂暂用天齐代理。

## 改动

### 1. `monitor/state.py`：筑底修复辅路径

- **新增** `T_REPAIR_VIA_STABIL = T_BREAKDOWN`（60）
- **逻辑**：近 60 日恐慌分曾 ≥ 60 **且** 出现缩量企稳 → 可进「筑底修复」
- **原主路径不变**：曾 ≥ 80 +（缩量企稳 / 底背离 / 波动回落）
- **依据**：案例库破位段缩量企稳出现率约 20%

### 2. `scripts/build_case_library.py`：浅熊 flag 分资产

- 商品/贵金属：峰→谷回撤浅于 **-15%** 才标 `suspect_shallow_bear`
- 其余：仍为 **-20%**
- **不改** 状态机打分（避免全局放宽 `DD_*` 抬高股票假阳性）

### 3. 未改（有意冻结）

- `FAMILY_W` / `CAPIT_W`：50 段证据显示有效
- SPAC：已用 `spac_extreme` 标签隔离，不单独改确认天数

## 回归

```bash
.venv/bin/python scripts/check_crashes.py
# → reports/check_crashes_after.txt
```

硬门禁目标：6/6 通过。
