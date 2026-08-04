"""指标层单元测试。"""
import unittest

import numpy as np
import pandas as pd

from monitor.indicators import compute


def _make_df(closes, volumes=None):
    """用收盘价序列构造标准六列 DataFrame。"""
    n = len(closes)
    dates = pd.date_range("2020-01-01", periods=n)
    volumes = volumes if volumes is not None else [1000000] * n
    return pd.DataFrame({
        "date": dates,
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": volumes,
    })


class TestRSI(unittest.TestCase):
    def test_rsi_all_up_returns_100(self):
        closes = np.arange(100, 120, dtype=float)
        df = _make_df(closes)
        out = compute(df)
        # 第 14 天及以后应为 100（连续上涨，loss=0）
        self.assertAlmostEqual(out["rsi14"].iloc[14], 100.0, places=6)
        self.assertTrue((out["rsi14"].iloc[15:] == 100.0).all())

    def test_rsi_all_down_returns_0(self):
        closes = np.arange(120, 100, -1, dtype=float)
        df = _make_df(closes)
        out = compute(df)
        self.assertAlmostEqual(out["rsi14"].iloc[14], 0.0, places=6)
        self.assertTrue((out["rsi14"].iloc[15:] == 0.0).all())


class TestDivergence(unittest.TestCase):
    def test_bearish_divergence(self):
        """价格创新高，但 RSI 低于前高 → 顶背离。"""
        n = 80
        base = 100.0
        # 前 60 天温和上涨并创新高
        closes = np.concatenate([
            np.linspace(base, base + 20, 30),
            np.linspace(base + 20, base + 10, 20),
            np.linspace(base + 10, base + 25, 25),
            np.full(5, base + 25),
        ])
        # 人为让后期 RSI 走低：后期涨幅小、跌幅大
        closes[50:55] = base + 8
        df = _make_df(closes)
        out = compute(df)
        # 至少存在顶背离信号
        self.assertTrue(out["div_bear"].iloc[60:].any())

    def test_bullish_divergence(self):
        """价格创新低，但 RSI 高于前低 → 底背离。"""
        base = 100.0
        closes = np.concatenate([
            np.linspace(base, base - 20, 30),
            np.linspace(base - 20, base - 10, 20),
            np.linspace(base - 10, base - 25, 25),
            np.full(5, base - 25),
        ])
        # 人为让后期 RSI 走高：后期跌幅小、涨幅大
        closes[50:55] = base - 8
        df = _make_df(closes)
        out = compute(df)
        self.assertTrue(out["div_bull"].iloc[60:].any())


class TestVolumeStructure(unittest.TestCase):
    def test_lowvol_high(self):
        """缩量新高：价格创新高但成交量 z-score < 0。"""
        n = 70
        closes = np.linspace(100, 130, n)
        volumes = [1000000] * 60 + [300000] * 10  # 后 10 天缩量
        df = _make_df(closes, volumes)
        out = compute(df)
        self.assertTrue(out["div_lowvol_high"].iloc[-5:].any())

    def test_vol_break(self):
        """放量破位：价格跌破 60 日低点且成交量 z-score > 1.5。"""
        n = 80
        closes = np.full(n, 100.0)
        volumes = [1000000] * 70 + [5000000] * 10  # 后 10 天放量
        # 最后几天突然跌破前 60 日低点
        closes[70:] = 80.0
        df = _make_df(closes, volumes)
        out = compute(df)
        self.assertTrue(out["div_vol_break"].iloc[-10:].any())


if __name__ == "__main__":
    unittest.main()
