"""指标层：四层指标，纯 pandas 实现，不依赖 TA-Lib。

输入 data.py 的标准六列 DataFrame，输出追加指标列的 DataFrame。
"""
import numpy as np
import pandas as pd

RSI_N = 14        # RSI 周期（Wilder）
ROC_N = 20        # 涨速 ROC 周期
ATR_N = 14        # ATR 周期
HV_N = 20         # 历史波动率窗口
VOL_Z_N = 20      # 成交量 z-score 窗口
HIGH_N = 252      # 52 周高点窗口
SIGMA_N = 252     # 日收益 σ 估计窗口
EXTREME_N = 60    # 极端波动统计窗口


def compute(df):
    df = df.copy().sort_values("date").reset_index(drop=True)
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    # 1. 趋势与位置层
    df["ma50"] = c.rolling(50).mean()
    df["ma200"] = c.rolling(200, min_periods=60).mean()
    df["bias50"] = (c / df["ma50"] - 1) * 100
    df["bias200"] = (c / df["ma200"] - 1) * 100
    high_52w = c.rolling(HIGH_N, min_periods=20).max()
    df["drawdown"] = (c / high_52w - 1) * 100          # 距 52 周高点回撤 %（<=0）
    is_new_high = c >= high_52w
    df["days_since_high"] = (~is_new_high).groupby(is_new_high.cumsum()).cumsum()

    # 2. 动量与加速度层
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / RSI_N, min_periods=RSI_N, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / RSI_N, min_periods=RSI_N, adjust=False).mean()
    # loss=0 时 Wilder RSI 应为 100；gain/loss 同时为 0 时视为中性 50
    with np.errstate(divide='ignore', invalid='ignore'):
        rs = gain / loss
    rsi = 100 - 100 / (1 + rs)
    flat = (gain == 0) & (loss == 0)
    df["rsi14"] = rsi.where(~flat, 50)
    df["day_change"] = c.pct_change() * 100
    df["roc20"] = c.pct_change(ROC_N) * 100
    df["ret20"] = c.pct_change(20) * 100
    df["ret60"] = c.pct_change(60) * 100
    df["accel_diff"] = df["ret20"] - df["ret60"] / 3   # 近 20 日涨速 vs 60 日匀速外推
    up = (delta > 0).astype(int)
    df["up_days"] = up.groupby((up == 0).cumsum()).cumsum()

    # 3. 波动率层
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr14"] = tr.ewm(alpha=1 / ATR_N, min_periods=ATR_N, adjust=False).mean()
    df["atr_pct"] = df["atr14"] / c * 100
    with np.errstate(divide='ignore', invalid='ignore'):
        log_ret = np.log(c.replace(0, np.nan) / c.shift().replace(0, np.nan))
    df["hv20"] = log_ret.rolling(HV_N).std() * np.sqrt(252) * 100
    ret = c.pct_change()
    sigma = ret.rolling(SIGMA_N, min_periods=60).std()
    extreme = (ret.abs() > 3 * sigma).astype(float).where(sigma.notna())
    df["extreme60"] = extreme.rolling(EXTREME_N, min_periods=1).sum()

    # 4. 量能与情绪层（无成交量数据时整列为 NaN，上层自动忽略）
    vma = v.rolling(VOL_Z_N).mean()
    vsd = v.rolling(VOL_Z_N).std()
    df["vol_z"] = ((v - vma) / vsd).where(v.notna() & (vsd > 0))
    prev_low60 = c.rolling(60, min_periods=20).min().shift()
    df["div_lowvol_high"] = (is_new_high & (df["vol_z"] < 0)).fillna(False)      # 缩量新高
    df["div_vol_break"] = ((c < prev_low60) & (df["vol_z"] > 1.5)).fillna(False)  # 放量破位

    # 5. 波动结构补充：布林带宽 (20, 2σ)
    ma20 = c.rolling(20).mean()
    sd20 = c.rolling(20).std()
    df["bb_width"] = 4 * sd20 / ma20 * 100

    # 6. 背离与量能结构信号
    rsi = df["rsi14"]
    hi60 = c >= c.rolling(60, min_periods=20).max()           # 创 60 日新高
    lo60 = c <= c.rolling(60, min_periods=20).min()           # 创 60 日新低
    rsi_prev_high = rsi.where(hi60).ffill().shift()           # 前一个 60 日新高点的 RSI
    rsi_prev_low = rsi.where(lo60).ffill().shift()            # 前一个 60 日新低点的 RSI
    df["div_bear"] = (hi60 & (rsi < rsi_prev_high)).fillna(False)  # 顶背离
    df["div_bull"] = (lo60 & (rsi > rsi_prev_low)).fillna(False)   # 底背离

    ret5_abs = c.pct_change(5).abs() * 100
    df["vol_stall"] = ((df["vol_z"] > 1.5) & (ret5_abs < df["atr_pct"] * 5)
                       & (df["drawdown"] > -15)).fillna(False)     # 放量滞涨（高位区）
    low10 = c.rolling(10).min()
    prev_low50 = c.shift(10).rolling(50, min_periods=20).min()
    df["lowvol_stabil"] = ((df["drawdown"] < -30)
                           & (df["vol_z"].rolling(10).mean() < 0)
                           & (low10 > prev_low50)).fillna(False)   # 缩量企稳

    return df
