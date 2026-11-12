# 旅程与存档接口

2026-11-12。世界包含多个旅程；旅程包含一个自动存档和最多20个手动存档。每个进度点保存完整的已确认历史、角色、长期日志与世界领域快照。读档使用不可变历史副本，默认仍归属原旅程，不在旅程列表里增加一条分支；玩家主动选择“以此建立新旅程”时才产生新文件夹。

## 界面与导航

底部栏目只有冒险、世界观文档、设置。冒险入口首项为开始旅程或继续冒险，次项为存档与读档。正文只显示场景、叙事、行动及末尾的日志入口；角色与消息为上层面板。首次创建世界先整理文档，完成准备后显示三个栏目。

## 旅程接口

所有路径以 `/api/v1` 为前缀，不接受跨世界的旅程或存档绑定。

| 方法与路径 | 行为 |
| --- | --- |
| GET /worlds/{world}/journeys | 文件夹列表，含 id、name、active_adventure_id、revision、mode |
| PATCH /journeys/{journey} | `{name}` 重命名整个旅程 |
| GET /journeys/{journey}/saves | 存档列表，kind 为 auto 或 manual，自动存档排序在前 |
| POST /journeys/{journey}/saves | `{adventure_id,name,expected_revision,request_id}`，手动保存，事务内检查20个上限及幂等 |
| DELETE /journeys/{journey}/saves/{save} | 删除手动存档入口；仍被历史引用的快照留作来源证据 |
| POST /journeys/{journey}/load/{save} | `{request_id,as_new_journey?,name?}`，返回新的运行记录；默认归属同一旅程 |

创建旅程和确认回合的事务内更新自动存档；未确认草案不会进入自动存档。替换后不再被引用的旧自动快照会回收，被读档来源引用的快照保持不可变。旧版检查点 API 继续列出手动检查点，自动存档统一由新接口管理。旧分支升级为各自独立旅程，保留原名称和进度。

## 统一开局

- POST /worlds/{world}/opening-options：`{mode,session_id?,selected_source_ids,description}`，返回 `{options:[{title,description}]}`。最多三项。
- POST /worlds/{world}/journey-start：同上，另加 `name` 和 `request_id`；使用最终自然语言描述理解角色、地点、起始时间、钱币及物品介绍。读写请求幂等，成功响应丢失后重试不重复创建或调用模型。
- 有所选资料时，开始流程继续执行原有世界分析及人物、事件、认知三域初始化；最终文本通过 `StartingPointSpec.player_description` 传入，全部领域应用成功才创建可玩的旅程并保存初始自动存档。模型网络等待期间不持有 SQLite 写事务。
- 玩家无需分别填写位置、数值和物品表。模型或领域校验失败保留编辑器，不自动发起额外付费重试。推荐开局、理解描述、分批初始化都可能产生模型调用，界面有说明。
- 推荐及描述理解的选中文本总量上限6万字，超出明确拒绝，不静默截断。背景初始化沿用现有按起点选取相关节选的分批预算，不意味着全文成为每回合提示词。其余资料仍可后续检索。
- 无可确认交谈对象的初始化安排转为等待交谈机会，并在身份处理日志中记录 deferred_conversation；不会补造目标或执行对话。领域验证器仍严格拒绝无效的正式交谈提议。

## 导出与迁移

`GET /worlds/{world}/transfer?part=world` 导出世界观；`part=zip&journeys=id1,id2` 导出世界观及所选完整旅程。省略 journeys 则全部导出。ZIP 内仍为 world.json 和 saves.json，不细分单个检查点。

存档包的原有字段和版本继续兼容，新增可选 `journey_metadata`：members 包含 adventure_id、journey_id、active，slots 包含 checkpoint_id、journey_id、kind。旧包没有此字段时按旧记录迁移，新包恢复时完整校验并重映射身份；任何失败全部回滚。选择性导出包含该文件夹下的所有运行历史与存档，跨文件夹的来源指针在已拥有完整历史的副本上解除，不夹带未选旅程。

`POST /world-transfers?target_world_id=...` 可把同版本世界观的存档/ZIP 导入当前世界，只新增具有新ID的旅程及其所有存档；不覆盖已有旅程、文档或本地存档的幂等请求标识。按世界ID、设定摘要及既有迁移映射匹配，设定版本有变化时拒绝合并，用户可从首页导入为独立世界。
