/**
 * M2-A 模型叙事实验手机界面（NarrationPreview）UI 行为与渲染测试
 * 依据: docs/contracts/M2_A_API.md v1.0 及 docs/collaboration/R6_GEMINI_MARCIN.md
 */

import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { NarrationPreview } from '../../../src/features/narration-preview/NarrationPreview';
import type {
  NarrationPreviewApi,
  NarrationPreviewResponse,
  ModelSessionResponse,
  ModelSessionTestResponse,
} from '../../../src/features/narration-preview/types';

function createDeferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: any) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function createMockApi(overrides: Partial<NarrationPreviewApi> = {}): NarrationPreviewApi {
  return {
    createSession: vi.fn().mockImplementation(async (config) => ({
      session_id: 'sess-test-12345678',
      model: config.model || 'gpt-4o-mini',
      expires_at: new Date(Date.now() + 1800000).toISOString(),
    })),
    deleteSession: vi.fn().mockResolvedValue(undefined),
    testSession: vi.fn().mockResolvedValue({
      ok: true,
      model: 'gpt-4o-mini',
    }),
    preview: vi.fn().mockImplementation(async (worldId, payload) => ({
      request_id: 'req-preview-001',
      mode: payload.mode,
      narrative: '海风吹拂着码头，老水手压低了兜帽，低声向你讲述了迷雾深处的怪谈。',
      references: ['chunk:c-1', 'entry:e-1'],
      sources: [
        {
          source_id: 'chunk:c-1',
          kind: 'chunk' as const,
          title: '码头见闻录',
          document_id: 'doc-1',
          chunk_id: 'c-1',
          chunk_index: 2,
        },
        {
          source_id: 'entry:e-1',
          kind: 'entry' as const,
          title: '老水手汉克',
          entry_id: 'e-1',
        },
      ],
    })),
    ...overrides,
  };
}

