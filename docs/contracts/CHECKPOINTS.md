# 正式旅程存档与分支 v1.0

## 语义

检查点保存某旅程当前已确认revision、角色/背包JSON、Fake/Live模式和创建时间。未确认草案和仅键入的行动不属于检查点。

恢复创建新的adventure，不覆盖原路线；复制检查点以前的已确认回合，revision延续该序号。分支后续的状态、草案、回合独立。继承回合使用服务端保留的inherited:<sequence>标识，避免新分支的新请求ID与旧回合碰撞。

世界设定库、文档和人工条目仍按world共享；恢复只回退玩家进度，不回滚设定编辑或恢复已删资料。来源回跳因此可能不可用。内存模型会话和Key不存入检查点，Live分支需要当前有效的模型连接。

## 接口

均以/api/v1开头：
- GET /adventures/{id}/checkpoints?offset=N：最近30个检查点，稳定分页。
- POST /adventures/{id}/checkpoints：{name,request_id,expected_revision}，201。只保存当前revision；旧版本409 ADVENTURE_CONFLICT。
- POST /checkpoints/{id}/restore：{name,request_id}，201返回新旅程，含origin_checkpoint_id。同一个检查点可以通过不同请求ID创建多条独立分支。

name为1–80字，request_id为1–80位字母/数字/下划线/连字符。同一旅程/同请求ID的相同保存返回原检查点；修改名称或版本409 REQUEST_CONFLICT。相同检查点/恢复请求ID/名称返回同一新旅程，重复请求不重复复制或创建分支；修改已恢复请求的名称409。

短BEGIN IMMEDIATE事务同时处理快照读取/保存，或新旅程创建/历史复制。原路线历史不足以匹配检查点revision时409 CHECKPOINT_HISTORY_INVALID，整个恢复回滚。世界软删后相关检查点读写/恢复均404，原始数据保留。

SQLite幂等新增adventure_checkpoints和分支来源/恢复请求列、唯一索引，不清空旧旅程。旧旅程character=null恢复仍为null，不补造角色和物品。第0回合也可保存初始状态。

## 当前限制

历史采用实际复制方式，存储与恢复成本随已确认回合数量增长，尚无大规模共享事件树优化。本阶段不提供删除检查点/改写回合，不做云同步或文件备份导出；这里的“存档”不是SQLite文件备份。未来若添加历史删除，必须先解决检查点所依赖的历史引用。
