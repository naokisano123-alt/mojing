# DeepSeekHarness｜R3-D 公共契约与第三轮集成
先读ROUND3及审阅B1-B8；R2已合并，别重复开发三包。你负责公共层与集成，不并行重写三Gemini独占模块。三个Gemini均新窗口同快照，交包仍经用户。

## 先处理旧能力承诺
- 从最新main独立feat/r3-integration，记录HEAD/现有PR。新增docs/contracts/M1_R3_API.md，继承v0.2和1.0-r2，按ROUND3冻结人工条目/来源/revision冲突字段与端点；校正搜索匹配/排序/nullable命中及限额/警告语义。
- B1明确存在：上传入口先按2MiB read/check再判suffix。先修成按格式配置限量读取，提供真实HTTP层“合法PDF/DOCX 2–8MiB可过前置限制、超各自limit拒绝、TXT仍2MiB”测试。不是把所有文件统一放到8MiB。
- 能力表max_bytes传给Marcin新的逐格式prop；解析模块缺依赖时能力声明须反映真实可用性。若选择解析依赖为启动必需，文档/CI明确；不得声称可选但未安装导致整个app无法import。
- 主管线对规范化文本做UTF-16总量最终校验；Li的warning_records优先入库，保留旧字符串fallback但禁止继续用中文子串猜机器码。
- search HTTP与WorldLoreView适配保留match_field和null命中语义，不再固定text/补0制造范围。契约排序/范围跟Ma统一后更新实际回归。

## 集成M1-D
你独占main.py/db.py/config.py/importer.py/errors.py、src/api/**、WorldLoreView/index.ts、App/页面入口、公共依赖、CI与说明。调用Ma的schema/service，不复制服务实现；把服务异常映射现有error结构。注册新增世界内条目接口，校验world访问边界；不开放公网未鉴权资料服务。
Marcin工作台用api注入，提供HTTP适配与R2搜索来源选择；来源点击接DocumentViewer。入口放真实WorldLoreView“人工设定”页签，不替换M0图鉴、不暗示AI已用条目。没有来源manual可保存；失效来源与409冲突需真实展示。
迁移前备份临时验证，保证R2文档warnings/encoding_semantics仍在；不能建空库冒充旧库迁移成功。

## 收包、测试和报告
按ROUND3逐包独立import分支核验snapshot/manifest/新旧哈希/文件所有权；补丁损坏可审阅重建，但必须三方比较并记录，不只比包内文件彼此一致。作者保留。
集成顺序先导入质量门槛，再条目后端/前端与来源生命周期。每一包有可测边界，不把三份共享文件建议挨个整文件复制。
实际执行前端npm test/build、Python全量、CI和HTTP端到端；新增入口/空密码PDF/UTF16/两人冲突/软删来源要有跨模块测试。旧报告41/82为已有记录，不要求新结果数量固定，不得删用例凑绿。
docs/reports/r3-integration.md记录包状态、迁移、冲突处理、提交与CI链接、真实浏览器/真机已验/未验。R2报告旧阶段保留但标历史，更新当前入口和PROJECT_HANDOFF状态。若main在收包时变更先合并，不强推。
当前线上Python后端仍未部署。先给可运行的本地闭环和局域网手机步骤；代码合并/构建/部署分别报告，不能把静态原型链接说成已支持真实导入。不接Key、不提前实施M2–M4。
