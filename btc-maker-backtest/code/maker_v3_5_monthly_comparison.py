# maker_v3_5_monthly_comparison.py
# 目的：
# 在 2026-01 整月上比较两个版本：
# - baseline
# - alpha1_mild_100
#
# 逻辑：
# 1) 逐日读取 trades 和 ob
# 2) trades 用“前一天 + 当天”，整体 +8 小时后切本地日
# 3) ob 读取当天文件，切本地日
# 4) 每天跑两个版本
# 5) 输出逐日结果 + 月度汇总
#
# 固定底座：
# - capital = 1,000,000 USDT
# - leverage = 3x
# - max_notional = 3,000,000 USDT
# - unit_size = 0.2 BTC
# - max_inventory_btc = 2 BTC
# - alpha2 threshold = 0.3
# - fill_window = 2 秒
# - 持有 10 秒
# - 只保留开仓 maker rebate
#
# 重要：
# - 本代码默认从 2026-01-02 开始跑到 2026-01-31
#   因为本地日切片需要“前一天 + 当天”的 trades 文件
# - 如果你以后补了 2025-12-31 trades 文件，再考虑把 01-01 加进来
#
# 建议：
# - 跑之前关闭 VPN
# - 插电
# - 不要同时开太多重程序

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

TRADES_DIR = "/Users/wushenghong/Desktop/okx trades btc 2026-01"
OB_DIR = "/Users/wushenghong/Desktop/okx ob btc 2026-01"

# 默认从 2 号开始，因为需要前一天 trades 文件
RUN_DAYS = list(range(2, 32))

capital = 1_000_000.0
leverage = 3.0
max_notional = capital * leverage

alpha2_threshold = 0.3
holding_seconds = 10
fill_window_seconds = 2
rebate_rate = 0.00005
unit_size = 0.2
max_inventory_btc = 2.0

STRATEGY_LIST = ["baseline", "alpha1_mild_100"]

# =========================
# B. 文件路径函数
# =========================
def trades_csv_path(day: int) -> str:
    return os.path.join(TRADES_DIR, f"BTC-USDT-SWAP-trades-{YEAR:04d}-{MONTH:02d}-{day:02d}.csv")

def ob_data_path(day: int) -> str:
    return os.path.join(OB_DIR, f"BTC-USDT-SWAP-L2orderbook-400lv-{YEAR:04d}-{MONTH:02d}-{day:02d}.data")

# =========================
# C. 数据读取函数
# =========================
def load_trades_one_local_day(prev_day: int, curr_day: int):
    local_start = pd.Timestamp(f"{YEAR:04d}-{MONTH:02d}-{curr_day:02d} 00:00:00")
    local_end = pd.Timestamp(f"{YEAR:04d}-{MONTH:02d}-{curr_day:02d} 23:59:59")

    prev_path = trades_csv_path(prev_day)
    curr_path = trades_csv_path(curr_day)

    usecols = ["created_time", "side", "size"]
    df_prev = pd.read_csv(prev_path, usecols=usecols)
    df_curr = pd.read_csv(curr_path, usecols=usecols)

    df = pd.concat([df_prev, df_curr], ignore_index=True)

    # 关键：trades 时间整体 +8 小时
    df["datetime"] = pd.to_datetime(df["created_time"], unit="ms") + pd.Timedelta(hours=8)
    df = df[(df["datetime"] >= local_start) & (df["datetime"] <= local_end)].copy()
    df = df.sort_values("datetime").reset_index(drop=True)

    df["signed_size"] = np.where(df["side"] == "buy", df["size"], -df["size"])
    return df

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
                imbalance_1 = (best_bid_size - best_ask_size) / denom if denom != 0 else 0

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
# D. 特征构造
# =========================
def prepare_day_features(day: int):
    df_trades = load_trades_one_local_day(day - 1, day)
    df_trades = df_trades.set_index("datetime")

    flow = df_trades["signed_size"].resample("1s").sum()
    flow_df = pd.DataFrame({"flow": flow})
    flow_df["flow_smooth"] = flow_df["flow"].rolling(5).mean()

    ob_df = load_ob_one_local_day(day)
    ob_df = ob_df.sort_values("datetime").reset_index(drop=True)
    ob_df = ob_df.set_index("datetime")

    result_base = ob_df.resample("1s").last().dropna().copy()
    result_base = result_base.join(flow_df[["flow", "flow_smooth"]], how="left")

    result_base["flow"] = result_base["flow"].fillna(0)
    result_base["flow_smooth"] = result_base["flow_smooth"].fillna(0)

    result_base["alpha2_signal"] = result_base["imbalance_1"].apply(
        lambda x: 1 if x > alpha2_threshold else (-1 if x < -alpha2_threshold else 0)
    )

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
# E. 信号函数
# =========================
def make_alpha1_signal(flow_smooth_value, threshold):
    if flow_smooth_value > threshold:
        return 1
    elif flow_smooth_value < -threshold:
        return -1
    else:
        return 0

