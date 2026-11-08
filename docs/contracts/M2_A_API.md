# M2-A 冻结契约 v1.0

状态：本轮开发依据。Codex已实现server/app/narration/core.py（纯草案边界与Fake）；以下HTTP/会话/界面由ROUND6各包实施，**本文件不代表端点已可用**。

## 范围与默认决策

采用OpenAI兼容Chat Completions；用户自填base_url/model/key。Key经密码输入框一次提交后清空，仅保存在服务端内存会话，不写磁盘、localStorage或日志。会话30分钟无操作过期，最多8个，随机不透明ID，服务重启失效。前端会暂时持有待提交输入，不能宣称从未接触Key。

M2-A是独立“模型叙事实验”，输出草案，不修改角色属性/时间/存档，不把预览当正式冒险回合。默认明确选择Fake或Live，失败只报错，不自动切Fake。先完整响应JSON，后续再做流式；停止可取消等待与服务端任务，但不能承诺服务商未计费。

## 核心接口（已实现，其他包直接使用）

ContextSource(source_id,kind,text)，kind为chunk/entry，source_id为chunk:<id>/entry:<id>。
NarrationRequest(action,sources:tuple)；ModelClient.generate(request)为async，返回仅含narrative:string、references:list[string]的对象。
generate_draft(request,client)校验输入与输出，返回NarrationDraft(narrative,references:tuple)；DraftError.code可用于HTTP映射，错误不带Key。
预算：行动2000 UTF-16单位，最多8份source，单份4000、总16000，叙事12000；禁止重复或未提供的引用。引用身份校验不证明内容真实、相关或NPC知情合法。无引用可返回[]，UI标明未引用资料。

不接受state_changes/elapsed_minutes等扩展输出，模型无权写数据库。超时与取消原样交给编排层，不伪装生成成功。FakeModelClient用于明确选择的测试模式，不需要Key。

## HTTP（DSH负责）

前缀/api/v1，统一原错误结构{error:{code,message,details?}}：
- POST /model-sessions body {base_url,model,api_key} →201 {session_id,model,expires_at}。不得回传Key。
- DELETE /model-sessions/{id} →204，重复删除幂等。
- POST /model-sessions/{id}/test →200 {ok:true,model}；仅用户点击才调用，使用最小回复提示，可能产生少量费用。
- POST /worlds/{world_id}/narration-preview body {action,mode:"fake"|"live",session_id?:string} →200 {request_id,mode,narrative,references:[source_id],sources:[{source_id,kind,title,document_id?,chunk_id?,chunk_index?,entry_id?}]}。sources只暴露本次提供模型的资料，references是其子集。服务端生成request_id。
- fake不使用会话；live必须会话有效，否则409 MODEL_SESSION_EXPIRED。不存在世界404，坏输入422，模型坏格式/引用502，连接失败/限流分别502/429，超时504。不回传上游原始错误正文。
- 客户端断开时DSH取消生成任务，finally关闭资源，无任何世界写入；事务连接不能跨越模型网络等待。60秒请求超时上限，不自动重试付费请求。

## 模块拼装

Li：server/app/model_gateway/**，提供ModelSessionStore.create(base_url,model,api_key)->dict（上面响应形状）；delete(session_id)、get_client(session_id)->ModelClient、async test(session_id)->dict；Domain GatewayError(code,message,status)。允许注入httpx.AsyncClient/transport与时钟以便测试，不启动全局联网。只配置OpenAI兼容接口，不硬编码服务商。
Ma：server/app/narration_context/**，提供build_context(conn,world_id,action)->ContextBundle，bundle.sources为tuple[ContextSource,...]，bundle.metadata为tuple[dict,...]，字段与HTTP sources一致。ContextError(code,message,status)。不commit、不建库、不联网。
Marcin：src/features/narration-preview/**，导出NarrationPreview({worldId,api,onOpenSource?})；api.createSession({base_url,model,api_key}),deleteSession(id),testSession(id),preview(worldId,{action,mode,session_id?},signal?)。全部Promise；错误含code/message。包内DTO自维护，DSH负责public API映射。onOpenSource(target:{source_id,kind,document_id?,chunk_id?,chunk_index?,entry_id?})。
DSH统一接HTTP、公用DTO与api实现，在资料库加独立实验入口；Codex核心模块只读，有问题报告，不临时绕校验。

## 独立开发补充（取代R6/R7任务等待状态）

Codex独立接手。四端点和预览界面已接通；缺失/过期session统一409。请求断开返回内部取消路径499（客户端已离线），停止后不返回成功；超时504并等待取消清理。

明确finish_reason=length/content_filter/tool_calls等不能成功；缺省字段兼容，仍须非空文本及草案结构/引用校验。生成max_tokens=2048，测试32，不自动重试或降级。metadata additive的selection_reason/truncated描述选择过程，不是相关性评分。

前端session操作可接AbortSignal；取消等待不证明会话未创建，过时创建尽力清理；删除失败必须可重试。POST结果始终为预览，无正式状态提交。下一阶段M2-B另行定义回合协议。
