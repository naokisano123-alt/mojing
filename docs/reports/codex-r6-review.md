# 第七轮审阅：M2-A 集成分支

审阅时间2026-11-08。main仍为8087d093724c7f554b2987c4af2b86f92a5afc06；新增代码在feat/r6-integration，固定审阅SHA f379293037ff2ce54e8f2f3abc6892a6fce8fc29。当前无开放PR。分支CI 42492465291成功。**尚未合并main，不能说用户从main下载就已有完整M2入口。**

报告r6-integration记录：pytest236、node:test62、Vitest21、build通过，Fake真实HTTP闭环成功；三包已导入，真实付费服务商未测。本次为固定SHA静态审阅与CI核对，未重新执行完整套件，不继承报告数字作本次实跑。

## 已完成

会话创建/删除/连接测试、叙事预览四端点及前端注入已存在；Li模块按字节限流读取响应、拒绝重定向、支持取消；Ma上下文有世界过滤与UTF-16预算；Marcin有Fake/Live入口、输入/生成/停止/引用展示。204空体和浏览器uuid降级也在集成分支。

## 具体缺口

| ID | 证据 | 影响 / 所有者 |
| --- | --- | --- |
| R7-01 P1 | main.py narration_preview无Request参数、不检查is_disconnected，仅wait_for(60s)与捕获CancelledError | 不能保证客户端断开会取消ASGI任务，上游可能继续；DSH |
| R7-02 P1 | routes缺session_id返回404，契约写409；测试也固化404 | 前端/契约不一致；DSH修实现和测试 |
| R7-03 P1 | client.generate未检查finish_reason；test仅确认dict含choices | length截断即使内容恰好是合法JSON也可能当成功；choices:[]或异常元素可被连接测试误报正常；Li |
| R7-04 P1 | NarrationPreview的connect/test/delete缺请求世代/卸载保护；删除失败catch忽略后仍清session | 迟到结果可恢复已移除会话或污染新世界；删除失败实际仍保留后端Key，UI却显示已断开；Marcin |
| R7-05 P1 | context fallback对全部chunks fetchall，confirmed条目全部fetchall+逐条来源查询 | 输出预算有限，但中间读取未受控；Ma分页/逐行停止与预算 |
| R7-06 P2 | WorldLoreView entry来源仅切人工设定并提示，没有定位该entry；上下文无选择说明 | 不能直接核对具体引用条目；DSH接线、Marcin保留callback |
| R7-07 P1 验收 | Live已有stub路由测试，但未见真实ModelSessionStore→httpx.MockTransport→核心的路由闭环与ASGI断开证据 | 真实Key不是自动化缺失的理由；DSH补全栈mock |
| R7-08 P2 | urlsplit遇坏IPv6等可能抛ValueError；netloc检查不等于有效host/port | 输入应稳定422，不漏原始异常；Li |

以上未做动态复现的项按代码证据列出，不宣称全部实际发生。第七轮先收口M2-A稳定性和集成，暂不做正式回合/数据库状态提交、流式、多模型扩展。


完整任务与代码快照：b812c471c15a940dfd90268cd760babb334f18c0；[第七轮入口](https://github.com/naokisano123-alt/mojing/blob/b812c471c15a940dfd90268cd760babb334f18c0/docs/collaboration/ROUND7.md)。本次main仅文档更新，不包含R6业务代码合并。
