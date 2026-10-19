"""数据层：多数据源适配器 + 本地 CSV 增量缓存。

统一输出六列：date, open, high, low, close, volume（volume 可为空）。
每个品种缓存为 data/<code>.csv；已有缓存时只增量拉取缺失日期。
单个品种失败抛异常由上层捕获，报告标注「数据缺失」，不中断整体。
"""
import ast
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
COLS = ["date", "open", "high", "low", "close", "volume"]

_UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
}
_session = requests.Session()

# 保存系统代理（OKX/Nasdaq 等现有源可能需要），但为 akshare 关闭代理读取，
# 因为本机代理对部分 Eastmoney 接口不稳定，而腾讯/新浪接口直连可用。
_ORIGINAL_PROXIES = requests.utils.getproxies().copy()
_session.trust_env = False
_session.proxies = _ORIGINAL_PROXIES


def _no_proxy_for_ak(func):
    """装饰器：执行 akshare 函数时临时屏蔽系统代理，避免本机代理偶发中断。"""
    def wrapper(*args, **kwargs):
        original_getproxies = requests.utils.getproxies
        # 同时屏蔽 akshare 内部 tqdm 进度条，避免批量拉取时刷屏。
        # stock_zh_a_hist_tx 在模块级 from akshare.utils.tqdm import get_tqdm，
        # 因此必须直接改其所在模块的局部引用。
        _patched_modules = []
        try:
            import akshare.utils.tqdm as _tqdm_module
            _orig_get_tqdm = _tqdm_module.get_tqdm
            _tqdm_module.get_tqdm = lambda: _DummyTqdm
            _patched_modules.append(("utils", _tqdm_module, _orig_get_tqdm))
        except Exception:  # noqa: BLE001
            pass

        try:
            import akshare.stock_feature.stock_hist_tx as _hist_tx_module
            _orig_hist_tx_get_tqdm = _hist_tx_module.get_tqdm
            _hist_tx_module.get_tqdm = lambda: _DummyTqdm
            _patched_modules.append(("hist_tx", _hist_tx_module, _orig_hist_tx_get_tqdm))
        except Exception:  # noqa: BLE001
            pass

        requests.utils.getproxies = lambda: {}
        try:
            return func(*args, **kwargs)
        finally:
            requests.utils.getproxies = original_getproxies
            for _name, mod, orig in _patched_modules:
                mod.get_tqdm = orig
    return wrapper


def _DummyTqdm(iterable=None, *args, **kwargs):
    """tqdm 占位：直接返回 iterable 本身，不做进度输出。"""
    if iterable is None:
        return _DummyTqdm
    return iterable


def _get_json(url, params=None, tries=6, timeout=20):
    """带重试的 GET JSON（本地代理偶发断连，重试即可）。"""
    last = None
    for i in range(tries):
        try:
            r = _session.get(url, params=params, headers=_UA, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"请求失败 {url}: {last}")


def _std(df):
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "high", "low", "close", "volume"]:
        if c not in df:
            df[c] = float("nan")
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[COLS].dropna(subset=["close"]).sort_values("date").reset_index(drop=True)


# ---------------- 中国市场符号解析 ----------------

def _cn_market(code):
    """根据 6 位代码判断交易所前缀：sh / sz / bj。"""
    c = code.zfill(6)
    # 沪市：主板 60x/68x/69x；科创板 688/689
    if c.startswith("600") or c.startswith("601") or c.startswith("603") \
            or c.startswith("605") or c.startswith("609") or c.startswith("688") \
            or c.startswith("689"):
        return "sh"
    # 深市：主板 000/001/002/003；创业板 300/301
    if c.startswith("000") or c.startswith("001") or c.startswith("002") \
            or c.startswith("003") or c.startswith("300") or c.startswith("301"):
        return "sz"
    # 北交所
    if c.startswith("43") or c.startswith("83") or c.startswith("87") or c.startswith("88"):
        return "bj"
    # 默认：6 开头沪市，其余深市
    if c[0] == "6":
        return "sh"
    return "sz"