describe('NarrationPreview 组件 UI 行为与交互测试', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('场景 1：默认 Fake 模拟模式下完整生成草案并展示来源与徽标', async () => {
    const api = createMockApi();
    render(<NarrationPreview worldId="w-test" api={api} />);

    // 验证草案边界横幅
    expect(screen.getByText(/实验性功能说明/)).toBeTruthy();
    expect(screen.getByText(/未写入存档的预览草案/)).toBeTruthy();

    // 默认 Fake 模式激活
    const fakeBtn = screen.getByTestId('mode-btn-fake');
    expect(fakeBtn.className).toContain('active');

    // 输入行动并点击生成
    const actionInput = screen.getByTestId('action-input');
    fireEvent.change(actionInput, { target: { value: '在码头酒馆询问海图的线索' } });

    const generateBtn = screen.getByTestId('generate-btn');
    expect(generateBtn).not.toBeDisabled();
    fireEvent.click(generateBtn);

    // 验证 API 调用入参
    await waitFor(() => {
      expect(api.preview).toHaveBeenCalledWith(
        'w-test',
        {
          action: '在码头酒馆询问海图的线索',
          mode: 'fake',
          session_id: undefined,
        },
        expect.any(AbortSignal)
      );
    });

    // 验证渲染结果
    expect(await screen.findByTestId('narration-result-card')).toBeTruthy();
    expect(screen.getByTestId('badge-fake')).toHaveTextContent('Fake 模拟');
    expect(screen.getByTestId('narration-prose')).toHaveTextContent('老水手压低了兜帽');
    expect(screen.getByTestId('source-item-chunk:c-1')).toHaveTextContent('码头见闻录');
    expect(screen.getByTestId('source-item-entry:e-1')).toHaveTextContent('老水手汉克');
  });

  it('场景 2：Live 模式会话创建、Key 立即清空与禁止持久化到浏览器存储', async () => {
    const api = createMockApi();
    render(<NarrationPreview worldId="w-test" api={api} />);

    // 切换到 Live 模式
    fireEvent.click(screen.getByTestId('mode-btn-live'));
    expect(screen.getByTestId('live-session-panel')).toBeTruthy();

    const baseUrlInput = screen.getByLabelText(/API 地址/);
    const modelInput = screen.getByLabelText(/模型标识/);
    const apiKeyInput = screen.getByLabelText(/API Key/);

    fireEvent.change(baseUrlInput, { target: { value: 'https://api.openai.com/v1' } });
    fireEvent.change(modelInput, { target: { value: 'deepseek-chat' } });
    fireEvent.change(apiKeyInput, { target: { value: 'sk-super-secret-key-12345' } });

    // 提交连接
    fireEvent.click(screen.getByTestId('connect-session-btn'));

    await waitFor(() => {
      expect(api.createSession).toHaveBeenCalledWith({
        base_url: 'https://api.openai.com/v1',
        model: 'deepseek-chat',
        api_key: 'sk-super-secret-key-12345',
      }, expect.any(AbortSignal));
    });

    // 验证会话激活面板显示
    expect(await screen.findByTestId('active-session-info')).toBeTruthy();

    // 关键安全验证 1：连接成功后会话面板替换表单，Key 输入框不再存在于 DOM
    // （集成修正：原断言检查已卸载节点的 value，永远读到旧值；改为断言卸载 + DOM 不含 Key）
    expect(screen.queryByLabelText(/API Key/)).toBeNull();
    expect(document.body.innerHTML).not.toContain('sk-super-secret-key-12345');

    // 关键安全验证 2：严禁持久化到 localStorage 或 sessionStorage
    expect(localStorage.getItem('api_key')).toBeNull();
    expect(localStorage.getItem('apiKey')).toBeNull();
    expect(sessionStorage.getItem('api_key')).toBeNull();
    expect(JSON.stringify(localStorage)).not.toContain('sk-super-secret-key-12345');
    expect(JSON.stringify(sessionStorage)).not.toContain('sk-super-secret-key-12345');
  });

  it('场景 3：Live 模式会话创建失败保留输入，供用户直接修正', async () => {
    const api = createMockApi({
      createSession: vi.fn().mockRejectedValue({
        code: 'INVALID_CREDENTIALS',
        message: '上游认证失败：API Key 无效',
      }),
    });

    render(<NarrationPreview worldId="w-test" api={api} initialMode="live" />);

    const apiKeyInput = screen.getByLabelText(/API Key/);
    fireEvent.change(apiKeyInput, { target: { value: 'sk-bad-key' } });

    fireEvent.click(screen.getByTestId('connect-session-btn'));

    expect(await screen.findByTestId('session-error-banner')).toHaveTextContent('上游认证失败');

    // 验证失败时输入不被静默擦除，用户无需重新全部打字
    expect(apiKeyInput).toHaveValue('sk-bad-key');
  });

  it('场景 4：会话连通性测试与主动断开会话', async () => {
    const api = createMockApi();
    render(<NarrationPreview worldId="w-test" api={api} initialMode="live" />);

    // 先建立会话
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: 'sk-valid-key' } });
    fireEvent.click(screen.getByTestId('connect-session-btn'));
    await screen.findByTestId('active-session-info');

    // 点击测试连接
    const testBtn = screen.getByTestId('test-session-btn');
    fireEvent.click(testBtn);

    await waitFor(() => {
      expect(api.testSession).toHaveBeenCalledWith('sess-test-12345678', expect.any(AbortSignal));
    });
    expect(await screen.findByTestId('test-result-message')).toHaveTextContent('连接正常');

    // 点击断开会话
    fireEvent.click(screen.getByTestId('disconnect-session-btn'));
    await waitFor(() => {
      expect(api.deleteSession).toHaveBeenCalledWith('sess-test-12345678', expect.any(AbortSignal));
    });

    // 会话重置，回到配置表单
    expect(screen.queryByTestId('active-session-info')).toBeNull();
    expect(screen.getByLabelText(/API Key/)).toBeTruthy();
  });

  it('场景 5：会话过期 409 MODEL_SESSION_EXPIRED 自动清理会话并给出明确警示', async () => {
    const api = createMockApi({
      preview: vi.fn().mockRejectedValue({
        code: 'MODEL_SESSION_EXPIRED',
        status: 409,
        message: '服务端会话已超时注销',
      }),
    });

    render(<NarrationPreview worldId="w-test" api={api} initialMode="live" />);

    // 先模拟连接成功
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: 'sk-valid' } });
    fireEvent.click(screen.getByTestId('connect-session-btn'));
    await screen.findByTestId('active-session-info');

    // 发起生成草案触发 409
    fireEvent.change(screen.getByTestId('action-input'), { target: { value: '在旧街寻找密道' } });
    fireEvent.click(screen.getByTestId('generate-btn'));

    // 验证会话自动清理并给出过期提示
    expect(await screen.findByTestId('session-expired-alert')).toHaveTextContent('模型会话已过期');
    expect(screen.queryByTestId('active-session-info')).toBeNull();
  });

  it('场景 6：停止生成（Abort）取消等待并完整保留玩家键入的草稿行动', async () => {
    const deferred = createDeferred<NarrationPreviewResponse>();
    const api = createMockApi({
      preview: vi.fn().mockImplementation((_worldId, _payload, signal: AbortSignal) => {
        signal.addEventListener('abort', () => {
          const err = new Error('Aborted');
          err.name = 'AbortError';
          deferred.reject(err);
        });
        return deferred.promise;
      }),
    });

    render(<NarrationPreview worldId="w-test" api={api} />);

    const actionText = '深入地下矿坑并点亮火把探路';
    const actionInput = screen.getByTestId('action-input');
    fireEvent.change(actionInput, { target: { value: actionText } });

    // 点击生成进入加载状态
    fireEvent.click(screen.getByTestId('generate-btn'));

    // 验证出现“停止生成”按钮
    const stopBtn = await screen.findByTestId('stop-btn');
    expect(stopBtn).toBeTruthy();

    // 点击停止生成
    fireEvent.click(stopBtn);

    // 验证停止提示
    expect(await screen.findByTestId('stopped-notice')).toHaveTextContent('已取消等待');
    expect(screen.getByTestId('stopped-notice')).toHaveTextContent('不能承诺上游服务商未扣费');

    // 关键草稿保护验证：输入框内的行动文字依然完整保留，绝不丢失用户的灵感
    expect(actionInput).toHaveValue(actionText);
  });

  it('场景 7：生成失败报错时提供手动重试并保留草稿', async () => {
    let callCount = 0;
    const api = createMockApi({
      preview: vi.fn().mockImplementation(async () => {
        callCount++;
        if (callCount === 1) {
          throw { code: 'UPSTREAM_ERROR', message: '上游服务商网关 502 Bad Gateway' };
        }
        return {
          request_id: 'req-retry-ok',
          mode: 'fake',
          narrative: '重试成功，雨过天晴。',
          references: [],
          sources: [],
        };
      }),
    });

    render(<NarrationPreview worldId="w-test" api={api} />);

    const actionText = '向守卫出示通行证';
    fireEvent.change(screen.getByTestId('action-input'), { target: { value: actionText } });
    fireEvent.click(screen.getByTestId('generate-btn'));

    // 第一次报错
    expect(await screen.findByTestId('generate-error-banner')).toHaveTextContent('UPSTREAM_ERROR');
    expect(screen.getByTestId('action-input')).toHaveValue(actionText);

    // 点击手动重试
    const retryBtn = screen.getByTestId('retry-btn');
    fireEvent.click(retryBtn);

    // 第二次成功
    expect(await screen.findByTestId('narration-prose')).toHaveTextContent('重试成功，雨过天晴。');
    expect(screen.getByTestId('no-references-label')).toHaveTextContent('本次生成未引用任何设定资料');
  });

  it('场景 8：跨世界切换时中止前一个请求，丢弃迟到响应', async () => {
    const deferred = createDeferred<NarrationPreviewResponse>();
    const api = createMockApi({
      preview: vi.fn().mockReturnValue(deferred.promise),
    });

    const { rerender } = render(<NarrationPreview worldId="world-A" api={api} />);

    fireEvent.change(screen.getByTestId('action-input'), { target: { value: '世界A中的行动' } });
    fireEvent.click(screen.getByTestId('generate-btn'));

    // 切换到世界 B
    rerender(<NarrationPreview worldId="world-B" api={api} />);

    // 迟到响应返回
    await act(async () => {
      deferred.resolve({
        request_id: 'req-late',
        mode: 'fake',
        narrative: '世界 A 的迟到剧情',
        references: [],
        sources: [],
      });
    });

    // 验证世界 A 的迟到结果未被渲染到世界 B 中
    expect(screen.queryByText('世界 A 的迟到剧情')).toBeNull();
  });

  it('场景 9：来源跳转点击触发注入的 onOpenSource 并携带精确坐标 payload', async () => {
    const api = createMockApi();
    const onOpenSource = vi.fn();

    render(<NarrationPreview worldId="w-test" api={api} onOpenSource={onOpenSource} />);

    fireEvent.change(screen.getByTestId('action-input'), { target: { value: '查阅资料' } });
    fireEvent.click(screen.getByTestId('generate-btn'));

    await screen.findByTestId('narration-result-card');

    // 点击切片来源跳转
    const jumpChunkBtn = screen.getByTestId('jump-source-chunk:c-1');
    fireEvent.click(jumpChunkBtn);

    expect(onOpenSource).toHaveBeenCalledWith({
      source_id: 'chunk:c-1',
      kind: 'chunk',
      document_id: 'doc-1',
      chunk_id: 'c-1',
      chunk_index: 2,
      entry_id: undefined,
    });

    // 点击设定条目来源跳转
    const jumpEntryBtn = screen.getByTestId('jump-source-entry:e-1');
    fireEvent.click(jumpEntryBtn);

    expect(onOpenSource).toHaveBeenCalledWith({
      source_id: 'entry:e-1',
      kind: 'entry',
      document_id: undefined,
      chunk_id: undefined,
      chunk_index: undefined,
      entry_id: 'e-1',
    });
  });
});

