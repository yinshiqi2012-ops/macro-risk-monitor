# 回归核对流程（v1 草案）

> 目的：任何指标选取、族权重、状态阈值的改动，都必须可回归、可对比、可留痕。

## 触发时机

以下任一改动前后都必须跑核对：

- `monitor/state.py` 顶部常量（`FAMILY_W` / `CAPIT_W` / `T_*` / `DD_*` 等）
- `monitor/indicators.py` 指标计算逻辑
- 新增/删除指标族成员
- 状态机迁移规则（`ALLOWED_FROM` / `CONFIRM_DAYS`）

## 核对层次

| 层次 | 内容 | 门禁级别 |
|---|---|---|
| **硬门禁** | 现有 6 案例核对（`scripts/check_crashes.py`） | 必须全绿，否则不允许合并改动 |
| **软门禁** | 案例库阶段特征表对比（跨段共性是否仍成立） | 允许个案失败，但需在报告中说明 |
| **抽检** | 改动后跑 `monitor.run` 出当日报告，人工看「哪一族在推分」是否合理 | 人工判断，不阻塞 |

## 硬门禁执行

```bash
# 改动前：记录基线
.venv/bin/python scripts/check_crashes.py > reports/check_crashes_before.txt

# 改动后：对比
.venv/bin/python scripts/check_crashes.py > reports/check_crashes_after.txt
diff reports/check_crashes_before.txt reports/check_crashes_after.txt
```

通过标准（与 `check_crashes.py` 现行口径一致）：

- 4 个崩盘案例：峰前 90 日内出现「加速/过热」（过热分 > 80）或「高位派筹」；峰→谷段出现「破位下跌 → 恐慌投降」且恐慌分 > 80；闪迪/海力士必须识别出「高位派筹」。
- NDX 2023–2024：「趋势延续」为众数，「加速/过热」占比 ≤ 20%。
- BTC 2024 年初：依次出现「启动 → 趋势延续 → 加速/过热」。

## 软门禁执行

案例库建成后，改动前后各生成一次 `cross_segment_summary.csv`，对比：

- 各阶段四族分位中位数是否发生漂移（如「加速/过热」的位置族中位数是否仍显著高于其他阶段）；
- 关键信号出现率是否仍符合直觉（如「高位派筹」的顶背离出现率应高于「趋势延续」）。

漂移超过阈值（建议：中位数变化 > 10 分位点）时，需在改动说明中解释原因。

## 留痕约定

每次改动在 git 提交信息或 `docs/` 下记录：

1. 改了哪些常量/逻辑；
2. 硬门禁结果（before/after 是否全绿）；
3. 软门禁漂移摘要（若有）；
4. 案例库依据（哪几段的共性支持这次改动）。

## 与 AGENTS.md 对齐

> 「指标与状态机参数调整后，必须重新运行验证脚本并记录结果。」——本流程即该约定的具体化。
