import { useEffect, useRef, useState } from 'react';
import { ApiClientError } from '../../api/client.ts';
import { ACTION_LIMIT } from '../../interaction.ts';
import { KNOWLEDGE_TYPE_LABELS } from '../world-encounters/api.ts';
import { WorldEncounterPanel } from '../world-encounters/WorldEncounterPanel.tsx';
import type { WorldEncountersTransport } from '../world-encounters/types.ts';
import {
  WORLD_START_ORIGIN_LABELS,
  WORLD_START_PHASE_LABELS,
  WORLD_START_RUN_KIND_LABELS,
  applyStartSuggestionToComposerDraft,
  computeMaterialSelection,
  sanitizeWorldStartBootstrap,
  sanitizeWorldStartCommitResult,
  sanitizeWorldStartDraftSummary,
  worldStartApi,
} from './api.ts';
import type {
  WorldStartBootstrapDto,
  WorldStartCommitResultDto,
  WorldStartDraftSummaryDto,
  WorldStartPhase,
  WorldStartPointOption,
  WorldStartRunKind,
  WorldStartSourceCitation,
  WorldStartTransport,
} from './types.ts';
import './world-start.css';

export interface WorldStartPanelProps {
  worldId: string;
  worldName?: string;
  transport?: WorldStartTransport;
  mode?: 'fake' | 'live';
  sessionId?: string | null;
  modelName?: string | null;
  disabled?: boolean;
  defaultRunKind?: WorldStartRunKind;
  parentAdventureId?: string | null;
  composerDraft?: string;
  onComposerDraftChange?: (nextDraft: string) => void;
  onExpandComposer?: (expanded: boolean) => void;
  onConfigureModel?: () => void;
  onRequestImportMaterials?: () => void;
  onJourneyStarted?: (result: WorldStartCommitResultDto) => void;
  onOpenSource?: (citation: WorldStartSourceCitation) => void;
  refreshTrigger?: number | string;
}

