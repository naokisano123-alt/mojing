# Codex R4 小修复交接

日期：2026-11-08。基线：02a781b0f170c83b072a89f3e6a1f0451fdb93c6。用户随后授权 Codex 直接修复工作量较小的遗留问题；本次仅处理 R4-01、R4-05、R4-06，不接管四助手的大块任务。

## 已修复

| 审阅编号 | 改动 | 验证 |
| --- | --- | --- |
| R4-01 | main.py 将解析器 TypedDict 警告按 dict 复制，保留缺省 count，不再访问 record.code 导致 AttributeError | 新增真实正常 PDF、DOCX、混合页 PDF 三条参数用例；修复前3条全失败，修复后上传201、警告/raw/chunks落库并在重建app后读回 |
| R4-06 / B2 | WorldLoreView 使用 capabilities.max_bytes 逐格式传给 DocumentUpload；取消“启用PDF就所有格式8MiB”的判断 | 生产 getUploadPolicy 测试自定义限额、能力失败TXT/MD回退及非法限额保守回退 |
| R4-05 / B7 前端 | 生产适配保留非正文null坐标；仅正文有效区间转换全文UTF-16；面板点击标题/文件名结果只打开真实块，不伪造正文高亮 | 生产 toLoreSearchItem→toLoreSourceTarget 测试：emoji前缀、真实0偏移、非正文null、非法/反向/越界区间 |

PDF/DOCX 的 2MiB 前置拦截在 R3 已修复，本次没有重复改上传限额主管线。查看器 warnings/encoding_semantics 展示也已有实现。

## 实际检查

独立目录还原固定SHA源码，不覆盖旧原型项目。

- npm ci：成功，使用仓库锁文件。
- npm test：55 passed（原50 + 新增生产适配测试5），0 failed。
- npm run build：TypeScript与Vite构建成功。
- server目录 python -m pytest -q：130 passed（原127 + 真实HTTP回归3），0 failed；另以collect-only核对共130例。
- 本地 Node24.22.1、Python3.12.14；pytest装在独立临时依赖目录，未改requirements/锁文件。CI继续使用原有Node22/Python3.11矩阵。
- 未做安卓真机验收、未自动更新在线站点/部署后端；新增前端测试是生产纯函数回归，不宣称替代真实React组件点击测试。

## 给四助手的接续

- DSH：先把本修复提交纳入自己的分支。R4-01/R4-05/R4-06不必再从零实现，但仍需在最终组装时回归。Li标准化模块到达后，将目前dict复制替换为约定normalize_warning_records调用。
- Li：继续警告纯模块、逐步预算与更完整HTTP矩阵；不要为R4-01再改main.py。保留此处真实成功路径回归，可复用合成样本。
- Marcin：本修复最小修改了你拥有的LoreSearchPanel.tsx、types.ts并新增sourceTarget.ts，实际事件已调用该生产函数。合包时保留null/可选跳转坐标，继续人工条目来源、超首批块可见性、草稿、并发响应和React渲染测试。
- Ma：任务范围不变，条目原子并发、严格绝对坐标、历史来源仍待你修。

既定三个Gemini可继续使用原R4统一快照，不必重开窗口丢进度。DSH负责以本次修复为目标基线三方合并旧快照产物；禁止用旧WorldLoreView/main/检索模块整文件覆盖。此次所有权例外来自用户明确授权，并已在此记录。

## 未解决的边界

本次不代表人工条目chunkIndex:0回跳、DocumentViewer超首批目标、revision并发、旧引用编辑或草稿生命周期均已完成；它们仍归R4原任务。M1不据此宣布全部验收，M2仍只规划。
