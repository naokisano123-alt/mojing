# DeepSeekHarness｜R2-D 四方集成与交付负责人
先读ROUND2.md与ROUND2_REVIEW.md。M1-A/B已合并，不重做DSH-01/02/03。三个Gemini分别交完整模块包，你主要做边界契约、共享文件与依赖接线、构建和发布验证，而不是同时重写它们模块。Codex本轮没有另开业务实现任务。

## 先做，且不阻塞三人独立实现
- 从最新main建feat/r2-integration，回报完整HEAD；本轮功能基线c83eccb44734dd16ef6da8e0c2382eff4bed99df。记录已开分支/PR，不能假定仓库仍静止。
- 新增docs/contracts/M1_R2_API.md，继承v0.2（新文档不改写历史契约），明确PDF/DOCX media_type/encoding/warnings语义、解析错误、限制；GET /api/v1/search的query/DTO/分页/来源字段。尽量遵循ROUND2拼装接口，不为个人偏好改变三人已依赖的模块边界。
- 校正已见文案“当前可检索”“在此世界观下试玩”，功能上线前不得暗示已支持；报告中旧阻塞/删除server回滚等标成历史。公开代码不等于公开数据库，不把未鉴权本地服务直接暴露公网。

## 你独占的集成文件
server/app/main.py、db.py、config.py、importer.py、errors.py；公共requirements/依赖；src/api/**；WorldLoreView.tsx/index.ts；App.tsx/WorldPages.tsx/model.ts/vite.config.ts；根package/lock、CI、README、契约和集成报告。
三个模块独占路径见ROUND2，接包前不并行在那里实现。若发现关键bug必须先分配给作者或登记移交再改。

## 三包导入流程
每个包独立import/r2-{marcin|li|ma}分支。读取manifest、snapshot/contract SHA和文件哈希，保留用户原包于仓库外；git diff检查自己未提交内容。逐文件基线/最新/产物三方比对，新增路径存在就停。先git apply --check，再应用、复核来源、保留作者信息；禁止整包覆盖/强推/删别人文件。
推荐先Marcin（A/B可靠性），再Li（解析），再Ma（检索）。依赖统一一次合并，所有者重叠的公共文件由你用薄适配层拼装，不将三份接线建议顺序复制覆盖。

## 具体接线与已知审阅项
1. Li提取模块进入原上传入口，旧TXT/MD逻辑与规范化/分块不变；扩展前端能力配置/DTO，只有实际可用格式才在accept与提示中启用。解析失败事务不残留半成品。
2. Ma检索注册到FastAPI，注入现有sqlite连接；前端公共client实现真实search，WorldLoreView展示SearchPanel，来源点击接MarcinViewer可选initialSource。删除世界/文档后搜索不可见。
3. 修WorldLoreView世界切换请求竞态；#lore刷新持久恢复所选remote worldId或可重建URL，不能把完整文档塞localStorage。返回列表可刷新最新文档计数。
4. 客户端取消≠服务端事务一定回滚。验证现有同步上传断连行为：若不能保证取消不入库，契约明示“停止等待，结果需刷新核对”，靠服务端哈希去重防重试重复；不要无依据承诺零提交。
5. 检查importer长段heading_path与UTF-16分页边界，用确切回归样本证明后修，保留无缝拼接约束。
6. 正式迁移如需新表/列须幂等、有版本、先备份验证，不DROP重建用户库；本轮Ma的无索引基线不必强行引schema迁移。
7. 建可复现CI或明确本地一键检查，覆盖npm test/build、Python测试与可选解析依赖。公开仓库CI不上传真实资料或数据库；依赖无法安装时如实报告原因。

## 交付
docs/reports/r2-integration.md 记录三个包的RECEIVED/IMPORTED/TESTED/REJECTED状态、来源SHA、实际修正、契约变动、测试结果、PR和部署状态。已有22前端/37后端只是先前报告的基线；本轮实际跑全量，不删旧用例凑通过。
验收：TXT/MD回归 + PDF/DOCX成功和扫描/损坏明确拒绝 + 超100块完整可读 + 搜索中文短词 + 来源高亮 + 跨世界/删除隔离 + 取消重试 + 刷新重启 + M0手势草稿不受影响。需要安卓真机的项目交用户，不以构建通过替代。
以可审阅PR交付，不将“提交了代码”写成“上线了”。现有Sites是前端静态产物，Python后端部署需单独验证；暂不可部署时提供本地完整闭环及明确限制，不擅改访问权限。
