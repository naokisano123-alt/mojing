# R6-A｜Ma：可追溯上下文选择模块

独占server/app/narration_context/**、server/tests/narration_context/**、本人r6报告。实现M2_A_API约定build_context(conn,world_id,action)与ContextBundle/ContextError；直接读现有SQLite，不改schema、旧检索或服务，不commit/联网。

校验世界active，严格world隔离；只选active文档chunk及confirmed人工条目，draft不自动提升。confirmed且needs_source_review为true（存在失效来源）条目保守排除；无来源manual confirmed合法，metadata明确人工设定。全部资料先按确定性规则筛选，不能读取另一世界或全库后再给模型。
建议：复用现有字面search_chunks匹配行动，在action较长或无命中时先取本世界confirmed条目、再按文档id/chunk_index稳定补足，不用随机或虚构语义相关性。所有SQL参数化。报告选择规则及局限。
最多8份、单份4000UTF-16、总16000；超长前缀安全截取不可切开代理对；空白不占条目。source_id带chunk:/entry:命名空间去重；metadata与sources完全一一对应。chunk metadata包含真实document_id/chunk_id/chunk_index，entry包含entry_id与标题。
这只是创作者资料预览上下文，不宣称已实现NPC秘密权限/长期记忆。引用身份由核心验证，不能承诺事实正确。
测试：跨世界、删除、draft、失效引用、无来源confirmed、emoji预算、稳定排序和无匹配回退、空资料合法、SQL特殊字符、metadata对应。已有R5服务可读调用，不修改其文件。给DSH最小拼装示例与实跑报告，模块独立交付。
