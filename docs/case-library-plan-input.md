# 案例库学习计划输入（Grilling 共识交接）

> 用途：作为后续 plan / 执行模型的输入。领域语言以根目录 `CONTEXT.md` 为准。

## 已确认决策

1. **方法**：案例库学习（可解释规则提炼）；第一阶段不上机器学习。
2. **一段** = 牛+熊合在一起的完整生命周期；半程样本只作对照，不计入段数。
3. **标注**：事件锚定（峰谷/回撤/均线等客观事件）定骨干，人工抽检/修改关键转折日；不以现行状态机输出作为标准答案。
4. **规模**：首期约 50 段完整生命周期，之后持续扩容。
5. **名单来源**：混合——约一半贴近观察清单/用户提供，约一半按资产类型外补已走完一轮的样本。
6. **配额方向**：股票 20–25 · 商品 8–10 · ETF 6–8 · BTC/加密 3–5 · 其余（如港股等）补齐至约 50。
7. **沉淀**：每段 → 阶段特征表（骨干锚点 + 八阶段窗口指纹）；多段 → 跨段共性摘要；再据此调整指标选取与 `monitor/state.py` 中权重/阈值，并跑回归核对。
8. **成功标准**：牛市假阳性可控前提下，最大化顶部与崩盘路径完整度。
9. **边界**：只做状态提示，不给买卖/仓位建议。

## 建议的下一步（供执行）

1. ~~产出首批候选完整生命周期名单~~ → **已锁定**：`docs/case-library-candidates-final.md`（50 段）。
2. ~~定义事件锚定规则~~ → `docs/event-anchoring-rules.md`
3. ~~定义阶段特征表字段~~ → `docs/stage-feature-schema.md`
4. ~~约定回归核对~~ → `docs/regression-check-protocol.md`
5. **下一步**：实现事件锚定脚本 + 阶段特征表生成，批量处理 final 名单；不可用数据源（如 LC0 代码、BTC 2017 深度）在跑数时降级/替换。

## 相关文件

- 领域词表：`/Users/truman/projects/vibecoding/宏观及风险监控系统/CONTEXT.md`
- 现行指标：`/Users/truman/projects/vibecoding/宏观及风险监控系统/monitor/indicators.py`
- 现行权重/阈值：`/Users/truman/projects/vibecoding/宏观及风险监控系统/monitor/state.py`
- 现行核对：`/Users/truman/projects/vibecoding/宏观及风险监控系统/scripts/check_crashes.py`
