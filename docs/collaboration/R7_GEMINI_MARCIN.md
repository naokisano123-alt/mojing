# R7-M｜Marcin：会话交互可靠性

仅改src/features/narration-preview/**、tests/ui/narration-preview/**、本人r7报告。使用ROUND7固定快照，别从main旧代码重新做R6。

1. connect/test/delete和preview统一使用请求世代+当前world+挂载保护；API可选signal按ROUND7扩展，取消/模式切换/会话更换/世界切换后旧结果不得复活。
2. 创建响应晚到时不挂到新世界；若已拿到过时session_id，尽力调用delete清理。不把取消等待等同服务端未创建，无法确认清理时显示可能保留至TTL。
3. 删除失败不显示成功，不静默丢失仍可重试的session_id；让用户重试或明确“仅离开本页”。Key不回显、不落存储，不能用自动重连解决旧会话问题。
4. 同一时刻会话操作/生成的互斥要明确；旧test结果不覆盖新会话，新动作开始时旧状态清理。所有finally也需世代检查。
5. sources可显示selection_reason与truncated的中文说明，字段缺省兼容，不能称为相关性评分；保留source callback。
6. RTL真实deferred测试：A连接晚到/B世界、delete失败、test旧响应、新建与删除竞争、卸载清理、取消后保留行动、Key未持久化。修先前集成修改的测试逻辑，不退回检查已卸载节点value。

不改WorldLoreView/src/api/依赖。给DSH最小signal/类型适配清单；实际跑test:ui/build，报告未跑项。根相对patch+manifest哈希+files交用户转DSH。
