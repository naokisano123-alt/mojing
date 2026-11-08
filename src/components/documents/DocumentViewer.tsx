import { useState, useEffect, useRef } from 'react';
import {
  FileText,
  Layers,
  AlignLeft,
  X,
  ChevronRight,
  ChevronDown,
  Copy,
  Check,
  ArrowLeft,
  ArrowRight,
  AlertCircle,
  Hash,
  RefreshCw,
} from 'lucide-react';
import type { LoreChunk, RawTextPage, WorldDocument } from '../../api/types';
import {
  getDocument,
  getDocumentChunks,
  getChunkByIndex,
  getDocumentRaw,
  ApiClientError,
} from '../../api/client';
import { Tag } from '../UI';
import {
  splitTextWithHighlight,
  deduplicateChunks,
  copyToClipboard,
} from './viewerUtils';

export interface SourceLocation {
  chunkIndex?: number;
  startChar?: number;
  endChar?: number;
}

export interface DocumentViewerProps {
  documentId: string;
  initialMode?: 'chunks' | 'raw';
  initialChunkIndex?: number;
  /** 可选来源高亮区间 (UTF-16 代码单元) */
  initialSource?: SourceLocation;
  onClose: () => void;
}

const CHUNK_PAGE_SIZE = 100; // 每页切片拉取上限
const PAGE_SIZE_RAW = 20_000; // 契约 §4.10 单页 20000 单位


function isPartialTextWarning(w: unknown): boolean {
  if (!w) return false;
  if (typeof w === 'string') {
    return w.includes('PARTIAL_TEXT') || w.includes('部分内容未提取');
  }
  if (typeof w === 'object' && w !== null) {
    const rec = w as { code?: string; message?: string };
    return (
      rec.code === 'PARTIAL_TEXT' ||
      (rec.message ? rec.message.includes('部分内容未提取') : false)
    );
  }
  return false;
}

function formatWarningText(w: unknown): string {
  if (typeof w === 'string') return w;
  if (typeof w === 'object' && w !== null) {
    const rec = w as { code?: string; message?: string; count?: number };
    const msg = rec.message || rec.code || '未知解析警告';
    if (typeof rec.count === 'number' && rec.count > 1) {
      return `${msg}（共 ${rec.count} 处）`;
    }
    return msg;
  }
  return String(w);
}

