# Gemini（Marcin）｜R3-M 人工条目工作台与资料提示
先读ROUND3及审阅表，保持M0交互。你只读用户快照；包经用户交DSH上传，禁止输出后要求整文件覆盖最新仓库。

## 大模块：人工条目工作台
新增src/features/lore-entries/**，api注入、不改src/api或WorldLoreView。实现分类/状态筛选、分页、列表/详情、新建/编辑/软删除确认。draft与confirmed由用户选择，修改保存前展示变更，不假装自动生成。
表单保留取消/网络错误时草稿；并发revision冲突显示“内容已更新”并允许读取新版/对比本地草稿，不自动重试覆盖。切世界/卸载取消读取、防迟到响应串世界；提交结果按实际响应显示。
来源选择复用注入searchSources（R2检索），用户选真实块形成引用；展示文档/标题/片段，支持移除/原文回跳；同名条目允许不同id。来源失效显示缺失警告，保留历史引用；手工无来源与来源丢失两者区分。实际UTF-16范围由服务端核验，前端不计算虚假excerpt。
本地types.ts/接口按ROUND3定义；DSH做HTTP薄适配。样式限本目录、320px/深浅/键盘与无障碍可用，不改全局styles。

## 修上一轮资料UI缺口
- DocumentUpload支持可选maxBytesByFormat，保留旧maxBytes兼容；按后缀匹配能力表，不用PDF是否启用决定所有格式8MiB；未知配置按保守默认，错误提示实际值。
- DocumentViewer渲染后端warnings和encoding_semantics；PARTIAL_TEXT醒目说明“部分内容未提取”，EXTRACTION_WARNING等也可见，不能泛称完整。警告文本纯文本，不注HTML。
- 任何事件/状态测试与真实HTTP结果分开；检查切文档清警告与异步竞态，不回退R2分页/高亮/复制修复。
专用tests可修改；不要改DocumentList、WorldLoreView、src/api、App、根依赖/lock。所需共享调用变更单列proposal。

## 验收
条目五分类/draft-confirmed/空状态/分页、长中文/emoji来源、拒绝跨世界来源时保草稿、409不覆写、取消和迟到读取、删文档来源失效、无来源人工条目、8MiB PDF与2MiB TXT分别校验、部分解析警告可见。
测试不能只测自造DTO；尽量交组件行为测试及可运行命令，新测试依赖给DSH统一加入。报告docs/reports/gemini-marcin-r3.md；标准包带真实snapshot SHA及接线示例，未运行明确注明。