def _split_code(code):
    """将 600519.SH / 000300.SH / 0700.HK 等拆分出纯数字和交易所。"""
    if "." in code:
        base, suffix = code.rsplit(".", 1)
        suffix = suffix.upper()
        if suffix in ("SH", "SZ", "BJ"):
            return base, suffix.lower()
        if suffix == "HK":
            return base, "hk"
        return base, suffix.lower()
    return code, None


def _market_for_code(code):
    """返回腾讯/新浪接口需要的 symbol 前缀。"""
    base, suffix = _split_code(code)
    if suffix == "hk":
        return "hk"
    if suffix in ("sh", "sz", "bj"):
        return suffix
    return _cn_market(base)


def _tx_symbol(code):
    """转换为 akshare.stock_zh_a_hist_tx 接口 symbol，例如 sh600519 / hk09992。"""
    base, suffix = _split_code(code)
    if suffix == "hk":
        # 港股在腾讯接口需要 5 位数字，前补 0
        return f"hk{base.zfill(5)}"
    return f"{_market_for_code(code)}{base}"


def _start_date(since):
    """将 since 转为 akshare 开始日期字符串。"""
    if since is None:
        return "19700101"
    return since.strftime("%Y%m%d")


def _end_date(asof=None):
    """默认结束日期，asof 时取 asof 否则取未来。"""
    if asof is not None:
        return pd.Timestamp(asof).strftime("%Y%m%d")
    # akshare 默认 end_date 可设很远；这里取 2050 年对齐原接口习惯
    return "20500101"


# ---------------- 各数据源适配器（返回标准六列 DataFrame） ----------------

