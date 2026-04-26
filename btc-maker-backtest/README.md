# BTC Maker Strategy 回测项目

## 一、项目简介
本项目基于 1 个月的 BTC trades 与 order book 数据，对 BTC maker strategy 进行回测，目标是评估策略是否能够满足以下要求：

- 月度 turnover > 500 倍
- 评估 Sharpe Ratio
- 评估 Calmar Ratio

回测中，maker rebate 按 **-0.5 bps** 的费率口径处理，等价于成交后获得 **0.5 bps 的 rebate 收益**。

---

## 二、最终策略版本
最终选择的版本为：

**baseline_v4_1_day_halt**

其含义为：

- 主信号：`alpha2 only`
- 风控模块：`v4.1 day halt`

---

## 三、Alpha 定义

### alpha1
基于 trades 的成交流方向信号。

构造方式：
- 主动买成交记正
- 主动卖成交记负
- 形成每秒 `flow`
- 再平滑得到 `flow_smooth`

作用：
- 研究短期成交流是否具有方向预测力
- 最终结论是：`alpha1` 有一定信息，但整体较弱，更适合作为辅助过滤器

### alpha2
基于盘口一档不平衡的主信号。

定义为：

`imbalance_1 = (best_bid_size - best_ask_size) / (best_bid_size + best_ask_size)`

作用：
- 衡量买一和卖一的相对强弱
- 最终结论是：`alpha2` 明显强于 `alpha1`，因此被选为主信号

### alpha3
基于成交流活跃度的风险/环境过滤器。

构造方式：
- 对每秒 `flow` 取绝对值，形成 `abs_flow`
- 平滑后得到 `abs_flow_smooth`

作用：
- 过滤掉过于剧烈、可能对 maker 不友好的时刻
- 最终结论是：`alpha3` 有一定辅助价值，但不是核心改进来源

---

## 四、最终策略逻辑

### 1. 信号
仅使用 `alpha2`：
- 当 `imbalance_1 > threshold` 时，在 best bid 挂买单
- 当 `imbalance_1 < -threshold` 时，在 best ask 挂卖单

### 2. 成交逻辑
使用短未来窗口的路径触达逻辑：
- 买单若未来 best bid 路径触达挂单价，则视为成交
- 卖单若未来 best ask 路径触达挂单价，则视为成交

### 3. 持仓与平仓
- 固定持仓时间：`10 秒`
- 到期后使用 future mid-price 近似平仓

### 4. 风控逻辑（v4.1 day halt）
- 当短期 rolling volatility 高于当日 99% 分位数时，暂停新开仓
- 当日内累计亏损达到 `-50,000` 后，当天剩余时间只平不开
- 已有仓位正常平仓，不做强平

---

## 五、核心参数
- capital = `1,000,000`
- leverage = `3`
- max_notional = `3,000,000`
- unit_size = `0.2 BTC`
- max_inventory_btc = `2.0`
- alpha2_threshold = `0.3`
- holding_seconds = `10`
- fill_window_seconds = `2`
- rebate_rate = `0.00005`
- vol_window = `30`
- halt_vol_q = `0.99`
- loss_limit = `-50,000`

---

## 六、代码文件说明

### `maker_v4_1_day_halt_monthly.py`
最终版本的月度回测脚本，用于得到：
- 每日结果
- 月度汇总结果

### `maker_v4_1_day_halt_single_day_check.py`
单日复核脚本，用于验证月度脚本与单日逻辑一致。

### `maker_v3_5_monthly_comparison.py`
无风控版本的月度比较脚本，用于对比：
- `baseline`
- `alpha1_mild_100`

---

## 七、月度结果

### baseline
- total_pnl = `-7,394,709`
- monthly_sharpe_ratio = `-291.100542`
- monthly_calmar_ratio = `-0.950156`
- monthly_turnover_multiple = `59,837.77`

### alpha1_mild_100
- total_pnl = `-6,981,018`
- monthly_sharpe_ratio = `-282.741738`
- monthly_calmar_ratio = `-0.950481`
- monthly_turnover_multiple = `56,729.75`

### baseline_v4_1_day_halt
- trade_count = `1,299,228`
- avg_raw_pnl = `-0.496660`
- avg_rebate = `0.905549`
- avg_total_pnl = `0.408889`
- win_rate = `0.787554`
- total_pnl = `531,240.241067`
- max_drawdown = `301,457.590253`
- monthly_sharpe_ratio = `1.819222`
- monthly_calmar_ratio = `1.762239`
- monthly_turnover_multiple = `47,060.571707`

---

## 八、结果解读
主要结论如下：

1. 月度 turnover 要求被大幅满足；
2. 单纯增加 alpha 过滤，无法从根本上扭转整月亏损；
3. 真正带来结构性改善的是 `v4.1 day halt` 风控；
4. 最终版本将策略从整月巨亏改善为整月盈利。

但也需要说明：

- 最终版本中 `avg_raw_pnl` 仍为负；
- 当前正收益主要来自：
  - maker rebate
  - 风控对灾难日尾部亏损的截断

因此，该结果更准确地说是一个：

**依赖 rebate capture，并通过风险控制实现稳定化的 maker 策略框架**

而不是已经具备强 standalone raw alpha 的方向性策略。

---

## 九、数据与建模限制
当前框架仍存在以下限制：

1. 主信号主要依赖一档盘口，未充分利用更高层级盘口结构；
2. 缺少更细粒度的同秒事件顺序与 queue position 信息；
3. fill 模型仍为研究型近似，而非真实撮合模拟；
4. `day halt` 风控有效，但属于较硬的截断式风控，未来仍可研究更精细的恢复或风险预算机制。

---

## 十、结果验证
使用 `maker_v4_1_day_halt_single_day_check.py` 对以下日期进行了单日复核：

- `2026-01-08`
- `2026-01-10`

复核结果与之前三天测试中的 `baseline_v4_1_day_halt` 完全一致，仅存在浮点误差，说明最终月度结果可信。