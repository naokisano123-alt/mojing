# R5-M｜Marcin：独立人工条目编辑工作台

先读ROUND5；独占 src/features/lore-entries/**、tests/lore-entries/**、tests/ui/lore-entries/**、docs/reports/gemini-marcin-r5.md。不要再修改查看器/上传/检索模块；Codex已补来源导航，保留chunkId callback。

完整交付一个稳定的编辑工作台：
1. 可选draftStore（R4接口）：get/set/delete；值含draft和baseRevision，key为worldId:entryId或worldId:new。每次编辑及时保存，切tab卸载重建后可恢复；空标题但改来源/类型也不能丢；成功仅清该条草稿。
2. 409冲突拉取远端仅作对照，用户文字和原baseRevision保留；显式确认后才采用新revision继续提交。恢复草稿不自动绑定最新revision。
3. 所有列表/详情/来源检索/保存/删除请求按worldId+请求世代隔离，旧响应不能污染新世界。取消等待不承诺服务端未保存。防重复提交。
4. PATCH只发变更字段，来源未变不重发，失效历史引用仍可显示；来源不可用要提示。api.update的payload可升级为Partial<LoreEntryDraft>，把类型接线要求交DSH。
5. React真实渲染测试覆盖取消恢复、卸载重挂、409对照、延迟响应、保存失败草稿保留、删除冲突；不要复制生产handler到测试里自测。临时API用注入stub即可，不等Ma接口实现。

DSH负责稳定draftStore注入、HTTP适配和Vitest/jsdom/RTL配置，你不改依赖。测试没运行就写未运行；原有node:test/build照常检查。交付manifest/patch/files/报告，写清DSH最多需要的接线变更。若R4包已完成，只交本轮补丁，不重写整个旧包。