def _fetch_okx(cfg, since, end=None):
    """OKX 日线（BTC-USDT），after 参数向历史翻页。"""
    end_ts = int(end.timestamp() * 1000) if end else None
    url = "https://www.okx.com/api/v5/market/history-candles"
    rows, after = [], None
    while True:
        params = {"instId": "BTC-USDT", "bar": "1Dutc", "limit": "100"}
        if after:
            params["after"] = after
        j = _get_json(url, params)
        batch = j.get("data") or []
        if not batch:
            break
        for r in batch:
            if r[8] != "1":  # 未收盘的当根 K 线跳过
                continue
            ts = int(r[0])
            if end_ts and ts > end_ts:
                batch = []
                break
            if since and ts < int(since.timestamp() * 1000):
                batch = []
                break
            rows.append(r)
        if not batch or (since and int(batch[-1][0]) < int(since.timestamp() * 1000)):
            break
        after = batch[-1][0]
        if len(batch) < 100:
            break
        time.sleep(0.5)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume",
                                     "volCcy", "volCcyQuote", "confirm"])
    df["date"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms")
    return _std(df)


def _fetch_nasdaq(cfg, since, end=None):
    """api.nasdaq.com 历史日线（SNDK 等美股 / NDX 等指数，指数无成交量）。"""
    start = (since or pd.Timestamp("2000-01-01")).strftime("%Y-%m-%d")
    todate = (end or pd.Timestamp.now()).strftime("%Y-%m-%d")
    j = _get_json(
        f"https://api.nasdaq.com/api/quote/{cfg['code']}/historical",
        {"assetclass": cfg.get("assetclass", "stocks"), "fromdate": start,
         "todate": todate, "limit": "9999"},
    )
    rows = (j.get("data") or {}).get("tradesTable", {}).get("rows") or []
    recs = []
    for r in rows:
        def num(s):
            s = str(s).replace("$", "").replace(",", "").strip()
            return float(s) if s not in ("--", "") else float("nan")
        recs.append({"date": pd.to_datetime(r["date"], format="%m/%d/%Y"),
                     "open": num(r["open"]), "high": num(r["high"]),
                     "low": num(r["low"]), "close": num(r["close"]),
                     "volume": num(r["volume"])})
    return _std(pd.DataFrame(recs))


def _fetch_naver(cfg, since, end=None):
    """Naver fchart 韩股日线（000660 海力士）。"""
    start = int((since or pd.Timestamp("2000-01-01")).strftime("%Y%m%d"))
    end_time = int((end or pd.Timestamp.now()).strftime("%Y%m%d"))
    r = _session.get(
        "https://fchart.stock.naver.com/siseJson.nhn",
        params={"symbol": cfg["code"].split(".")[0], "requestType": "1",
                "startTime": start, "endTime": end_time,
                "timeframe": "day"},
        headers=_UA, timeout=20,
    )
    data = ast.literal_eval(r.text.strip().rstrip(","))
    recs = [{"date": pd.to_datetime(str(row[0])), "open": row[1], "high": row[2],
             "low": row[3], "close": row[4], "volume": row[5]}
            for row in data[1:] if row and str(row[0]).isdigit()]
    return _std(pd.DataFrame(recs))


def _fetch_em_index(cfg, since, end=None):
    """东方财富海外指数日线（100.NDX 等）。"""
    end_str = (end or pd.Timestamp("2050-01-01")).strftime("%Y%m%d")
    j = _get_json(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        {"secid": cfg["secid"], "fields1": "f1,f2,f3,f4,f5,f6",
         "fields2": "f51,f52,f53,f54,f55,f56,f57,f58", "klt": "101", "fqt": "1",
         "beg": (since or pd.Timestamp("2000-01-01")).strftime("%Y%m%d"),
         "end": end_str, "ut": "fa5fd1943c7b386f172d6893dbfba10b"},
    )
    klines = (j.get("data") or {}).get("klines") or []
    recs = []
    for line in klines:
        f = line.split(",")
        recs.append({"date": f[0], "open": f[1], "close": f[2], "high": f[3],
                     "low": f[4], "volume": f[5]})
    return _std(pd.DataFrame(recs))


def _fetch_ak_index(cfg, since, end=None):
    import akshare as ak
    df = ak.stock_zh_index_daily(symbol=cfg["code"])
    return _std(df)


def _fetch_ak_sge(cfg, since, end=None):
    """上金所现货（Au99.99 / Ag(T+D)），无成交量。"""
    import akshare as ak
    df = ak.spot_hist_sge(symbol=cfg.get("ak_symbol", "Au99.99"))
    df["volume"] = float("nan")
    return _std(df)


def _fetch_ak_futures(cfg, since, end=None):
    """新浪商品期货主连（AG0 沪银），带成交量。"""
    import akshare as ak
    df = ak.futures_main_sina(symbol=cfg.get("ak_symbol", "AG0"))
    df = df.rename(columns={"日期": "date", "开盘价": "open", "最高价": "high",
                            "最低价": "low", "收盘价": "close", "成交量": "volume"})
    return _std(df)


@_no_proxy_for_ak
def _fetch_ak_hist_tx(tx_symbol, since, end=None):
    """统一使用 akshare.stock_zh_a_hist_tx 获取日线（A股/ETF/LOF/港股均支持）。"""
    import akshare as ak
    start = _start_date(since)
    end_str = _end_date(end)
    df = ak.stock_zh_a_hist_tx(symbol=tx_symbol, start_date=start,
                               end_date=end_str, adjust="qfq")
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume"])
    df = df.rename(columns={"close": "close"})
    # 腾讯接口列名已是 date/open/close/high/low/volume，无需大改
    return _std(df)


def _fetch_ak_stock(cfg, since, end=None):
    """A 股个股（含沪市/深市/科创板/创业板/北交所）。"""
    return _fetch_ak_hist_tx(_tx_symbol(cfg["code"]), since, end)


def _fetch_ak_etf(cfg, since, end=None):
    """场内 ETF/LOF。ETF 接口未覆盖时，腾讯接口对 LOF 同样返回日线。"""
    return _fetch_ak_hist_tx(_tx_symbol(cfg["code"]), since, end)


def _fetch_ak_hk(cfg, since, end=None):
    """港股，symbol 去掉 .HK 后接 hk 前缀。"""
    return _fetch_ak_hist_tx(_tx_symbol(cfg["code"]), since, end)


# Eastmoney 全球指数代码到中文名称映射
_GLOBAL_INDEX_EM_MAP = {
    "KS11": "韩国KOSPI",
    "KOSPI200": "韩国KOSPI200",
    "N225": "日经225",
    "HSI": "恒生指数",
    "DJI": "道琼斯",
    "IXIC": "纳斯达克",
    "SPX": "标普500",
    "GDAXI": "德国DAX",
    "FTSE": "英国富时100",
    "FCHI": "法国CAC40",
}


@_no_proxy_for_ak
def _fetch_ak_global_index_em(symbol, since, end=None):
    """尝试使用 akshare.index_global_hist_em 获取全球指数。"""
    import akshare as ak
    from akshare.index.index_global_em import index_global_em_symbol_map
    em_name = _GLOBAL_INDEX_EM_MAP.get(symbol)
    if em_name is None:
        # 允许用户直接写中文名称
        em_name = symbol if symbol in index_global_em_symbol_map else None
    if em_name is None:
        raise RuntimeError(f"akshare 未覆盖该全球指数名称/代码: {symbol}")
    df = ak.index_global_hist_em(symbol=em_name)
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    # 列名推测：日期/开盘/收盘/最高/最低/成交量；以实际列名做兜底
    rename = {}
    for old, new in [("日期", "date"), ("日期时间", "date"), ("开盘", "open"),
                     ("收盘", "close"), ("最高", "high"), ("最低", "low"),
                     ("成交量", "volume")]:
        if old in df.columns:
            rename[old] = new
    df = df.rename(columns=rename)
    df["volume"] = float("nan")
    return _std(df)


def _fetch_yfinance(symbol, since, end=None):
    """yfinance 备用获取日线（用于全球指数及港股 akshare 不可用时）。"""
    import yfinance as yf
    ticker = yf.Ticker(symbol)
    if since is not None:
        df = ticker.history(start=since.strftime("%Y-%m-%d"),
                            end=(end or datetime.now().strftime("%Y-%m-%d")),
                            interval="1d")
    else:
        df = ticker.history(period="max", interval="1d")
    if df is None or df.empty:
        return pd.DataFrame(columns=COLS)
    df = df.reset_index()
    df = df.rename(columns={
        "Date": "date", "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Volume": "volume"
    })
    return _std(df)


def _fetch_ak_global_index(cfg, since, end=None):
    """全球指数。先尝试 akshare，失败再尝试 yfinance。"""
    code = cfg["code"]
    yf_symbol = code if code.startswith("^") else f"^{code}"
    last_err = None
    try:
        return _fetch_ak_global_index_em(code, since, end)
    except Exception as e:  # noqa: BLE001
        last_err = e
    try:
        return _fetch_yfinance(yf_symbol, since, end)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"akshare: {last_err}; yfinance({yf_symbol}): {e}")


