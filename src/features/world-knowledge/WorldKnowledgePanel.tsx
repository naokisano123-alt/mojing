import { useEffect, useRef, useState } from 'react';
import { ApiClientError } from '../../api/client';
import {
  CHANNEL_LABELS,
  KNOWLEDGE_TYPE_LABELS,
  filterNewsItems,
  formatConfidenceLevel,
  formatLearnedTime,
  mergeNewsItems,
  worldKnowledgeApi,
} from './api';
import type {
  KnowledgeType,
  WorldKnowledgeTransport,
  WorldNewsItem,
} from './types';
import './world-knowledge.css';

export interface WorldKnowledgePanelProps {
  adventureId: string;
  startsAt?: string;
  transport?: WorldKnowledgeTransport;
  disabled?: boolean;
  defaultOpen?: boolean;
  pageSize?: number;
}

const FILTER_OPTIONS: ReadonlyArray<{ value: KnowledgeType | 'all'; label: string }> = [
  { value: 'all', label: '全部已获知' },
  { value: 'confirmed', label: '确认事实' },
  { value: 'rumor', label: '市井传闻' },
  { value: 'suspected', label: '怀疑推测' },
  { value: 'mistaken', label: '误信传言' },
];

export function WorldKnowledgePanel({
  adventureId,
  startsAt,
  transport = worldKnowledgeApi,
  disabled = false,
  defaultOpen = true,
  pageSize = 30,
}: WorldKnowledgePanelProps) {
  const [items, setItems] = useState<WorldNewsItem[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [filter, setFilter] = useState<KnowledgeType | 'all'>('all');
  const [loading, setLoading] = useState<boolean>(true);
  const [loadingMore, setLoadingMore] = useState<boolean>(false);
  const [error, setError] = useState<string>('');

  const epochRef = useRef<number>(0);
  const activeControllerRef = useRef<AbortController | null>(null);

  const loadInitial = async (targetAdventureId: string) => {
    const generation = ++epochRef.current;
    activeControllerRef.current?.abort();
    const controller = new AbortController();
    activeControllerRef.current = controller;

    setLoading(true);
    setLoadingMore(false);
    setError('');

    try {
      const page = await transport.listWorldNews(targetAdventureId, {
        limit: pageSize,
        signal: controller.signal,
      });
      if (epochRef.current !== generation || controller.signal.aborted) return;
      setItems(Array.isArray(page.items) ? page.items : []);
      setNextBefore(page.next_before ?? null);
    } catch (err: unknown) {
      if (epochRef.current !== generation || controller.signal.aborted) return;
      const message =
        err instanceof ApiClientError
          ? err.friendlyMessage
          : err instanceof Error && err.message
            ? err.message
            : '消息读取失败，请稍后重试。';
      setError(message);
    } finally {
      if (epochRef.current === generation && !controller.signal.aborted) {
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    setItems([]);
    setNextBefore(null);
    setFilter('all');
    if (!adventureId.trim()) {
      setLoading(false);
      return;
    }
    void loadInitial(adventureId);
    return () => {
      epochRef.current += 1;
      activeControllerRef.current?.abort();
      activeControllerRef.current = null;
    };
  }, [adventureId, transport, pageSize]);

  const handleLoadMore = async () => {
    if (!nextBefore || loading || loadingMore || disabled) return;
    const generation = epochRef.current;
    const controller = new AbortController();
    activeControllerRef.current = controller;
    setLoadingMore(true);
    setError('');

    try {
      const page = await transport.listWorldNews(adventureId, {
        before: nextBefore,
        limit: pageSize,
        signal: controller.signal,
      });
      if (epochRef.current !== generation || controller.signal.aborted) return;
      setItems(prev => mergeNewsItems(prev, Array.isArray(page.items) ? page.items : []));
      setNextBefore(page.next_before ?? null);
    } catch (err: unknown) {
      if (epochRef.current !== generation || controller.signal.aborted) return;
      const message =
        err instanceof ApiClientError
          ? err.friendlyMessage
          : err instanceof Error && err.message
            ? err.message
            : '更多消息读取失败，请重试。';
      setError(message);
    } finally {
      if (epochRef.current === generation && !controller.signal.aborted) {
        setLoadingMore(false);
      }
    }
  };

  const visibleItems = filterNewsItems(items, filter);

  return (
    <details
      open={defaultOpen}
      className="world-knowledge-panel"
      aria-label="世界消息与传闻"
      data-viewport="mobile-ready"
    >
      <summary className="world-knowledge-summary">
        <span>世界消息与传闻（已获知 {items.length} 条）</span>
      </summary>

      <div className="world-knowledge-header">
        <p className="world-knowledge-hint">
          仅显示当前角色通过亲历、公告、交谈或传闻已获知的消息；未获知的世界动态不会在此显示。
        </p>
        <button
          type="button"
          className="text-button"
          disabled={disabled || loading || loadingMore}
          onClick={() => void loadInitial(adventureId)}
        >
          刷新消息
        </button>
      </div>

      {items.length > 0 && (
        <div className="world-knowledge-filters" role="group" aria-label="按认知类型筛选">
          {FILTER_OPTIONS.map(option => (
            <button
              key={option.value}
              type="button"
              className={`world-knowledge-filter-btn ${filter === option.value ? 'is-selected' : ''}`}
              aria-pressed={filter === option.value}
              disabled={disabled || loading}
              onClick={() => setFilter(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}

      {loading && (
        <p role="status" className="muted">
          正在读取已知消息…
        </p>
      )}

      {error && (
        <div role="alert" className="error">
          <p>{error}</p>
          <button
            type="button"
            className="secondary"
            disabled={disabled || loading || loadingMore}
            onClick={() => void (nextBefore && items.length > 0 ? handleLoadMore() : loadInitial(adventureId))}
          >
            重试读取
          </button>
        </div>
      )}

      {!loading && !error && items.length === 0 && (
        <div className="world-knowledge-empty" data-testid="world-knowledge-empty">
          <p>当前尚未通过任何渠道获知外界消息。</p>
          <p>可通过现场观察、查看城镇公告、与人物交谈或打听传闻获取新消息。</p>
        </div>
      )}

      {!loading && items.length > 0 && visibleItems.length === 0 && (
        <div className="world-knowledge-empty">
          <p>当前筛选分类下暂无已获知的消息。</p>
        </div>
      )}

      {visibleItems.length > 0 && (
        <ul className="world-knowledge-list" aria-label="已知消息列表">
          {visibleItems.map(item => {
            const typeLabel = KNOWLEDGE_TYPE_LABELS[item.knowledge_type] ?? '已知消息';
            const channelLabel = CHANNEL_LABELS[item.channel] ?? '未知渠道';
            const sourceText = item.source_label || channelLabel;
            const confidenceText = formatConfidenceLevel(item.confidence, item.knowledge_type);
            const learnedTimeText = formatLearnedTime(item.learned_minute, startsAt);

            return (
              <li
                key={item.fact_id}
                className="world-knowledge-card"
                data-knowledge-type={item.knowledge_type}
                data-channel={item.channel}
              >
                <div className="world-knowledge-meta">
                  <span className="world-knowledge-badge" data-type={item.knowledge_type}>
                    {typeLabel}
                  </span>
                  <span>来源：{sourceText}</span>
                  <span>{confidenceText}</span>
                  <span>获知于 {learnedTimeText}</span>
                  {item.revision > 1 && <span>已更正（第 {item.revision} 版）</span>}
                </div>
                <h4 className="world-knowledge-title">{item.title}</h4>
                <p className="world-knowledge-content">{item.content}</p>
              </li>
            );
          })}
        </ul>
      )}

      {nextBefore && (
        <div className="world-knowledge-actions">
          <button
            type="button"
            className="secondary full"
            disabled={disabled || loading || loadingMore}
            onClick={() => void handleLoadMore()}
          >
            {loadingMore ? '正在加载更早消息…' : '加载更早消息'}
          </button>
        </div>
      )}
    </details>
  );
}
