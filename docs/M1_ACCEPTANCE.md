# 墨境 M1 验收记录

> 更新：2026-11-08｜代码基线 `1e5ea77`（PR #5 后）
> 原则：**代码合并 ≠ 验收通过**。本表只记录实际执行过的证据；没跑过的写"未验收"，不继承 M0 的用户确认。

## 1. 结论

| 维度 | 状态 |
| --- | --- |
| 功能代码 | 四格式导入、检索、人工条目已合并进 main |
| 自动化测试 | 后端 pytest + 前端 node:test + Vitest 组件测试（见 §2） |
| 真实 HTTP 成功路径 | 有合成样本覆盖（PDF/DOCX/TXT/MD） |
| 安卓真机 | **未验收** |
| 线上部署 | **未部署**（静态前端无法运行 Python 后端） |
| M1 是否完成 | **未宣布完成**：R4-02 原子 revision、R4-04 来源跳块、R4-07 旧引用编辑、R4-08 草稿生命周期仍待各自任务交付 |

## 2. 功能与证据

| 功能 | 证据 | 状态 |
| --- | --- | --- |
| TXT/MD 导入与分块 | `tests/test_documents.py`、`tests/test_importer.py` | 已通过 |
| PDF/DOCX 正常导入（201 后可读） | `tests/test_documents.py` 真实样本回归 + Codex 复核 | 已通过（合成样本） |
| 无效/加密/扫描 PDF 明确拒绝 | `parsers` 测试 | 已通过 |
| 上传限额逐格式（B1） | 3 条 HTTP 边界用例 | 已通过 |
| UTF-16 文本量守门（B3） | 主管线 + 解析器测试 | 已通过 |
| 原文/分块覆盖不变式 | `test_chunks_cover_text_without_gaps` | 已通过 |
| 中文子串检索 | `lore_search` 测试 + 路由用例 | 已通过 |
| 检索 `match_field` 与 null 坐标 | 后端 + Codex 前端适配测试 | 已通过 |
| 条目 CRUD / revision 冲突 | `test_lore_entries_api.py`、Ma 服务测试 | 已通过（**串行**冲突） |
| 同 revision 并发写只成功一个 | R4-02 已由 Codex 复现为失败；原子修复在 R5 交付 | **未通过（待修复）** |
| 来源绝对坐标与块内校验 | 服务测试 + HTTP 用例 | 已通过 |
| 来源跨世界/已删拒绝 | `test_source_from_other_world_is_rejected` | 已通过 |
| 文档软删后条目仍可编辑 | 需要 PATCH 三态语义 | **未验收（待 R4-07）** |
| 条目来源跳转真实块 | Codex 来源导航修复 | 代码已合并，**组件级测试待 Marcin R5** |
| 切 tab 草稿恢复 | draftStore 尚未接面板 | **未实现** |
| M0 手势/草稿回归 | `npm test`（node:test） | 已通过 |
| 320px / 深浅色 / 移动布局 | 无浏览器自动化 | **未验收** |
| 安卓软键盘、旋转、真机手势 | — | **未验收** |

## 3. 本轮实际执行的命令

```powershell
# 后端
cd server; ..\.venv\Scripts\python.exe -m pytest

# 前端逻辑（node:test）
npm test

# 前端组件（Vitest + jsdom + RTL）
npm run test:ui

# 构建
npm run build
```

数字以当次输出为准，不在此固化"永远通过"的数量。

## 4. 已知限制（对外说明时不可省略）

- 不接模型、不收 API Key，真实世界不能驱动 AI 冒险
- PDF/DOCX 仅文本提取，无 OCR；扫描件直接拒绝
- 检索是字面子串，不承诺分词召回或海量性能
- 仅本地单用户，无鉴权，禁止直接暴露公网
- 线上站点仍是静态 M0 原型，后端能力只在本地可用