FETCHERS = {
    "okx": _fetch_okx,
    "nasdaq": _fetch_nasdaq,
    "naver": _fetch_naver,
    "em_index": _fetch_em_index,
    "ak_index": _fetch_ak_index,
    "ak_sge": _fetch_ak_sge,
    "ak_futures": _fetch_ak_futures,
    "ak_stock": _fetch_ak_stock,
    "ak_etf": _fetch_ak_etf,
    "ak_hk": _fetch_ak_hk,
    "ak_global_index": _fetch_ak_global_index,
}


def cache_path(code):
    return DATA_DIR / f"{code}.csv"


def load(code):
    p = cache_path(code)
    if not p.exists():
        return None
    return _std(pd.read_csv(p))


def update(cfg, asof=None):
    """增量更新缓存并返回截至 asof 的数据。失败向上抛异常。

    asof 模式用于历史回放：若缓存已覆盖 asof 则直接截断返回；否则拉取到 asof
    为止，且不把未来数据写回缓存，避免污染历史口径。
    """
    code, cached = cfg["code"], load(cfg["code"])
    since = None
    if cached is not None and len(cached):
        since = cached["date"].max()
        if asof is None and since >= pd.Timestamp.now().normalize() - pd.Timedelta(days=1):
            return cached  # 缓存已是最新
        if asof is not None and since >= pd.Timestamp(asof):
            return cached[cached["date"] <= pd.Timestamp(asof)].reset_index(drop=True)
    new = FETCHERS[cfg["source"]](cfg, since, asof)
    if cached is not None and len(cached):
        df = pd.concat([cached, new]).drop_duplicates("date", keep="last")
        df = df.sort_values("date").reset_index(drop=True)
    else:
        df = new
    DATA_DIR.mkdir(exist_ok=True)
    if asof is None:
        df.to_csv(cache_path(code), index=False)
    if asof is not None:
        df = df[df["date"] <= pd.Timestamp(asof)].reset_index(drop=True)
    return df
