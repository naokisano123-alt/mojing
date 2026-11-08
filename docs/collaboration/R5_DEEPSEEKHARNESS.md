# R5-D｜DeepSeekHarness：公共接线、测试基础设施与验收

先读ROUND5和Codex两份修复报告。你可实时读写，负责共享文件及最终整合；三个Gemini分别独立开发，勿重复实现其模块。

优先完成无需等包的工作：
- 从最新main创建集成分支，记录SHA；保留Codex PR #4的小修复和本轮来源导航。
- 配置Vitest/jsdom/React Testing Library与npm run test:ui，include tests/ui/**/*.test.tsx，保留原node:test；固定兼容Node22版本，CI两套都运行。Marcin已有tests/ui用例时先核对再配置。
- 写M1_R4_API.md/1.0-r4落实ROUND4固定语义及ROUND5调整。梳理public DTO/PATCH partial，不用as never掩盖差异。
- WorldLoreView用稳定useRef注入draftStore，值含draft+baseRevision，不同world key隔离；等待Marcin可选prop交付后再接正式面板。

接包顺序可独立择优：Li、Ma、Marcin分别import分支验证，再接共享路由/DTO，最终合流。Li替换临时dict复制为标准化函数；Ma对接HTTP错误和事务边界；Marcin对接partial PATCH/草稿。不修改Codex查看器来迁就旧包：发现重叠提交做三方合并并保留chunkId来源定位。

验收：
- npm ci/test/test:ui/build；后端完整pytest。
- 增加装配层测试证明真实props/API映射；组件测试由Marcin承担，不复刻业务逻辑凑数量。
- M1_ACCEPTANCE记录四格式上传、重启、原文/来源、并发、编辑/冲突、手机键盘/手势各自证据。未真机填未验收；合并不等于部署。
- docs/reports/r5-integration.md按每包状态登记基线/作者/哈希核验/导入提交/实跑命令/待项；更新README/server README。
- M2_PLAN只写下一阶段提案：Key管理、模型取消/重试、资料引用与输出草案/正式状态分离。先验收M1，不自行启用真实AI。

不得force push或旧ZIP覆盖，不删旧测试/历史数据；新依赖和锁文件仅你维护。已有R4包照收，按真实基线核验，不要求用户全部重开窗口。
