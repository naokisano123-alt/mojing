/**
 * 墨境资料查看器辅助工具函数 (Gemini Marcin - R2-M)
 * 纯逻辑函数，便于独立单元测试与浏览器兼容
 */

import type { LoreChunk } from '../../api/types';

/**
 * 判断字符编码是否为 UTF-16 高代理 (0xD800 - 0xDBFF)
 */
export function isHighSurrogate(code: number): boolean {
  return code >= 0xd800 && code <= 0xdbff;
}

/**
 * 判断字符编码是否为 UTF-16 低代理 (0xDC00 - 0xDFFF)
 */
export function isLowSurrogate(code: number): boolean {
  return code >= 0xdc00 && code <= 0xdfff;
}

/**
 * 修正 UTF-16 索引，防止切断代理对 (Surrogate Pairs, 如 Emoji)
 *
 * @param str 源字符串
 * @param index 待截断的 UTF-16 代码单元索引
 * @param roundMode 'expand' 向外扩展包含整个字符，'shrink' 向内收缩避免损坏字符
 */
export function adjustSurrogateBoundary(
  str: string,
  index: number,
  roundMode: 'expand' | 'shrink' = 'expand',
): number {
  if (index <= 0) return 0;
  if (index >= str.length) return str.length;

  const code = str.charCodeAt(index);
  // 如果 index 正好落在一个低代理项上，说明前面有一个高代理项
  if (isLowSurrogate(code)) {
    const prevCode = str.charCodeAt(index - 1);
    if (isHighSurrogate(prevCode)) {
      return roundMode === 'expand' ? index + 1 : index - 1;
    }
  }

  return index;
}

/**
 * 安全的 UTF-16 字符串截取，绝不把 Emoji 切成孤立的代理对
 */
export function safeUtf16Slice(
  str: string,
  start: number,
  end: number,
): string {
  const safeStart = adjustSurrogateBoundary(str, Math.max(0, start), 'shrink');
  const safeEnd = adjustSurrogateBoundary(str, Math.min(str.length, end), 'expand');
  if (safeStart >= safeEnd) return '';
  return str.slice(safeStart, safeEnd);
}

export interface HighlightSegment {
  text: string;
  isHighlight: boolean;
}

/**
 * 根据页面字符偏移和目标来源起止范围，将页面文本切分为 [前文本, 高亮文本, 后文本]
 *
 * @param pageText 当前分页的文本内容
 * @param pageOffset 当前分页在整篇文档中的起始字符偏移 (UTF-16)
 * @param targetStartChar 目标来源起始字符偏移 (UTF-16)
 * @param targetEndChar 目标来源结束字符偏移 (UTF-16)
 */
export function splitTextWithHighlight(
  pageText: string,
  pageOffset: number,
  targetStartChar: number | undefined = undefined,
  targetEndChar: number | undefined = undefined,
): HighlightSegment[] {
  if (targetStartChar === undefined || !pageText) {
    return [{ text: pageText, isHighlight: false }];
  }

  const endChar = targetEndChar !== undefined ? targetEndChar : targetStartChar + 50;
  const pageEndChar = pageOffset + pageText.length;

  // 如果目标范围与当前页无交集
  if (endChar <= pageOffset || targetStartChar >= pageEndChar) {
    return [{ text: pageText, isHighlight: false }];
  }

  const relStart = Math.max(0, targetStartChar - pageOffset);
  const relEnd = Math.min(pageText.length, Math.max(relStart, endChar - pageOffset));

  const safeRelStart = adjustSurrogateBoundary(pageText, relStart, 'shrink');
  const safeRelEnd = adjustSurrogateBoundary(pageText, relEnd, 'expand');

  const segments: HighlightSegment[] = [];

  if (safeRelStart > 0) {
    segments.push({
      text: pageText.slice(0, safeRelStart),
      isHighlight: false,
    });
  }

  if (safeRelEnd > safeRelStart) {
    segments.push({
      text: pageText.slice(safeRelStart, safeRelEnd),
      isHighlight: true,
    });
  }

  if (safeRelEnd < pageText.length) {
    segments.push({
      text: pageText.slice(safeRelEnd),
      isHighlight: false,
    });
  }

  return segments;
}

/**
 * 按 chunk_index 去重合并切片列表并保持升序
 */
export function deduplicateChunks(
  existing: LoreChunk[],
  incoming: LoreChunk[],
): LoreChunk[] {
  const map = new Map();
  for (const c of existing) {
    map.set(c.chunk_index, c);
  }
  for (const c of incoming) {
    map.set(c.chunk_index, c);
  }
  return Array.from(map.values()).sort((a, b) => a.chunk_index - b.chunk_index);
}

export interface CopyResult {
  success: boolean;
  errorMessage?: string;
}

/**
 * 异步剪贴板写入并提供异常防护
 */
export async function copyToClipboard(text: string): Promise<CopyResult> {
  if (typeof navigator !== 'undefined' && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    try {
      await navigator.clipboard.writeText(text);
      return { success: true };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      return {
        success: false,
        errorMessage: `复制失败（未获剪贴板权限或被浏览器拒绝）：${msg}`,
      };
    }
  }

  // 降级使用传统 document.execCommand('copy')
  if (typeof document !== 'undefined') {
    try {
      const textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.setAttribute('readonly', '');
      textarea.style.position = 'fixed';
      textarea.style.left = '-9999px';
      textarea.style.top = '0';
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(textarea);
      if (ok) {
        return { success: true };
      }
      return { success: false, errorMessage: '复制命令执行失败，请手动选取文字复制。' };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      return { success: false, errorMessage: `复制降级执行异常：${msg}` };
    }
  }

  return { success: false, errorMessage: '当前运行环境不支持剪贴板操作。' };
}
