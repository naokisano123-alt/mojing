# Codex 来源导航交付

基线8bdf92cc88c6d1aba6673e58cf3bd913e1396d35；用户授权继续推进及更新分工。此前暂无新增提交或开放PR。

完成：人工设定引用传真实chunkId；查看器通过现有getChunkById解析并核对文档身份、全文、绝对UTF-16区间与代理对边界。非首批目标块加入可见列表并展开，正文引用标记；分页offset单独保存，不被额外插入的目标块污染。同文档新target重载，切换/卸载中止旧展开及分页结果，全文缺失不以preview冒充。

本地npm test 60通过（新增5条真实生产resolver测试）、npm run build通过；后端未改，最终由CI验证原有后端。测试覆盖真实ID/索引、不需要目标时无额外读取、非首批列表合并、错误身份与缺全文、非法UTF-16、取消后的迟到响应。本轮未完成React DOM/安卓真机验收，DSH/Marcin继续补装配和组件层检查。

文件：DocumentViewer、WorldLoreView、LoreEntriesPanel及其callback类型、新sourceNavigation.ts、tests/documents/source-navigation.test.ts。未更改M0或数据库。R4-04代码路径已补齐，其最终UI验收仍需完成。R4并发/草稿/解析预算继续由ROUND5各独立包完成。

来源接口为additive：callback传documentId、chunkId或真实chunkIndex、可选全文区间。旧index入口继续工作，不依赖后端新增chunk_index。旧Gemini包必须三方合并，不恢复第0块硬编码。当前任务所有权以ROUND5为准。
