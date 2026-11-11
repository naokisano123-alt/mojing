import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Search,
  X,
  FileText,
  ChevronRight,
  AlertCircle,
  Loader2,
  ChevronLeft,
  ExternalLink,
  Sparkles,
} from 'lucide-react';
import type {
  LoreSearchPanelProps,
  LoreSearchResult,
  LoreSearchItem,
} from './types';
import './lore-search.css';
import { toLoreSourceTarget } from './sourceTarget';

const DEFAULT_QUICK_KEYWORDS = ['人物', '陆平', '旧街', '红枫', '码头', '苏晚'];

/**
 * 安全高亮渲染函数：避免使用 dangerouslySetInnerHTML，通过纯 React 节点渲染。
 */
function renderHighlightedSnippet(snippet: string, query: string): React.ReactNode {
  if (!query || !query.trim()) {
    return snippet;
  }

  const cleanQuery = query.trim().toLowerCase();
  const lowerSnippet = snippet.toLowerCase();
  const parts: React.ReactNode[] = [];
  let cursor = 0;

  while (cursor < snippet.length) {
    const matchIdx = lowerSnippet.indexOf(cleanQuery, cursor);
    if (matchIdx === -1) {
      parts.push(snippet.slice(cursor));
      break;
    }

    if (matchIdx > cursor) {
      parts.push(snippet.slice(cursor, matchIdx));
    }

    const matchLen = cleanQuery.length;
    const matchedText = snippet.slice(matchIdx, matchIdx + matchLen);
    parts.push(
      <mark key={matchIdx} className="lore-search-highlight">
        {matchedText}
      </mark>
    );

    cursor = matchIdx + matchLen;
  }

  return parts;
}

