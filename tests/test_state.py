"""状态层单元测试。"""
import unittest

import numpy as np
import pandas as pd

from monitor import state


def _make_score_input(n=300, seed=0):
    """构造满足 state.score / run_state_machine 所需列的 DataFrame。"""
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    ma50 = pd.Series(close).rolling(50).mean().values
    ma200 = pd.Series(close).rolling(200, min_periods=60).mean().values
    bias50 = (close / ma50 - 1) * 100
    bias200 = (close / ma200 - 1) * 100
    high_52w = pd.Series(close).rolling(252, min_periods=20).max().values
    drawdown = (close / high_52w - 1) * 100
    rsi14 = rng.uniform(20, 80, n)
    roc20 = rng.uniform(-10, 10, n)
    ret20 = rng.uniform(-5, 5, n)
    ret60 = rng.uniform(-8, 8, n)
    accel_diff = ret20 - ret60 / 3
    up_days = rng.integers(0, 8, n)
    atr_pct = rng.uniform(0.5, 3.0, n)
    hv20 = rng.uniform(5, 40, n)
    bb_width = rng.uniform(1, 8, n)
    vol_z = rng.uniform(-2, 2, n)
    return pd.DataFrame({
        "date": pd.date_range("2020-01-01", periods=n),
        "close": close,
        "open": close, "high": close, "low": close,
        "volume": rng.uniform(1e6, 5e6, n),
        "ma50": ma50, "ma200": ma200,
        "bias50": bias50, "bias200": bias200,
        "rsi14": rsi14, "roc20": roc20,
        "ret20": ret20, "ret60": ret60,
        "accel_diff": accel_diff, "up_days": up_days,
        "atr_pct": atr_pct, "hv20": hv20, "bb_width": bb_width,
        "vol_z": vol_z, "drawdown": drawdown,
        "div_bear": [False] * n, "div_bull": [False] * n,
        "vol_stall": [False] * n, "lowvol_stabil": [False] * n,
        "div_lowvol_high": [False] * n, "div_vol_break": [False] * n,
    })


class TestScore(unittest.TestCase):
    def test_score_produces_required_columns(self):
        df = _make_score_input()
        out = state.score(df)
        for col in ["f_position", "f_momentum", "f_volatility", "f_volume",
                    "overheat", "capit", "posmom"]:
            self.assertIn(col, out.columns)

    def test_overheat_is_max_of_weighted_and_posmom(self):
        df = _make_score_input()
        out = state.score(df)
        mask = out["overheat"].notna() & out["posmom"].notna()
        # overheat 取「四族加权」与「位置+动量」的较高者
        self.assertTrue((out.loc[mask, "overheat"] >= out.loc[mask, "posmom"]).all())


class TestStateMachine(unittest.TestCase):
    def test_state_column_added(self):
        df = _make_score_input()
        sc = state.score(df)
        out = state.run_state_machine(sc)
        self.assertIn("state", out.columns)

    def test_panic_state_appears_when_extreme(self):
        """构造深度回撤 + 高恐慌分 的数据，状态机应进入恐慌投降。"""
        df = _make_score_input()
        n = len(df)
        # 最后 30 天：价格深度回撤、RSI 极低、波动大、放量
        df.loc[n - 30:, "drawdown"] = -30
        df.loc[n - 30:, "rsi14"] = 10
        df.loc[n - 30:, "atr_pct"] = 5
        df.loc[n - 30:, "hv20"] = 60
        df.loc[n - 30:, "vol_z"] = 3
        df.loc[n - 30:, "roc20"] = -20
        df.loc[n - 30:, "close"] = df["close"].iloc[n - 31] * 0.7
        df.loc[n - 30:, "ma50"] = df["close"] * 1.1
        sc = state.score(df)
        out = state.run_state_machine(sc)
        states = set(out["state"].dropna())
        self.assertIn("恐慌投降", states)


if __name__ == "__main__":
    unittest.main()
