# 墨境第六轮集成报告（R6-D，M2-A）

- **分支**：`feat/r6-integration`
- **基线**：`8087d09`（PR #8 后 main）+ 本分支包含两项热修（204 空响应、uuid 降级）
- **契约**：`docs/contracts/M2_A_API.md` v1.0

## 1. 状态总表

| 项 | 状态 | 说明 |
| --- | --- | --- |
| R6-D 路由与客户端 | **DONE** | 4 个端点 + 4 个前端函数 |
| R6-M（Marcin） | **IMPORTED / TESTED** | `import/r6-marcin` @ `4e379db`，6 文件，9 条 UI 用例 |
| R6-L（Li） | **IMPORTED / TESTED** | `import/r6-li` @ `c989be0`，9 文件 |
| R6-A（Ma） | **IMPORTED / TESTED** | `import/r6-ma` @ `eb17d5e`，5 文件 |
| Fake 闭环（真实 HTTP + 真实上下文） | **DONE** | 月渡世界 8 份来源，references 指向真实 chunk |
| Live 闭环 | **未验证** | 无 Key、无真实服务商调用（按契约不在自动化中使用真 Key） |
| 真机 / 部署 | **未验收 / 未部署** | |

## 2. 收包核验

| 包 | 分支 | 提交 | 校验 |
| --- | --- | --- | --- |
| gemini-marcin-R6-8087d09 | import/r6-marcin | 4e379db | 6/6 sha256 一致，apply 通过 |
| gemini-li-R6-8087d09 | import/r6-li | c989be0 | 9/9 一致，apply 通过 |
| gemini-R6-A-8087d09 | import/r6-ma | eb17d5e | 5/5 一致，apply 通过 |

三支 `--no-ff` 合并，作者保留。

## 3. 集成接线

| 位置 | 改动 |
| --- | --- |
| `server/app/main.py` | 4 个 M2 端点；`ModelSessionStore` 懒加载（Li 只导出类，未导出工厂）；shutdown 时 `clear()`；上下文在事务内构建、关闭后再 `await` 模型；60s 超时；`CancelledError` 原样抛出 |
| `src/api/types.ts` / `client.ts` | M2 DTO + `createModelSession` / `deleteModelSession` / `testModelSession` / `previewNarration` |
| `WorldLoreView.tsx` | 新增「叙事实验」页签，注入 Marcin 的 `NarrationPreview`；来源点击：chunk → 查看器定位，entry → 切到「人工设定」并提示 |
| `vitest.config.ts` + `tests/setup/jest-dom.ts` | 补 `@testing-library/jest-dom` 与 setup，Marcin 用例的 matcher 才能生效 |
| `package.json/lock` | 新增 jest-dom 开发依赖（唯一维护方） |

## 4. 集成修正（integration_fix）

- **Marcin 的 `narration-preview.test.tsx` 场景 2**：原断言检查已卸载输入框的 `value`（会话建立后表单会被替换，旧节点永远保留旧值），必然失败。改为断言「输入框已从 DOM 移除」且「body 不含 Key 明文」。行为本身（连接成功即清空 Key、不落存储）未改变。

## 5. 实际验证

| 项 | 结果 |
| --- | --- |
| `pytest` | **236 passed** |
| `npm test`（node:test） | **62 passed** |
| `npm run test:ui`（Vitest） | **21 passed**（Marcin 9 + 装配 4 + 基础设施 1 + 其他 7） |
| `npm run build` | 通过，1609 modules |
| Fake 预览（真实 HTTP） | 200，8 份来源，references 指向真实 `chunk:<id>` |
| 会话创建 | 201，响应不含 Key，返回不透明 id |
| Key 泄露检查 | 响应体、localStorage、sessionStorage 均无明文 |

## 6. 已知限制

- Live 模式未做真实调用；用户配置 Key 后需自行做连通测试（可能产生少量费用）
- `@app.on_event("shutdown")` 有弃用警告，后续可迁 lifespan
- 真机与线上部署仍未验收
