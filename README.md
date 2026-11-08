# 墨境 · 手机端文字冒险世界模拟器

React + TypeScript + Vite + Tailwind CSS，配合 FastAPI + SQLite 本地资料库。优先适配安卓手机浏览器，支持浅色和深色主题。

当前有三条独立体验：

- **M0 本地试玩**：世界卡片、自由行动输入、小说叙事、角色、图鉴、时间线、存档与分支；剧情由预置数据和关键词规则模拟。
- **M1 世界资料库**：世界持久化，TXT/Markdown/PDF/DOCX 文本导入、分块与原文浏览、中文子串检索、人工设定条目及来源引用。支持草稿恢复、revision冲突处理与来源失效提示。

- **M2-A 叙事实验**：资料库内可选Fake测试或配置OpenAI兼容服务生成Live草案；支持连接测试、停止/重试和引用原文回跳。Key仅放后端内存会话，闲置30分钟后失效。

M2输出为预览草案，不修改角色、金钱、时间或存档。Python后端尚未部署；现有静态站点不代表完整M2版本。PDF/DOCX无OCR，检索为字面匹配。

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

## 使用模型叙事实验

1. 按上方启动前后端，主页进入“世界资料库”，新建世界并导入资料。
2. 进入“叙事实验”。Fake模式免Key，可先检查输入/停止/来源交互。
3. Live模式填写服务商API根地址（通常以/v1结尾，不含/chat/completions）、准确模型名称和Key，建立会话。
4. 主动点击“测试连接”会产生一次极短服务商请求，可能计费。连接成功后输入行动并生成草案。
5. 停止后保留输入，手动重试；关闭连接不能保证服务商未计费。需要移除凭据时点“断开会话”，删除失败会保留重试入口。

真实服务商尚待用户配置验证；标准Chat Completions默认输出额度2048 tokens，服务商需支持相应参数。模型仅能引用本次提供的资料，但程序不能保证叙事完全符合事实。

## 当前进度

Codex已独立接手，旧四助手任务仅作历史；不再等待交包。见[本轮交付](docs/reports/codex-solo-m2.md)、[M2验收](docs/M2_ACCEPTANCE.md)与[下一阶段](docs/M2_PLAN.md)。M0/M1原数据保留，下一阶段才开发正式回合与状态提交。
