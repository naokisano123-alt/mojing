# R7-L｜Li：网关响应与错误边界

仅model_gateway/**与其测试、本人r7报告，不改路由/草案核心/依赖。

修R7-03/08：generate显式检查choices[0]的finish_reason，length/content_filter/tool_calls等拒绝为稳定错误，即使content是合法JSON也不能假成功。finish_reason缺省的兼容策略写明且测试，正常stop继续交核心校验。test需检查非空choices、message/content字符串及完成状态，choices:[]不能返回ok:true。

抽取generate/test重复的有界HTTP读取与错误映射为私有公共函数，保留256KiB上限、超时、资源关闭、不跟随重定向与取消传播。不要做整包改风格；不得在错误中返回上游原文/Key，httpx异常repr也不直接输出。覆盖不同httpx RequestError/ProtocolError，保留取消为CancelledError。

URL校验对urlsplit/hostname/port ValueError转稳定422；拒绝空host/无效端口/坏IPv6/userinfo/query/fragment，保留明确允许的本地自建http/https。不要用阻止全部私有地址的策略破坏既定本地使用。

SessionStore结构保留；补TTL过期后test/create/get边界与失败不泄露。fake HTTP transport验证正常、截断、内容过滤、空choices、错误编码、超大块、302、超时/协议错、取消后流closed。测试不用真实Key/网络。交付说明需DSH映射的新增错误码和兼容策略。
