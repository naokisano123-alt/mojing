# 第五轮：四个独立工作包（当前生效）

代码起点：8bdf92cc88c6d1aba6673e58cf3bd913e1396d35；实际开工使用包含本任务书及 Codex 来源导航修复的提交快照，记录完整 SHA。先读 PROJECT_STATUS、R4 契约与本轮个人任务书。

本轮 Codex 已处理查看器来源导航：人工引用传 chunkId；按真实ID解析并核对文档/区间；首批之外目标直接显示；独立分页游标避免跳块；同文档换来源重新定位；取消后的请求不更新旧视图。见 [交付报告](../reports/codex-source-navigation.md)。

## 分工

| 助手 | 独立模块 | 任务书 |
| --- | --- | --- |
| Gemini Marcin | 人工条目编辑器：草稿、冲突对比、异步生命周期 | [R5_GEMINI_MARCIN](R5_GEMINI_MARCIN.md) |
| Gemini Li | 文件解析：警告标准化、逐步资源预算、HTTP导入矩阵 | [R5_GEMINI_LI](R5_GEMINI_LI.md) |
| Gemini Ma | 人工条目服务：原子并发、引用完整性、事务回滚 | [R5_GEMINI_MA](R5_GEMINI_MA.md) |
| DeepSeekHarness | 测试基础设施、公共契约与薄适配、集成验收 | [R5_DEEPSEEKHARNESS](R5_DEEPSEEKHARNESS.md) |

这不是重做 R4：已有 R4 包或在途进度继续保留，按本轮范围补交差异。未开工者用本轮同一快照；已开工者保留原 snapshot_commit，如实登记，交 DSH 三方合并，不伪造基线。

## 固定边界

- Marcin 只改 src/features/lore-entries/**、tests/lore-entries/**、tests/ui/lore-entries/** 和本人报告。
- Li 只改 server/app/parsers/**、server/app/import_policy/**、对应 server/tests/parsers/**、import_policy/**、import_pipeline/** 和本人报告。
- Ma 只改 server/app/lore_entries/**、server/tests/lore_entries/** 和本人报告。
- DSH 唯一修改公共路由/数据库事务封装、src/api、WorldLoreView、package/锁/测试配置/CI、公共契约及集成报告。不得提前重写三个 Gemini 的独占模块。
- DocumentViewer、sourceNavigation、viewerUtils、检索面板、本轮来源导航测试由 Codex 已完成，本轮其他助手只读；发现问题报告具体复现。M0手势/数据结构/视觉保持受保护。

## 可独立开发的接口

三个 Gemini 均使用现有模块入口；现有 R4 固定接口继续有效。Marcin 基于注入 api + draftStore 开发，无需等后端；Ma 服务接收 conn，不自行提交；Li 解析器返回 ParsedDocument，新增 normalize_warning_records(records, legacy_warnings=()) 纯函数由 DSH 最后接入。

来源 callback 保留本轮 chunkId:string / 可选 chunkIndex，严禁恢复 chunkIndex:0。所有坐标为全文UTF-16半开区间。本轮不强制后端先增加 chunk_index，客户端已可按 chunkId 独立定位。PATCH省略source_links必须保留原引用；其他 R4约定不变。

DSH给Marcin提供Vitest/jsdom/RTL运行配置，Marcin测试目录固定 tests/ui/lore-entries；配置未到仍可用注入API写组件及测试，不假报实跑。Ma/Li的测试在各自后端目录独立运行，无需等前端。

## 防覆写与交付

三个Gemini包仍由用户转DSH：manifest.json、根目录相对patch.diff、仅改动文件files/、实际测试报告。manifest含任务/作者、完整snapshot_commit、旧blob SHA或SHA256、新内容SHA256、增改删清单；未知填UNKNOWN。禁止绝对路径和tmp/repo_test前缀。交包前干净同快照git apply --check。

DSH每包独立import分支，比较基线→产物→当前分支，重叠修改做三方合并，不整包覆盖；不force push/reset --hard/clean删除他人工作，不重建整棵不含base_tree的Git树。保留原作者和integration_fix记录。Gemini不改共享文件；建议写报告，不以等待超时当作所有权转让。

验收按结果：真实HTTP成功路径、原子revision、草稿行为、来源定位和M0回归分别证明。测试数量不是功能完成证明，未真机/未部署明确标注。本轮仍收口M1，M2只规划，不接Key或真实模型。
