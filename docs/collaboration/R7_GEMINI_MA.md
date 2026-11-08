# R7-A｜Ma：上下文有界读取

仅narration_context/**和其测试、本人r7报告，不改lore_search/lore_entries/核心/DB schema。

当前输出8份/16000单位，但fallback fetchall读取全世界文本。改为稳定keyset分页/迭代，有预算就停止，禁止先fetchall所有chunks。confirmed条目也避免全量取文本+N次来源查询，采用有界页/批查询；不依赖私有_fetch_sources_for_entry继续扩散，优先在本模块SQL显式核对引用关系，保持失效来源排除语义。

保留world隔离、draft排除、active文档、无来源manual confirmed合法、稳定排序、emoji安全截断与来源一一对应。动作太长或无命中回退是确定性补足，不宣传语义召回。不得用固定LIMIT前N条就漏掉后面有效条目而不说明；预算/扫描上限若必要需显式稳定且报告。

metadata additive提供selection_reason与truncated，按ROUND7约定。长chunk裁剪只表示送模型片段，回跳仍真实chunkId/index。输出总预算仍由Codex核心二次校验。

测试：大量chunks/confirmed模拟数据、前面空白或失效、匹配与回退、稳定分页、预算停止、跨世界、重复、emoji。用真实SQL查询计数/游标包装证明不会fetchall全部正文，不用仅检查len(result)<=8的测试冒充资源有界。报告复杂度与剩余局限，独立交包供DSH最后装配。