it('stopped response cannot replace a newer generation, even if transport ignores abort', async () => {
  const old = createDeferred<NarrationPreviewResponse>();
  const fresh = createDeferred<NarrationPreviewResponse>();
  const api = createMockApi({ preview: vi.fn().mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise) });
  render(<NarrationPreview worldId="w" api={api} />);
  fireEvent.change(screen.getByTestId('action-input'), { target: { value: '看看港口' } });
  fireEvent.click(screen.getByTestId('generate-btn'));
  fireEvent.click(screen.getByTestId('stop-btn'));
  fireEvent.click(screen.getByTestId('generate-btn'));
  const result = (narrative: string): NarrationPreviewResponse => ({ request_id: narrative, mode: 'fake', narrative, references: [], sources: [] });
  await act(async () => { old.resolve(result('旧结果')); });
  expect(screen.queryByText('旧结果')).toBeNull();
  expect(screen.getByTestId('stop-btn')).toBeTruthy();
  await act(async () => { fresh.resolve(result('新结果')); });
  expect(screen.getByTestId('narration-prose')).toHaveTextContent('新结果');
});

it('failed deletion retains the session and offers a real retry', async () => {
  const api = createMockApi({ deleteSession: vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(undefined) });
  render(<NarrationPreview worldId="w" api={api} initialMode="live" />);
  fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: 'secret' } });
  fireEvent.click(screen.getByTestId('connect-session-btn'));
  await screen.findByTestId('active-session-info');
  fireEvent.click(screen.getByTestId('disconnect-session-btn'));
  expect(await screen.findByRole('alert')).toHaveTextContent('移除会话失败');
  expect(screen.getByTestId('active-session-info')).toBeTruthy();
  fireEvent.click(screen.getByTestId('disconnect-session-btn'));
  await waitFor(() => expect(screen.queryByTestId('active-session-info')).toBeNull());
  expect(api.deleteSession).toHaveBeenCalledTimes(2);
});

it('late session creation after changing world is cleaned up, never attached', async () => {
  const pending = createDeferred<ModelSessionResponse>();
  const api = createMockApi({ createSession: vi.fn().mockReturnValue(pending.promise) });
  const view = render(<NarrationPreview worldId="a" api={api} initialMode="live" />);
  fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: 'secret' } });
  fireEvent.click(screen.getByTestId('connect-session-btn'));
  view.rerender(<NarrationPreview worldId="b" api={api} initialMode="live" />);
  await act(async () => pending.resolve({ session_id: 'orphan', model: 'm', expires_at: new Date().toISOString() }));
  expect(api.deleteSession).toHaveBeenCalledWith('orphan');
  expect(screen.queryByTestId('active-session-info')).toBeNull();
  expect(screen.getByLabelText(/API Key/)).toHaveValue('');
});
