# R6-D｜DeepSeekHarness：路由、预览编排与集成

共享文件唯一集成者。先读M2_A_API和Codex narration核心；不代写三包，不进入正式状态引擎。所有代码从最新main分支起，记录基线并保留M0/M1。

无需等包先做：公共DTO/API函数、契约对应HTTP路由壳和注入接口测试、WorldLoreView独立“模型叙事实验”入口，接口stub只用于测试，运行时缺包给明确未就绪，不能伪装Live。现有核心Fake可由显式fake mode接通，不需Key。
收Ma包后：短生命周期读取conn构造bundle，关闭DB事务，再开始模型等待。收Li包后：app内创建会话store，注入路由，关闭应用清理资源。收Marcin包后：映射api并注入，来源点击复用查看器，entry来源打开条目详情或明确导航，不能当chunk。
POST预览编排：校验world/action→context→选择Fake或有效Live client→generate_draft→附来源metadata。错误映射按契约；request断开/60s超时取消上游task并等待清理，finally不残留task，禁止取消后成功response或任何世界写入。仅手动重试，不在失败时自动调用Fake。保留费用不确定说明。
合成transport与真实TestClient测试：Fake/Live mock、会话CRUD/过期、坏引用拒绝、取消/超时、连接资源释放、请求前后DB所有相关表不变、不泄露凭据；组件装配test与旧M0/M1回归完整。
依赖/锁/CI仅你改；三包分别import分支核验manifest/patch/哈希，再三方合并PR，不用旧包覆盖共享代码。
报告r6-integration逐包核验、实际命令、Fake与Live mock通过、真实服务商未调用、用户后续连通测试与线上部署状态。npm test/test:ui/build、全pytest，更新README与M2进度。不要把预览宣称为可持续冒险，也不要把用户M1验收继承成M2真机通过。
