#带有风控的alpha2
# maker_v4_1_day_halt_monthly.py
# 目的：
# 跑最终入选版本的整月结果：
# baseline_v4_1_day_halt
#
# 策略：
# - alpha2 only
# - v4.1 风控：
#   1) 高波动暂停：current_vol > 当天 rolling_vol 的 q99 时，不开新仓
#   2) 日内累计亏损 <= -50,000 时，当天后面只平不开
#
# 数据：
# - 只使用 order book
# - 不再使用 trades / alpha1 / alpha3
#
# 输出：
# - 每日结果
# - 月度汇总结果

import os
import json
import pandas as pd
import numpy as np

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 2000)
pd.set_option("display.max_colwidth", None)

# =========================
# A. 配置
# =========================
YEAR = 2026
MONTH = 1
RUN_DAYS = list(range(1, 32))

OB_DIR = "/Users/wushenghong/Desktop/okx ob btc 2026-01"

capital = 1_000_000.0
leverage = 3.0
max_notional = capital * leverage

alpha2_threshold = 0.3
holding_seconds = 10
fill_window_seconds = 2
rebate_rate = 0.00005
unit_size = 0.2
max_inventory_btc = 2.0

LOSS_LIMIT = -50_000
HALT_VOL_Q = 0.99
VOL_WINDOW = 30

# =========================
# B. 文件路径
# =========================
def ob_data_path(day: int) -> str:
    return os.path.join(
        OB_DIR,
        f"BTC-USDT-SWAP-L2orderbook-400lv-{YEAR:04d}-{MONTH:02d}-{day:02d}.data"
    )

