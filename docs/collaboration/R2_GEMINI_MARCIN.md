# Gemini（Marcin）｜R2-M 资料阅读与导入可靠性
先读 ROUND2.md、ROUND2_REVIEW.md、项目全貌和契约 v0.2。你是离线快照执行者，完成后文件包交用户，再由 DeepSeekHarness 核验上传。该任务独占范围以 ROUND2.md 表格为准；不要同时改 App、WorldLoreView、src/api 或根配置。

## 交付一个完整可复用的资料阅读模块
1. DocumentViewer 支持完整切片分页/按需加载，不限前100块；显示服务端 total，加载更多失败可重试，避免重复块。
2. 展开全文失败必须显式错误与重试，不把摘要伪装为全文。每块独立加载状态，关闭/换文档取消旧请求；清空旧 doc/cache/raw/page 状态，防止迟到响应串文档。
3. UTF-16 来源区间定位及高亮，允许 optional initialSource；不能切断 emoji。使用服务端 next_offset 和实际页边界历史回退，不依赖恒定 20000。加载失败后的下一次成功清理旧错误。
4. 复制 await 后再显示已复制；权限失败和 clipboard 不可用给准确提示，清理 timer。
5. 上传取消表现为中性状态，防取消/重试旧请求覆盖新请求结果；同文件再次选取可重试；避免卸载后状态更新。重复409应能通过 optional onViewExisting(documentId) 查看已有文档，保留现有强制重复入口但不默认推荐。
6. 对“已上传100%但仍解析/等待响应”展示准确阶段；客户端取消后不要保证服务端没有保存，应建议刷新列表核对。你可以提出 client.ts 改动补丁，但作为 shared-file-proposal，由 DSH 实施，不混入你授权路径的直接替换。
7. 键盘/弹层关闭、滚动、深浅色、320px显示保留可用；资料文本按纯文本呈现，不注入HTML。

## 格式扩展边界
现有 TXT/MD 逻辑先修好。为后续PDF/DOCX允许新增可选 supportedFormats prop/能力注入，默认保持TXT/MD，不能先默认启用未接通格式。公共media_type/DTO扩展交DSH。维护原 props兼容，DocumentList 继续支持删除确认，不自行物理删除。

## 测试与交付
覆盖超过100块、切文档迟到响应、展开500不显示假全文、取消后立即重试、复制拒绝、重复查看、emoji跨页定位、上一页实际边界、失败后恢复。不要仅测DTO字面对象；组件行为测试框架若需新增，给DSH依赖清单与安装命令，不改根package/lock。不能运行就交可运行测试和“未运行”说明。
输出标准包（ROUND2.md），INTEGRATION列出可选新props以及DSH对WorldLoreView所需的最小接线。报告放 docs/reports/gemini-marcin-r2.md；包标识必须带 marcin、R2-M、snapshot SHA。
