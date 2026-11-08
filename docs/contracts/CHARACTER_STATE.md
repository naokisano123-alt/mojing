# 冒险角色状态 v1.0

适用“冒险记录”，不迁移M0人物、不从世界资料自动推断玩家身份。新旅程初始化角色，旧旅程迁移后character=null，保留原叙事，不虚构历史属性。

## 角色与提议

创建旅程body可包含character：name/location各1–80字，stamina/spirit为整数0–100，coins/elapsed_minutes为整数0–1,000,000,000。默认旅人/起点、体力精神100、铜币和耗时0。网页提供姓名、初始位置与铜币输入；初始化后本阶段不可直接改写角色。

当NarrationRequest.character存在时，模型输出允许额外字段proposed_changes；没有角色的旧旅程和纯预览仍使用原先严格两字段格式。

仅允许：stamina_delta、spirit_delta（整数-100..100），coins_delta（整数-1000..1000），minutes（整数0..1440），location（1–80字）。拒绝bool、浮点、未知字段、负余额、属性越界、负耗时；不静默截断或修正数值。姓名和能力不能通过提议改写。背包仅允许[INVENTORY](INVENTORY.md)约定的数量增减。

草案result增加state_before、proposed_changes、projected_state。生成草案不改变角色；模型没有提议时为null。Fake提供显式测试提议（耗时10分钟、体力-1），不是自然语言规则裁决。

## 原子确认

POST /adventures/{id}/drafts/{draft_id}/commit可传{apply_changes:true|false}；缺省false以兼容旧客户端。只有true且草案存在合法提议才采用。客户端不能在commit请求自带新delta覆盖服务器提议。

服务器再次校验状态与提议，在同一个短事务内更新revision、角色JSON和回合记录。重复相同确认不重复扣款/加时间；同一草案已确认后改变apply_changes选择返回409 COMMIT_CONFLICT。旧版本/旧状态草案拒绝，不得覆盖新状态。

已确认回合result保存state_before/state_after/changes_accepted；即使选择false，也留下未改状态的快照。旧回合缺这些字段时UI兼容。状态迁移仅幂等ADD COLUMN，不清空资料和旅程。

## 产品边界

程序目前只核对字段、类型、范围和提交一致性，不判断经济价值、移动合理性或叙事因果。玩家必须核对提议；未采用状态时叙事中的变化不自动成为账目事实。体力为0不自动代表死亡；背包已有数量账本，尚无战斗、死亡、技能、装备、正式历法或NPC知识规则。此阶段是可追溯状态账本，不宣称完成世界模拟引擎。

背包扩展：character可含inventory，proposed_changes可含inventory_changes，规则见[INVENTORY](INVENTORY.md)。其他旧字段与纯预览输出约束不变。
