# R7-D｜DeepSeekHarness：取消编排与完整验收

R6成果在feat/r6-integration尚未合main。以ROUND7任务快照创建新集成分支，三包各import分支修补；不要把main旧代码当起点丢掉R6。

你独占main.py/config、src/api、WorldLoreView与装配测试、契约/依赖/CI/报告。三Gemini目录交付前不改，包到后integration_fix记录局部修改。

优先R7-01：端点注入FastAPI Request，生成task与断开监听竞争，60秒总期限；断开或超时取消并await生成task，finally取消并await监听task。客户端断开不尝试伪造成功；连接测试也应用相同取消边界。不可只捕获CancelledError就当断开已实现。保持模型等待前数据库连接关闭。
用真实ASGI http.disconnect消息或本地服务器连接关闭证明上游stub/transport收到取消且stream closed；不要只直接cancel service函数。模拟上游慢回复、不响应、响应与断开同时到达，避免后台残留task。

缺session统一409，修旧测试404期望；对request输入长度先守门再context query。别将未知内部异常内容回给前端。迁lifespan处理store清理可以局部进行，避免扩大成全main重构。
Live全栈mock必须用真实ModelSessionStore+httpx.MockTransport+真实context builder+generate_draft，不用全StubStore代替；验证401/429/timeout、坏引用、无资料、有效返回；真实Key未测单列。DB无写入要比较各相关表内容，不能只比三个表行数。
前端signal透传按Marcin包接线；entry来源应定位具体条目，可扩展人工面板initialEntryId（包到后协商最小改动），至少提供明确条目详情，不只弹alert切tab。
补metadata中文提示与旧字段兼容；更新验收表、README状态。最终npm test/test:ui/build与pytest、CI完整通过，再开PR main（含R6+R7）。报告r7-integration逐包SHA/哈希/真实命令和未做的真机/付费服务商/部署。不以“无Key”跳过Live mock。
