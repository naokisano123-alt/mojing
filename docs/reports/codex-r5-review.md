# R5合并后的审阅与局部整理

审阅基线：1cb7d026827ce0426db9b478992881e19a50e0f1，PR #6已合并；CI 42485223656成功，无开放PR（读取时）。集成报告记录后端159、前端逻辑62、组件11通过；本次未另行重跑前端，以已核对CI为证据，不把报告数字当本次实跑。

## 实质进展

三个R5包已接入：条目编辑草稿/冲突流程、警告标准化/逐步解析预算、原子revision和严格来源坐标；稳定draftStore已由资料库注入。M1自动化覆盖明显增强。安卓真机与线上后端仍未验收，不据此宣布产品上线。

## 发现并修复：服务函数提前提交

SQLite RELEASE最外层SAVEPOINT会提交事务。服务入口若收到尚无外层事务的连接，create/update/delete完成后即已提交，违背注入连接由调用方决定提交/回滚的约定。

实际复现：fresh sqlite3连接调用create_entry后in_transaction=False；调用rollback后表内仍有1条新记录。

修复提取统一_begin_savepoint：无外层事务时先BEGIN，再建局部SAVEPOINT；函数返回后保留调用方事务，已有外层事务不受影响。新增三个真实数据库回归覆盖create/update/delete返回后rollback，核对原始条目、revision和deleted_at均恢复。

一条旧来源故障注入测试依赖了隐式提交；现在在关闭foreign_keys前显式commit准备数据，因为SQLite事务内不应用该PRAGMA。原有来源状态断言未删减。

本地完整后端pytest：162例全部通过（159+新增3）；Python3.12环境。最终兼容性由仓库Python3.11 CI验证。未改前端业务、接口、迁移和依赖。

## 整理

根README改为当前M0+M1说明、正确启动命令、测试入口与目录；旧M0长篇记录移至docs/history/M0_README.md并保留。业务代码只集中SAVEPOINT启动逻辑，避免全仓格式化干扰协作。

## 后续关注

WorldLoreView仍有as unknown/as never桥接，可后续用显式DTO映射整理并配装配测试；本次未顺手改动前端业务。M1_ACCEPTANCE仍需手机验收证据。第五轮已合并的个人任务不应重复开工；下一轮应依据真机反馈和M2产品决策确定范围。
