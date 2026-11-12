# M3 首轮集成契约 v1

面向独立开发包。下列 Python 入口与 JSON 字段是首轮约定，尚未成为已上线接口。任务在同一代码基线上开发，各包先用独立 SQLite fixture 和 Fake 或 Mock 自测。共享文件和最终 API 路由由 DeepSeekHarness 统一接线。

## 公共身份和时间

- 实例根表约定 `world_instances(id, world_id, adventure_id, world_minute, revision)`。ID 为字符串，初期一个正式旅程一个实例，M0 无实例。子表外键引用 `world_instances.id`，领域对象身份均在实例内唯一。
- `world_minute` 为开局后的整数游戏分钟，来自已确认角色耗时。当前无角色的旧旅程接入时从 0 开始，保留角色空值。打开页面、现实时间流逝和模型任务执行都不改变这个值。
- `world_revision` 为实例根版本，与 `adventure_revision` 分离。前台权威变更和后台事件、知识提交均通过实例版本比较后推进。相同游戏分钟也可能存在多个实例版本。
- 已确认回合、时钟和初始任务登记在一个短写事务内。模块数据库写入口接收已打开的 `sqlite3.Connection`，模块不自行提交或调用模型。网络、反序列化和大段处理留在事务外。后台提交前复核实例版本、开关、活动租约和队列领取令牌，失败回滚。

## 事件模块和模型输入输出

所有拟议变更先解析为结构化对象；未知字段、跨实例引用、非法时间或版本、伪造来源均拒绝。初始化时活跃主要事件线 3～6 条，后续可暂时少于目标；临时事件上限由集成配置注入。

- 初始化输入由集成提供 `instance_id`、`world_minute` 和受限的 Canon 资料及本次允许引用 ID；事件后续提议只拿需要处理的到期事件及相关事实。
- 事件权威记录含 `event_id`、`kind`（`thread` 或 `incident`）、`title`、`description`、`location`、`scope`（`local/regional/national/international`）、`participants`、`stage`、`status`、`created_minute`、`updated_minute`、`next_update_minute`、`cause_event_ids`、`source_ids`、`revision`。`status` 取 `active/stalled/completed/failed`。
- 提议是操作列表，操作用 `create` 或 `update`，更新须带 `event_id` 和 `expected_event_revision`。服务端分配新 ID、版本和实际提交时刻，模型不能回写已确认历史。影响仅通过受限领域操作表达，首轮持久化事件和可追溯后果，不直接写玩家账本。知识传播不是事件描述中的公开标记，走单独的传播提议。
- 事件包提供可独立测试的 `validate_proposal(payload, context)`、`apply_proposal(conn, instance_id, proposal)`、`snapshot(conn, instance_id)`、`restore(conn, target_instance_id, snapshot)`。另提供到期事件分页读取和 Fake 初始化 fixture。校验器内部纯计算；应用时复核数据库内事件版本与引用。

## 队列、用量及凭据

- 持久任务字段含 `job_id`、`instance_id`、`kind`（`seed/advance`）、`trigger_adventure_id`、`trigger_revision`、`base_world_revision`、`target_world_minute`、`status`、`attempts`、`next_retry_at`、`lease_token`、`lease_until` 及脱敏错误码。队列不能存 Key、模型会话 ID 或密钥请求头。
- 状态采用 `pending/running/completed/failed/waiting_for_model/cancelled`。按实例和幂等键唯一，已完成不重跑；过期租约可回收，旧令牌不能提交。待运行任务可合并为更新的已确认时钟目标，旧模型提议不能跨版本自动覆盖。
- 队列包提供 `enqueue(conn, trigger)`、`claim(conn, now)`、`complete(conn, job_id, lease_token)` 及失败/重试/等待模型入口。Worker 注入取快照、模型调用、原子应用回调；独立跑测使用 Fake。模型缺失、关闭、离线时不得发新付费请求。恢复仅处理已确认游戏时间，暂停期间不补造事件。
- 独立模式为脉动使用独立内存会话；复用模式只在内存中引用有效叙事会话。复用不改变叙事会话的所有权，关闭脉动不会删除它。分别记用途、调用数、实测或估算 token、按游戏日归档。预算检查必须在发请求前留出额度，失败超时只能按实际证据标记用量。预算不足延后，避免忙循环。

## 知识、传播及玩家投影

- 权威事件与知情记录分表。记录最少含 `fact_id`、`holder_id`、`knowledge_type`（`confirmed/suspected/rumor/mistaken`）、`content`、`source_event_id`、`source_person_id`、`channel`、`learned_minute`、`confidence` 与状态。玩家使用该实例的 `player` 持有人标识。禁止通配符授知；每条记录须指定具体持有人并通过来源、渠道、转述链和时间校验。允许当前已生成的数名 NPC 与玩家共同知晓一条消息，不以“覆盖当前全部 NPC”的人数比例拒绝。同一开局的长记录分批原子写入，不增加模型调用。
- 初版渠道包括 `observed/notice/conversation/rumor`。传播必须有有效来源、接收人、游戏分钟；转述者需已知道所转述内容，不得早于来源时间。不同持有人可保留不同可信度和版本。
- 知识包提供 `record_transmission(conn, instance_id, proposal)`、`list_known(conn, instance_id, holder_id, *, before=None, limit=30)`、`snapshot(conn, instance_id)`、`restore(conn, target_instance_id, snapshot)`。玩家前端和叙事上下文使用同一种投影，返回知情条目的公开字段，不返回隐藏标题、总数、未来时间或不必要的真实事件 ID。展示入口由集成方挂载。
- 可约定玩家 GET `/api/v1/adventures/{id}/world-news`，返回 `items` 和不透明分页游标 `next_before`。路由从当前旅程解析实例和玩家身份，客户端不传任意 holder。组件以 Fake 传输独立验收。

## 快照、重启与误差边界

- 各模块快照是 JSON 可序列化的领域数据，含内部格式版本，不含活跃任务、凭据和进程租约。分支和导入新建实例，领域对象 ID 可在新实例内复用。所有外部实体引用必须重新核对，跨实例引用拒绝或按显式映射重建。
- 检查点保存精确的实例版本及快照；同分钟后提交的事件和消息不能进入此前快照。恢复或导入在一个写事务内完成，失败不留下半个实例。建议首轮用完整小快照，后续再优化为事件重放或增量存储。
- 模型能提出因果与文本，程序能验证结构和已编码的事实。报告必须区分 Fake、Mock、真实服务商与安卓真机四种证据。
