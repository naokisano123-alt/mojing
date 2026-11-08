import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Sparkles,
  AlertTriangle,
  Server,
  Zap,
  Square,
  RefreshCw,
  Trash2,
  CheckCircle2,
  ExternalLink,
  ShieldAlert,
  Info,
} from 'lucide-react';
import type {
  NarrationPreviewProps,
  NarrationMode,
  ModelSessionResponse,
  NarrationPreviewResponse,
  SourceJumpTarget,
} from './types';
import './narration-preview.css';

const ACTION_MAX_LENGTH = 2000;

export function NarrationPreview({
  worldId,
  api,
  onOpenSource,
  initialMode = 'fake',
}: NarrationPreviewProps) {
  // 模式：fake | live
  const [mode, setMode] = useState<NarrationMode>(initialMode);

  // Live 模式会话状态（仅保存在组件内存与状态中，严禁持久化到 localStorage）
  const [session, setSession] = useState<ModelSessionResponse | null>(null);
  const [baseUrl, setBaseUrl] = useState('https://api.openai.com/v1');
  const [modelName, setModelName] = useState('gpt-4o-mini');
  const [apiKey, setApiKey] = useState('');
  const [disconnecting, setDisconnecting] = useState(false);
  const [sessionConnecting, setSessionConnecting] = useState(false);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [sessionExpiredNotice, setSessionExpiredNotice] = useState<string | null>(null);

  // 会话连接测试状态
  const [testingConnection, setTestingConnection] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  // 叙事生成与交互状态
  const [action, setAction] = useState('');
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<{ code: string; message: string } | null>(null);
  const [stoppedNotice, setStoppedNotice] = useState<string | null>(null);
  const [previewResult, setPreviewResult] = useState<NarrationPreviewResponse | null>(null);

  // 引用与并发控制 Ref
  const abortControllerRef = useRef<AbortController | null>(null);
  const currentWorldIdRef = useRef(worldId);
  const operationRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(false);
  const epochRef = useRef(0);

  const currentOperation = (controller: AbortController, epoch: number) =>
    mountedRef.current && epochRef.current === epoch && !controller.signal.aborted;

  const cancelWork = () => {
    operationRef.current?.abort();
    operationRef.current = null;
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
  };

  // 监听 worldId 切换，重置并 abort 正在进行的请求，防止迟到响应污染
  useEffect(() => {
    mountedRef.current = true;
    epochRef.current += 1;
    cancelWork();
    setSessionConnecting(false);
    setTestingConnection(false);
    setDisconnecting(false);
    setApiKey('');
    setTestResult(null);
    setSessionError(null);
    currentWorldIdRef.current = worldId;
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setGenerating(false);
    setPreviewResult(null);
    setGenerateError(null);
    setStoppedNotice(null);
    setSessionExpiredNotice(null);
  }, [worldId]);

  useEffect(() => () => {
    mountedRef.current = false;
    epochRef.current += 1;
    cancelWork();
  }, []);

  // 建立模型会话（仅 Live 模式）
  const handleConnectSession = async (e: React.FormEvent) => {
    e.preventDefault();
    if (operationRef.current || abortControllerRef.current) return;
    const controller = new AbortController();
    const epoch = epochRef.current;
    if (!baseUrl.trim() || !modelName.trim() || !apiKey.trim()) {
      setSessionError('请完整填写 API 地址、模型名称和 API Key');
      return;
    }

    operationRef.current = controller;
    setSessionConnecting(true);
    setSessionError(null);
    setSessionExpiredNotice(null);
    setTestResult(null);

    try {
      const res = await api.createSession({
        base_url: baseUrl.trim(),
        model: modelName.trim(),
        api_key: apiKey.trim(),
      }, controller.signal);
      if (!currentOperation(controller, epoch)) {
        // The server may have created it even if this view stopped waiting.
        void api.deleteSession(res.session_id).catch(() => {
          if (mountedRef.current) setSessionExpiredNotice('旧会话移除未确认，凭据可能保留至会话过期。');
        });
        return;
      }
      // 提交成功立即清空密码框中的敏感 Key
      setApiKey('');
      setSession(res);
    } catch (err: any) {
      if (!currentOperation(controller, epoch)) return;
      // 失败时保留用户当前输入的 base_url, modelName, apiKey 供其修正，不静默擦除
      setSessionError(err?.message || err?.code || '建立模型会话失败');
    } finally {
      if (currentOperation(controller, epoch)) {
        operationRef.current = null;
        setSessionConnecting(false);
      }
    }
  };

  const handleDisconnectSession = async () => {
    if (!session || operationRef.current || abortControllerRef.current) return;
    const controller = new AbortController();
    const epoch = epochRef.current;
    operationRef.current = controller;
    setDisconnecting(true);
    setSessionError(null);
    try {
      await api.deleteSession(session.session_id, controller.signal);
      if (!currentOperation(controller, epoch)) return;
      setSession(null);
      setTestResult(null);
      setSessionExpiredNotice(null);
    } catch {
      if (currentOperation(controller, epoch))
        setSessionError('移除会话失败，服务端可能仍保留凭据。请重试，或等待会话过期。');
    } finally {
      if (currentOperation(controller, epoch)) {
        operationRef.current = null;
        setDisconnecting(false);
      }
    }
  };

  const handleTestSession = async () => {
    if (!session || operationRef.current || abortControllerRef.current) return;
    const controller = new AbortController();
    const epoch = epochRef.current;
    operationRef.current = controller;
    setTestingConnection(true);
    setTestResult(null);
    try {
      const res = await api.testSession(session.session_id, controller.signal);
      if (!currentOperation(controller, epoch)) return;
      setTestResult({ ok: res.ok, message: res.ok ? '连接正常，模型已返回有效文本。' : '模型未返回有效回复。' });
    } catch (err: any) {
      if (!currentOperation(controller, epoch)) return;
      if (err?.code === 'MODEL_SESSION_EXPIRED') {
        setSession(null);
        setSessionExpiredNotice('模型会话已过期，请重新配置并连接');
      } else {
        setTestResult({ ok: false, message: err?.message || '连接测试失败' });
      }
    } finally {
      if (currentOperation(controller, epoch)) {
        operationRef.current = null;
        setTestingConnection(false);
      }
    }
  };

  // 执行叙事生成
  const handleGenerate = useCallback(async () => {
    const trimmedAction = action.trim();
    if (!trimmedAction || generating || abortControllerRef.current || operationRef.current) return;
    if (action.length > ACTION_MAX_LENGTH) {
      setGenerateError({ code: 'INPUT_TOO_LONG', message: `行动输入超过 ${ACTION_MAX_LENGTH} 字符上限` });
      return;
    }

    if (mode === 'live' && !session) {
      setSessionError('Live 模式需要先建立有效的模型会话');
      return;
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;
    const requestWorldId = currentWorldIdRef.current;

    setGenerating(true);
    setGenerateError(null);
    setStoppedNotice(null);

    try {
      const res = await api.preview(
        requestWorldId,
        {
          action: trimmedAction,
          mode,
          session_id: mode === 'live' ? session?.session_id : undefined,
        },
        controller.signal
      );

      // 迟到响应防护：若在等待期间切换了世界，丢弃响应，防止污染新页面
      if (!mountedRef.current || controller.signal.aborted || abortControllerRef.current !== controller || requestWorldId !== currentWorldIdRef.current) {
        return;
      }

      setPreviewResult(res);
    } catch (err: any) {
      // 若切换了世界，不处理旧世界的错误
      if (!mountedRef.current || controller.signal.aborted || abortControllerRef.current !== controller || requestWorldId !== currentWorldIdRef.current) {
        return;
      }

      // 如果是用户主动取消 / 中断
      if (controller.signal.aborted || err?.name === 'AbortError') {
        setStoppedNotice('已取消等待。注意：停止操作能取消客户端等待与服务端任务，但不能承诺上游服务商未扣费。');
      } else if (err?.code === 'MODEL_SESSION_EXPIRED' || err?.status === 409) {
        setSession(null);
        setSessionExpiredNotice('模型会话已过期（服务端闲置超时 30 分钟或服务已重启），请重新连接');
        setGenerateError({ code: 'MODEL_SESSION_EXPIRED', message: '会话已过期，请重新连接' });
      } else {
        setGenerateError({
          code: err?.code || 'GENERATE_FAILED',
          message: err?.message || '生成叙事草案失败，请稍后重试',
        });
      }
      // 无论成功还是失败，均完整保留用户已键入的草稿行动文本 action
    } finally {
      if (mountedRef.current && abortControllerRef.current === controller && requestWorldId === currentWorldIdRef.current) {
        setGenerating(false);
        abortControllerRef.current = null;
      }
    }
  }, [action, generating, mode, session, api]);

  // 停止生成
  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setGenerating(false);
    setStoppedNotice('已取消等待并请求停止生成；不能承诺上游服务商未扣费。');
  };

  // 点击参考来源跳转
  const handleSourceClick = (target: SourceJumpTarget) => {
    if (onOpenSource) {
      onOpenSource(target);
    }
  };

  return (
    <div className="narration-preview" data-testid="narration-preview">
      {/* 草案边界醒目横幅 */}
      <div className="narration-draft-alert">
        <Info size={16} />
        <div>
          <strong>实验性功能说明：</strong>
          当前为 M2-A 模型叙事实验，输出结果仅为未写入存档的预览草案，不会修改角色属性、金钱、时间线或世界存储，不影响 M0 试玩数据。
        </div>
      </div>

      {/* 模式选择 */}
      <div className="narration-mode-selector" role="radiogroup" aria-label="生成模式切换">
        <button
          type="button"
          className={`narration-mode-btn ${mode === 'fake' ? 'active' : ''}`}
          onClick={() => {
            handleStop();
            operationRef.current?.abort();
            operationRef.current = null;
            setSessionConnecting(false); setTestingConnection(false); setDisconnecting(false);
            setApiKey('');
            setMode('fake');
            setGenerateError(null);
            setStoppedNotice(null);
          }}
          data-testid="mode-btn-fake"
        >
          <Zap size={14} />
          Fake 模拟模式（本地免 Key）
        </button>
        <button
          type="button"
          className={`narration-mode-btn ${mode === 'live' ? 'active' : ''}`}
          onClick={() => {
            handleStop();
            setMode('live');
            setGenerateError(null);
            setStoppedNotice(null);
          }}
          data-testid="mode-btn-live"
        >
          <Sparkles size={14} />
          Live 真实模型（OpenAI 兼容）
        </button>
      </div>

      {/* Live 模式会话管理区 */}
      {mode === 'live' && (
        <div className="narration-section-panel" data-testid="live-session-panel">
          <div className="narration-panel-title">
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <Server size={15} />
              模型服务商连接配置
            </span>
            {session && (
              <span className="narration-badge-live">
                <CheckCircle2 size={12} />
                会话已激活
              </span>
            )}
          </div>

          {sessionExpiredNotice && (
            <div className="narration-error-banner" data-testid="session-expired-alert">
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <ShieldAlert size={14} />
                <span>{sessionExpiredNotice}</span>
              </div>
            </div>
          )}

          {session && sessionError && <div role="alert" className="narration-error-banner">{sessionError}</div>}
          {session ? (
            <div className="narration-session-active" data-testid="active-session-info">
              <div className="narration-session-meta">
                <span>
                  <strong>模型：</strong>
                  {session.model}
                </span>
                <span>
                  <strong>会话 ID：</strong>
                  {session.session_id.slice(0, 8)}...
                </span>
                <span>
                  <strong>有效期：</strong>
                  闲置 30 分钟，每次使用后延长
                </span>
              </div>
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                凭据状态：API Key 仅暂存服务端内存会话，浏览器未持久化存储。
              </div>

              {testResult && (
                <div
                  style={{
                    fontSize: 12,
                    padding: '6px 10px',
                    borderRadius: 4,
                    background: testResult.ok ? 'var(--paper)' : '#fdf2f0',
                    color: testResult.ok ? 'var(--green)' : '#ba6452',
                  }}
                  data-testid="test-result-message"
                >
                  {testResult.message}
                </div>
              )}

              <div className="narration-session-actions">
                <button
                  type="button"
                  className="secondary"
                  style={{ minHeight: 34, padding: '6px 12px', fontSize: 12 }}
                  onClick={handleTestSession}
                  disabled={testingConnection || disconnecting || generating}
                  data-testid="test-session-btn"
                >
                  <RefreshCw size={12} className={testingConnection ? 'animate-spin' : ''} />
                  {testingConnection ? '正在测试...' : '测试连接'}
                </button>
                <button
                  type="button"
                  className="secondary"
                  style={{ minHeight: 34, padding: '6px 12px', fontSize: 12, color: '#ba6452' }}
                  disabled={testingConnection || disconnecting || generating}
                  onClick={handleDisconnectSession}
                  data-testid="disconnect-session-btn"
                >
                  <Trash2 size={12} />
                  {disconnecting ? '正在移除...' : '断开会话'}
                </button>
                <span style={{ fontSize: 10, color: 'var(--muted)' }}>
                  提示：测试连接会发起极简调用，可能产生微量费用。
                </span>
              </div>
            </div>
          ) : (
            <form onSubmit={handleConnectSession} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div className="narration-form-row">
                <label htmlFor="narration-base-url">API 地址 (Base URL)</label>
                <input
                  id="narration-base-url"
                  type="text"
                  className="narration-input"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://api.openai.com/v1"
                  disabled={sessionConnecting}
                />
              </div>

              <div className="narration-form-row">
                <label htmlFor="narration-model-name">模型标识 (Model)</label>
                <input
                  id="narration-model-name"
                  type="text"
                  className="narration-input"
                  value={modelName}
                  onChange={(e) => setModelName(e.target.value)}
                  placeholder="gpt-4o-mini 或 deepseek-chat 等"
                  disabled={sessionConnecting}
                />
              </div>

              <div className="narration-form-row">
                <label htmlFor="narration-api-key">API Key (密码框传输，连接成功即清空)</label>
                <input
                  id="narration-api-key"
                  type="password"
                  autoComplete="off"
                  className="narration-input"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="sk-..."
                  disabled={sessionConnecting}
                />
              </div>

              {sessionError && (
                <div className="narration-error-banner" data-testid="session-error-banner">
                  {sessionError}
                </div>
              )}

              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 4 }}>
                <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                  Key 仅保存在服务端 30 分钟内存会话，不写磁盘或本地存储。
                </span>
                <button
                  type="submit"
                  className="primary"
                  style={{ minHeight: 36, padding: '8px 16px', fontSize: 12 }}
                  disabled={sessionConnecting || !apiKey.trim()}
                  data-testid="connect-session-btn"
                >
                  {sessionConnecting ? '正在连接...' : '连接并建立会话'}
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      {/* 行动输入与操作面板 */}
      <div className="narration-section-panel">
        <div className="narration-panel-title">
          <span>玩家行动描述</span>
          <span className={`narration-budget-counter ${action.length > ACTION_MAX_LENGTH ? 'overlimit' : ''}`}>
            {action.length} / {ACTION_MAX_LENGTH} 字
          </span>
        </div>

        <div className="narration-action-box">
          <textarea
            className="narration-textarea"
            value={action}
            onChange={(e) => setAction(e.target.value)}
            placeholder="描述你的角色想要采取的行动，例如：向集市铁匠打听古老遗迹的入口与守卫分布..."
            disabled={generating}
            data-testid="action-input"
          />
        </div>

        {generateError && (
          <div className="narration-error-banner" data-testid="generate-error-banner">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span>
                <strong>错误 [{generateError.code}]：</strong>
                {generateError.message}
              </span>
              <button
                type="button"
                className="secondary"
                style={{ minHeight: 28, padding: '4px 8px', fontSize: 11 }}
                onClick={handleGenerate}
                disabled={generating}
                data-testid="retry-btn"
              >
                <RefreshCw size={11} />
                手动重试
              </button>
            </div>
          </div>
        )}

        {stoppedNotice && (
          <div className="narration-draft-alert" data-testid="stopped-notice">
            <AlertTriangle size={15} />
            <div className="narration-stop-hint">{stoppedNotice}</div>
          </div>
        )}

        <div className="narration-controls">
          {generating ? (
            <button
              type="button"
              className="secondary"
              style={{ color: '#ba6452', minHeight: 40 }}
              onClick={handleStop}
              data-testid="stop-btn"
            >
              <Square size={14} />
              停止生成
            </button>
          ) : (
            <button
              type="button"
              className="primary"
              style={{ minHeight: 40 }}
              onClick={handleGenerate}
              disabled={
                sessionConnecting || testingConnection || disconnecting ||
                !action.trim() ||
                action.length > ACTION_MAX_LENGTH ||
                (mode === 'live' && !session)
              }
              data-testid="generate-btn"
            >
              <Sparkles size={14} />
              生成叙事草案
            </button>
          )}

          {generating && (
            <span style={{ fontSize: 12, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 6 }}>
              <RefreshCw size={13} className="animate-spin" />
              模型正在构思叙事草案...
            </span>
          )}
        </div>
      </div>

      {/* 叙事结果呈现卡片 */}
      {previewResult && (
        <div className="narration-result-card" data-testid="narration-result-card">
          <div className="narration-result-header">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {previewResult.mode === 'fake' ? (
                <span className="narration-badge-fake" data-testid="badge-fake">
                  <Zap size={11} />
                  Fake 模拟
                </span>
              ) : (
                <span className="narration-badge-live" data-testid="badge-live">
                  <Sparkles size={11} />
                  Live 真实模型
                </span>
              )}
              <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                草案预览（未入存档）
              </span>
            </div>
            <span style={{ fontSize: 10, color: 'var(--muted)' }} title={`请求标识: ${previewResult.request_id}`}>
              Req: {previewResult.request_id.slice(0, 8)}
            </span>
          </div>

          <div className="narration-prose" data-testid="narration-prose">
            {previewResult.narrative.split('\n\n').map((paragraph, index) => (
              <p key={index} style={{ marginBottom: index === previewResult.narrative.split('\n\n').length - 1 ? 0 : 12 }}>
                {paragraph}
              </p>
            ))}
          </div>

          {/* 引用设定资料列表 */}
          <div className="narration-sources-block" data-testid="narration-sources-block">
            <div className="narration-sources-title">
              <Info size={13} />
              参考世界设定来源
            </div>

            {previewResult.references.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--muted)', padding: '6px 0' }} data-testid="no-references-label">
                本次生成未引用任何设定资料。
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {previewResult.references.map((refId) => {
                  const source = previewResult.sources.find((s) => s.source_id === refId);
                  const title = source?.title || refId;
                  const kind = source?.kind || (refId.startsWith('chunk:') ? 'chunk' : 'entry');

                  return (
                    <div key={refId} className="narration-source-item" data-testid={`source-item-${refId}`}>
                      <div className="narration-source-info">
                        <span className="tag" style={{ fontSize: 9 }}>
                          {kind === 'chunk' ? '资料切片' : '设定条目'}
                        </span>
                        <span style={{ fontSize: 10, color: 'var(--muted)' }}>
                          {source?.selection_reason === 'literal_match' ? '字面命中' : source?.selection_reason ? '补充参考' : ''}
                          {source?.truncated ? ' · 已节选' : ''}
                        </span>
                        <span style={{ fontWeight: 500 }} title={title}>
                          {title}
                        </span>
                      </div>

                      {source && onOpenSource && (
                        <button
                          type="button"
                          className="narration-source-jump-btn"
                          onClick={() =>
                            handleSourceClick({
                              source_id: source.source_id,
                              kind: source.kind,
                              document_id: source.document_id,
                              chunk_id: source.chunk_id,
                              chunk_index: source.chunk_index,
                              entry_id: source.entry_id,
                            })
                          }
                          data-testid={`jump-source-${refId}`}
                        >
                          <ExternalLink size={12} />
                          定位原文
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
