import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ApiClientError } from '../../api/client';
import {
  PULSE_MODE_LABELS,
  PULSE_REASON_LABELS,
  PULSE_STATE_DESCRIPTIONS,
  PULSE_STATE_LABELS,
  formatPulseUsageSummary,
  sanitizePulseStatus,
  worldPulseApi,
} from './api';
import type {
  PulseConnectionMode,
  WorldPulseStatus,
  WorldPulseTransport,
} from './types';
import './world-pulse.css';

export interface WorldPulsePanelProps {
  adventureId: string;
  adventureMode?: 'fake' | 'live';
  narrationSessionId?: string | null;
  narrationModel?: string | null;
  transport?: WorldPulseTransport;
  disabled?: boolean;
  defaultOpen?: boolean;
  heartbeatIntervalMs?: number;
  defaultBaseUrl?: string;
  defaultModel?: string;
  onStatusChange?: (status: WorldPulseStatus) => void;
}

const DEFAULT_BASE_URL = 'https://api.deepseek.com';
const DEFAULT_MODEL_NAME = 'deepseek-flash';
const DEFAULT_HEARTBEAT_MS = 20_000;

export function WorldPulsePanel({
  adventureId,
  adventureMode = 'live',
  narrationSessionId = null,
  narrationModel = null,
  transport = worldPulseApi,
  disabled = false,
  defaultOpen = true,
  heartbeatIntervalMs = DEFAULT_HEARTBEAT_MS,
  defaultBaseUrl = DEFAULT_BASE_URL,
  defaultModel = DEFAULT_MODEL_NAME,
  onStatusChange,
}: WorldPulsePanelProps) {
  const [open, setOpen] = useState(defaultOpen);
  const [status, setStatus] = useState<WorldPulseStatus | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string>('');
  const [notice, setNotice] = useState<string>('');

  // 独立配置优先展示，也支持复用叙事会话
  const [configTab, setConfigTab] = useState<'independent' | 'reuse_narration'>('independent');
  const [showConfigForm, setShowConfigForm] = useState<boolean>(false);
  const [baseUrl, setBaseUrl] = useState<string>(defaultBaseUrl);
  const [modelName, setModelName] = useState<string>(defaultModel);
  // Key 严格只在输入与请求期间保存在组件内存中，提交成功、切换旅程或卸载后立即清空
  const [apiKey, setApiKey] = useState<string>('');

  // 正式旅程首次启用需显式确认额外消耗
  const [confirmingEnable, setConfirmingEnable] = useState<boolean>(false);
  const confirmedAdventuresRef = useRef<Set<string>>(new Set());

  const epochRef = useRef<number>(0);
  const mountedRef = useRef<boolean>(false);
  const currentAdventureIdRef = useRef<string>(adventureId);
  const activeControllerRef = useRef<AbortController | null>(null);
  const heartbeatTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const statusRef = useRef<WorldPulseStatus | null>(null);

  const updateStatus = useCallback(
    (next: WorldPulseStatus) => {
      const clean = sanitizePulseStatus(next);
      statusRef.current = clean;
      setStatus(clean);
      onStatusChange?.(clean);
    },
    [onStatusChange],
  );

  const clearHeartbeat = useCallback(() => {
    if (heartbeatTimerRef.current !== null) {
      clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = null;
    }
  }, []);

  const isForegroundOnline = () => {
    const visible =
      typeof document === 'undefined' || document.visibilityState !== 'hidden';
    const online =
      typeof navigator === 'undefined' || navigator.onLine !== false;
    return visible && online;
  };

  const sendActivitySafe = useCallback(
    async (targetAdventureId: string, active: boolean, expectedGeneration?: number) => {
      if (!targetAdventureId.trim()) return;
      if (active && !isForegroundOnline()) return;
      try {
        const res = await transport.setActivity(targetAdventureId, active);
        if (
          expectedGeneration !== undefined &&
          mountedRef.current &&
          epochRef.current === expectedGeneration &&
          currentAdventureIdRef.current === targetAdventureId &&
          res &&
          typeof res === 'object' &&
          'state' in res
        ) {
          updateStatus(res as WorldPulseStatus);
        }
      } catch {
        // 活动租约心跳失败由后端短 TTL 兜底，不打断前台操作
      }
    },
    [transport, updateStatus],
  );

  const loadStatus = useCallback(
    async (targetAdventureId: string) => {
      if (!targetAdventureId.trim()) {
        setLoading(false);
        return;
      }
      const generation = ++epochRef.current;
      activeControllerRef.current?.abort();
      const controller = new AbortController();
      activeControllerRef.current = controller;

      setLoading(true);
      setError('');

      try {
        const raw = await transport.getStatus(targetAdventureId, {
          signal: controller.signal,
        });
        if (!mountedRef.current || epochRef.current !== generation || controller.signal.aborted) {
          return;
        }
        const clean = sanitizePulseStatus(raw);
        updateStatus(clean);
      } catch (err: unknown) {
        if (!mountedRef.current || epochRef.current !== generation || controller.signal.aborted) {
          return;
        }
        const message =
          err instanceof ApiClientError
            ? err.friendlyMessage
            : err instanceof Error && err.message
              ? err.message
              : '读取世界脉动状态失败，请稍后重试。';
        setError(message);
      } finally {
        if (mountedRef.current && epochRef.current === generation && !controller.signal.aborted) {
          setLoading(false);
        }
      }
    },
    [transport, updateStatus],
  );

  // 监听旅程切换与组件卸载：清除旧请求与计时器、清空内存 Key、尝试结束旧旅程活动租约
  useEffect(() => {
    mountedRef.current = true;
    const prevAdventureId = currentAdventureIdRef.current;
    currentAdventureIdRef.current = adventureId;

    epochRef.current += 1;
    activeControllerRef.current?.abort();
    activeControllerRef.current = null;
    clearHeartbeat();

    // 严禁跨旅程残留未提交的 API Key 或状态
    setApiKey('');
    setBusy(false);
    setError('');
    setNotice('');
    setConfirmingEnable(false);
    setShowConfigForm(false);
    setConfigTab('independent');
    setStatus(null);
    statusRef.current = null;

    if (prevAdventureId && prevAdventureId !== adventureId) {
      void sendActivitySafe(prevAdventureId, false);
    }

    if (adventureId.trim()) {
      void loadStatus(adventureId);
    } else {
      setLoading(false);
    }

    return () => {
      mountedRef.current = false;
      epochRef.current += 1;
      activeControllerRef.current?.abort();
      activeControllerRef.current = null;
      clearHeartbeat();
      setApiKey('');
      const leavingId = currentAdventureIdRef.current;
      if (leavingId && leavingId.trim()) {
        void sendActivitySafe(leavingId, false);
      }
    };
  }, [adventureId, transport, clearHeartbeat, loadStatus, sendActivitySafe]);

  // 仅当存在可见旅程且脉动已开启时，管理前台活动租约续期与可见性/网络事件
  useEffect(() => {
    clearHeartbeat();
    const targetId = adventureId.trim();
    if (!targetId) return;

    const pulseEnabled = Boolean(status?.enabled);

    if (pulseEnabled && isForegroundOnline()) {
      void sendActivitySafe(targetId, true, epochRef.current);
    }
    if (pulseEnabled && heartbeatIntervalMs > 0) {
      heartbeatTimerRef.current = setInterval(() => {
        if (
          mountedRef.current &&
          currentAdventureIdRef.current === targetId &&
          isForegroundOnline() &&
          statusRef.current?.enabled
        ) {
          void sendActivitySafe(targetId, true, epochRef.current);
        }
      }, heartbeatIntervalMs);
    }

    const handleVisibilityChange = () => {
      if (!mountedRef.current || currentAdventureIdRef.current !== targetId) return;
      if (document.visibilityState === 'hidden') {
        void sendActivitySafe(targetId, false, epochRef.current);
        setNotice('已切入后台，世界脉动已暂停请求；返回前台时不会补演后台期间事件。');
      } else if (isForegroundOnline()) {
        setNotice('');
        if (statusRef.current?.enabled) {
          void sendActivitySafe(targetId, true, epochRef.current);
        }
      }
    };

    const handleOffline = () => {
      if (!mountedRef.current || currentAdventureIdRef.current !== targetId) return;
      void sendActivitySafe(targetId, false, epochRef.current);
      if (statusRef.current) {
        updateStatus({
          ...statusRef.current,
          state: statusRef.current.enabled ? 'offline' : 'off',
          reason_code: 'CLIENT_OFFLINE',
        });
      }
      setNotice('网络连接已断开，后台世界脉动已暂停；恢复联网后从当前已确认时间继续。');
    };

    const handleOnline = () => {
      if (!mountedRef.current || currentAdventureIdRef.current !== targetId) return;
      setNotice('');
      if (document.visibilityState !== 'hidden') {
        if (statusRef.current?.enabled) {
          void sendActivitySafe(targetId, true, epochRef.current);
        }
        void loadStatus(targetId);
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('offline', handleOffline);
    window.addEventListener('online', handleOnline);

    return () => {
      clearHeartbeat();
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('offline', handleOffline);
      window.removeEventListener('online', handleOnline);
    };
  }, [
    adventureId,
    status?.enabled,
    heartbeatIntervalMs,
    clearHeartbeat,
    loadStatus,
    sendActivitySafe,
    updateStatus,
  ]);

  const handleConnectIndependent = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy || disabled || !adventureId.trim()) return;
    if (!baseUrl.trim() || !modelName.trim() || !apiKey.trim()) {
      setError('请完整填写脉动 API 地址、模型名称和 API Key。');
      return;
    }

    const generation = ++epochRef.current;
    activeControllerRef.current?.abort();
    const controller = new AbortController();
    activeControllerRef.current = controller;

    setBusy(true);
    setError('');
    setNotice('');

    try {
      const next = await transport.connect(
        adventureId,
        {
          mode: 'independent',
          base_url: baseUrl.trim(),
          model: modelName.trim(),
          api_key: apiKey.trim(),
        },
        { signal: controller.signal },
      );
      if (!mountedRef.current || epochRef.current !== generation || controller.signal.aborted) {
        return;
      }
      // 连接成功立即从内存中清空 API Key 并收起输入框
      setApiKey('');
      setShowConfigForm(false);
      updateStatus(next);
      setNotice(
        next.enabled
          ? '独立脉动会话已重新绑定并恢复就绪。'
          : '独立脉动模型已连接（脉动当前仍处于关闭状态，需显式开启后才会运行）。',
      );
    } catch (err: unknown) {
      if (!mountedRef.current || epochRef.current !== generation || controller.signal.aborted) {
        return;
      }
      const message =
        err instanceof ApiClientError
          ? err.friendlyMessage
          : err instanceof Error && err.message
            ? err.message
            : '连接独立脉动模型失败，请检查配置后重试。';
      setError(message);
    } finally {
      if (mountedRef.current && epochRef.current === generation && !controller.signal.aborted) {
        setBusy(false);
      }
    }
  };

  const handleConnectReuseNarration = async () => {
    if (busy || disabled || !adventureId.trim()) return;
    const sid = (narrationSessionId ?? '').trim();
    if (!sid) {
      setError('当前尚未连接前台叙事模型会话，请先连接叙事模型或使用上方独立 API 配置。');
      return;
    }

    const generation = ++epochRef.current;
    activeControllerRef.current?.abort();
    const controller = new AbortController();
    activeControllerRef.current = controller;

    setBusy(true);
    setError('');
    setNotice('');

    try {
      const next = await transport.connect(
        adventureId,
        {
          mode: 'reuse_narration',
          narration_session_id: sid,
        },
        { signal: controller.signal },
      );
      if (!mountedRef.current || epochRef.current !== generation || controller.signal.aborted) {
        return;
      }
      setApiKey('');
      setShowConfigForm(false);
      updateStatus(next);
      setNotice(
        next.enabled
          ? '已复用当前叙事会话并恢复脉动。'
          : '已绑定复用当前叙事会话（连接本身不开启脉动，请点击下方按钮显式开启）。',
      );
    } catch (err: unknown) {
      if (!mountedRef.current || epochRef.current !== generation || controller.signal.aborted) {
        return;
      }
      const message =
        err instanceof ApiClientError
          ? err.friendlyMessage
          : err instanceof Error && err.message
            ? err.message
            : '复用叙事会话失败，请重新连接。';
      setError(message);
    } finally {
      if (mountedRef.current && epochRef.current === generation && !controller.signal.aborted) {
        setBusy(false);
      }
    }
  };

  const executeSetEnabled = async (targetEnabled: boolean) => {
    if (busy || disabled || !adventureId.trim()) return;
    const generation = ++epochRef.current;
    activeControllerRef.current?.abort();
    const controller = new AbortController();
    activeControllerRef.current = controller;

    setBusy(true);
    setError('');
    setNotice('');
    setConfirmingEnable(false);

    try {
      // Fake 旅程首次开启时若尚未绑定 fake 会话，先静默绑定 fake 模式
      if (
        targetEnabled &&
        isFakeJourney &&
        (!statusRef.current || statusRef.current.mode === 'unbound')
      ) {
        await transport.connect(adventureId, { mode: 'fake' }, { signal: controller.signal });
        if (!mountedRef.current || epochRef.current !== generation || controller.signal.aborted) {
          return;
        }
      }

      const next = await transport.setEnabled(adventureId, targetEnabled, {
        signal: controller.signal,
      });
      if (!mountedRef.current || epochRef.current !== generation || controller.signal.aborted) {
        return;
      }
      updateStatus(next);

      if (!targetEnabled) {
        clearHeartbeat();
        void sendActivitySafe(adventureId, false, generation);
      }
    } catch (err: unknown) {
      if (!mountedRef.current || epochRef.current !== generation || controller.signal.aborted) {
        return;
      }
      const message =
        err instanceof ApiClientError
          ? err.friendlyMessage
          : err instanceof Error && err.message
            ? err.message
            : targetEnabled
              ? '开启世界脉动失败，请稍后重试。'
              : '关闭世界脉动失败，请稍后重试。';
      setError(message);
    } finally {
      if (mountedRef.current && epochRef.current === generation && !controller.signal.aborted) {
        setBusy(false);
      }
    }
  };

  const isFakeJourney = adventureMode === 'fake' || status?.mode === 'fake';
  const currentMode: PulseConnectionMode = status?.mode ?? (isFakeJourney ? 'fake' : 'unbound');
  const currentState = status?.state ?? 'off';
  const isEnabled = Boolean(status?.enabled);
  const needsConnection =
    !isFakeJourney && (currentMode === 'unbound' || currentState === 'waiting_for_model');
  const shouldShowConnectionSection =
    !isFakeJourney && (needsConnection || showConfigForm);

  const handleRequestEnable = () => {
    if (busy || disabled) return;
    if (needsConnection) {
      setShowConfigForm(true);
      setError('请先完成模型会话连接（独立配置或复用叙事会话），再启用世界脉动。');
      return;
    }
    if (!isFakeJourney && !confirmedAdventuresRef.current.has(adventureId)) {
      setConfirmingEnable(true);
      return;
    }
    void executeSetEnabled(true);
  };

  const handleConfirmEnable = () => {
    confirmedAdventuresRef.current.add(adventureId);
    void executeSetEnabled(true);
  };

  const usageFormatted = formatPulseUsageSummary(status?.day_usage, currentMode);
  const reasonText =
    status?.reason_summary ||
    (status?.reason_code ? PULSE_REASON_LABELS[status.reason_code] : null);

  return (
    <details
      open={open}
      onToggle={event => {
        const next = event.currentTarget.open;
        setOpen(next);
        if (!next) setApiKey('');
      }}
      className="world-pulse-panel"
      aria-label="世界脉动控制与预算"
      data-viewport="mobile-ready"
      data-pulse-state={currentState}
      data-pulse-mode={currentMode}
    >
      <summary className="world-pulse-summary">
        <span className="world-pulse-summary-title">世界脉动设置</span>
        <span
          className="world-pulse-state-badge"
          data-state={currentState}
          data-testid="pulse-state-badge"
        >
          {PULSE_STATE_LABELS[currentState]}
        </span>
      </summary>

      {open && <div className="world-pulse-body">
        <div className="world-pulse-status-card" data-testid="pulse-status-card">
          <div className="world-pulse-status-row">
            <span>
              <strong>当前状态：</strong>
              {PULSE_STATE_LABELS[currentState]}
            </span>
            <span>
              <strong>连接方式：</strong>
              {PULSE_MODE_LABELS[currentMode]}
            </span>
            {status?.model && (
              <span data-testid="pulse-bound-model">
                <strong>绑定模型：</strong>
                {status.model}
              </span>
            )}
          </div>
          <p className="world-pulse-state-desc">
            {PULSE_STATE_DESCRIPTIONS[currentState]}
          </p>
          {reasonText && (
            <p className="world-pulse-reason" data-testid="pulse-reason-text">
              状态说明：{reasonText}
            </p>
          )}
          {status?.simulation?.initialized && <div className="world-pulse-simulation" aria-label="远景模拟结果">
            <strong>{status.reason_code === 'PULSE_JOB_FAILED' || currentState !== 'ready' ? '已保存的远景事件' : '远景模拟成功'}</strong>
            <p>以下仅为远景代称，具体消息仍须由角色在旅程中获知。</p>
            <ul>{status.simulation.events.map((event, index) => <li key={index}>
              <span>{event.kind === 'thread' ? '大事件' : '小事件'}：</span>{event.label}
            </li>)}</ul>
            {!status.simulation.events.length && <p>当前远景事件已暂告一段落。</p>}
          </div>}
        </div>

        {/* 额外消耗常驻说明 */}
        <div className="world-pulse-cost-banner" data-testid="pulse-cost-notice">
          {isFakeJourney ? (
            <p>
              模拟旅程演示模式 · 无需 API Key。开启后公告栏会随已确认的游戏时间推进，不产生任何外部模型费用。
            </p>
          ) : (
            <p>
              <strong>费用与边界说明：</strong>
              世界脉动默认关闭。连接会话不会自动开启脉动；启用后，初始化局势和推进到期事件可能发起额外的模型调用并消耗 Token。关闭或暂停期间普通冒险叙事仍可继续，且恢复时不会补演离线期间的历史。
            </p>
          )}
        </div>

        {/* 等待重连醒目提示 */}
        {currentState === 'waiting_for_model' && (
          <div
            role="status"
            className="world-pulse-warning-banner"
            data-testid="pulse-reconnect-banner"
          >
            <p>
              模型会话已过期或后端已重启，当前处于<strong>等待重连</strong>状态。为保障凭据安全，系统绝不会静默切换到其他 Key，请重新连接后继续脉动。
            </p>
          </div>
        )}

        {/* 离线状态醒目提示与恢复入口 */}
        {currentState === 'offline' && (
          <div
            role="status"
            className="world-pulse-warning-banner"
            data-testid="pulse-offline-banner"
          >
            <p>
              当前处于离线或后台暂停状态，已停止发起付费模型请求。恢复前台在线后将按当前已确认游戏时间继续，不补演离线期间事件。
            </p>
            <button
              type="button"
              className="secondary"
              disabled={busy || disabled}
              onClick={() => void sendActivitySafe(adventureId, true, epochRef.current)}
              data-testid="pulse-resume-online-btn"
            >
              恢复在线状态
            </button>
          </div>
        )}

        {/* 预算受限醒目提示 */}
        {currentState === 'budget_limited' && (
          <div
            role="status"
            className="world-pulse-warning-banner"
            data-testid="pulse-budget-banner"
          >
            <p>
              当游戏日或单轮脉动预算已达上限，后台推演已自动延后（无忙循环重试）。你的前台冒险回合与普通叙事不受影响，可继续正常游玩。
            </p>
          </div>
        )}

        {/* 显式启停主控区 */}
        <div className="world-pulse-toggle-row">
          <div className="world-pulse-toggle-actions">
            {isEnabled ? (
              <button
                type="button"
                className="secondary"
                aria-pressed={true}
                disabled={busy || loading || disabled}
                onClick={() => void executeSetEnabled(false)}
                data-testid="pulse-disable-btn"
              >
                {busy ? '正在关闭…' : '关闭世界脉动'}
              </button>
            ) : (
              <button
                type="button"
                className="primary"
                aria-pressed={false}
                disabled={busy || loading || disabled}
                onClick={handleRequestEnable}
                data-testid="pulse-enable-btn"
              >
                {busy ? '正在处理…' : isFakeJourney ? '开启脉动演示' : '开启世界脉动'}
              </button>
            )}

            {!isFakeJourney && !needsConnection && (
              <button
                type="button"
                className="text-button"
                disabled={busy || loading || disabled}
                onClick={() => {
                  setApiKey('');
                  setShowConfigForm(prev => !prev);
                }}
                data-testid="pulse-toggle-config-btn"
              >
                {showConfigForm ? '收起连接设置' : '更换模型连接'}
              </button>
            )}

            <button
              type="button"
              className="text-button"
              disabled={busy || loading || disabled}
              onClick={() => void loadStatus(adventureId)}
              data-testid="pulse-refresh-status-btn"
            >
              刷新脉动状态
            </button>
          </div>
        </div>

        {/* 正式旅程首次启用额外消耗二次确认框 */}
        {confirmingEnable && !isEnabled && (
          <div
            role="region"
            aria-label="启用世界脉动额外消耗确认"
            className="world-pulse-confirm-box"
            data-testid="pulse-cost-confirm-dialog"
          >
            <p>
              <strong>确认启用真实模型世界脉动？</strong>
              启用后，当你在旅程中推进游戏时间且有到期世界事件时，后台将额外调用模型（{status?.model || modelName}）并产生 Token 消耗。随时可以关闭，关闭不会删除已有叙事会话或世界历史。
            </p>
            <div className="world-pulse-confirm-actions">
              <button
                type="button"
                className="primary"
                disabled={busy || disabled}
                onClick={handleConfirmEnable}
                data-testid="pulse-confirm-enable-btn"
              >
                确认开启世界脉动
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy || disabled}
                onClick={() => setConfirmingEnable(false)}
                data-testid="pulse-cancel-enable-btn"
              >
                暂不开启
              </button>
            </div>
          </div>
        )}

        {/* 模型会话连接区（独立配置优先，同时支持复用叙事会话） */}
        {shouldShowConnectionSection && (
          <div className="world-pulse-config-section" data-testid="pulse-config-section">
            <div
              className="world-pulse-mode-tabs"
              role="tablist"
              aria-label="脉动模型连接方式"
            >
              <button
                type="button"
                role="tab"
                aria-selected={configTab === 'independent'}
                className={`world-pulse-tab-btn ${configTab === 'independent' ? 'is-active' : ''}`}
                disabled={busy || disabled}
                onClick={() => {
                  setError('');
                  setConfigTab('independent');
                }}
                data-testid="pulse-tab-independent"
              >
                独立 API 连接（优先推荐）
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={configTab === 'reuse_narration'}
                className={`world-pulse-tab-btn ${configTab === 'reuse_narration' ? 'is-active' : ''}`}
                disabled={busy || disabled}
                onClick={() => {
                  setApiKey('');
                  setError('');
                  setConfigTab('reuse_narration');
                }}
                data-testid="pulse-tab-reuse"
              >
                复用当前叙事会话
              </button>
            </div>

            {configTab === 'independent' ? (
              <form
                className="world-pulse-form"
                onSubmit={handleConnectIndependent}
                data-testid="pulse-independent-form"
              >
                <p className="world-pulse-form-hint">
                  推荐使用独立 API Key 或单独模型，便于在服务商后台单独观察脉动消耗。凭据仅保存在服务端内存会话中，提交后立即从页面清除。
                </p>
                <label className="world-pulse-field">
                  <span>脉动 API 地址 (Base URL)</span>
                  <input
                    type="url"
                    aria-label="脉动 API 地址"
                    autoCapitalize="none"
                    spellCheck={false}
                    value={baseUrl}
                    disabled={busy || disabled}
                    onChange={e => setBaseUrl(e.target.value)}
                    placeholder={DEFAULT_BASE_URL}
                    data-testid="pulse-base-url-input"
                  />
                </label>
                <label className="world-pulse-field">
                  <span>脉动模型名称 (Model)</span>
                  <input
                    type="text"
                    aria-label="脉动模型名称"
                    autoCapitalize="none"
                    spellCheck={false}
                    value={modelName}
                    disabled={busy || disabled}
                    onChange={e => setModelName(e.target.value)}
                    placeholder={DEFAULT_MODEL_NAME}
                    data-testid="pulse-model-input"
                  />
                </label>
                <label className="world-pulse-field">
                  <span>脉动 API Key（提交或离开即清空）</span>
                  <input
                    type="password"
                    aria-label="脉动 API Key"
                    autoComplete="off"
                    value={apiKey}
                    disabled={busy || disabled}
                    onChange={e => setApiKey(e.target.value)}
                    placeholder="sk-..."
                    data-testid="pulse-api-key-input"
                  />
                </label>
                <div className="world-pulse-form-actions">
                  <button
                    type="submit"
                    className="secondary"
                    disabled={busy || disabled || !apiKey.trim()}
                    data-testid="pulse-connect-independent-btn"
                  >
                    {busy ? '正在绑定…' : '绑定独立脉动会话'}
                  </button>
                </div>
              </form>
            ) : (
              <div className="world-pulse-reuse-box" data-testid="pulse-reuse-panel">
                <p className="world-pulse-form-hint">
                  直接复用当前旅程已连接的前台叙事模型会话，无需重复输入 API Key。关闭世界脉动不会断开前台叙事会话。
                </p>
                <p className="world-pulse-reuse-status">
                  {narrationSessionId ? (
                    <span>
                      已检测到可用叙事会话{narrationModel ? `（模型：${narrationModel}）` : ''}。
                    </span>
                  ) : (
                    <span>
                      当前尚未建立前台叙事会话，请先在冒险页顶部的“连接模型 API”中建立会话，或切换至“独立 API 连接”。
                    </span>
                  )}
                </p>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy || disabled || !narrationSessionId}
                  onClick={() => void handleConnectReuseNarration()}
                  data-testid="pulse-connect-reuse-btn"
                >
                  {busy ? '正在绑定…' : '绑定并复用叙事会话'}
                </button>
              </div>
            )}
          </div>
        )}

        {/* 当游戏日实际 / 估算用量展示区 */}
        <div className="world-pulse-usage-card" data-testid="pulse-usage-card">
          <h4 className="world-pulse-usage-title">
            当游戏日脉动用量{status?.day_usage?.game_day ? `（第 ${status.day_usage.game_day} 游戏日）` : ''}
          </h4>
          <div className="world-pulse-usage-grid">
            <span data-testid="pulse-actual-tokens">{usageFormatted.actualTokensText}</span>
            <span data-testid="pulse-estimated-tokens">{usageFormatted.estimatedTokensText}</span>
            <span data-testid="pulse-billed-calls">{usageFormatted.billedCallsText}</span>
            {usageFormatted.budgetRemainingText && (
              <span data-testid="pulse-budget-remaining">
                {usageFormatted.budgetRemainingText}
              </span>
            )}
            {usageFormatted.simulatedUsageText && (
              <span data-testid="pulse-simulated-usage">
                {usageFormatted.simulatedUsageText}
              </span>
            )}
          </div>
          {usageFormatted.timeoutWarningText && (
            <p className="world-pulse-timeout-note" data-testid="pulse-timeout-warning">
              {usageFormatted.timeoutWarningText}
            </p>
          )}
        </div>

        {loading && (
          <p role="status" className="muted">
            正在同步脉动状态…
          </p>
        )}

        {notice && !error && (
          <p role="status" className="world-pulse-notice" data-testid="pulse-notice">
            {notice}
          </p>
        )}

        {error && (
          <div role="alert" className="error world-pulse-error" data-testid="pulse-error">
            <p>{error}</p>
          </div>
        )}
      </div>}
    </details>
  );
}
