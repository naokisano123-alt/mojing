import { useEffect, useRef, useState } from 'react';
import { ApiClientError } from '../../api/client.ts';
import { ACTION_LIMIT } from '../../interaction.ts';
import {
  CHANNEL_LABELS,
  KNOWLEDGE_TYPE_LABELS,
  OPTION_KIND_LABELS,
  OUTCOME_LABELS,
  applyEncounterOptionToComposerDraft,
  sanitizeEncounterEntrypointBundle,
  worldEncountersApi,
} from './api.ts';
import type {
  EncounterActionOption,
  EncounterCommitResultDto,
  EncounterDraftDto,
  EncounterNewsItem,
  WorldEncounterEntrypointBundle,
  WorldEncountersTransport,
} from './types.ts';
import './world-encounters.css';

export interface WorldEncounterPanelProps {
  adventureId: string;
  transport?: WorldEncountersTransport;
  disabled?: boolean;
  defaultOpen?: boolean;
  composerDraft?: string;
  onComposerDraftChange?: (
    nextDraft: string,
    meta: { option: EncounterActionOption; newsItem: EncounterNewsItem },
  ) => void;
  onExpandComposer?: (expanded: boolean) => void;
  onTurnCommitted?: (result: EncounterCommitResultDto) => void;
  refreshTrigger?: number | string;
}

