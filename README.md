# 墨境 · 手机端文字冒险世界模拟器

React + TypeScript + Vite + Tailwind CSS，配合 FastAPI + SQLite 本地资料库。优先适配安卓手机浏览器，支持浅色和深色主题。

当前有两条独立体验：

- **M0 本地试玩**：世界卡片、自由行动输入、小说叙事、角色、图鉴、时间线、存档与分支；剧情由预置数据和关键词规则模拟。
- **M1 世界资料库**：世界持久化，TXT/Markdown/PDF/DOCX 文本导入、分块与原文浏览、中文子串检索、人工设定条目及来源引用。支持草稿恢复、revision冲突处理与来源失效提示。

尚未接真实AI或API Key，导入的设定还不会驱动冒险剧情。Python后端尚未部署到线上；现有静态站点仍为M0演示。PDF/DOCX不含OCR，搜索不含语义理解。

## 启动

从仓库根目录执行，建议Node.js22.18+，Python3.11+。

~~~bash
npm ci
npm run dev
~~~

后端另开终端，先建立并激活Python虚拟环境，再执行：

~~~bash
python -m pip install -r server/requirements.txt -r server/requirements-parsers.txt
python -m uvicorn app.main:app --app-dir server --host 127.0.0.1 --port 8000
~~~

开发时Vite将/api代理到127.0.0.1:8000。手机与电脑在同一局域网时，打开Vite输出的Network地址；电脑需允许对应端口。当前后端按本地单用户设计。

## 检查

~~~bash
npm test
npm run test:ui
npm run build
~~~

后端测试先在根目录安装额外依赖，再进入server运行：

~~~bash
python -m pip install -r server/requirements-dev.txt
cd server
python -m pytest
~~~

测试结果以具体提交的CI和实跑报告为准。安卓软键盘、旋转、系统边缘手势及窄屏阅读仍需真机验收，详见[M1验收记录](docs/M1_ACCEPTANCE.md)。

## 目录

| 路径 | 内容 |
| --- | --- |
| src/pages、model.ts、hooks | M0页面、演示数据与手势 |
| src/api | HTTP客户端及DTO |
| src/components/documents | 资料库、上传、查看器、来源导航 |
| src/features/lore-entries、lore-search | 人工设定工作台、资料检索 |
| server/app | 路由、SQLite、解析、条目服务 |
| tests、server/tests | 逻辑、组件、HTTP及后端回归 |
| docs/contracts、reports | 接口契约、审阅与实跑记录 |
| docs/collaboration | 四助手任务与防覆写约定 |

M0使用浏览器localStorage键mojing-m0-v1；M1使用SQLite，二者隔离。保留旧存档与数据库，迁移不重建清空。

## 当前进度与协作

第五轮三包已通过PR #6合并，详情见[R5集成报告](docs/reports/r5-integration.md)。本次复核补齐调用方事务回滚保护，见[审阅与整理报告](docs/reports/codex-r5-review.md)。

四助手任务入口：[ROUND5](docs/collaboration/ROUND5.md)。已有成果优先核对集成报告，避免重复实施；工作包必须带真实快照SHA和哈希清单，由DeepSeekHarness三方合并，禁止旧ZIP直接覆盖。

[项目说明](docs/PROJECT_STATUS.md) · [M2提案](docs/M2_PLAN.md) · [M0历史原型记录](docs/history/M0_README.md)。M2仍处于规划阶段。
