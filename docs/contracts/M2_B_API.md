# M2-B 第一阶段：连续叙事记录 v1.0

本阶段实现“生成草案→确认保存叙事”，不实施属性、财物、时间、分支或NPC知识结算。M2-A叙事实验保持纯预览。

## 数据与接口

所有路径以/api/v1开头。adventures按world隔离，mode在创建时固定fake/live，revision初始0。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET/POST | /worlds/{world_id}/adventures | 每页30个旅程，offset分页；创建body{name,mode} |
| GET | /adventures/{id}?before=N | 最新30个已确认回合（或序号N前30个），附当前revision和当前版本latest_draft |
| POST | /adventures/{id}/drafts | body{request_id,action,expected_revision,session_id?}，生成并保存待确认草案 |
| GET | /adventures/{id}/drafts/{request_id} | 查询pending/ready/committed/failed/discarded与草案result |
| DELETE | /adventures/{id}/drafts/{request_id} | 丢弃未确认草案，204；已确认草案409，不删除正式回合 |
| POST | /adventures/{id}/drafts/{request_id}/commit | 显式确认，原子追加一个回合并revision+1 |

request_id为1–80位字母/数字/下划线/连字符。同旅程相同ID、action、base_revision：已完成草案返回缓存，不再调用模型；pending返回409 DRAFT_PENDING，failed/discarded返回409 DRAFT_FAILED。同ID配不同内容409 REQUEST_CONFLICT。重试失败生成需新ID，由用户显式触发，不自动产生付费重试。

每份草案绑定base_revision；确认需当前revision仍匹配，否则409 ADVENTURE_CONFLICT。重复确认同一草案幂等，不增加第二条回合；即使后来版本已前进，返回当前旅程信息供刷新。多份草案竞争同一版本只允许一个确认。确认与回合插入在同一BEGIN IMMEDIATE短事务，失败全部回滚。

pending预留90秒过期，服务重启后可查询/后续请求恢复为failed；网络/超时/断开将pending标failed，无正式回合。生成成功但响应丢失时，刷新旅程可恢复ready草案，不需要重发模型调用。确认响应丢失时重试相同commit路径安全。

## 上下文和边界

模型只接最近6个已确认回合；每个历史行动最多500 UTF-16单位、叙事1400，总上限12000。未确认/丢弃/失败草案不进入历史。资料仍由现有上下文模块构造，引用只能来自本次提供的资料。历史作为不可信叙事数据，不是系统指令。

world软删后其旅程读写均404；不删除历史数据。SQLite新增表为幂等迁移，不重建M1数据。凭据不进表，仅调用时传后端内存会话ID。Fake与Live旅程模式不可混用。

客户端停止等待不能证明服务商未计费，也不能证明一个已成功返回服务器的草案没有被保存；但没有显式commit就不会写入正式回合。确认是明确的持久写入操作，已确认回合本阶段不提供撤回/改写。来源元数据保存快照，原始资料后续删除时回跳可能不可用。
