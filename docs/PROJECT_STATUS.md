> 当前进入[M2-A](M2_PLAN.md)，四助手按[ROUND6](collaboration/ROUND6.md)开工。用户已反馈安卓已测流程正常；Codex已提交草案校验与Fake核心，M2路由/真实模型/UI仍待各包实现。以下为先前M1阶段记录，当前进度以本段与最新报告为准。

> 本轮更新：[来源导航已补齐](reports/codex-source-navigation.md)，当前协作见[ROUND5](collaboration/ROUND5.md)。并发、草稿和解析健壮性继续分块推进，M1尚待完整验收。

> 后续状态补充：[Codex小修复](reports/codex-r4-hotfix.md)已恢复正常PDF/DOCX上传，接通逐格式前端限额并修检索null坐标。R4其余任务继续；以下审阅基线所述上传阻断已被这次修复替代。

# 墨境｜当前项目状态与新助手入场说明

更新：2026-11-08。审阅代码基线：`8296630705a7e61de95fbf532b8a38846dd82c78`（PR #3 合并）。当前任务以 [ROUND4](collaboration/ROUND4.md) 为准；PROJECT_HANDOFF 保留早期产品设计，不能用其中“尚无后端”的历史描述判断现状。

## 产品是什么

墨境是安卓手机浏览器优先的 AI 文字冒险世界模拟器：创作者导入自己的世界资料，玩家以自由文字决定行动，在有规则、记忆和长期后果的世界里生活。可以探索、谋生、建立关系、失败；不强制英雄主线。视觉兼顾中文小说阅读与现代移动应用，兼容浅色/深色。未来可扩展 PWA 和独立 App。

建议行动是编辑辅助，玩家保留最终决定权。世界资料是数据，未来不得当作模型的系统指令。玩家、角色、NPC、创作者的知识边界最终需要分别建模；这些原则尚不等于已有实现。

## 实际完成到哪里

| 层 | 当前实现 | 边界 |
| --- | --- | --- |
| M0 本地试玩 | 世界卡片、创建、故事输入、角色、图鉴、时间线/存档/分支、设置、主题、手势 | 关键词模拟与预置雾港剧情；不是 AI 或正式状态引擎 |
| M1 世界资料库 | FastAPI + SQLite；世界与文档持久化、TXT/MD 导入、分块、原文分页、中文子串搜索 | 本地单用户服务；没有账户、云同步或正式线上后端 |
| M1 富文档 | pypdf/python-docx 模块、UTF-16 限额、结构化警告和查看器展示 | R4 审阅发现的 HTTP 上传警告异常已由 Codex 小修复；不支持 OCR |
| M1 人工设定 | 五类条目 CRUD、draft/confirmed、引用、revision、软删；前端工作台已接线 | 有并发覆盖、来源定位/校验及 UI 生命周期缺口，见 R4 审阅 |
| M2–M4 | 早期方向 | 未接真实 AI、未要求 Key、未完成正式回合/知识权限引擎、PWA |

导入资料库与 M0 演示是两条数据路径。进入“演示试玩”不会让雾港剧情读取用户资料。人工 confirmed 只表示用户确认录入，不是 AI 已验证事实。

## 必须保留的手机交互

世界内不重复显示品牌栏与全局设置；时间地点栏常驻。光环为右上角低对比四分之一圆入口，不置于主页设置。设置区分阅读、叙事、模型入口。故事默认显示可换行建议及“…”；点击建议展开编辑并追加草稿，不能覆盖手写文字。

四个底部图标是故事、角色、卷轴图鉴、存档。左滑向后页、右滑向前页；故事最左页连续两次右滑才回主页，主页同样连续两次右滑进入封面。交互卡片允许横滑切页，输入编辑、文本选择、弹层、纵向滚动与系统边缘手势要正确区分。不得回退用户确认过的滑动修复。

## 技术与目录

React 19、TypeScript 5.7、Vite 6、Tailwind 4；Python FastAPI；SQLite 标准库连接与显式提交/回滚。暂不换框架或 ORM。

- `src/App.tsx`、`src/pages/**`、`src/model.ts`：M0 与页面协调。
- `src/api/**`：真实 HTTP DTO、客户端。
- `src/components/documents/**`：资料库接线、上传、文档列表/查看器。
- `src/features/lore-entries/**`、`lore-search/**`：人工条目、检索工作台。
- `server/app/main.py`、`db.py`、`config.py`：共享路由、迁移与配置。
- `server/app/parsers/**`、`import_policy/**`：富文档解析与限额策略。
- `server/app/lore_entries/**`、`lore_search/**`：人工设定服务、资料检索。
- `tests/**`、`server/tests/**`、`.github/workflows/ci.yml`：检查。
- `docs/contracts/**`、`docs/reports/**`、`docs/collaboration/**`：契约、实跑报告、当前任务。

## 启动与验证

在仓库根目录（不要把 README 的历史示例目录名当作额外子目录）：

~~~bash
npm ci
npm run dev
npm test
npm run build
~~~

后端另开终端：

~~~bash
python -m venv .venv
# 按系统激活 .venv
python -m pip install -r server/requirements.txt -r server/requirements-parsers.txt -r server/requirements-dev.txt
cd server
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
python -m pytest
~~~

Vite API 转发和环境配置以当前 vite.config.ts、server/README.md 为准。手机访问需局域网地址与可达后端；不要因为代码库公开就把未设鉴权的本地服务直接暴露公网。

M0 数据键 `mojing-m0-v1` 必须保留；SQLite 迁移须幂等且保留原数据，不提交实际数据库、用户资料、密钥或 .env。

## 协作与交付

DeepSeekHarness 可实时读写，负责共享文件、契约、接线、集成与 CI。三个 Gemini 只有指定提交快照，分别完成独立模块，交付包通过用户转给 DeepSeekHarness。他们不是本轮已自动启动的后台代理。

先读 R4 总表，再读自己的任务书；R4 只收口 M1 的可靠性与验收，M2 仅规划。公开仓库不等于站点更新，合并不等于后端已部署。每份报告区分“代码存在”“测试实跑”“手机验收”“已部署”。