export function WorldEncounterPanel({
  adventureId,
  transport = worldEncountersApi,
  disabled = false,
  defaultOpen = true,
  composerDraft,
  onComposerDraftChange,
  onExpandComposer,
  onTurnCommitted,
  refreshTrigger,
}: WorldEncounterPanelProps) {
  const [bundle, setBundle] = useState<WorldEncounterEntrypointBundle | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string>('');
  const [composerNotice, setComposerNotice] = useState<string>('');

  const [selectedFactId, setSelectedFactId] = useState<string>('');
  const [selectedOption, setSelectedOption] = useState<EncounterActionOption | null>(null);
  const [localDraftText, setLocalDraftText] = useState<string>('');
  const [activeDraft, setActiveDraft] = useState<EncounterDraftDto | null>(null);

  const epochRef = useRef<number>(0);
  const activeControllerRef = useRef<AbortController | null>(null);
  const lastAdventureIdRef = useRef<string>(adventureId);

  const effectiveDraftText = composerDraft !== undefined ? composerDraft : localDraftText;

  const updateDraftText = (
    nextText: string,
    meta?: { option: EncounterActionOption; newsItem: EncounterNewsItem },
  ) => {
    setLocalDraftText(nextText);
    if (meta && onComposerDraftChange) {
      onComposerDraftChange(nextText, meta);
    }
    if (onExpandComposer) {
      onExpandComposer(true);
    }
  };

  const loadEntrypoint = async (targetAdventureId: string) => {
    const generation = ++epochRef.current;
    activeControllerRef.current?.abort();
    const controller = new AbortController();
    activeControllerRef.current = controller;

    setLoading(true);
    setError('');

    try {
      const raw = await transport.getEntrypointBundle(targetAdventureId, {
        signal: controller.signal,
      });
      if (epochRef.current !== generation || controller.signal.aborted) return;
      const clean = sanitizeEncounterEntrypointBundle(raw);
      setBundle(clean);
      if (clean.news_items.length > 0 && !selectedFactId) {
        setSelectedFactId(clean.news_items[0].fact_id);
      }
    } catch (err: unknown) {
      if (epochRef.current !== generation || controller.signal.aborted) return;
      const msg =
        err instanceof ApiClientError
          ? err.friendlyMessage
          : err instanceof Error && err.message
            ? err.message
            : '读取世界消息行动入口失败，请稍后重试。';
      setError(msg);
    } finally {
      if (epochRef.current === generation && !controller.signal.aborted) {
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    const switched = lastAdventureIdRef.current !== adventureId;
    lastAdventureIdRef.current = adventureId;
    if (switched) {
      setBundle(null);
      setSelectedFactId('');
      setSelectedOption(null);
      setLocalDraftText('');
      setActiveDraft(null);
      setComposerNotice('');
      setError('');
    }
    if (!adventureId.trim()) {
      setBundle(null);
      setLoading(false);
      return;
    }
    void loadEntrypoint(adventureId);
    return () => {
      epochRef.current += 1;
      activeControllerRef.current?.abort();
      activeControllerRef.current = null;
    };
  }, [adventureId, transport, refreshTrigger]);

  const handleSelectOption = (newsItem: EncounterNewsItem, option: EncounterActionOption) => {
    if (disabled || submitting) return;
    setSelectedFactId(newsItem.fact_id);
    setSelectedOption(option);
    setActiveDraft(null);
    setError('');

    if (!option.contactable) {
      // 人物不在场时也允许将查证意图写入编辑框以发起寻访草案，同时给出明确提示
      const fallbackText = option.suggested_action_text;
      setComposerNotice(option.disabled_reason || '该人物当前不在场或已动身离开。');
      updateDraftText(fallbackText, { option, newsItem });
      return;
    }

    const applied = applyEncounterOptionToComposerDraft(
      effectiveDraftText,
      option,
      ACTION_LIMIT,
    );
    if (applied.error || applied.nextDraft === null) {
      setComposerNotice(applied.error || '草稿空间不足。');
      return;
    }
    setComposerNotice('已将查证建议写入行动输入区，可自由编辑后生成草案（当前未推进时间）。');
    updateDraftText(applied.nextDraft, { option, newsItem });
  };

  const handlePrepareDraft = async () => {
    if (!bundle || !selectedOption || !effectiveDraftText.trim() || disabled || submitting) {
      return;
    }
    const generation = epochRef.current;
    const controller = new AbortController();
    activeControllerRef.current = controller;

    setSubmitting(true);
    setError('');

    try {
      const draftRes = await transport.prepareDraft(
        adventureId,
        {
          fact_id: selectedOption.fact_id,
          option_kind: selectedOption.kind,
          target_npc_id: selectedOption.target_npc_id ?? null,
          action_text: effectiveDraftText.trim(),
          player_location: bundle.player_location,
        },
        { signal: controller.signal },
      );
      if (epochRef.current !== generation || controller.signal.aborted) return;
      setActiveDraft(draftRes);
    } catch (err: unknown) {
      if (epochRef.current !== generation || controller.signal.aborted) return;
      const msg =
        err instanceof ApiClientError
          ? err.friendlyMessage
          : err instanceof Error && err.message
            ? err.message
            : '生成查证草案失败，请重试。';
      setError(msg);
    } finally {
      if (epochRef.current === generation && !controller.signal.aborted) {
        setSubmitting(false);
      }
    }
  };

  const handleCommitDraft = async () => {
    if (!activeDraft || disabled || submitting) return;
    const generation = epochRef.current;
    const controller = new AbortController();
    activeControllerRef.current = controller;

    setSubmitting(true);
    setError('');

    try {
      const commitRes = await transport.commitDraft(adventureId, activeDraft.draft_id, {
        signal: controller.signal,
      });
      if (epochRef.current !== generation || controller.signal.aborted) return;
      setActiveDraft(null);
      setSelectedOption(null);
      setLocalDraftText('');
      setComposerNotice('回合已确认，新获知的线索已记入世界消息。');
      setBundle(prev =>
        prev
          ? {
              ...prev,
              world_minute: commitRes.world_minute,
              world_revision: commitRes.world_revision,
              news_items: commitRes.all_player_news,
            }
          : prev,
      );
      if (onTurnCommitted) {
        onTurnCommitted(commitRes);
      }
    } catch (err: unknown) {
      if (epochRef.current !== generation || controller.signal.aborted) return;
      const msg =
        err instanceof ApiClientError
          ? err.friendlyMessage
          : err instanceof Error && err.message
            ? err.message
            : '确认查证回合失败，请重试。';
      setError(msg);
    } finally {
      if (epochRef.current === generation && !controller.signal.aborted) {
        setSubmitting(false);
      }
    }
  };

  const newsItems = bundle?.news_items ?? [];

  return (
    <details
      open={defaultOpen}
      className="world-encounter-panel"
      aria-label="世界消息行动入口"
      data-viewport="mobile-ready"
    >
      <summary className="world-encounter-summary">
        <span>世界消息与主动查证（已获知 {newsItems.length} 条）</span>
        {bundle && (
          <span className="world-encounter-clock" data-testid="encounter-world-minute">
            当前地点：{bundle.player_location} · 第 {bundle.world_minute} 分钟
          </span>
        )}
      </summary>

      <div className="world-encounter-header">
        <p className="world-encounter-hint">
          点选传闻下方的建议可将询问或查证行动写入输入区；选项本身不会推进游戏时间或自动确认回合。
        </p>
        <button
          type="button"
          className="text-button world-encounter-touch-btn"
          disabled={disabled || loading || submitting}
          onClick={() => void loadEntrypoint(adventureId)}
        >
          刷新消息与人物
        </button>
      </div>

      {loading && (
        <p role="status" className="muted">
          正在读取可查证消息与在场人物…
        </p>
      )}

      {error && (
        <div role="alert" className="error" data-testid="encounter-error-alert">
          <p>{error}</p>
        </div>
      )}

      {composerNotice && (
        <p role="status" className="world-encounter-notice" data-testid="encounter-composer-notice">
          {composerNotice}
        </p>
      )}

      {!loading && newsItems.length === 0 && (
        <div className="world-encounter-empty" data-testid="encounter-empty-state">
          <p>当前尚未获知任何可追查的外界传闻或消息。</p>
        </div>
      )}

      {newsItems.length > 0 && (
        <ul className="world-encounter-news-list" aria-label="可发起查证的世界消息列表">
          {newsItems.map(item => {
            const typeLabel = KNOWLEDGE_TYPE_LABELS[item.knowledge_type] ?? '已知消息';
            const channelLabel = CHANNEL_LABELS[item.channel] ?? '未知渠道';
            const itemOptions = bundle?.options_by_fact_id[item.fact_id] ?? [];

            return (
              <li
                key={item.fact_id}
                className="world-encounter-card"
                data-testid={`encounter-news-card-${item.fact_id}`}
                data-knowledge-type={item.knowledge_type}
              >
                <div className="world-encounter-meta">
                  <span className="world-encounter-badge" data-type={item.knowledge_type}>
                    {typeLabel}
                  </span>
                  <span>来源：{item.source_label || channelLabel}</span>
                  <span>第 {item.learned_minute} 分钟获知</span>
                </div>

                <h4 className="world-encounter-title">{item.title}</h4>
                <p className="world-encounter-content">{item.content}</p>

                {itemOptions.length > 0 && (
                  <details
                    open
                    className="world-encounter-options-drawer"
                    data-testid={`encounter-options-drawer-${item.fact_id}`}
                  >
                    <summary className="world-encounter-options-summary">
                      从此消息发起查证或交谈（{itemOptions.length} 项建议 · 点击写入输入区）
                    </summary>
                    <div
                      className="world-encounter-options-grid"
                      role="group"
                      aria-label={`「${item.title}」的查证建议`}
                    >
                      {itemOptions.map(opt => (
                        <button
                          key={opt.option_id}
                          type="button"
                          data-testid={`encounter-option-${opt.option_id}`}
                          data-option-kind={opt.kind}
                          data-contactable={String(opt.contactable)}
                          className={`world-encounter-option-btn world-encounter-touch-btn ${
                            selectedOption?.option_id === opt.option_id ? 'is-selected' : ''
                          } ${!opt.contactable ? 'is-unavailable' : ''}`}
                          disabled={disabled || submitting}
                          onClick={() => handleSelectOption(item, opt)}
                        >
                          <span className="option-kind-tag">{OPTION_KIND_LABELS[opt.kind]}</span>
                          <strong className="option-main-label">{opt.label}</strong>
                          <span className="option-sub-desc">{opt.description}</span>
                        </button>
                      ))}
                    </div>
                  </details>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {selectedOption && (
        <div className="world-encounter-composer-box" data-testid="encounter-composer-box">
          <div className="world-encounter-composer-header">
            <label htmlFor={`encounter-editor-${adventureId}`}>
              编辑查证行动（已选：{selectedOption.label} · 发送前可自由修改）
            </label>
          </div>
          <textarea
            id={`encounter-editor-${adventureId}`}
            data-testid="encounter-action-editor"
            className="world-encounter-textarea"
            rows={3}
            maxLength={ACTION_LIMIT}
            value={effectiveDraftText}
            disabled={disabled || submitting}
            onChange={e => {
              setLocalDraftText(e.target.value);
              if (onComposerDraftChange && bundle) {
                const newsItem =
                  bundle.news_items.find(n => n.fact_id === selectedOption.fact_id) ??
                  bundle.news_items[0];
                if (newsItem) {
                  onComposerDraftChange(e.target.value, { option: selectedOption, newsItem });
                }
              }
            }}
          />
          <div className="world-encounter-composer-actions">
            <button
              type="button"
              className="primary world-encounter-touch-btn"
              data-testid="encounter-prepare-draft-btn"
              disabled={disabled || submitting || !effectiveDraftText.trim()}
              onClick={() => void handlePrepareDraft()}
            >
              {submitting ? '正在推演回应…' : '生成查证草案'}
            </button>
          </div>
        </div>
      )}

      {activeDraft && (
        <article
          className="world-encounter-draft-card"
          data-testid="encounter-draft-card"
          data-outcome={activeDraft.outcome}
        >
          <div className="world-encounter-draft-header">
            <strong>
              待确认查证草案 · {OUTCOME_LABELS[activeDraft.outcome] ?? activeDraft.outcome_label}
            </strong>
            <span className="muted">尚未写入回合 · 未直接结算金钱或背包</span>
          </div>
          <p className="world-encounter-draft-action">你的行动：{activeDraft.action_text}</p>
          <p className="world-encounter-draft-reply" data-testid="encounter-draft-reply">
            {activeDraft.narrative_reply}
          </p>

          {activeDraft.conflict_note && (
            <div
              role="note"
              className="world-encounter-conflict-box"
              data-testid="encounter-conflict-note"
            >
              <strong>说法冲突提示：</strong>
              <span>{activeDraft.conflict_note}</span>
            </div>
          )}

          {activeDraft.proposed_transmissions.length > 0 && (
            <div
              className="world-encounter-transmissions-preview"
              data-testid="encounter-transmissions-preview"
            >
              <strong>确认后将获知的消息提议：</strong>
              <ul>
                {activeDraft.proposed_transmissions.map((tx, idx) => (
                  <li key={`${tx.title}-${idx}`}>
                    <span className="world-encounter-badge" data-type={tx.knowledge_type}>
                      {KNOWLEDGE_TYPE_LABELS[tx.knowledge_type]}
                    </span>{' '}
                    <strong>{tx.title}</strong>：{tx.content}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="world-encounter-draft-actions">
            <button
              type="button"
              className="primary world-encounter-touch-btn"
              data-testid="encounter-commit-draft-btn"
              disabled={disabled || submitting}
              onClick={() => void handleCommitDraft()}
            >
              确认并写入回合
            </button>
            <button
              type="button"
              className="text-button world-encounter-touch-btn"
              data-testid="encounter-discard-draft-btn"
              disabled={disabled || submitting}
              onClick={() => setActiveDraft(null)}
            >
              暂不采用，继续编辑
            </button>
          </div>
        </article>
      )}
    </details>
  );
}
