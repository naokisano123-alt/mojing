import '@testing-library/jest-dom/vitest';
// jsdom 没有布局滚动实现；页面切换仍可用 spy 验证滚动目标。
window.scrollTo = () => {};
