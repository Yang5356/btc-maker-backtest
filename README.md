# BTC Maker Strategy 回测研究

## 一、项目简介

本项目基于 1 个月的 BTC 高频 **trades** 与 **order book** 数据，研究并回测一个短周期 **maker strategy**，目标是评估该策略是否能够满足以下要求：

- 月度 turnover > 500 倍
- 评估 Sharpe Ratio
- 评估 Calmar Ratio

项目最初并不是直接从完整策略出发，而是先做 **alpha research**，分别研究了：

- 基于成交流的 `alpha1`
- 基于盘口一档不平衡的 `alpha2`
- 基于成交活跃度的环境过滤器 `alpha3`

在此基础上，逐步搭建 maker 回测框架，并最终引入风险控制模块，将策略从月度巨亏改善为月度盈利。 [oai_citation:0‡第一个alpha.docx](sediment://file_00000000063c7207a3d2e1a53a62a152)

> **说明**：本仓库不包含原始 trades 与 order book 数据，仅保留研究框架、代码、图表与汇总结果。

---

## 二、研究思路

这个项目的研究路径可以概括为：

1. **先做 alpha validation，而不是直接做完整策略**
2. **确认哪个 alpha 更适合作为 maker 主信号**
3. **搭建 maker 回测原型**
4. **逐步修正成交假设与账户约束**
5. **测试辅助 filter 的边际贡献**
6. **识别整月失效的真正原因**
7. **设计风险控制并完成整月验证**

这条主线很重要，因为项目最终的核心收获不是“多叠了几个 alpha”，而是发现：

> 在当前回测框架下，**单纯 alpha filter 改善有限**；真正带来结构性改善的，是对灾难日尾部风险的压缩。

---

## 三、Alpha 定义

### 1. alpha1：基于 trade flow 的短期方向信号

`alpha1` 来自 trades 数据。

构造方式：
- 将主动买成交记为正、主动卖成交记为负
- 得到每秒聚合的 `flow`
- 再对 `flow` 做平滑，得到 `flow_smooth`

它反映的是：

> 最近几秒 taker 主动成交压力偏向哪一边。 [oai_citation:1‡第一个alpha.docx](sediment://file_00000000063c7207a3d2e1a53a62a152)

研究结论：
- `alpha1` 有一定短周期 edge
- 但整体较弱、噪声较大
- 更适合作为辅助信号，而不是主策略核心 [oai_citation:2‡第一个alpha.docx](sediment://file_00000000063c7207a3d2e1a53a62a152)

---

### 2. alpha2：基于盘口一档不平衡的主信号

`alpha2` 来自 order book 数据。

核心定义为：

`imbalance_1 = (best_bid_size - best_ask_size) / (best_bid_size + best_ask_size)`

它反映的是：

> 当前最优买卖盘口的相对厚度，衡量短周期内哪一边的挂单结构更强。 [oai_citation:3‡第一个alpha.docx](sediment://file_00000000063c7207a3d2e1a53a62a152)

研究方式：
- 测试多个 threshold
- 测试多个 horizon：1s / 5s / 10s

核心结论：
- threshold 越高，平均收益通常越好
- 1s 有 edge
- 5s 更强
- 10s 仍在增强
- 胜率也随着 horizon 拉长而改善

因此：

> `alpha2` 明显强于 `alpha1`，更适合作为 maker 策略的核心主信号。

---

### 3. alpha3：基于成交活跃度的环境过滤器

`alpha3` 不是方向信号，而是一个 **trades-based activity-risk filter**。

构造方式：
- 先按秒计算 `flow`
- 取绝对值 `abs_flow = abs(flow)`
- 再做 rolling 平滑，得到 `abs_flow_smooth`

过滤规则：
- 只有当 `abs_flow_smooth <= threshold` 时，才允许 `alpha2` 开仓

它的作用不是判断涨跌，而是判断：

> 当前这几秒市场是不是太冲、太躁，不适合继续做 maker。 [oai_citation:4‡第一个alpha.docx](sediment://file_00000000063c7207a3d2e1a53a62a152)

研究结论：
- `alpha3` 在逻辑上合理，也有一定辅助价值
- 但边际贡献不如 `alpha2` 明显
- 在当前框架下，单纯靠 filter 并没有从根本上改变整月结果

---

## 四、从 Alpha Research 到 Maker Strategy

在确认 `alpha2` 更强之后，项目从“信号研究”进入了“maker 回测原型”。

第一版 maker 原型的核心设定是：

- 主信号只用 `alpha2`
- 当 `imbalance_1 > 0.3` 时，在 best bid 挂买单
- 当 `imbalance_1 < -0.3` 时，在 best ask 挂卖单
- 持有 10 秒
- 用 future mid-price 近似平仓
- 计入双边 maker rebate
- 固定单位仓位

这一版的目标不是求最真实，而是：

> **先把 maker 的完整逻辑闭环跑通**——什么时候挂 bid / ask、什么情况下算成交、成交后如何赚钱、rebate 如何加入。

---

## 五、策略框架演进

### 1. v1：先跑通闭环，但很快发现过于乐观
最早版本的问题在于：
- 成交假设过于宽松
- 容易出现“几乎白拿 spread + rebate”的错觉
- 会明显高估 maker 策略表现

因此，后续项目的重点逐渐转向：
- 更认真地处理 fill 逻辑
- 加入账户级约束
- 避免原型策略过于乐观 [oai_citation:5‡第一个alpha.docx](sediment://file_00000000063c7207a3d2e1a53a62a152)

### 2. v2 / v3：逐步修正成交假设与过滤逻辑
后续版本中，项目逐步引入：
- 更保守的 fill 近似
- 仓位、杠杆、名义本金等账户约束
- `alpha1` / `alpha3` 等过滤器测试

这一步的重要发现是：

> 即使辅助 filter 有一定改善，**也无法从根本上解释整月表现恶化**。真正主导结果的，是少数灾难日的大额尾部亏损。

### 3. v4.1：风险控制成为核心
最终版本的关键改动，不是再加新 alpha，而是：

- 高波动暂停开仓
- 日内累计亏损达到阈值后停机（day halt）

这一思路与项目后期总结一致：

> v4 最该改的不是再加更多 alpha，而是重构 maker 框架本身，优先解决 tail risk。 [oai_citation:6‡第一个alpha.docx](sediment://file_0000000080fc720ba2f13c1591bb0f9a)

---

## 六、最终版本

最终入选版本为：

**baseline_v4_1_day_halt**

其核心结构是：

- 主信号：`alpha2 only`
- 风控模块：`v4.1 day halt`

风控逻辑包括：
- 当短期 rolling volatility 过高时，暂停新开仓
- 当日内累计亏损达到阈值后，当天只平不开

最终结论可以概括为：

> 当前回测框架下，单纯 alpha filter 改善有限，而 `v4.1 day_halt` 风控显著压缩了灾难日尾部风险，并将 `alpha2 only` 策略从整月巨亏改善为整月盈利；该结果已通过单日复核验证，脚本口径一致。

---

## 七、月度结果

### baseline
- total_pnl = `-7,394,709`
- monthly_sharpe_ratio = `-291.10`
- monthly_calmar_ratio = `-0.950`
- monthly_turnover_multiple = `59,837.77`

### alpha1_mild_100
- total_pnl = `-6,981,018`
- monthly_sharpe_ratio = `-282.74`
- monthly_calmar_ratio = `-0.950`
- monthly_turnover_multiple = `56,729.75`

### baseline_v4_1_day_halt
- trade_count = `1,299,228`
- avg_raw_pnl = `-0.49666`
- avg_rebate = `0.905549`
- avg_total_pnl = `0.408889`
- win_rate = `0.787554`
- total_pnl = `531,240.24`
- max_drawdown = `301,457.59`
- monthly_sharpe_ratio = `1.819222`
- monthly_calmar_ratio = `1.762239`
- monthly_turnover_multiple = `47,060.57`

可以看到：

- turnover 远高于 500 倍/月要求
- 单纯加 `alpha1` 的改善有限
- 真正将结果拉回来的，是风险控制

---

## 八、代表性结论

### 1. alpha2 比 alpha1 更适合作为主信号
因为它：
- 更强
- 更稳
- 规律更一致
- 随 threshold 提高而改善
- 在 5s / 10s horizon 上表现更好 [oai_citation:7‡第一个alpha.docx](sediment://file_00000000063c7207a3d2e1a53a62a152)

### 2. 两类数据都发挥了作用
- trades 提供成交行为信息（alpha1 / alpha3）
- order book 提供盘口结构信息（alpha2）

也就是说：
- trade flow 反映的是 **taker 主动成交压力**
- order book imbalance 反映的是 **maker 挂单结构偏向** [oai_citation:8‡第一个alpha.docx](sediment://file_00000000063c7207a3d2e1a53a62a152)

### 3. 风控比“再加几个 filter”更重要
这是整个项目最核心的研究收获之一。

---

## 九、局限性与反思

这个项目也有一些需要诚实保留的局限：

### 1. raw pnl 仍为负
这点非常关键：

> `raw pnl` 仍为负，说明当前正收益主要来自 maker rebate 与风险控制，而不是裸 alpha 本身已经足够强。

### 2. 主信号仍主要依赖一档盘口
如果能进一步使用更完整的盘口层级信息（如多档 bid/ask 价格与对应数量），可能有助于增强 alpha 研究与市场状态刻画。 [oai_citation:9‡第一个alpha.docx](sediment://file_00000000063c7207a3d2e1a53a62a152)

### 3. 微观事件顺序与真实撮合信息有限
当前框架缺少：
- 更细粒度的同步事件排序
- queue position
- 更真实的撮合优先级建模

因此，像 `alpha1` 这类依赖成交顺序的信息，可能尚未被完全体现。 [oai_citation:10‡第一个alpha.docx](sediment://file_00000000063c7207a3d2e1a53a62a152)

### 4. fill 模型仍属于研究型近似
虽然已经比最初版本更保守，但仍不是 production-level execution simulation。 [oai_citation:11‡第一个alpha.docx](sediment://file_00000000063c7207a3d2e1a53a62a152)

### 5. 风控仍较为粗糙
`day halt` 在当前框架下非常有效，但仍属于较硬的截断式风控。未来可继续研究：
- regime-based reopen logic
- inventory skew / reservation price
- 更真实的报价与退出逻辑 [oai_citation:12‡第一个alpha.docx](sediment://file_0000000080fc720ba2f13c1591bb0f9a)

---

## 十、仓库内容

本仓库包含：
- 项目总结与方法说明
- 三版本月度结果表
- 关键图表
- 最终月度回测代码
- 单日复核代码
- 无风控版本月度对比代码

不包含：
- 原始 trades 数据
- 原始 order book 数据

---

## 十一、项目结论

如果用一句话总结这个项目：

> 这不是一个“靠堆更多 alpha 获胜”的项目，而是一个通过 **alpha research → maker backtest → tail-risk control** 逐步识别问题、修正框架，并最终把策略从整月巨亏改善为整月盈利的研究过程。
