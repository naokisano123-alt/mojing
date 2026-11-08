# 第七轮：M2-A 可靠性收口（当前生效）

先读[审阅](ROUND7_REVIEW.md)和M2_A_API v1.0。本轮代码在feat/r6-integration，基线f379293037ff2ce54e8f2f3abc6892a6fce8fc29；main尚未含这批代码。三个Gemini必须使用**本批任务书发布的完整快照**，不要只下载main。DSH可基于该快照开r7分支，最后连同R6代码一起PR到main。

| 人员 | 独立包 | 文件范围 |
| --- | --- | --- |
| Marcin | 会话UI与异步生命周期 | src/features/narration-preview/**、tests/ui/narration-preview/** |
| Li | 网关响应有效性与URL边界 | server/app/model_gateway/**、server/tests/model_gateway/** |
| Ma | 上下文有界读取与来源可解释性 | server/app/narration_context/**、server/tests/narration_context/** |
| DeepSeekHarness | 断开取消、路由状态、全栈mock、合并验收 | main/config/API/WorldLoreView、集成测试、契约/CI/README |

个人任务书为R7_*.md；每人可写本人r7报告。Codex本轮只审阅和派工，不并行抢这些模块。已有R6代码保留，修差异，不重做整包。

## 固定接口补充

- 预览缺失/过期会话统一409 MODEL_SESSION_EXPIRED，不是404。
- Gateway的length/content_filter/tool_calls等未完成或非文本终态不能成功；正常finish_reason=stop。兼容接口缺finish_reason可保留兼容但明确记录，不把明确截断当成功。
- context metadata可additive增加selection_reason:"literal_match"|"confirmed_fallback"|"document_fallback"、truncated:boolean；既有source字段不变。这是选择过程说明，不是模型相关性评分。主UI容忍字段缺省。
- 面板api.createSession/testSession/deleteSession可additive接受可选AbortSignal参数；DSH在HTTP薄适配透传。所有写操作abort只表示停止等待，不代表后端未执行。过时创建成功需要用返回ID尽力清理孤立会话；失败时明确告知可能保留至TTL。
- 资料库来源回调继续按真实chunkId/entryId处理，不修改预览响应为正式存档。

## 协作和防覆写

三个Gemini用同一包含任务书的快照新开窗口，登记完整snapshot_commit、code_baseline和契约版本。交付manifest.json、根相对patch.diff、仅改动files/、实跑报告；逐路径旧blob/SHA256与新SHA256、增改删必填。无tmp/绝对路径，干净基线apply --check；未知写UNKNOWN。

用户转包给DSH。DSH各包独立import分支校验、三方合并、保留作者；不ZIP覆盖，不强推/reset清理他人修改，API更新保留expected SHA，Git树保留完整base_tree。公共依赖、锁、路由仅DSH改。已有功能文件所有权以本轮为准，其余M0/M1/草案核心保护。

验收：三包单测+真实store/transport全栈mock+真实ASGI断开取消；M0/M1回归、构建、手机M2入口。自动化不使用真实Key，不因没有Key跳过Live mock测试。报告分别写代码、自动化、真机、真实服务商与部署状态。修复通过后DSH开PR合main；不要把M1用户“已测正常”当作M2验收。