# =========================
# C. 读取 OB 单日本地日
# =========================
def load_ob_one_local_day(curr_day: int):
    local_start = pd.Timestamp(f"{YEAR:04d}-{MONTH:02d}-{curr_day:02d} 00:00:00")
    local_end = pd.Timestamp(f"{YEAR:04d}-{MONTH:02d}-{curr_day:02d} 23:59:59")

    ob_path = ob_data_path(curr_day)

    asks_book = {}
    bids_book = {}
    records = []

    with open(ob_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            msg = json.loads(line)
            ts = int(msg["ts"])
            dt = pd.to_datetime(ts, unit="ms")

            if dt < local_start:
                continue
            if dt > local_end:
                break

            for price_str, size_str, _ in msg["asks"]:
                price = float(price_str)
                size = float(size_str)
                if size == 0:
                    asks_book.pop(price, None)
                else:
                    asks_book[price] = size

            for price_str, size_str, _ in msg["bids"]:
                price = float(price_str)
                size = float(size_str)
                if size == 0:
                    bids_book.pop(price, None)
                else:
                    bids_book[price] = size

            if asks_book and bids_book:
                best_ask = min(asks_book.keys())
                best_bid = max(bids_book.keys())

                best_ask_size = asks_book[best_ask]
                best_bid_size = bids_book[best_bid]

                mid_price = (best_bid + best_ask) / 2
                spread = best_ask - best_bid

                denom = best_bid_size + best_ask_size
                imbalance_1 = (
                    (best_bid_size - best_ask_size) / denom if denom != 0 else 0
                )

                records.append({
                    "datetime": dt,
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "mid_price": mid_price,
                    "spread": spread,
                    "imbalance_1": imbalance_1
                })

    ob_df = pd.DataFrame(records)
    return ob_df

# =========================
# D. 构造日特征
# =========================
def prepare_day_features(day: int):
    ob_df = load_ob_one_local_day(day)
    ob_df = ob_df.sort_values("datetime").reset_index(drop=True)
    ob_df = ob_df.set_index("datetime")

    result_base = ob_df.resample("1s").last().dropna().copy()

    result_base["alpha2_signal"] = result_base["imbalance_1"].apply(
        lambda x: 1 if x > alpha2_threshold else (-1 if x < -alpha2_threshold else 0)
    )

    result_base["mid_ret"] = result_base["mid_price"].pct_change().fillna(0)
    result_base["rolling_vol"] = result_base["mid_ret"].rolling(VOL_WINDOW).std()

    result_base = result_base.reset_index()

    future_min_bid = pd.concat(
        [result_base["best_bid"].shift(-i) for i in range(1, fill_window_seconds + 1)],
        axis=1
    ).min(axis=1)

    future_max_ask = pd.concat(
        [result_base["best_ask"].shift(-i) for i in range(1, fill_window_seconds + 1)],
        axis=1
    ).max(axis=1)

    result_base["future_min_bid_in_window"] = future_min_bid
    result_base["future_max_ask_in_window"] = future_max_ask

    return result_base

# =========================
# E. 风险函数
# =========================
def calc_max_drawdown(cum_pnl_series: pd.Series) -> float:
    if cum_pnl_series.empty:
        return np.nan
    running_max = cum_pnl_series.cummax()
    drawdown = running_max - cum_pnl_series
    return drawdown.max()

def calc_sharpe(total_pnl_series: pd.Series) -> float:
    if total_pnl_series.empty:
        return np.nan
    std = total_pnl_series.std()
    if pd.isna(std) or std == 0:
        return np.nan
    return total_pnl_series.mean() / std * np.sqrt(len(total_pnl_series))

def calc_calmar(total_return: float, max_drawdown: float) -> float:
    if pd.isna(max_drawdown) or max_drawdown == 0:
        return np.nan
    return total_return / max_drawdown

# =========================
# F. 单日回测
# =========================
def run_strategy(base_df):
    df = base_df.copy()

    halt_vol_threshold = df["rolling_vol"].dropna().quantile(HALT_VOL_Q)

    # baseline = alpha2 only
    df["signal"] = df["alpha2_signal"]

    df["fill_long"] = (
        (df["signal"] == 1) &
        (df["future_min_bid_in_window"] <= df["best_bid"])
    )

    df["fill_short"] = (
        (df["signal"] == -1) &
        (df["future_max_ask_in_window"] >= df["best_ask"])
    )

    df["filled"] = df["fill_long"] | df["fill_short"]

    trade_records = []
    open_positions = []
    inventory = 0.0
    intraday_cum_pnl = 0.0
    day_halted = False

    n = len(df)

    for i in range(n):
        current_row = df.iloc[i]
        current_time = current_row["datetime"]

        # 先平仓
        remaining_positions = []
        for pos in open_positions:
            if pos["exit_idx"] == i:
                exit_mid_price = current_row["mid_price"]

                if pos["direction"] == "long":
                    raw_pnl = (exit_mid_price - pos["entry_price"]) * pos["size"]
                    inventory -= pos["size"]
                else:
                    raw_pnl = (pos["entry_price"] - exit_mid_price) * pos["size"]
                    inventory += pos["size"]

                entry_rebate = pos["entry_price"] * pos["size"] * rebate_rate
                total_rebate = entry_rebate
                total_pnl = raw_pnl + total_rebate
                turnover = (pos["entry_price"] + exit_mid_price) * pos["size"]

                intraday_cum_pnl += total_pnl

                trade_records.append({
                    "entry_time": pos["entry_time"],
                    "exit_time": current_time,
                    "direction": pos["direction"],
                    "entry_price": pos["entry_price"],
                    "exit_mid_price": exit_mid_price,
                    "size": pos["size"],
                    "raw_pnl": raw_pnl,
                    "total_rebate": total_rebate,
                    "total_pnl": total_pnl,
                    "turnover": turnover,
                    "intraday_cum_pnl_after_exit": intraday_cum_pnl
                })
            else:
                remaining_positions.append(pos)

        open_positions = remaining_positions

        # 日内累计亏损停机
        if intraday_cum_pnl <= LOSS_LIMIT:
            day_halted = True

        if day_halted:
            continue

        # 高波动暂停
        current_vol = current_row["rolling_vol"]
        if pd.notna(current_vol) and current_vol > halt_vol_threshold:
            continue

        if not current_row["filled"]:
            continue

        exit_idx = i + holding_seconds
        if exit_idx >= n:
            continue

        signal = current_row["signal"]
        current_mid = current_row["mid_price"]

        if signal == 1:
            new_inventory = inventory + unit_size
            new_notional = abs(new_inventory) * current_mid

            if new_inventory <= max_inventory_btc and new_notional <= max_notional:
                open_positions.append({
                    "entry_time": current_time,
                    "exit_idx": exit_idx,
                    "direction": "long",
                    "entry_price": current_row["best_bid"],
                    "size": unit_size
                })
                inventory = new_inventory

        elif signal == -1:
            new_inventory = inventory - unit_size
            new_notional = abs(new_inventory) * current_mid

            if abs(new_inventory) <= max_inventory_btc and new_notional <= max_notional:
                open_positions.append({
                    "entry_time": current_time,
                    "exit_idx": exit_idx,
                    "direction": "short",
                    "entry_price": current_row["best_ask"],
                    "size": unit_size
                })
                inventory = new_inventory

    trades = pd.DataFrame(trade_records)

    alpha2_signal_count = int((df["alpha2_signal"] != 0).sum())
    final_signal_count = int((df["signal"] != 0).sum())

    if trades.empty:
        return {
            "alpha2_signal_count": alpha2_signal_count,
            "final_signal_count": final_signal_count,
            "trade_count": 0,
            "fill_rate": np.nan,
            "avg_raw_pnl": np.nan,
            "avg_rebate": np.nan,
            "avg_total_pnl": np.nan,
            "win_rate": np.nan,
            "total_pnl": 0.0,
            "std_total_pnl": np.nan,
            "median_total_pnl": np.nan,
            "max_drawdown": np.nan,
            "sharpe_ratio": np.nan,
            "calmar_ratio": np.nan,
            "daily_turnover_multiple": 0.0,
            "halt_vol_threshold": halt_vol_threshold
        }

    trades = trades.sort_values("exit_time").reset_index(drop=True)
    trades["cum_pnl"] = trades["total_pnl"].cumsum()

    total_pnl = trades["total_pnl"].sum()
    max_drawdown = calc_max_drawdown(trades["cum_pnl"])
    sharpe_ratio = calc_sharpe(trades["total_pnl"])
    calmar_ratio = calc_calmar(total_pnl, max_drawdown)
    daily_turnover_multiple = trades["turnover"].sum() / capital

    return {
        "alpha2_signal_count": alpha2_signal_count,
        "final_signal_count": final_signal_count,
        "trade_count": len(trades),
        "fill_rate": len(trades) / final_signal_count if final_signal_count > 0 else np.nan,
        "avg_raw_pnl": trades["raw_pnl"].mean(),
        "avg_rebate": trades["total_rebate"].mean(),
        "avg_total_pnl": trades["total_pnl"].mean(),
        "win_rate": (trades["total_pnl"] > 0).mean(),
        "total_pnl": total_pnl,
        "std_total_pnl": trades["total_pnl"].std(),
        "median_total_pnl": trades["total_pnl"].median(),
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe_ratio,
        "calmar_ratio": calmar_ratio,
        "daily_turnover_multiple": daily_turnover_multiple,
        "halt_vol_threshold": halt_vol_threshold
    }

# =========================
# G. 主程序
# =========================
daily_results = []

print("===== baseline_v4_1_day_halt 月度回测开始 =====")

for day in RUN_DAYS:
    day_str = f"{YEAR:04d}-{MONTH:02d}-{day:02d}"
    print(f"\n----- 正在处理 {day_str} -----")

    base_df = prepare_day_features(day)
    res = run_strategy(base_df)
    res["day"] = day_str
    daily_results.append(res)

    print(
        f"trade_count={res['trade_count']}, "
        f"total_pnl={res['total_pnl']:.2f}, "
        f"max_dd={res['max_drawdown']:.2f}, "
        f"sharpe={res['sharpe_ratio']:.4f}"
    )

daily_df = pd.DataFrame(daily_results)

print("\n===== baseline_v4_1_day_halt 每日结果 =====")
print(daily_df.to_string(index=False))

# 月度汇总（按日聚合）
month_total_pnl = daily_df["total_pnl"].sum()
month_trade_count = daily_df["trade_count"].sum()
month_avg_total_pnl = (
    month_total_pnl / month_trade_count if month_trade_count > 0 else np.nan
)
month_avg_raw_pnl = np.average(
    daily_df["avg_raw_pnl"].fillna(0),
    weights=daily_df["trade_count"].replace(0, np.nan).fillna(0)
) if month_trade_count > 0 else np.nan
month_avg_rebate = np.average(
    daily_df["avg_rebate"].fillna(0),
    weights=daily_df["trade_count"].replace(0, np.nan).fillna(0)
) if month_trade_count > 0 else np.nan
month_win_rate = np.average(
    daily_df["win_rate"].fillna(0),
    weights=daily_df["trade_count"].replace(0, np.nan).fillna(0)
) if month_trade_count > 0 else np.nan

cum_daily_pnl = daily_df["total_pnl"].cumsum()
month_max_drawdown = calc_max_drawdown(cum_daily_pnl)
month_sharpe = calc_sharpe(daily_df["total_pnl"])
month_calmar = calc_calmar(month_total_pnl, month_max_drawdown)
month_turnover_multiple = daily_df["daily_turnover_multiple"].sum()

monthly_summary = pd.DataFrame([{
    "strategy": "baseline_v4_1_day_halt",
    "trade_count": month_trade_count,
    "avg_raw_pnl": month_avg_raw_pnl,
    "avg_rebate": month_avg_rebate,
    "avg_total_pnl": month_avg_total_pnl,
    "win_rate": month_win_rate,
    "total_pnl": month_total_pnl,
    "max_drawdown": month_max_drawdown,
    "monthly_sharpe_ratio": month_sharpe,
    "monthly_calmar_ratio": month_calmar,
    "monthly_turnover_multiple": month_turnover_multiple
}])

print("\n===== baseline_v4_1_day_halt 月度汇总 =====")
print(monthly_summary.to_string(index=False))