#最后复核“整月转正”的结果是不是可信
# maker_v4_1_day_halt_single_day_check.py
# 目的：
# 用和月度脚本完全一致的口径，单独复核 2026-01-08 / 2026-01-10
# 看是否能复现之前三天测试里的 baseline_v4_1_day_halt 结果

import os
import json
import pandas as pd
import numpy as np

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 2000)

# =========================
# A. 配置
# =========================
YEAR = 2026
MONTH = 1
CHECK_DAYS = [8, 10]

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

# 之前三天测试里的参考值
expected_results = {
    "2026-01-08": {
        "trade_count": 5839,
        "total_pnl": -50122.677789,
        "max_drawdown": 52080.627107,
        "sharpe_ratio": -83.784824,
    },
    "2026-01-10": {
        "trade_count": 61082,
        "total_pnl": 69581.993874,
        "max_drawdown": 64.747840,
        "sharpe_ratio": 299.688283,
    }
}

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

    return pd.DataFrame(records)

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
                })
            else:
                remaining_positions.append(pos)

        open_positions = remaining_positions

        # 日内停机
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

    if trades.empty:
        return {
            "trade_count": 0,
            "total_pnl": 0.0,
            "max_drawdown": np.nan,
            "sharpe_ratio": np.nan,
        }

    trades = trades.sort_values("exit_time").reset_index(drop=True)
    trades["cum_pnl"] = trades["total_pnl"].cumsum()

    return {
        "trade_count": len(trades),
        "total_pnl": trades["total_pnl"].sum(),
        "max_drawdown": calc_max_drawdown(trades["cum_pnl"]),
        "sharpe_ratio": calc_sharpe(trades["total_pnl"]),
    }

# =========================
# G. 主程序
# =========================
rows = []

print("===== v4.1 单日复核开始 =====")
print("复核目标：月度脚本口径是否能复现之前三天测试里的 baseline_v4_1_day_halt 结果")

for day in CHECK_DAYS:
    day_str = f"{YEAR:04d}-{MONTH:02d}-{day:02d}"
    print(f"\n----- 正在复核 {day_str} -----")

    base_df = prepare_day_features(day)
    actual = run_strategy(base_df)
    expected = expected_results[day_str]

    row = {
        "day": day_str,
        "expected_trade_count": expected["trade_count"],
        "actual_trade_count": actual["trade_count"],
        "trade_count_match": expected["trade_count"] == actual["trade_count"],

        "expected_total_pnl": expected["total_pnl"],
        "actual_total_pnl": actual["total_pnl"],
        "total_pnl_diff": actual["total_pnl"] - expected["total_pnl"],

        "expected_max_drawdown": expected["max_drawdown"],
        "actual_max_drawdown": actual["max_drawdown"],
        "max_drawdown_diff": actual["max_drawdown"] - expected["max_drawdown"],

        "expected_sharpe_ratio": expected["sharpe_ratio"],
        "actual_sharpe_ratio": actual["sharpe_ratio"],
        "sharpe_diff": actual["sharpe_ratio"] - expected["sharpe_ratio"],
    }
    rows.append(row)

check_df = pd.DataFrame(rows)

print("\n===== 单日复核结果 =====")
print(check_df.to_string(index=False))