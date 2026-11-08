# 第二轮仓库审阅与派单依据
审阅日期：2026-11-08；固定 SHA：c83eccb44734dd16ef6da8e0c2382eff4bed99df。本次是代码/契约/报告静态审阅；未独立安装并重跑全部测试。以下“已有测试结果”来自 DSH 报告，不代表本次重新验证。

## 已确认进展
- main 已合并 PR #1，无待处理 open PR（本次读取时）。
- M1-A/B 已有 server/app/{main,db,config,errors,importer}.py；SQLite worlds/documents/chunks；TXT/MD 上传、分块与 UTF-16 来源偏移、软删除。
- 已有 src/api/*、DocumentList/Upload/Viewer/WorldLoreView 与首页真实资料库接线；契约 v0.2、Gemini GEM-01 审阅和 DSH 报告已入库。
- 报告最新节记录前端 22 passed、后端 37 passed，早期段落仍保留“等待 GEM-02/未接线/回滚删除 server 即可”的旧状态，需清楚标注历史，不能继续当最新事实。
- 旧 PROJECT_HANDOFF 的“仅 M0、M1 尚未开始”和旧两助手安排已经落后；本轮任务以 ROUND2.md 为准。

## 直接可见的问题/风险
| 文件 | 代码证据或风险 | 派给 |
|---|---|---|
| DocumentViewer.tsx | 首次 getDocumentChunks limit=100，无下一页请求；大文档 >100 块后续无法浏览 | Marcin |
| DocumentViewer.tsx | expandChunk catch 将 text_preview 当展开正文；网络失败会把摘要看成全文 | Marcin |
| DocumentViewer.tsx | documentId 变化没有清空 expandedChunkMap/rawOffset；旧块/旧原文状态可能串文档，异步展开也需防竞态 | Marcin |
| DocumentViewer.tsx | 原文跳转只算页起点，无区间高亮；复制未 await 就显示已复制；分页不能假设代理对边界固定减 20000 | Marcin |
| DocumentUpload.tsx / api/client.ts | 取消会作为 error 显示；重试与旧请求回调竞争、XHR abort 清理需实际行为测试；取消客户端不等于服务端绝不提交 | Marcin/DSH |
| WorldLoreView.tsx | 提示“可检索”但 /search 仍 planned；降级文案“在此世界观下”可能暗示已用导入设定 | DSH 先校正文案；Ma 实现搜索 |
| WorldLoreView.tsx / App 接线 | worldId 变更请求无取消/最新结果保护；#lore 刷新依赖内存上下文 | DSH |
| server/app/main.py / 契约 §5.5 | 同步解析/提交没有明确断连事务保证；“客户端取消就不提交”的契约承诺需验证或纠正 | DSH |
| importer.py | 长段切分后 heading_path 的段落选择值得用多标题、长段、emoji回归验证；此项为疑点，未断言已复现 | Li 提供兼容测试建议，DSH 修共享 importer |
| server/db.py | 简单 CREATE IF NOT EXISTS；后续格式/检索接线需兼容既有库，不可删除重建 | DSH |

## 下一步选择
不重做 A/B；先提升真实资料阅读可靠性，再增加 PDF/DOCX 和中文检索。这三块主要依赖既有 document/chunk 数据，适合离线 Gemini 并行做完整模块。正式条目 CRUD、AI 接入仍留后续，避免把 DSH 又变成主要算法实现者。
