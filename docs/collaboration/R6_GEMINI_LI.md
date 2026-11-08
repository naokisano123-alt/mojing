# R6-L｜Li：会话与OpenAI兼容模型适配

独占server/app/model_gateway/**、server/tests/model_gateway/**、本人r6报告。按M2_A_API实现ModelSessionStore与GatewayError；generate采用Codex ModelClient接口，禁止修改核心或公共路由。

内存会话：30分钟无操作TTL、最多8个（满则明确错误不静默踢旧会话），secrets随机ID；get/test成功使用时刷新TTL，expires_at按UTC。Key字段repr=False，不落盘、不回传、不记录；delete幂等，过期不可复用。过期get删除并报409，clear可供应用关闭时清理。get_client返回的客户端不暴露repr中的Key。

base_url为用户配置的API根（例如https://example/v1），只拼接/chat/completions；仅http/https，禁止userinfo/query/fragment，不跟随重定向，不将Key送往跳转地址。本地自建接口允许loopback/private，当前仅本地单用户使用。不要自行添加第三方域名探测或下载模型。
构造系统消息明确输出仅narrative/references的JSON，资料单独作为不可信数据，action与source_id保持对应；提示防注入不等于已解决知识越权。
非流式请求，合理超时，限制响应正文（例如256KiB），禁止无限读取；解析chat choices/message.content的JSON并交核心最终校验，不静默剥离错误字段。明确截断/空回复/不支持格式/认证/限流/网络错误，不打印上游原文、Authorization或Key；不自动重试。httpx客户端资源必须关闭，async取消传播。
test仅主动调用，用最短提示，成功返回ok/model；失败脱敏。HTTP模拟transport覆盖成功、401/429/timeout、坏JSON、截断、超大响应、重定向、TTL/容量/delete、取消和敏感信息不外泄。无真实网络/Key。依赖建议交DSH，不改requirements。
