import { useState, useEffect } from 'react';
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

export interface DocumentViewerProps {
  documentId: string;
  initialMode?: 'chunks' | 'raw';
  initialChunkIndex?: number;
  onClose: () => void;
}

const PAGE_SIZE_RAW = 20_000; // 契约 §4.10 单页 20000 单位

export function DocumentViewer({
  documentId,
  initialMode = 'chunks',
  initialChunkIndex,
  onClose,
}: DocumentViewerProps) {
  const [mode, setMode] = useState<'chunks' | 'raw'>(initialMode);
  const [doc, setDoc] = useState<WorldDocument | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Chunks state
  const [chunks, setChunks] = useState<LoreChunk[]>([]);
  const [expandedChunkMap, setExpandedChunkMap] = useState<Record<number, string>>({});
  const [loadingChunkIndex, setLoadingChunkIndex] = useState<number | null>(null);
  const [copiedChunkIndex, setCopiedChunkIndex] = useState<number | null>(null);

  // Raw text state
  const [rawPage, setRawPage] = useState<RawTextPage | null>(null);
  const [rawOffset, setRawOffset] = useState(0);
  const [loadingRaw, setLoadingRaw] = useState(false);

  // 加载文档元信息与初始分块
  useEffect(() => {
    let active = true;
    const controller = new AbortController();

    async function loadMetaAndChunks() {
      setLoading(true);
      setError(null);
      try {
        const [docData, chunksData] = await Promise.all([
          getDocument(documentId, controller.signal),
          getDocumentChunks(documentId, { limit: 100, include_text: false }, controller.signal),
        ]);
        if (!active) return;
        setDoc(docData);
        setChunks(chunksData.items);
        setLoading(false);

        // 若指定了初始展开块
        if (initialChunkIndex !== undefined) {
          expandChunk(initialChunkIndex, controller.signal);
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

  // 加载原文分页
  useEffect(() => {
    if (mode !== 'raw') return;
    let active = true;
    const controller = new AbortController();

    async function loadRaw() {
      setLoadingRaw(true);
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
          setError(err.friendlyMessage);
        } else {
          setError('读取原文分页失败。');
        }
      }
    }

    loadRaw();
    return () => {
      active = false;
      controller.abort();
    };
  }, [documentId, mode, rawOffset]);

  async function expandChunk(chunkIndex: number, signal?: AbortSignal) {
    if (expandedChunkMap[chunkIndex] !== undefined) {
      // 已展开，点击则收起
      setExpandedChunkMap(prev => {
        const next = { ...prev };
        delete next[chunkIndex];
        return next;
      });
      return;
    }

    setLoadingChunkIndex(chunkIndex);
    try {
      const fullChunk = await getChunkByIndex(documentId, chunkIndex, signal);
      setExpandedChunkMap(prev => ({
        ...prev,
        [chunkIndex]: fullChunk.text || fullChunk.text_preview,
      }));
    } catch {
      // 容错降级展示 preview
      const preview = chunks.find(c => c.chunk_index === chunkIndex)?.text_preview || '';
      setExpandedChunkMap(prev => ({ ...prev, [chunkIndex]: preview }));
    } finally {
      setLoadingChunkIndex(null);
    }
  }

  function handleCopySnippet(chunkIndex: number, text: string) {
    navigator.clipboard?.writeText(text);
    setCopiedChunkIndex(chunkIndex);
    setTimeout(() => setCopiedChunkIndex(null), 2000);
  }

  function jumpToRawOffset(startChar: number) {
    // 计算包含 startChar 的分页 offset
    const targetOffset = Math.floor(startChar / PAGE_SIZE_RAW) * PAGE_SIZE_RAW;
    setRawOffset(targetOffset);
    setMode('raw');
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
                <Tag>{doc.encoding.toUpperCase()}</Tag>
                <Tag>{(doc.byte_size / 1024).toFixed(1)} KB</Tag>
                <span>{doc.chunk_count} 个章节切片</span>
                <span>{doc.text_length} 字符</span>
                <span>{doc.line_count} 行</span>
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
            <Layers size={15} /> 章节切片 ({chunks.length})
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
                const isLoadingThis = loadingChunkIndex === c.chunk_index;

                return (
                  <article key={c.id || c.chunk_index} className="chunk-item">
                    <div className="chunk-item-top">
                      <div className="chunk-heading-path">
                        <Tag># {c.chunk_index + 1}</Tag>
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
                          onClick={() => jumpToRawOffset(c.start_char)}
                        >
                          <Hash size={12} /> 原文定位
                        </button>
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}

          {!loading && !error && mode === 'raw' && (
            <div className="raw-container">
              {loadingRaw ? (
                <div className="empty" style={{ padding: '30px 0' }}>
                  <p>正在读取原文段落...</p>
                </div>
              ) : rawPage ? (
                <>
                  <div className="raw-text-view">
                    {rawPage.text || '（此页无文本内容）'}
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
                        disabled={rawOffset <= 0}
                        onClick={() => setRawOffset(Math.max(0, rawOffset - PAGE_SIZE_RAW))}
                      >
                        <ArrowLeft size={14} /> 上一页
                      </button>
                      <button
                        type="button"
                        className="secondary"
                        style={{ minHeight: '36px', padding: '6px 14px' }}
                        disabled={rawPage.next_offset === null}
                        onClick={() => rawPage.next_offset !== null && setRawOffset(rawPage.next_offset)}
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
