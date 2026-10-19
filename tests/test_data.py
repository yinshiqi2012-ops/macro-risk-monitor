"""数据层单元测试。"""
import unittest

from monitor.data import _cn_market, _tx_symbol, _split_code


class TestMarketParsing(unittest.TestCase):
    def test_sz_000_prefix(self):
        self.assertEqual(_cn_market("000001"), "sz")
        self.assertEqual(_cn_market("000001.SZ"), "sz")

    def test_sz_300_prefix(self):
        self.assertEqual(_cn_market("300750"), "sz")

    def test_sh_600_prefix(self):
        self.assertEqual(_cn_market("600519"), "sh")

    def test_sh_688_prefix(self):
        self.assertEqual(_cn_market("688981"), "sh")

    def test_bj_prefix(self):
        self.assertEqual(_cn_market("430001"), "bj")

    def test_tx_symbol_sz(self):
        self.assertEqual(_tx_symbol("000001.SZ"), "sz000001")

    def test_tx_symbol_sh(self):
        self.assertEqual(_tx_symbol("600519.SH"), "sh600519")

    def test_tx_symbol_hk(self):
        self.assertEqual(_tx_symbol("00700.HK"), "hk00700")

    def test_split_code_with_dot(self):
        self.assertEqual(_split_code("600519.SH"), ("300573", "sz"))
        self.assertEqual(_split_code("000660.KS"), ("000660", "ks"))

    def test_split_code_bare(self):
        self.assertEqual(_split_code("000001"), ("000001", None))


if __name__ == "__main__":
    unittest.main()
