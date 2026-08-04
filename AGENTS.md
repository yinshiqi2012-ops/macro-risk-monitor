# 宏观及风险监控系统 · Agent 规则

## 输出约定

1. **所有生成的文件、报告或目录，必须在回复中给出绝对路径。**
2. **必须同时给出在 macOS Finder 中查看的命令**，让用户可以直接定位到文件：
   - 单个文件：提供 `open -R <absolute-path>`（在文件夹中显示该文件并高亮）
   - 目录：提供 `open <absolute-path>`（直接打开该目录）
3. 如果一次生成了多个相关文件，应列出每个文件的 `open -R` 命令，或给出打开所在目录的 `open` 命令。
4. 路径使用原样字符串，不要转义或缩写（例如使用 `~/projects` 展开后的绝对路径）。

## 示例

生成报告后，回复中应包含：

```text
报告已生成：/Users/truman/projects/宏观及风险监控系统/reports/my_watchlist-2026-08-01.md
在 Finder 中查看：open -R /Users/truman/projects/宏观及风险监控系统/reports/my_watchlist-2026-08-01.md
查看 reports 目录：open /Users/truman/projects/宏观及风险监控系统/reports
```

## 其他约定

- 工作语言：中文。
- 只做状态提示，不输出买卖建议或仓位管理。
- 指标与状态机参数调整后，必须重新运行验证脚本并记录结果。
