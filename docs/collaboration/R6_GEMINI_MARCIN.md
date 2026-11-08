# R6-M｜Marcin：模型叙事实验手机界面

独占src/features/narration-preview/**、tests/ui/narration-preview/**、本人r6报告。先读M2_A_API；对接注入api，无需等路由；DSH负责src/api、WorldLoreView和依赖。不重写现有故事页面。

交付完整NarrationPreview：明确Fake/Live切换，提示为未写入存档的草案；配置base_url/model/password Key，提交成功立即清空Key，失败仅当前输入保留供修正，离开组件清理，禁止存入浏览器存储。会话连接测试、移除、过期提示，隐藏凭据原文，明确连接测试可能产生少量费用。不要每次输入自动测连通。

自由行动输入中文友好，生成/停止/手动重试；生成中禁重复提交，失败/停止保留草稿；切世界和卸载abort，迟到响应不能污染新页。不能承诺停止未扣费。不使用M0预置剧情伪装Live成功。

结果显示叙事、mode与来源列表，无引用标明未引用；纯React文本，不注入HTML。来源点击调用注入onOpenSource，不自己猜chunk0；模型输出不修改角色/时间/存档。会话ID仅会话内存，可选组件重挂时重新连接，先不做持久化。

真实RTL组件测试：成功/错误/停止/重试、Key清空与无持久化、过期、跨世界迟到响应、来源回跳payload、Fake标签。使用注入stub/deferred Promise，不能复制生产handler自测。npm test/test:ui/build实际运行，缺依赖记未运行。交包只改独占路径，公共类型需求写报告由DSH处理。
