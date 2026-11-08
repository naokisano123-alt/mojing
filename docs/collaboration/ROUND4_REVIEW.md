# 第四轮仓库审阅：已合并不等于闭环已验收

日期：2026-11-08；代码基线 `8296630705a7e61de95fbf532b8a38846dd82c78`；PR #3 已合并，当前无开放 PR（本次读取时）。[CI run 42468032481](https://github.com/naokisano123-alt/mojing/actions/runs/42468032481) 的 frontend/backend 两项及其 test/build 步骤均 success。

## 已确认的进展

R3 三包已进入主分支：人工条目前端、Li 的导入策略和 typed warnings、Ma 的条目服务和检索修正。DSH 已接迁移、五个条目端点、客户端及“人工设定”页签。DocumentUpload 已支持 maxBytesByFormat，DocumentViewer 已渲染 warnings/encoding_semantics。后端检索已逐项展开 heading JSON、采用 ASCII 大小写规则、区分正文/标题/文件名并返回真实/null 坐标。这些工作不应重做。

r3-integration 报告 §8 声明前端 50、后端 127 通过及构建通过；这些是集成者记录。该报告 §2/§5 仍留“待包”旧状态，不能视为最终清单。

## 本次验证方式与实际结果

Codex 通过 GitHub 读取固定 SHA 的代码、契约、报告、CI job 状态；在独立 scratch 目录还原该 SHA 的 server/app 文本文件，使用当前环境 Python 3.12 + FastAPI TestClient、python-docx/reportlab 生成无隐私样本，真实调用服务。未修改主仓库业务代码，未重跑完整 npm/pytest（本地未装 pytest），未做安卓真机验收。以下复现与 CI 通过可以同时成立：现有测试没有覆盖这些完整路径。

| ID / 级别 | 证据与影响 | 负责方 |
| --- | --- | --- |
| R4-01 / P0 | main.py:464 使用 record.code/message/count；parsers/types.py 定义 DocumentWarningRecord 为 TypedDict，PDF/DOCX 实际给 dict 且部分 count 缺省。真实正常 DOCX 与文本 PDF 经 HTTP 上传都抛 AttributeError: 'dict' object has no attribute 'code'（关闭异常透传时为 500），无法保存 | DSH 立即修入口；Li 交标准序列化与端到端回归 |
| R4-02 / P1 | lore_entries/service.py 先 SELECT 比较 revision，后 UPDATE 只有 WHERE id，无原子版本条件。使用两个独立连接，在校验 title 处用 barrier 确保二者都读到 rev1，再并发提交：Writer A、B 均成功且都返回 rev2，后写覆盖前写 | Ma 原子修复；DSH HTTP/事务接线 |
| R4-03 / P1 | 来源契约说全文 UTF-16，但 service.py:202 接受“绝对失败后按相对猜测”。实测第二块 [1610,3219) 传 [0,1) 被 201 接受并悄悄改写成 [1610,1611)，非法绝对引用变成另一段合法文字 | Ma；DSH 修契约错误公式 |
| R4-04 / P1 | LoreEntriesPanel.tsx:753 定位来源固定 chunkIndex:0；来源 DTO 没有实际 chunk_index。后续块点击会展开错误块；DocumentViewer 首次只装第一页，超出首批的指定块也需实际纳入可见列表 | Marcin 修消费者；Ma additive 元数据；DSH resolver/DTO |
| R4-05 / P1 | WorldLoreView.tsx:201 起仍将 null 坐标补 0；LoreSearchPanel 也不按 match_field 区分标题/文件名点击。后端 B7 已修，前端链路仍可能出现虚假区间，报告“层已完成”与当前代码不符 | DSH 薄适配；Marcin 检索 UI |
| R4-06 / P1 | DocumentUpload 逐格式 prop 已存在，但 WorldLoreView 只存 formats、按 rich boolean 给所有格式 8MiB，未传 capabilities.max_bytes。自定义服务器限额及 TXT 2MiB 提示仍不一致 | DSH 接线并加集成检查 |
| R4-07 / P1 | 条目表单每次 PATCH 都发全部 source_links，后端对所有来源要求 active；一旦文档软删，用户仅改标题也可能被旧引用阻断。当前“保留历史引用”需要编辑语义闭环 | Ma 保留现有引用；Marcin PATCH 差异；DSH 契约 |
| R4-08 / P1 | 条目列表仅以 isMountedRef 判断旧响应；切世界 cleanup 后下一 effect 又置 true。写操作和详情/冲突请求也无请求世代检查。草稿保存在面板 useRef，切资料库 tab 会卸载面板，取消后暂存内容也随之丢失 | Marcin 生命周期/草稿；DSH 持有容器 |
| R4-09 / P1 验收缺口 | tests/lore-entries/entries-panel.test.ts 多数只测试测试文件自己定义的 Map、标签、模拟处理函数，并未导入真实组件/控制器。绿灯不能证明实际点击、409 或卸载保护 | Marcin 真正组件测试；DSH 测试框架/CI |
| R4-10 / P2 | PDF/DOCX 先累积全部页/块，最后才核对文本上限；PDF 的 HEADERS_FOOTERS_SKIPPED 机器码却搭配“不支持分离”，会误导已去掉页眉页脚。需逐步预算与诚实告警，不能宣称形成完整进程内资源隔离 | Li |

## 冻结结论

M1 的代码范围已明显扩大，但目前不能宣布 M1 全部完成。R4 先解决导入成功路径、来源正确性、并发一致性及真实 UI 测试，再建立 M1 验收记录。M2 只出下一阶段设计，不接模型或 Key，不扩大到账户、OCR、云同步、PWA 或正式状态引擎。业务修复由四助手执行，本次提交只更新审阅与任务文档。