def build_signal(df, strategy_name):
    out = df.copy()

    if strategy_name == "baseline":
        out["signal"] = out["alpha2_signal"]

    elif strategy_name == "alpha1_mild_100":
        out["alpha1_signal"] = out["flow_smooth"].apply(lambda x: make_alpha1_signal(x, 100))
        out["signal"] = out.apply(
            lambda row: 1 if row["alpha2_signal"] == 1 and row["alpha1_signal"] != -1
            else (-1 if row["alpha2_signal"] == -1 and row["alpha1_signal"] != 1 else 0),
            axis=1
        )

    else:
        raise ValueError(f"Unknown strategy_name: {strategy_name}")

    return out

# =========================
# F. 风险函数
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
# G. 单日回测
# =========================
def run_backtest(base_df, strategy_name):
    df = build_signal(base_df, strategy_name)

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
    n = len(df)

    for i in range(n):
        current_row = df.iloc[i]
        current_time = current_row["datetime"]

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
                    "turnover": turnover
                })
            else:
                remaining_positions.append(pos)

        open_positions = remaining_positions

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
            "trades_df": pd.DataFrame()
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
        "trades_df": trades
    }

# =========================
# H. 月度主循环
# =========================
daily_results = []
monthly_trade_store = {strategy: [] for strategy in STRATEGY_LIST}

print("===== v3.5 月度回测开始 =====")

for day in RUN_DAYS:
    day_str = f"{YEAR:04d}-{MONTH:02d}-{day:02d}"
    print(f"\n----- 正在处理 {day_str} -----")

    result_base = prepare_day_features(day)

    for strategy_name in STRATEGY_LIST:
        res = run_backtest(result_base, strategy_name)

        trades_df = res.pop("trades_df")
        if not trades_df.empty:
            trades_df = trades_df.copy()
            trades_df["day"] = day_str
            trades_df["strategy"] = strategy_name
            monthly_trade_store[strategy_name].append(trades_df)

        row = {"day": day_str, "strategy": strategy_name}
        row.update(res)
        daily_results.append(row)

        print(
            f"{strategy_name}: "
            f"trade_count={row['trade_count']}, "
            f"total_pnl={row['total_pnl']:.2f}, "
            f"sharpe={row['sharpe_ratio']:.4f}, "
            f"calmar={row['calmar_ratio']:.4f}"
        )

print("\n===== v3.5 月度逐日结果 =====")
daily_df = pd.DataFrame(daily_results)
print(daily_df.to_string(index=False))

# =========================
# I. 月度汇总
# =========================
monthly_summary = []

for strategy_name in STRATEGY_LIST:
    if monthly_trade_store[strategy_name]:
        all_trades = pd.concat(monthly_trade_store[strategy_name], ignore_index=True)
        all_trades = all_trades.sort_values(["exit_time", "entry_time"]).reset_index(drop=True)
        all_trades["cum_pnl"] = all_trades["total_pnl"].cumsum()

        total_pnl = all_trades["total_pnl"].sum()
        max_drawdown = calc_max_drawdown(all_trades["cum_pnl"])
        sharpe_ratio = calc_sharpe(all_trades["total_pnl"])
        calmar_ratio = calc_calmar(total_pnl, max_drawdown)
        monthly_turnover_multiple = all_trades["turnover"].sum() / capital

        monthly_summary.append({
            "strategy": strategy_name,
            "trade_count": len(all_trades),
            "avg_raw_pnl": all_trades["raw_pnl"].mean(),
            "avg_rebate": all_trades["total_rebate"].mean(),
            "avg_total_pnl": all_trades["total_pnl"].mean(),
            "win_rate": (all_trades["total_pnl"] > 0).mean(),
            "total_pnl": total_pnl,
            "std_total_pnl": all_trades["total_pnl"].std(),
            "median_total_pnl": all_trades["total_pnl"].median(),
            "max_drawdown": max_drawdown,
            "monthly_sharpe_ratio": sharpe_ratio,
            "monthly_calmar_ratio": calmar_ratio,
            "monthly_turnover_multiple": monthly_turnover_multiple
        })
    else:
        monthly_summary.append({
            "strategy": strategy_name,
            "trade_count": 0,
            "avg_raw_pnl": np.nan,
            "avg_rebate": np.nan,
            "avg_total_pnl": np.nan,
            "win_rate": np.nan,
            "total_pnl": 0.0,
            "std_total_pnl": np.nan,
            "median_total_pnl": np.nan,
            "max_drawdown": np.nan,
            "monthly_sharpe_ratio": np.nan,
            "monthly_calmar_ratio": np.nan,
            "monthly_turnover_multiple": 0.0
        })

monthly_summary_df = pd.DataFrame(monthly_summary)

print("\n===== v3.5 月度汇总结果 =====")
print(monthly_summary_df.to_string(index=False))

print("\n===== 按月度 Sharpe 排序 =====")
print(monthly_summary_df.sort_values(by=["monthly_sharpe_ratio", "monthly_calmar_ratio"], ascending=[False, False]).to_string(index=False))

print("\n===== 按月度 total_pnl 排序 =====")
print(monthly_summary_df.sort_values(by=["total_pnl", "monthly_sharpe_ratio"], ascending=[False, False]).to_string(index=False))

print("\n===== v3.5 月度回测完成 =====")