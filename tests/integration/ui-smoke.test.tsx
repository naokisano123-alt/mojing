import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Tag } from '../../src/components/UI';

describe('UI 测试基础设施', () => {
  it('能真实渲染 React 组件并查询文本', () => {
    render(<Tag>世界资料库 · 已就绪</Tag>);
    expect(screen.getByText('世界资料库 · 已就绪')).toBeTruthy();
  });
});