export function WorldStartPanel({
  worldId,
  worldName,
  transport = worldStartApi,
  mode: propMode,
  sessionId = null,
  modelName = null,
  disabled = false,
  defaultRunKind = 'initial',
  parentAdventureId = null,
  composerDraft,
  onComposerDraftChange,
  onExpandComposer,
  onConfigureModel,
  onRequestImportMaterials,
  onJourneyStarted,
  onOpenSource,
  refreshTrigger,
}: WorldStartPanelProps) {
  const [bootstrap, setBootstrap] = useState<WorldStartBootstrapDto | null>(null);
  const [phase, setPhase] = useState<WorldStartPhase>('waiting');
  const [runKind, setRunKind] = useState<WorldStartRunKind>(defaultRunKind);
  const [mode, setMode] = useState<'fake' | 'live'>(
    propMode ?? (sessionId ? 'live' : 'fake'),
  );

  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [selectedStartId, setSelectedStartId] = useState<string>('');
  const [adventureName, setAdventureName] = useState<string>('');
  const [characterName, setCharacterName] = useState<string>('');
  const [characterRole, setCharacterRole] = useState<string>('');
  const [location, setLocation] = useState<string>('');
  const [startsAt, setStartsAt] = useState<string>('');

  const [draft, setDraft] = useState<WorldStartDraftSummaryDto | null>(null);
  const [appliedResult, setAppliedResult] = useState<WorldStartCommitResultDto | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string>('');
  const [notice, setNotice] = useState<string>('');

  const epochRef = useRef<number>(0);
  const activeControllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef<boolean>(false);
  const currentWorldIdRef = useRef<string>(worldId);

  useEffect(() => {
    if (propMode) {
      setMode(propMode);
    }
  }, [propMode]);

  const applyStartOptionFields = (sp: WorldStartPointOption, wName: string) => {
    setSelectedStartId(sp.start_id);
    setLocation(sp.location);
    setStartsAt(sp.starts_at);
    setCharacterName(sp.character_name);
    setCharacterRole(sp.character_role);
    setAdventureName(`${wName || '新世界'} · ${sp.location}`);
  };

  const loadBootstrap = async (targetWorldId: string) => {
    const generation = ++epochRef.current;
    activeControllerRef.current?.abort();
    const controller = new AbortController();
    activeControllerRef.current = controller;

    setLoading(true);
    setError('');

    try {
      const raw = await transport.getBootstrap(targetWorldId, {
        signal: controller.signal,
      });
      if (
        !mountedRef.current ||
        epochRef.current !== generation ||
        controller.signal.aborted ||
        currentWorldIdRef.current !== targetWorldId
      ) {
        return;
      }

      const clean = sanitizeWorldStartBootstrap(raw);
      setBootstrap(clean);

      const defaultSelected = clean.materials
        .filter(m => m.selected_by_default && m.char_count > 0)
        .map(m => m.source_id);
      setSelectedSourceIds(defaultSelected);

      if (clean.start_points.length > 0) {
        applyStartOptionFields(clean.start_points[0], clean.world_name);
      } else {
        setSelectedStartId('');
        setLocation(clean.world_name ? `${clean.world_name}起点` : '');
        setStartsAt('1000-01-01T08:00');
        setCharacterName('旅人');
        setCharacterRole('探索者');
        setAdventureName(clean.world_name ? `${clean.world_name} · 自由旅程` : '新的旅程');
      }

      if (clean.applied_result) {
        setAppliedResult(clean.applied_result);
        setPhase('applied');
      } else if (clean.latest_draft) {
        const saved = clean.latest_draft;
        setDraft(saved);
        setSelectedSourceIds(saved.selected_source_ids);
        setSelectedStartId(saved.start_point.start_id || '');
        setLocation(saved.start_point.location);
        setStartsAt(saved.start_point.starts_at);
        setCharacterName(saved.start_point.character_name);
        setCharacterRole(saved.start_point.character_role || '');
        setAdventureName(saved.start_point.adventure_name);
        setMode(saved.mode);
        setRunKind(saved.run_kind);
        setPhase('ready_to_confirm');
      } else if (defaultSelected.length === 0) {
        setPhase('insufficient_materials');
      } else {
        setPhase('waiting');
      }
    } catch (err: unknown) {
      if (
        !mountedRef.current ||
        epochRef.current !== generation ||
        controller.signal.aborted ||
        currentWorldIdRef.current !== targetWorldId
      ) {
        return;
      }
      const msg =
        err instanceof ApiClientError
          ? err.friendlyMessage
          : err instanceof Error && err.message
            ? err.message
            : '读取世界开局资料失败，请稍后重试。';
      setError(msg);
      setPhase('failed_retryable');
    } finally {
      if (
        mountedRef.current &&
        epochRef.current === generation &&
        !controller.signal.aborted &&
        currentWorldIdRef.current === targetWorldId
      ) {
        setLoading(false);
      }
    }
  };

  // 监听世界切换或离开：立即中止在途请求并递增 epoch，杜绝旧世界迟到响应污染当前世界
  useEffect(() => {
    mountedRef.current = true;
    currentWorldIdRef.current = worldId;
    epochRef.current += 1;
    activeControllerRef.current?.abort();
    activeControllerRef.current = null;

    setBootstrap(null);
    setDraft(null);
    setAppliedResult(null);
    setSelectedSourceIds([]);
    setSelectedStartId('');
    setAdventureName('');
    setCharacterName('');
    setCharacterRole('');
    setLocation('');
    setStartsAt('');
    setSubmitting(false);
    setError('');
    setNotice('');
    setRunKind(defaultRunKind);

    if (worldId.trim()) {
      void loadBootstrap(worldId);
    } else {
      setLoading(false);
    }

    return () => {
      mountedRef.current = false;
      epochRef.current += 1;
      activeControllerRef.current?.abort();
      activeControllerRef.current = null;
    };
  }, [worldId, transport, refreshTrigger]);

  const materials = bootstrap?.materials ?? [];
  const materialSelection = computeMaterialSelection(
    worldId,
    materials,
    selectedSourceIds,
  );

  const handleToggleMaterial = (sourceId: string) => {
    if (disabled || submitting) return;
    setSelectedSourceIds(prev => {
      const next = prev.includes(sourceId)
        ? prev.filter(id => id !== sourceId)
        : [...prev, sourceId];
      const nextSel = computeMaterialSelection(worldId, materials, next);
      if (!draft && !appliedResult) {
        setPhase(nextSel.has_usable_materials ? 'waiting' : 'insufficient_materials');
      }
      return next;
    });
  };

  const handleAnalyzeStart = async (explicitKind?: WorldStartRunKind) => {
    if (disabled || submitting || !worldId.trim()) return;

    if (!materialSelection.has_usable_materials) {
      setPhase('insufficient_materials');
      setError('当前资料不足或未勾选任何设定文档，请先勾选/导入世界资料，或直接开启普通冒险。');
      return;
    }

    if (mode === 'live' && !sessionId) {
      setError('真实模型分析需要先连接模型 API 会话；你也可以先切换为模拟模式或直接开启普通冒险。');
      return;
    }

    const targetWorldId = worldId;
    const effectiveRunKind: WorldStartRunKind =
      explicitKind ?? (phase === 'failed_retryable' ? 'retry' : runKind);

    const generation = ++epochRef.current;
    activeControllerRef.current?.abort();
    const controller = new AbortController();
    activeControllerRef.current = controller;

    setSubmitting(true);
    setPhase('analyzing');
    setError('');
    setNotice('');

    try {
      const draftRes = await transport.analyzeStart(
        targetWorldId,
        {
          selected_source_ids: materialSelection.selected_source_ids,
          start_point: {
            start_id: selectedStartId || null,
            location: location.trim() || bootstrap?.world_name || '起始地点',
            starts_at: startsAt.trim() || '1000-01-01T08:00',
            character_name: characterName.trim() || '旅人',
            character_role: characterRole.trim() || '探索者',
            adventure_name:
              adventureName.trim() ||
              `${bootstrap?.world_name || worldName || '新世界'} · 旅程`,
          },
          mode,
          session_id: mode === 'live' ? sessionId : undefined,
          run_kind: effectiveRunKind,
          retry_of_draft_id: draft?.draft_id ?? null,
          parent_adventure_id: parentAdventureId,
        },
        { signal: controller.signal },
      );

      if (
        !mountedRef.current ||
        epochRef.current !== generation ||
        controller.signal.aborted ||
        currentWorldIdRef.current !== targetWorldId
      ) {
        return;
      }

      const cleanDraft = sanitizeWorldStartDraftSummary(
        draftRes,
        new Set(materialSelection.selected_source_ids),
      );
      setDraft(cleanDraft);
      setRunKind(cleanDraft.run_kind);
      setPhase('ready_to_confirm');
      setNotice('AI 开局草案已生成（尚未写入正式存档，核对摘要后即可确认进入旅程）。');
    } catch (err: unknown) {
      if (
        !mountedRef.current ||
        epochRef.current !== generation ||
        controller.signal.aborted ||
        currentWorldIdRef.current !== targetWorldId
      ) {
        return;
      }
      const msg =
        err instanceof ApiClientError
          ? err.friendlyMessage
          : err instanceof Error && err.message
            ? err.message
            : 'AI 开局分析失败，请重试或直接开启普通冒险。';
      setError(msg);
      setPhase(
        msg.includes('INSUFFICIENT_MATERIALS') ? 'insufficient_materials' : 'failed_retryable',
      );
    } finally {
      if (
        mountedRef.current &&
        epochRef.current === generation &&
        !controller.signal.aborted &&
        currentWorldIdRef.current === targetWorldId
      ) {
        setSubmitting(false);
      }
    }
  };

  const handleCancelAnalyzing = () => {
    epochRef.current += 1;
    activeControllerRef.current?.abort();
    activeControllerRef.current = null;
    setSubmitting(false);
    setPhase(
      draft
        ? 'ready_to_confirm'
        : materialSelection.has_usable_materials
          ? 'waiting'
          : 'insufficient_materials',
    );
    setNotice('已取消分析等待，未确认草案不会写入正式存档。');
  };

  const handleConfirmDraft = async () => {
    if (!draft || disabled || submitting) return;
    const targetWorldId = worldId;
    const generation = ++epochRef.current;
    activeControllerRef.current?.abort();
    const controller = new AbortController();
    activeControllerRef.current = controller;

    setSubmitting(true);
    setError('');
    setNotice('');

    try {
      const commitRes = await transport.confirmStart(
        targetWorldId,
        draft.draft_id,
        {
          adventure_name: adventureName.trim() || draft.start_point.adventure_name,
          run_kind: draft.run_kind,
        },
        { signal: controller.signal },
      );

      if (
        !mountedRef.current ||
        epochRef.current !== generation ||
        controller.signal.aborted ||
        currentWorldIdRef.current !== targetWorldId
      ) {
        return;
      }

      const cleanCommit = sanitizeWorldStartCommitResult(commitRes);
      setAppliedResult(cleanCommit);
      setDraft(null);
      setPhase('applied');
      setNotice('已确认并建立初始局势，现在可以自由输入行动或从世界消息发起查证。');
      onJourneyStarted?.(cleanCommit);
    } catch (err: unknown) {
      if (
        !mountedRef.current ||
        epochRef.current !== generation ||
        controller.signal.aborted ||
        currentWorldIdRef.current !== targetWorldId
      ) {
        return;
      }
      const msg =
        err instanceof ApiClientError
          ? err.friendlyMessage
          : err instanceof Error && err.message
            ? err.message
            : '确认建立旅程失败，请重试。';
      setError(msg);
      setPhase('failed_retryable');
    } finally {
      if (
        mountedRef.current &&
        epochRef.current === generation &&
        !controller.signal.aborted &&
        currentWorldIdRef.current === targetWorldId
      ) {
        setSubmitting(false);
      }
    }
  };

  const handleDiscardDraft = async () => {
    if (!draft || disabled || submitting) return;
    const targetDraftId = draft.draft_id;
    const targetWorldId = worldId;
    setDraft(null);
    setPhase(materialSelection.has_usable_materials ? 'waiting' : 'insufficient_materials');
    setNotice('已放弃未确认草案，正式事件、人物与知识未发生任何写入。');
    try {
      await transport.discardDraft(targetWorldId, targetDraftId);
    } catch {
      // 忽略清理失败，前端已回到等待选择状态
    }
  };

  const handleQuickStartAdventure = async () => {
    if (disabled || submitting || !worldId.trim()) return;
    const targetWorldId = worldId;
    const generation = ++epochRef.current;
    activeControllerRef.current?.abort();
    const controller = new AbortController();
    activeControllerRef.current = controller;

    setSubmitting(true);
    setError('');
    setNotice('');

    try {
      const quickRes = await transport.startQuickAdventure(
        targetWorldId,
        {
          adventure_name:
            adventureName.trim() ||
            `${bootstrap?.world_name || worldName || '新世界'} · 自由旅程`,
          mode,
          session_id: mode === 'live' ? sessionId : undefined,
          location: location.trim() || undefined,
          starts_at: startsAt.trim() || undefined,
          character_name: characterName.trim() || undefined,
          selected_source_ids: materialSelection.selected_source_ids,
        },
        { signal: controller.signal },
      );

      if (
        !mountedRef.current ||
        epochRef.current !== generation ||
        controller.signal.aborted ||
        currentWorldIdRef.current !== targetWorldId
      ) {
        return;
      }

      const cleanQuick = sanitizeWorldStartCommitResult(quickRes);
      setAppliedResult(cleanQuick);
      setDraft(null);
      setPhase('applied');
      setNotice('已跳过 AI 局势分析直接开启普通冒险，可立即自由输入行动。');
      onJourneyStarted?.(cleanQuick);
    } catch (err: unknown) {
      if (
        !mountedRef.current ||
        epochRef.current !== generation ||
        controller.signal.aborted ||
        currentWorldIdRef.current !== targetWorldId
      ) {
        return;
      }
      const msg =
        err instanceof ApiClientError
          ? err.friendlyMessage
          : err instanceof Error && err.message
            ? err.message
            : '开启普通冒险失败，请重试。';
      setError(msg);
      setPhase('failed_retryable');
    } finally {
      if (
        mountedRef.current &&
        epochRef.current === generation &&
        !controller.signal.aborted &&
        currentWorldIdRef.current === targetWorldId
      ) {
        setSubmitting(false);
      }
    }
  };

  const handleWriteSuggestionToComposer = (suggestionText: string) => {
    const applied = applyStartSuggestionToComposerDraft(
      composerDraft ?? '',
      suggestionText,
      ACTION_LIMIT,
    );
    if (applied.error || applied.nextDraft === null) {
      setNotice(applied.error || '草稿空间不足。');
      return;
    }
    onComposerDraftChange?.(applied.nextDraft);
    onExpandComposer?.(true);
    setNotice('已将开局行动建议填入编辑框，可自由修改后发送。');
  };

  const displayWorldTitle = bootstrap?.world_name || worldName || '当前世界';

  return (
    <section
      className="world-start-panel"
      aria-label="世界开局与初始局势建立"
      data-viewport="mobile-ready"
      data-testid="world-start-panel"
      data-phase={phase}
      data-run-kind={runKind}
    >
      <div className="world-start-header">
        <div className="world-start-title-group">
          <h3 className="world-start-title">{displayWorldTitle} · 开局与初始局势</h3>
          <p className="world-start-subtitle">
            从已导入的世界资料建立初始局势，或跳过设置直接开启普通冒险。
          </p>
        </div>
        <span
          className="world-start-phase-badge"
          data-phase={phase}
          data-testid="world-start-phase-badge"
        >
          {WORLD_START_PHASE_LABELS[phase]} · {WORLD_START_RUN_KIND_LABELS[runKind]}
        </span>
      </div>

      {/* API 用途与模型配置沿用说明 */}
      <div
        className="world-start-api-notice"
        data-testid="world-start-api-disclosure"
        role="note"
      >
        <strong>模型调用与边界说明：</strong>
        <span>
          导入文档本身只保存资料，不会自动调用 AI。当你主动点击「启动 AI 开局分析」时，系统将读取所选资料节选与起点，
          {mode === 'live'
            ? `复用当前已连接的模型会话（${modelName || (sessionId ? '已激活会话' : '待连接会话')}）生成初始局势草案，可能消耗模型 Token。`
            : '使用本地模拟模式（免 API Key）生成初始局势草案。'}
          未确认的草案不会写入正式事件、人物、知识与世界时间。
        </span>
      </div>

      {loading && (
        <p role="status" className="muted" data-testid="world-start-loading">
          正在读取世界资料与候选起点…
        </p>
      )}

      {notice && (
        <p role="status" className="muted" data-testid="world-start-notice">
          {notice}
        </p>
      )}

      {error && (
        <div
          role="alert"
          className="world-start-error-box"
          data-testid="world-start-error-alert"
        >
          <p>{error}</p>
          {phase === 'failed_retryable' && (
            <div className="world-start-actions-row">
              <button
                type="button"
                className="primary world-start-touch-btn"
                disabled={disabled || submitting}
                onClick={() => void handleAnalyzeStart('retry')}
                data-testid="world-start-retry-btn"
              >
                重试 AI 分析
              </button>
              <button
                type="button"
                className="secondary world-start-touch-btn"
                disabled={disabled || submitting}
                onClick={() => void handleQuickStartAdventure()}
                data-testid="world-start-error-quick-btn"
              >
                普通冒险先行
              </button>
            </div>
          )}
        </div>
      )}

      {!loading && phase !== 'applied' && (
        <>
          {/* 1. 资料选择区 */}
          <div className="world-start-section" data-testid="world-start-materials-section">
            <div className="world-start-section-header">
              <h4 className="world-start-section-title">
                1. 选择开局依据资料（已选 {materialSelection.selected_source_ids.length} / {materials.length} 份）
              </h4>
              {materialSelection.requires_batch_extraction && (
                <span className="tag" data-testid="world-start-batch-hint">
                  长文将分 {materialSelection.estimated_batches} 批提取并合并结果
                </span>
              )}
            </div>

            {materials.length === 0 || !materialSelection.has_usable_materials ? (
              <div data-testid="world-start-insufficient-box">
                <p className="muted">
                  {materials.length === 0
                    ? '当前世界尚未导入任何设定文档或人工条目（资料不足）。你可以先去导入设定，或直接开启普通冒险。'
                    : '请至少勾选一份用于建立初始局势的设定资料，或直接开启普通冒险。'}
                </p>
                <div className="world-start-actions-row">
                  {onRequestImportMaterials && (
                    <button
                      type="button"
                      className="secondary world-start-touch-btn"
                      onClick={onRequestImportMaterials}
                      data-testid="world-start-go-import-btn"
                    >
                      前往导入世界资料
                    </button>
                  )}
                  <button
                    type="button"
                    className="primary world-start-touch-btn"
                    disabled={disabled || submitting}
                    onClick={() => void handleQuickStartAdventure()}
                    data-testid="world-start-quick-start-insufficient-btn"
                  >
                    普通冒险先行（无需 AI 分析）
                  </button>
                </div>
              </div>
            ) : null}

            {materials.length > 0 && (
              <ul className="world-start-materials-list" aria-label="可选世界设定资料">
                {materials.map(mat => {
                  const checked = selectedSourceIds.includes(mat.source_id);
                  return (
                    <li key={mat.source_id}>
                      <label className="world-start-material-item">
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={disabled || submitting}
                          onChange={() => handleToggleMaterial(mat.source_id)}
                          data-testid={`world-start-material-checkbox-${mat.source_id}`}
                        />
                        <div>
                          <strong>{mat.title}</strong>{' '}
                          <span className="tag">
                            {mat.kind === 'lore_entry' ? '人工设定' : '导入文档'} · {mat.char_count} 字
                          </span>
                          {mat.excerpt_preview && (
                            <p className="muted" style={{ fontSize: '0.8125rem', marginTop: 4 }}>
                              {mat.excerpt_preview}
                            </p>
                          )}
                        </div>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {/* 2. 起点与角色选择区 */}
          <div className="world-start-section" data-testid="world-start-point-section">
            <div className="world-start-section-header">
              <h4 className="world-start-section-title">2. 选择起点与开局时刻</h4>
              <span className="muted" style={{ fontSize: '0.75rem' }}>
                可点选资料提取的候选起点，也可直接修改下方字段
              </span>
            </div>

            {bootstrap && bootstrap.start_points.length > 0 && (
              <ul className="world-start-points-list" aria-label="候选开局起点">
                {bootstrap.start_points.map(sp => {
                  const isSelected = selectedStartId === sp.start_id;
                  return (
                    <li key={sp.start_id}>
                      <button
                        type="button"
                        className={`world-start-point-btn ${isSelected ? 'is-selected' : ''}`}
                        disabled={disabled || submitting}
                        onClick={() => applyStartOptionFields(sp, bootstrap.world_name)}
                        data-testid={`world-start-point-option-${sp.start_id}`}
                      >
                        <div>
                          <span
                            className="world-start-origin-tag"
                            data-origin={sp.origin}
                          >
                            {WORLD_START_ORIGIN_LABELS[sp.origin]}
                          </span>
                          <strong>{sp.label}</strong>
                        </div>
                        <span className="muted" style={{ fontSize: '0.8125rem' }}>
                          地点：{sp.location} · 角色：{sp.character_name}（{sp.character_role}） · 时刻：{sp.starts_at.replace('T', ' ')}
                        </span>
                        <span style={{ fontSize: '0.85rem' }}>{sp.summary}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}

            <div className="world-start-form-grid">
              <label className="world-start-field">
                <span>旅程名称</span>
                <input
                  aria-label="旅程名称"
                  value={adventureName}
                  maxLength={80}
                  disabled={disabled || submitting}
                  onChange={e => setAdventureName(e.target.value)}
                  data-testid="world-start-adventure-name-input"
                />
              </label>
              <label className="world-start-field">
                <span>起始地点</span>
                <input
                  aria-label="起始地点"
                  value={location}
                  maxLength={80}
                  disabled={disabled || submitting}
                  onChange={e => setLocation(e.target.value)}
                  data-testid="world-start-location-input"
                />
              </label>
              <label className="world-start-field">
                <span>角色姓名</span>
                <input
                  aria-label="角色姓名"
                  value={characterName}
                  maxLength={80}
                  disabled={disabled || submitting}
                  onChange={e => setCharacterName(e.target.value)}
                  data-testid="world-start-character-name-input"
                />
              </label>
              <label className="world-start-field">
                <span>开局时间</span>
                <input
                  aria-label="开局时间"
                  value={startsAt}
                  maxLength={40}
                  disabled={disabled || submitting}
                  onChange={e => setStartsAt(e.target.value)}
                  data-testid="world-start-starts-at-input"
                />
              </label>
              <label className="world-start-field">
                <span>运行模式（沿用现有模型设置）</span>
                <select
                  aria-label="开局运行模式"
                  value={mode}
                  disabled={disabled || submitting}
                  onChange={e => setMode(e.target.value as 'fake' | 'live')}
                  data-testid="world-start-mode-select"
                >
                  <option value="fake">模拟模式（免 API Key）</option>
                  <option value="live">真实模型模式（复用已连接会话）</option>
                </select>
              </label>
            </div>

            {mode === 'live' && !sessionId && onConfigureModel && (
              <div className="world-start-actions-row">
                <button
                  type="button"
                  className="secondary world-start-touch-btn"
                  onClick={onConfigureModel}
                  data-testid="world-start-configure-model-btn"
                >
                  前往连接模型 API 会话
                </button>
              </div>
            )}

            <div className="world-start-actions-row">
              {phase === 'analyzing' ? (
                <button
                  type="button"
                  className="secondary world-start-touch-btn"
                  onClick={handleCancelAnalyzing}
                  data-testid="world-start-cancel-analyze-btn"
                >
                  正在分析初始局势… 点击取消等待
                </button>
              ) : (
                <button
                  type="button"
                  className="primary world-start-touch-btn"
                  disabled={disabled || submitting || !materialSelection.has_usable_materials}
                  onClick={() => void handleAnalyzeStart()}
                  data-testid="world-start-analyze-btn"
                >
                  启动 AI 开局分析
                </button>
              )}

              <button
                type="button"
                className="secondary world-start-touch-btn"
                disabled={disabled || submitting}
                onClick={() => void handleQuickStartAdventure()}
                data-testid="world-start-quick-adventure-btn"
              >
                普通冒险先行（跳过 AI 分析）
              </button>
            </div>
          </div>
        </>
      )}

      {/* 4. 草案摘要区（仅展示玩家应知内容，确认前绝不写入正式账本） */}
      {draft && phase === 'ready_to_confirm' && (
        <article
          className="world-start-draft-card"
          aria-label="玩家应知的开局局势草案摘要"
          data-testid="world-start-draft-card"
        >
          <div className="world-start-section-header">
            <strong>
              玩家应知的开局摘要 · {WORLD_START_RUN_KIND_LABELS[draft.run_kind]}（第 {draft.attempt_number} 次生成）
            </strong>
            <span className="tag" data-testid="world-start-draft-uncommitted-tag">
              待确认草案 · 尚未写入正式事件/人物/知识
            </span>
          </div>

          <p className="muted" style={{ fontSize: '0.8125rem', marginBottom: 8 }}>
            起点：{draft.start_point.location} · 角色：{draft.start_point.character_name} · 时刻：
            {draft.start_point.starts_at.replace('T', ' ')} · 分 {draft.batch_info.total_batches} 批提取合并（共{' '}
            {draft.batch_info.merged_chunks} 个切片）
          </p>

          <p
            className="world-start-summary-card"
            data-testid="world-start-opening-summary"
          >
            {draft.opening_situation_summary}
          </p>

          {/* 玩家已知地点 */}
          {draft.known_places.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <strong>你已知晓的地点（{draft.known_places.length} 处）</strong>
              <ul className="world-start-summary-list" data-testid="world-start-known-places">
                {draft.known_places.map(pl => (
                  <li key={pl.place_id} className="world-start-summary-card">
                    <span className="world-start-origin-tag" data-origin={pl.origin}>
                      {WORLD_START_ORIGIN_LABELS[pl.origin]}
                    </span>
                    <strong>{pl.name}</strong>：{pl.summary}
                    {pl.source_refs.length > 0 && (
                      <div style={{ marginTop: 4 }}>
                        {pl.source_refs.map(ref => (
                          <button
                            key={ref.source_id}
                            type="button"
                            className="text-button"
                            onClick={() => onOpenSource?.(ref)}
                            data-testid={`world-start-ref-${ref.source_id}`}
                          >
                            引用：{ref.title}
                          </button>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 玩家应知的在场或已认识人物（不含后台 NPC 私事） */}
          {draft.known_contacts.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <strong>在场与已知人物（{draft.known_contacts.length} 位 · 仅显示你应知的公开信息）</strong>
              <ul className="world-start-summary-list" data-testid="world-start-known-contacts">
                {draft.known_contacts.map(npc => (
                  <li key={npc.npc_id} className="world-start-summary-card">
                    <span className="world-start-origin-tag" data-origin={npc.origin}>
                      {WORLD_START_ORIGIN_LABELS[npc.origin]}
                    </span>
                    <strong>
                      {npc.name}（{npc.role} · {npc.location}）
                    </strong>
                    <p style={{ margin: '4px 0 0' }}>{npc.public_impression}</p>
                    <p className="muted" style={{ fontSize: '0.8125rem', margin: '2px 0 0' }}>
                      已知关系：{npc.known_relation}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 玩家开局有渠道获知的消息与传闻（不含未获知后台事件） */}
          {draft.player_known_facts.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <strong>开局已获知的消息与传闻（{draft.player_known_facts.length} 条）</strong>
              <ul className="world-start-summary-list" data-testid="world-start-known-facts">
                {draft.player_known_facts.map(fact => (
                  <li key={fact.fact_id} className="world-start-summary-card">
                    <span className="world-start-origin-tag" data-origin={fact.origin}>
                      {WORLD_START_ORIGIN_LABELS[fact.origin]}
                    </span>
                    <span className="tag" style={{ marginRight: 6 }}>
                      {KNOWLEDGE_TYPE_LABELS[fact.knowledge_type]}
                    </span>
                    <strong>{fact.title}</strong>：{fact.content}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 资料空白处的 AI 推演补写说明 */}
          {draft.inferred_notes.length > 0 && (
            <div style={{ marginTop: 12 }} data-testid="world-start-inferred-notes">
              <strong>旅程推演补写记录：</strong>
              <ul className="world-start-summary-list">
                {draft.inferred_notes.map((note, idx) => (
                  <li key={idx} className="world-start-summary-card">
                    <span className="world-start-origin-tag" data-origin="inferred">
                      {WORLD_START_ORIGIN_LABELS.inferred}
                    </span>
                    {note}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 已有设定冲突保留与提示 */}
          {draft.conflict_warnings.length > 0 && (
            <div
              className="world-start-conflict-box"
              role="note"
              data-testid="world-start-conflict-warnings"
            >
              <strong>原始设定冲突提示：</strong>
              {draft.conflict_warnings.map(cw => (
                <div key={cw.conflict_id} style={{ marginTop: 4 }}>
                  <p style={{ margin: 0 }}>{cw.summary}</p>
                  <p className="muted" style={{ margin: 0 }}>
                    {cw.resolution_hint}
                  </p>
                </div>
              ))}
            </div>
          )}

          <div className="world-start-actions-row">
            <button
              type="button"
              className="primary world-start-touch-btn"
              disabled={disabled || submitting}
              onClick={() => void handleConfirmDraft()}
              data-testid="world-start-confirm-btn"
            >
              {submitting ? '正在建立旅程…' : '确认进入旅程'}
            </button>
            <button
              type="button"
              className="secondary world-start-touch-btn"
              disabled={disabled || submitting}
              onClick={() => void handleAnalyzeStart('retry')}
              data-testid="world-start-reanalyze-btn"
            >
              重新生成分析（重试）
            </button>
            <button
              type="button"
              className="text-button world-start-touch-btn"
              disabled={disabled || submitting}
              onClick={() => void handleDiscardDraft()}
              data-testid="world-start-discard-btn"
            >
              放弃草案，返回修改起点
            </button>
          </div>
        </article>
      )}

      {/* 5. 已应用确认结果区（返回旅程后继续自由输入与消息查证） */}
      {appliedResult && phase === 'applied' && (
        <article
          className="world-start-applied-card"
          aria-label="已建立的旅程开局状态"
          data-testid="world-start-applied-card"
        >
          <div className="world-start-section-header">
            <strong>
              已进入旅程：{appliedResult.adventure_name}（{WORLD_START_RUN_KIND_LABELS[appliedResult.run_kind]}）
            </strong>
            <span className="tag" data-testid="world-start-instance-badge">
              实例 {appliedResult.world_instance_id} · 已绑定 {appliedResult.bound_source_ids.length} 份资料
            </span>
          </div>

          <p
            className="world-start-summary-card"
            data-testid="world-start-applied-narrative"
          >
            {appliedResult.opening_narrative}
          </p>

          <p className="muted" style={{ fontSize: '0.8125rem', marginTop: 6 }}>
            角色：{appliedResult.player_character.name} · 当前位置：{appliedResult.player_character.location} ·
            起始时间：{appliedResult.starts_at.replace('T', ' ')}
          </p>

          {appliedResult.suggested_actions.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <span className="muted" style={{ fontSize: '0.8125rem' }}>
                点击下方开局行动灵感可填入行动编辑框（你也可以直接在下方编辑框自由输入任意行动）：
              </span>
              <div className="world-start-actions-row">
                {appliedResult.suggested_actions.map((act, idx) => (
                  <button
                    key={idx}
                    type="button"
                    className="secondary world-start-touch-btn"
                    disabled={disabled}
                    onClick={() => handleWriteSuggestionToComposer(act)}
                    data-testid={`world-start-suggested-action-${idx}`}
                  >
                    {act}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="world-start-actions-row" style={{ marginTop: 12 }}>
            <button
              type="button"
              className="secondary world-start-touch-btn"
              disabled={disabled || submitting}
              onClick={() => {
                setAppliedResult(null);
                setDraft(null);
                setRunKind('reopen');
                setPhase(
                  materialSelection.has_usable_materials ? 'waiting' : 'insufficient_materials',
                );
              }}
              data-testid="world-start-reopen-btn"
            >
              重新开局 (Reopen)
            </button>
            <button
              type="button"
              className="text-button world-start-touch-btn"
              disabled={disabled || submitting}
              onClick={() => {
                setAppliedResult(null);
                setDraft(null);
                setRunKind('branch');
                setPhase(
                  materialSelection.has_usable_materials ? 'waiting' : 'insufficient_materials',
                );
              }}
              data-testid="world-start-branch-btn"
            >
              建立新分支开局 (Branch)
            </button>
          </div>
        </article>
      )}
    </section>
  );
}

export interface WorldStartJourneyHostProps {
  worldId: string;
  worldName?: string;
  startTransport?: WorldStartTransport;
  encounterTransport?: WorldEncountersTransport;
  initialAdventureId?: string;
  mode?: 'fake' | 'live';
  sessionId?: string | null;
  modelName?: string | null;
  onActionSubmitted?: (actionText: string, adventureId: string) => void;
}

/**
 * 提供给宿主的最小挂载桥接容器（WorldStartJourneyHost）：
 * 验证并确保：
 * 1. 导入资料后选择起点 -> AI 分析 -> 查看玩家应知摘要 -> 确认进入旅程（或普通冒险先行）；
 * 2. 返回旅程后可继续在编辑框自由输入任意行动；
 * 3. 已有的「世界消息主动查证（WorldEncounterPanel）」点击后仍能填入同一个行动编辑框供玩家修改发送。
 */
export function WorldStartJourneyHost({
  worldId,
  worldName,
  startTransport = worldStartApi,
  encounterTransport,
  initialAdventureId = '',
  mode,
  sessionId = null,
  modelName = null,
  onActionSubmitted,
}: WorldStartJourneyHostProps) {
  const [activeAdventureId, setActiveAdventureId] = useState<string>(initialAdventureId);
  const [composerDraft, setComposerDraft] = useState<string>('');
  const [composerExpanded, setComposerExpanded] = useState<boolean>(true);
  const [submittedHistory, setSubmittedHistory] = useState<string[]>([]);

  useEffect(() => {
    setActiveAdventureId(initialAdventureId);
    setComposerDraft('');
    setSubmittedHistory([]);
  }, [worldId, initialAdventureId]);

  const handleSubmitFreeAction = () => {
    const trimmed = composerDraft.trim();
    if (!trimmed) return;
    setSubmittedHistory(prev => [...prev, trimmed]);
    onActionSubmitted?.(trimmed, activeAdventureId);
    setComposerDraft('');
  };

  return (
    <div className="world-start-host-container" data-testid="world-start-journey-host">
      <WorldStartPanel
        worldId={worldId}
        worldName={worldName}
        transport={startTransport}
        mode={mode}
        sessionId={sessionId}
        modelName={modelName}
        composerDraft={composerDraft}
        onComposerDraftChange={setComposerDraft}
        onExpandComposer={setComposerExpanded}
        onJourneyStarted={res => {
          setActiveAdventureId(res.adventure_id);
          setComposerExpanded(true);
        }}
      />

      {activeAdventureId && (
        <section
          className="world-start-section"
          aria-label="旅程自由行动与消息查证区"
          data-testid="host-active-journey-workspace"
        >
          <div className="world-start-section-header">
            <h4 className="world-start-section-title">当前旅程自由行动输入</h4>
            <span className="tag" data-testid="host-active-adventure-id">
              旅程 ID：{activeAdventureId}
            </span>
          </div>

          {composerExpanded && (
            <div>
              <label htmlFor={`host-action-editor-${worldId}`}>你的行动（可自由输入或由消息查证填入）</label>
              <textarea
                id={`host-action-editor-${worldId}`}
                data-testid="host-free-action-textarea"
                className="world-encounter-textarea"
                rows={3}
                maxLength={ACTION_LIMIT}
                value={composerDraft}
                placeholder="写下你想做的事，或点击下方世界消息查证建议填入此处……"
                onChange={e => setComposerDraft(e.target.value)}
              />
              <div className="world-start-actions-row">
                <button
                  type="button"
                  className="primary world-start-touch-btn"
                  disabled={!composerDraft.trim()}
                  onClick={handleSubmitFreeAction}
                  data-testid="host-send-free-action-btn"
                >
                  发送自由行动
                </button>
              </div>
            </div>
          )}

          {submittedHistory.length > 0 && (
            <ul data-testid="host-submitted-actions-list">
              {submittedHistory.map((item, idx) => (
                <li key={idx}>{item}</li>
              ))}
            </ul>
          )}

          {encounterTransport && (
            <WorldEncounterPanel
              adventureId={activeAdventureId}
              transport={encounterTransport}
              composerDraft={composerDraft}
              onComposerDraftChange={next => setComposerDraft(next)}
              onExpandComposer={open => setComposerExpanded(open)}
            />
          )}
        </section>
      )}
    </div>
  );
}