export function DocumentViewer({

  documentId,
  initialMode = 'chunks',
  initialChunkIndex,
  initialSource,
  onClose,
}: DocumentViewerProps) {
  const [mode, setMode] = useState<'chunks' | 'raw'>(initialMode);
  const [doc, setDoc] = useState<WorldDocument | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Chunks state
  const [chunks, setChunks] = useState<LoreChunk[]>([]);
  const [totalChunks, setTotalChunks] = useState(0);
  const [loadingMoreChunks, setLoadingMoreChunks] = useState(false);
  const [loadMoreChunksError, setLoadMoreChunksError] = useState<string | null>(null);

  // 展开全文状态：独立追踪每个切片的内容与失败状态，严禁将摘要混作全文
  const [expandedChunkMap, setExpandedChunkMap] = useState<Record<number, string>>({});
  const [chunkExpandErrorMap, setChunkExpandErrorMap] = useState<Record<number, string>>({});
  const [loadingChunkMap, setLoadingChunkMap] = useState<Record<number, boolean>>({});

  // 复制反馈与定时器保护
  const [copiedChunkIndex, setCopiedChunkIndex] = useState<number | null>(null);
  const [copyFeedbackError, setCopyFeedbackError] = useState<string | null>(null);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Raw text state 与精确历史回退栈
  const [rawPage, setRawPage] = useState<RawTextPage | null>(null);
  const [rawOffset, setRawOffset] = useState(() => {
    if (initialSource?.startChar !== undefined) {
      return Math.floor(initialSource.startChar / PAGE_SIZE_RAW) * PAGE_SIZE_RAW;
    }
    return 0;
  });
  const [offsetHistory, setOffsetHistory] = useState<number[]>([0]);
  const [loadingRaw, setLoadingRaw] = useState(false);
  const [rawError, setRawError] = useState<string | null>(null);

  // 高亮来源引用
  const [activeHighlight, setActiveHighlight] = useState<SourceLocation | undefined>(
    initialSource || (initialChunkIndex !== undefined ? { chunkIndex: initialChunkIndex } : undefined),
  );

  const highlightElementRef = useRef<HTMLElement | null>(null);

  // 清理复制反馈定时器
  useEffect(() => {
    return () => {
      if (copyTimerRef.current) {
        clearTimeout(copyTimerRef.current);
      }
    };
  }, []);

  // 监听 ESC 键关闭弹窗
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        onClose();
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // 加载文档元信息与初始分块（严格隔离切文档状态，防止迟到响应串文档）
  useEffect(() => {
    let active = true;
    const controller = new AbortController();

    // 切换文档时立即清空旧状态
    setDoc(null);
    setChunks([]);
    setTotalChunks(0);
    setExpandedChunkMap({});
    setChunkExpandErrorMap({});
    setLoadingChunkMap({});
    setRawPage(null);
    setRawOffset(initialSource?.startChar ? Math.floor(initialSource.startChar / PAGE_SIZE_RAW) * PAGE_SIZE_RAW : 0);
    setOffsetHistory([0]);
    setError(null);
    setLoadMoreChunksError(null);
    setRawError(null);
    setLoading(true);

    async function loadMetaAndChunks() {
      try {
        const [docData, chunksData] = await Promise.all([
          getDocument(documentId, controller.signal),
          getDocumentChunks(documentId, { limit: CHUNK_PAGE_SIZE, offset: 0, include_text: false }, controller.signal),
        ]);
        if (!active) return;

        setDoc(docData);
        setChunks(chunksData.items);
        setTotalChunks(chunksData.total);
        setLoading(false);

        // 若指定了初始展开块
        const targetIndex = initialSource?.chunkIndex ?? initialChunkIndex;
        if (targetIndex !== undefined) {
          expandChunk(targetIndex, controller.signal);
        }
      } catch (err: unknown) {
        if (!active) return;
        setLoading(false);
        if (err instanceof ApiClientError) {
          setError(err.friendlyMessage);
        } else {
          setError('加载文档信息失败。');
        }
      }
    }

    loadMetaAndChunks();
    return () => {
      active = false;
      controller.abort();
    };
  }, [documentId]);

  // 按需加载更多切片（突破 100 块上限，展示服务端真实 total）
  async function handleLoadMoreChunks() {
    if (loadingMoreChunks || chunks.length >= totalChunks) return;

    setLoadingMoreChunks(true);
    setLoadMoreChunksError(null);

    try {
      const res = await getDocumentChunks(documentId, {
        limit: CHUNK_PAGE_SIZE,
        offset: chunks.length,
        include_text: false,
      });

      setChunks(prev => deduplicateChunks(prev, res.items));
      setTotalChunks(res.total);
    } catch (err: unknown) {
      if (err instanceof ApiClientError) {
        setLoadMoreChunksError(err.friendlyMessage);
      } else {
        setLoadMoreChunksError('加载后续切片失败，请检查网络后重试。');
      }
    } finally {
      setLoadingMoreChunks(false);
    }
  }

  // 加载原文分页（严格使用实际 offset 与 next_offset）
  useEffect(() => {
    if (mode !== 'raw') return;
    let active = true;
    const controller = new AbortController();

    async function loadRaw() {
      setLoadingRaw(true);
      setRawError(null);
      try {
        const data = await getDocumentRaw(
          documentId,
          { offset: rawOffset, limit: PAGE_SIZE_RAW },
          controller.signal,
        );
        if (active) {
          setRawPage(data);
          setLoadingRaw(false);
        }
      } catch (err: unknown) {
        if (!active) return;
        setLoadingRaw(false);
        if (err instanceof ApiClientError) {
          setRawError(err.friendlyMessage);
        } else {
          setRawError('读取原文分页失败。');
        }
      }
    }

    loadRaw();
    return () => {
      active = false;
      controller.abort();
    };
  }, [documentId, mode, rawOffset]);

  // 自动滚动至高亮区域
  useEffect(() => {
    if (highlightElementRef.current) {
      highlightElementRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [mode, rawPage, expandedChunkMap]);

  // 展开切片全文（显式错误处理，拒绝把摘要伪装为正文）
  async function expandChunk(chunkIndex: number, signal?: AbortSignal) {
    if (expandedChunkMap[chunkIndex] !== undefined) {
      // 已展开，点击收起
      setExpandedChunkMap(prev => {
        const next = { ...prev };
        delete next[chunkIndex];
        return next;
      });
      return;
    }

    setLoadingChunkMap(prev => ({ ...prev, [chunkIndex]: true }));
    setChunkExpandErrorMap(prev => {
      const next = { ...prev };
      delete next[chunkIndex];
      return next;
    });

    try {
      const fullChunk = await getChunkByIndex(documentId, chunkIndex, signal);
      const textContent = fullChunk.text || fullChunk.text_preview;
      setExpandedChunkMap(prev => ({
        ...prev,
        [chunkIndex]: textContent,
      }));
    } catch (err: unknown) {
      const msg = err instanceof ApiClientError ? err.friendlyMessage : '获取全文失败，请重试。';
      setChunkExpandErrorMap(prev => ({
        ...prev,
        [chunkIndex]: msg,
      }));
      // 严禁在此处将 preview 填入 expandedChunkMap，保持正文真实性
    } finally {
      setLoadingChunkMap(prev => ({ ...prev, [chunkIndex]: false }));
    }
  }

  // 复制切片正文：正确 await 异步结果并提供权限被拒时的错误提示
  async function handleCopySnippet(chunkIndex: number, text: string) {
    setCopyFeedbackError(null);
    const res = await copyToClipboard(text);
    if (res.success) {
      setCopiedChunkIndex(chunkIndex);
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => {
        setCopiedChunkIndex(null);
      }, 2000);
    } else {
      setCopyFeedbackError(res.errorMessage || '复制失败');
    }
  }

  // 跳转原文定位
  function jumpToRawOffset(startChar: number, endChar?: number) {
    const targetOffset = Math.floor(startChar / PAGE_SIZE_RAW) * PAGE_SIZE_RAW;
    setOffsetHistory([0, targetOffset]);
    setRawOffset(targetOffset);
    setActiveHighlight({ startChar, endChar });
    setMode('raw');
  }

  // 原文分页：下一页
  function handleNextRawPage() {
    if (!rawPage || rawPage.next_offset === null) return;
    setOffsetHistory(prev => [...prev, rawOffset]);
    setRawOffset(rawPage.next_offset);
  }

  // 原文分页：上一页（从历史回退栈准确还原，避免假定边界固定减 20000）
  function handlePrevRawPage() {
    if (offsetHistory.length <= 1) {
      setRawOffset(0);
      return;
    }
    const newHistory = [...offsetHistory];
    newHistory.pop(); // 弹出当前
    const prevOffset = newHistory[newHistory.length - 1] ?? 0;
    setOffsetHistory(newHistory);
    setRawOffset(prevOffset);
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <section
        role="dialog"
        aria-modal="true"
        aria-label="世界观资料查看器"
        className="modal viewer-modal"
        onClick={e => e.stopPropagation()}
      >
        <div className="viewer-header">
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="flex items-center gap-2">
              <FileText size={18} style={{ color: 'var(--green)' }} />
              <h2 style={{ fontSize: '18px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {doc?.filename || '资料查看器'}
              </h2>
            </div>
            {doc && (
              <div className="viewer-meta-line">
                {doc.encoding_semantics === 'extracted_text' ? (
                  <Tag>EXTRACTED TEXT</Tag>
                ) : (
                  <Tag>{doc.encoding.toUpperCase()}</Tag>
                )}
                <Tag>{(doc.byte_size / 1024).toFixed(1)} KB</Tag>
                <span>{doc.chunk_count} 个章节切片</span>
                <span>{doc.text_length} 字符</span>
                <span>{doc.line_count} 行</span>
              </div>
            )}
            {doc && doc.warnings && doc.warnings.length > 0 && (
              <div className="viewer-warnings-container">
                {doc.warnings.some(isPartialTextWarning) && (
                  <div className="viewer-warning-banner" role="alert">
                    <AlertCircle size={16} />
                    <div>
                      <strong>部分内容未提取</strong>：该文档包含复杂排版、图片或不支持的元素，部分文本未能完整提取。
                    </div>
                  </div>
                )}
                <div className="viewer-warnings-box" role="status">
                  <div className="viewer-warnings-title">
                    <AlertCircle size={13} />
                    <span>导入解析提示 ({doc.warnings.length})</span>
                  </div>
                  <ul className="viewer-warnings-list">
                    {doc.warnings.map((w, idx) => (
                      <li key={idx} className="viewer-warning-item">
                        {formatWarningText(w)}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label="关闭查看器"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>

        <div className="viewer-modes">
          <button
            type="button"
            className={`viewer-mode-btn ${mode === 'chunks' ? 'active' : ''}`}
            onClick={() => setMode('chunks')}
          >
            <Layers size={15} /> 章节切片 ({chunks.length}{totalChunks > chunks.length ? ` / ${totalChunks}` : ''})
          </button>
          <button
            type="button"
            className={`viewer-mode-btn ${mode === 'raw' ? 'active' : ''}`}
            onClick={() => setMode('raw')}
          >
            <AlignLeft size={15} /> 全文溯源 (分页)
          </button>
        </div>

        <div className="viewer-body">
          {loading && (
            <div className="empty" style={{ padding: '40px 0' }}>
              <div className="status-dot" style={{ width: '12px', height: '12px' }} />
              <p>正在读取文档资料与切片...</p>
            </div>
          )}

          {error && (
            <div className="upload-error-callout" style={{ margin: '20px 0' }}>
              <AlertCircle size={18} />
              <div>{error}</div>
            </div>
          )}

          {copyFeedbackError && (
            <div className="upload-error-callout" style={{ margin: '10px 0' }}>
              <AlertCircle size={16} />
              <div>{copyFeedbackError}</div>
            </div>
          )}

          {!loading && !error && mode === 'chunks' && (
            <div className="chunks-list">
              {chunks.length === 0 && (
                <div className="empty">
                  <p>该文档未产生章节切片。</p>
                </div>
              )}
              {chunks.map(c => {
                const isExpanded = expandedChunkMap[c.chunk_index] !== undefined;
                const fullText = expandedChunkMap[c.chunk_index];
                const isLoadingThis = Boolean(loadingChunkMap[c.chunk_index]);
                const expandError = chunkExpandErrorMap[c.chunk_index];
                const isTargetHighlight = activeHighlight?.chunkIndex === c.chunk_index;

                return (
                  <article
                    key={c.id || c.chunk_index}
                    id={`chunk-item-${c.chunk_index}`}
                    ref={isTargetHighlight ? (el => { highlightElementRef.current = el; }) : undefined}
                    className={`chunk-item ${isTargetHighlight ? 'highlighted' : ''}`}
                  >
                    <div className="chunk-item-top">
                      <div className="chunk-heading-path">
                        <Tag># {c.chunk_index + 1}</Tag>
                        {isTargetHighlight && (
                          <span className="source-target-badge">来源定位命中</span>
                        )}
                        {c.heading_path?.length > 0 ? (
                          c.heading_path.map((h, i) => (
                            <span key={i} className="chunk-heading-tag">
                              {h}
                            </span>
                          ))
                        ) : (
                          <span className="chunk-heading-tag" style={{ color: 'var(--muted)', background: 'var(--bg)' }}>
                            正文片段
                          </span>
                        )}
                      </div>
                      <span className="chunk-char-range">
                        [{c.start_char} ~ {c.end_char}]
                      </span>
                    </div>

                    {!isExpanded ? (
                      <p className="chunk-preview-line">{c.text_preview}</p>
                    ) : (
                      <div className="chunk-text-content">
                        {fullText}
                      </div>
                    )}

                    {expandError && (
                      <div className="chunk-expand-error">
                        <AlertCircle size={14} />
                        <span>{expandError}</span>
                        <button
                          type="button"
                          className="text-button"
                          style={{ color: '#ba6452', textDecoration: 'underline' }}
                          onClick={() => expandChunk(c.chunk_index)}
                        >
                          重试展开
                        </button>
                      </div>
                    )}

                    <div className="flex items-center justify-between mt-3 pt-2" style={{ borderTop: '1px solid var(--line)' }}>
                      <button
                        type="button"
                        className="text-button"
                        onClick={() => expandChunk(c.chunk_index)}
                      >
                        {isLoadingThis ? (
                          '读取中...'
                        ) : isExpanded ? (
                          <>收起正文 <ChevronDown size={14} /></>
                        ) : (
                          <>展开全文 ({c.text_length} 字) <ChevronRight size={14} /></>
                        )}
                      </button>

                      <div className="flex items-center gap-2">
                        {isExpanded && (
                          <button
                            type="button"
                            className="text-button"
                            title="复制切片内容"
                            onClick={() => handleCopySnippet(c.chunk_index, fullText)}
                          >
                            {copiedChunkIndex === c.chunk_index ? (
                              <><Check size={13} /> 已复制</>
                            ) : (
                              <><Copy size={13} /> 复制</>
                            )}
                          </button>
                        )}
                        <button
                          type="button"
                          className="text-button"
                          style={{ color: 'var(--muted)', fontSize: '11px' }}
                          title="在全文中定位该段落"
                          onClick={() => jumpToRawOffset(c.start_char, c.end_char)}
                        >
                          <Hash size={12} /> 原文定位
                        </button>
                      </div>
                    </div>
                  </article>
                );
              })}

              {/* 切片更多加载入口 */}
              {chunks.length < totalChunks && (
                <div className="load-more-container">
                  {loadMoreChunksError && (
                    <div className="upload-error-callout mb-2">
                      <AlertCircle size={14} />
                      <span>{loadMoreChunksError}</span>
                    </div>
                  )}
                  <button
                    type="button"
                    className="secondary full"
                    disabled={loadingMoreChunks}
                    onClick={handleLoadMoreChunks}
                  >
                    {loadingMoreChunks ? (
                      <><RefreshCw size={14} className="spin" /> 正在加载后续切片...</>
                    ) : (
                      `加载更多切片（当前 ${chunks.length} / 共 ${totalChunks} 块）`
                    )}
                  </button>
                </div>
              )}
            </div>
          )}

          {!loading && !error && mode === 'raw' && (
            <div className="raw-container">
              {loadingRaw ? (
                <div className="empty" style={{ padding: '30px 0' }}>
                  <p>正在读取原文段落...</p>
                </div>
              ) : rawError ? (
                <div className="upload-error-callout" style={{ margin: '20px 0' }}>
                  <AlertCircle size={18} />
                  <div>
                    <div>{rawError}</div>
                    <button
                      type="button"
                      className="text-button mt-2"
                      style={{ color: '#ba6452', textDecoration: 'underline' }}
                      onClick={() => setRawOffset(rawOffset)}
                    >
                      重试加载该页
                    </button>
                  </div>
                </div>
              ) : rawPage ? (
                <>
                  <div className="raw-text-view">
                    {(() => {
                      const segments = splitTextWithHighlight(
                        rawPage.text || '',
                        rawPage.offset,
                        activeHighlight?.startChar,
                        activeHighlight?.endChar,
                      );
                      return segments.map((seg, idx) => {
                        if (seg.isHighlight) {
                          return (
                            <mark
                              key={idx}
                              ref={el => { highlightElementRef.current = el; }}
                              className="raw-highlight-mark"
                            >
                              {seg.text}
                            </mark>
                          );
                        }
                        return <span key={idx}>{seg.text}</span>;
                      });
                    })()}
                  </div>
                  <div className="raw-pagination-bar">
                    <span className="muted text-xs">
                      字符偏移：{rawPage.offset} ~ {rawPage.offset + rawPage.length} / 共 {rawPage.total_length} 字
                    </span>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        className="secondary"
                        style={{ minHeight: '36px', padding: '6px 14px' }}
                        disabled={rawOffset <= 0 && offsetHistory.length <= 1}
                        onClick={handlePrevRawPage}
                      >
                        <ArrowLeft size={14} /> 上一页
                      </button>
                      <button
                        type="button"
                        className="secondary"
                        style={{ minHeight: '36px', padding: '6px 14px' }}
                        disabled={rawPage.next_offset === null}
                        onClick={handleNextRawPage}
                      >
                        下一页 <ArrowRight size={14} />
                      </button>
                    </div>
                  </div>
                </>
              ) : null}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