export function LoreSearchPanel({
  worldId,
  search,
  onOpenSource,
  placeholder = '搜索人物、地点、势力、物品或原文关键词...',
  pageSize = 20,
  className = '',
  compact = false,
  onQueryChange,
}: LoreSearchPanelProps) {
  const [inputValue, setInputValue] = useState('');
  const [activeQuery, setActiveQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchResult, setSearchResult] = useState<LoreSearchResult | null>(null);
  const [offset, setOffset] = useState(0);

  // 竞态与取消控制器
  const abortControllerRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);

  // 当外部 worldId 变更时，彻底清空前一个世界的状态并拒绝迟到响应
  useEffect(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    requestIdRef.current += 1;
    setInputValue('');
    setActiveQuery('');
    onQueryChange?.('');
    setSearchResult(null);
    setError(null);
    setLoading(false);
    setOffset(0);
  }, [worldId]);

  // 组件卸载时取消未完成请求
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, []);

  const executeSearch = useCallback(
    async (targetQuery: string, targetOffset: number) => {
      const clean = targetQuery.trim();
      if (!clean) {
        requestIdRef.current += 1;
        abortControllerRef.current?.abort();
        onQueryChange?.('');
        setActiveQuery('');
        setSearchResult(null);
        setError(null);
        setLoading(false);
        return;
      }

      // 取消上一个未完成的请求
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      const controller = new AbortController();
      abortControllerRef.current = controller;

      const currentReqId = ++requestIdRef.current;
      setLoading(true);
      setError(null);
      setActiveQuery(clean);
      onQueryChange?.(clean);
      setOffset(targetOffset);

      try {
        const result = await search(
          {
            worldId,
            query: clean,
            limit: pageSize,
            offset: targetOffset,
          },
          controller.signal
        );

        // 如果在此期间有更新的请求发起，则丢弃旧响应
        if (currentReqId !== requestIdRef.current) {
          return;
        }

        setSearchResult(result);
        setLoading(false);
      } catch (err: unknown) {
        if (currentReqId !== requestIdRef.current) {
          return;
        }

        // 如果是主动取消的，不显示为错误
        if (
          (err instanceof DOMException && err.name === 'AbortError') ||
          (err instanceof Error && err.name === 'AbortError')
        ) {
          return;
        }

        setLoading(false);
        if (err instanceof Error) {
          setError(err.message || '检索请求失败，请检查网络或后端服务连接。');
        } else {
          setError('检索请求失败，请稍后重试。');
        }
      }
    },
    [worldId, search, pageSize, onQueryChange]
  );

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    executeSearch(inputValue, 0);
  }

  function handleClear() {
    requestIdRef.current += 1;
    setLoading(false);
    setInputValue('');
    setActiveQuery('');
    onQueryChange?.('');
    setSearchResult(null);
    setError(null);
    setOffset(0);
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
  }

  function handleQuickChip(keyword: string) {
    setInputValue(keyword);
    executeSearch(keyword, 0);
  }

  function handlePrevPage() {
    if (offset <= 0 || loading) return;
    const newOffset = Math.max(0, offset - pageSize);
    executeSearch(activeQuery, newOffset);
  }

  function handleNextPage() {
    if (!searchResult || loading) return;
    if (offset + pageSize >= searchResult.total) return;
    const newOffset = offset + pageSize;
    executeSearch(activeQuery, newOffset);
  }

  const total = searchResult?.total ?? 0;
  const currentPage = Math.floor(offset / pageSize) + 1;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className={`lore-search-panel ${className}`} role="region" aria-label="设定资料检索面板">
      {/* 搜索输入与提交栏 */}
      <form className="lore-search-form" onSubmit={handleSubmit}>
        <div className="lore-search-input-wrap">
          <Search size={16} className="lore-search-input-icon" />
          <input
            type="text"
            className="lore-search-input"
            value={inputValue}
            onChange={e => setInputValue(e.target.value)}
            placeholder={placeholder}
            maxLength={100}
            aria-label="检索关键词"
          />
          {inputValue && (
            <button
              type="button"
              className="lore-search-clear-btn"
              onClick={handleClear}
              aria-label="清空输入"
            >
              <X size={15} />
            </button>
          )}
        </div>
        <button
          type="submit"
          className="lore-search-submit-btn"
          disabled={!inputValue.trim() || loading}
        >
          {loading ? (
            <>
              <Loader2 size={15} className="animate-spin" />
              <span>搜索中</span>
            </>
          ) : (
            <>
              <Search size={15} />
              <span>搜索</span>
            </>
          )}
        </button>
      </form>

      {/* 常用关键词快速推荐 */}
      {!compact && !searchResult && !loading && (
        <div className="lore-search-quick-bar" aria-label="推荐关键词">
          <span className="lore-search-quick-label">
            <Sparkles size={13} style={{ display: 'inline', marginRight: 4 }} />
            常用探测词:
          </span>
          {DEFAULT_QUICK_KEYWORDS.map(k => (
            <button
              key={k}
              type="button"
              className="lore-search-chip"
              onClick={() => handleQuickChip(k)}
            >
              {k}
            </button>
          ))}
        </div>
      )}

      {/* 错误提示栏 */}
      {error && (
        <div className="lore-search-status-banner lore-search-status-error" role="alert">
          <AlertCircle size={16} />
          <span>{error}</span>
          <button
            type="button"
            className="lore-search-retry-btn"
            onClick={() => executeSearch(activeQuery, offset)}
          >
            重试
          </button>
        </div>
      )}

      {/* 加载中提示 */}
      {loading && !searchResult && (
        <div className="lore-search-status-banner" role="status">
          <Loader2 size={16} className="animate-spin" />
          <span>正在检索设定切片与原文...</span>
        </div>
      )}

      {/* 搜索结果概览 */}
      {searchResult && !error && (
        <div className="lore-search-results-summary">
          找到 <strong>{total}</strong> 处与「<strong>{activeQuery}</strong>」相关的设定记录
        </div>
      )}

      {/* 空结果展示 */}
      {searchResult && total === 0 && !loading && !error && (
        <div className="lore-search-status-banner" role="status">
          <AlertCircle size={16} />
          <div>
            {compact ? '文档中没有匹配的内容。' : <>未在当前世界资料中检索到与「<strong>{activeQuery}</strong>」相关的内容。</>}
            <div style={{ fontSize: '11px', color: 'var(--muted, #8a8279)', marginTop: 4 }}>
              提示：可尝试缩短词长（如搜索单字或双字人名），或确认文档是否已导入。
            </div>
          </div>
        </div>
      )}

      {/* 结果条目列表 */}
      {searchResult && searchResult.items.length > 0 && (
        <div className="lore-search-results-list" role="list">
          {searchResult.items.map((item: LoreSearchItem) => {
            const headingBreadcrumb =
              item.heading_path && item.heading_path.length > 0
                ? item.heading_path.join(' > ')
                : '正文总览';

            return (
              <div key={item.chunk_id} className="lore-search-card" role="listitem">
                <div className="lore-search-card-meta">
                  <div className="lore-search-doc-tag">
                    <FileText size={12} />
                    <span>{item.filename}</span>
                  </div>
                  <div className="lore-search-heading-breadcrumb" title={headingBreadcrumb}>
                    <ChevronRight size={12} />
                    <span>{headingBreadcrumb}</span>
                  </div>
                </div>

                {/* 摘要正文（标明是切片摘要，不伪装成全文） */}
                <div className="lore-search-snippet">
                  {renderHighlightedSnippet(item.snippet, activeQuery)}
                </div>

                <div className="lore-search-card-footer">
                  <span className="lore-search-hint-note">
                    切片 #{item.chunk_index + 1}
                  </span>
                  {onOpenSource && (
                    <button
                      type="button"
                      className="lore-search-source-btn"
                      onClick={() => onOpenSource(toLoreSourceTarget(item))}
                      title="打开文档切片并定位原文"
                    >
                      <span>定位原文</span>
                      <ExternalLink size={12} />
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* 分页控制栏 */}
      {searchResult && total > pageSize && (
        <div className="lore-search-pagination" aria-label="检索结果分页">
          <button
            type="button"
            className="lore-search-page-btn"
            onClick={handlePrevPage}
            disabled={offset === 0 || loading}
          >
            <ChevronLeft size={14} />
            <span>上一页</span>
          </button>

          <span className="lore-search-page-info">
            第 {currentPage} / {totalPages} 页 (共 {total} 条)
          </span>

          <button
            type="button"
            className="lore-search-page-btn"
            onClick={handleNextPage}
            disabled={offset + pageSize >= total || loading}
          >
            <span>下一页</span>
            <ChevronRight size={14} />
          </button>
        </div>
      )}
    </div>
  );
}

