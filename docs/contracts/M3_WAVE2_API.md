# M3 第二轮接口约定

本文件定义模块交付边界和联调草案。现有 M3 首轮接口继续可用，数据库仍使用 `world_instances.revision`，对外用 `world_revision`。

## 推演与提交

沿用 `PulseWorker` 的三个回调和 `PulseModelRequest / PulseModelResponse`。Marcin 提供事件域实现，Li 提供运行期，DeepSeekHarness 负责组合回调。

| 边界 | 约定 |
| --- | --- |
| 取快照 | `read_snapshot(conn, instance_id, job) -> dict`，含本实例可用 Canon 节选与其 ID，以及限定数量的到期事件和必要的因果引用。世界分钟与版本取已确认实例根。回调只读，空输入可返回明确的跳过或阻塞原因。 |
| 模型输出 | 对象含 `operations` 和可选 `transmissions`。程序由任务绑定 `kind`、实例、分钟和版本。响应如回显身份或时间，必须一致。拒绝未知根字段、任意字段路径、未授权来源或实例。独立模块以假模型响应自测。 |
| 纯校验 | `validate_proposal(proposal, context)` 在事务外运行。DeepSeekHarness 拆分两个数组，传给各自模块。事件包验证结构、引用和版本，知识包验证传播字段结构。 |
| 原子应用 | `apply_proposal(conn, instance_id, validated, job)` 在同一个短写事务内先核对租约、开关、活动状态与实例版本，再应用事件和传播。任何一项失败则回滚领域写入。用量证据单独结算。程序分配事件 ID，不让模型写玩家账本。 |
| 首版传播引用 | 可引用本次后台输入中已存在、获准使用的事件 ID。Seed 可先写入权威事件，首版省略传播。禁止引用本批新建事件的临时 ID、批量授知和把权威描述自动当作玩家消息。 |

Advance 只由已确认时间触发。无到期事件不发请求。关闭、暂停、离线和缺连接期间不积压将来要补演的历史。恢复或重连以当前已确认分钟锚定。Worker 重启时只恢复安全可重试任务，先核对当下开关、连接与活动条件。前台回合确认不等待模型。

## 手机端控制契约

前端通过注入 transport 独立开发。DeepSeekHarness 将调用接到同源、受既有本机或托管访问控制保护的 API。以下为拟定方法，真实服务商地址沿用现有模式的 HTTPS 与允许地址策略。

| transport 方法 | 请求与响应 |
| --- | --- |
| `getStatus(adventureId)` | 返回 `enabled`、`mode`（`fake / independent / reuse_narration / unbound`）、`state`（`off / ready / waiting_for_model / offline / budget_limited / working`），可附脱敏的 `model`、`reason_code` 和当游戏日用量。无 Key、会话 ID、隐藏事件或任务负载。 |
| `connect(adventureId, config)` | 独立模式提交 `mode, base_url, model, api_key`；复用模式提交 `mode, narration_session_id`。只绑定有效会话，连接本身不启用脉动、不测试付费请求。会话凭据留在内存。 |
| `setEnabled(adventureId, enabled)` | 用户明确启停。开关与会话分离，关闭保留叙事会话及已有历史。过期或重启后需重新连接，不能静默切换到另一个 Key。 |
| `setActivity(adventureId, active)` | 活动租约只从当前可见旅程续期，切后台、离开或断网尝试结束；后端以短 TTL 兜底。读取状态或消息不会续期。前台恢复不推进世界时间，也不补演离线期间事件。 |

Fake 旅程保留现有无费用入口。正式旅程首次启用需要明确提示额外模型消耗。独立 Key 推荐但可复用叙事会话。摘要区分实际/估算 token、可能计费的请求和模拟用量。手机端可操作状态只显示安全字段；暂停等细分操作如未完成，可通过关闭和重新启用覆盖首版流程。

## 玩家视角

玩家消息与叙事上下文共用同一实例、同一 `player` 持有人投影。叙事上下文以独立、限长的不可信数据块交给模型。保持已确认事实、传闻、推测和误信的区分，不能以传闻结算状态、改账或认定他人知情。事件、转述人、来源时间、分支实例需在应用时核验。已有冒险日志和隐藏时间事件规则继续适用。
