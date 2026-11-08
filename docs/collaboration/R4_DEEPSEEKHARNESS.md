# R4-D｜DeepSeekHarness：修入口、公共接线与 M1 验收

先读 ROUND4、ROUND4_REVIEW、PROJECT_STATUS。你是本轮唯一实时集成者；三个 Gemini 由用户分别交包，你不需要代替他们重写整模块。

## 第一优先：当天可独立完成的阻断修复

main.py:464 不能把 TypedDict 当 dataclass。先用当前 dict 结构正确读取 code/message，count 可省略；JSON 端到端验证**正常**文本 PDF 与 DOCX 都201，随后能取详情/raw/chunks/警告。坏 PDF 422 或超限413不能代替成功路径。Li 包到达后统一用 normalize_warning_records，避免出现两套长期维护的转换逻辑。

在 server/tests/test_documents.py 增加最小真实成功回归（合成样本无用户资料）；后续 Li import_pipeline 做更大矩阵。修复记录与结果可以先独立提交到工作分支，不等三个包。

## 公共契约与基础设施

1. 整理 docs/contracts/M1_R4_API.md（1.0-r4），把 ROUND4 的固定接口落实，修 R3 坐标公式笔误并说明被 R4 覆盖部分，保留历史端点兼容。
2. 配置 UI 测试：Vitest/jsdom/React Testing Library，检查 Node22兼容版本，唯一维护 package.json/lock/config。原 npm test 的 node:test 继续运行；加 test:ui 且 CI 真正跑它，明确 include tests/ui/**/*.test.tsx，不要把 node:test 文件误收进 Vitest。不重写既有测试以追数字。
3. src/api 类型/客户端：来源 chunk_index additive，跳转 null 合法，PATCH source_links 可选，去掉 payload as never/as unknown 强制桥接并做显式 DTO 映射；旧后端缺新字段要兼容。DELETE 的 revision 必须严格验证为正整数，不让 bool/小数通过参数 coercion；与 Ma 服务语义对齐。

## 收包后集成（你的共享文件）

- WorldLoreView 保存 capabilities.max_bytes 并逐格式传 DocumentUpload.maxBytesByFormat。能力请求失败保守退 TXT/MD 2MiB；不能用 richFormats 一个布尔开关控制所有文件。后端能力声明与实际装载一致（parsers/lore_entries 字段按契约实际返回，缺依赖不能启动崩溃后仍声称支持）。
- 搜索适配不将 null 改0；把 match_field 与真实坐标交给新面板。来源 callback 有 chunkId 无 index 时调用已有/新增客户端 getChunk，核对 document_id，解析真实 chunk_index，出错给反馈而不是跳第0块。
- WorldLoreView 注入稳定 draftStore，切换子 tab 不销毁草稿；不同世界的 key 隔离。表单异步操作返回后不在新世界重开旧世界查看器。
- 接 Ma 原子 revision/来源元数据，路由与 db 事务不得掩盖冲突或丢失404/409/422；并发冲突不简单全部转500。用 HTTP 测试补 update/update、update/delete 以及回滚。
- 接 Li 警告转换与预算，不让缺省 count 或未知合法 warning code 崩溃；能力和限额统一来自配置/策略。
- tests/integration/** 验证真实适配和页签装配，不能只检查源码包含字符串。复杂组件测试由 Marcin 包补齐。

## 验收与报告

写 docs/reports/r4-integration.md，逐任务列：未收/已核验/已合并/实跑通过/阻塞，并列原包名、实际基线、作者、导入分支/提交、manifest 哈希校验和三方合并情况。R3 的坏补丁路径不能再默默修掉当“原包正常”；补丁问题原样记录。

实际执行 npm ci、npm test、npm run test:ui、npm run build；安装三套后端 requirements 后 server 目录 python -m pytest。保留全部旧用例；数字以本次输出为准。绿色 CI 后仍检查关键成功路径。

新增 docs/M1_ACCEPTANCE.md：功能/HTTP/UI/真机逐项证据与待项。手机无法访问则写“未验收”，别继承用户对 M0 的确认来充当 M1 真机通过。更新根 README/server README 的当前功能、启动方式、配置、限制、目录，不再开头声称全项目“无后端请求”。

新增 docs/M2_PLAN.md **仅规划**：先小闭环模型连接与可取消流式叙事，区分模型输出草案与正式状态提交、资料引用/权限、Key 保管、超时/停止/重试、模型假实现的可测试边界。明确哪些需要用户产品决策；本轮不写真实模型客户端、不收 Key、不做 M3。

## 完成与防覆写

先 fetch/status；脏目录保留，用 worktree；包独立 import 分支、先 apply --check，禁止批量覆盖和强推。只有在 Gemini 交付后才修改其独占文件，并在报告标 integration_fix。合并 PR 与部署分别报告，公开仓库不代表服务上线。交付 PR、各实际命令结果、验收文档、未解决问题和下一步，不用“全完成”掩盖待真机项。
