"""状态层：252 日滚动分位归一化 → 四族子分 → 过热/恐慌双分 → 八阶段状态机（v2）。

跨品种可比的核心：不用绝对阈值，所有指标先换算成品种自身 252 日
滚动窗口的分位数（0-100）。四族（趋势位置/动量/波动率/量能结构）
族内分位均值、族间加权——共线指标同族平均，不再各自独立加权。
权重与阈值集中在顶部常量，便于调参。状态切换需连续 2 日确认，
迁移带方向约束，防单日噪声与中间态乱跳。
"""
import numpy as np
import pandas as pd

PCTL_WINDOW = 252      # 分位数滚动窗口
PCTL_MIN = 60          # 分位数最少样本

# ---- 四族打分权重（v2，族内先分位均值再族间加权；共线指标同族平均）----
# 旧版 v1 权重对照（已弃用）：过热 = BIAS50/RSI/ROC/加速 各 0.25
FAMILY_W = {"position": 0.30, "momentum": 0.30, "volatility": 0.20, "volume": 0.20}
# 恐慌分权重（v1：drawdown 0.30 / downspeed 0.20 / atr 0.20 / vol_down 0.15 / oversold 0.15）
CAPIT_W = {"drawdown": 0.30, "momentum_down": 0.20, "volatility": 0.20,
           "vol_down": 0.15, "oversold": 0.15}
VOL_DOWN_FULL = 2.0    # 下跌日成交量 z-score 达到该值记满分
VOL_UP_FULL = 2.0      # 量能放大项：vol_z 达到该值记满分
UPDAYS_FULL = 8        # 连涨几天视为加速满分

# ---- 八阶段状态机阈值（v2）----
T_PANIC = 80           # 恐慌投降：恐慌分 >= 此值
T_BREAKDOWN = 60       # 破位下跌：恐慌分 >= 此值且仍在急跌
BREAKDOWN_ROC_PCTL = 30  # 破位下跌要求 ROC 分位低于此值（仍在下跌）
T_ACCEL_POSMOM = 80    # 加速/过热：位置族与动量族均值 >= 此值（且在高位区）
T_DIST_RECENT_OH = 90  # 高位派筹：近 30 日内过热分曾 >= 此值
T_DIST_POS = 65        # 高位派筹：位置族分位下限（短历史品种的替代前置条件）
T_DIST_MOM = 60        # 高位派筹：动量走弱判定线（f_momentum 低于此值且在回落）
T_START_MOM = 45       # 启动：动量族分位升破此值
T_TREND_MOM = 40       # 趋势延续：动量族分位下限
DD_GATE_OVERHEAT = -25   # 加速/过热要求距 52 周高点回撤不深于该值（%）
DD_GATE_DIST = -30       # 高位派筹同样要求仍在高位区域
DD_GATE_PANIC = -10      # 恐慌投降要求回撤深于该值（创新高时的波动毛刺不算恐慌）
DD_BOTTOM = -25          # 底部盘整要求回撤深于该值（%）
T_BOTTOM_VOL = 40        # 底部盘整要求波动率族分位低于此值
RECENT_OVERHEAT_N = 30   # 高位派筹：回看多少日内曾过热
RECENT_PANIC_N = 60      # 筑底修复：回看多少日内曾恐慌
DIVERGENCE_N = 10        # 背离/结构信号的有效期（日）
CONFIRM_DAYS = 2         # 状态切换确认天数
CONFIRM_DAYS_DOWN = {"破位下跌": 1}  # 下行状态确认天数（崩盘速度快，放宽确认）
# 案例库证据：破位段「缩量企稳」出现率约 20%。允许在仅达破位恐慌分（未达投降）时，
# 凭缩量企稳进入筑底修复，避免必须先打满 T_PANIC 才认修复。
T_REPAIR_VIA_STABIL = T_BREAKDOWN

STATES = ["底部盘整", "启动", "趋势延续", "加速/过热",
          "高位派筹", "破位下跌", "恐慌投降", "筑底修复"]

STATE_TIPS = {
    "底部盘整": "观察，纳入清单。",
    "启动": "试错建仓区，止损清晰。",
    "趋势延续": "持有，让利润奔跑。",
    "加速/过热": "收紧风控，减仓不加仓。",
    "高位派筹": "减仓/离场主窗口。",
    "破位下跌": "不接飞刀，执行离场。",
    "恐慌投降": "观察，不急于抄底。",
    "筑底修复": "分批试错，等待启动确认。",
}

# 方向约束：候选态只能从列出的前序状态进入（破位/恐慌任何时候都可进入）
_ALL = set(STATES)
ALLOWED_FROM = {
    "底部盘整": {"恐慌投降", "筑底修复", "破位下跌", "底部盘整", "启动", "趋势延续"},
    "启动": {"底部盘整", "筑底修复", "破位下跌", "启动"},
    "趋势延续": {"启动", "趋势延续", "加速/过热", "高位派筹"},
    "加速/过热": {"趋势延续", "启动", "筑底修复", "加速/过热"},
    "高位派筹": {"加速/过热", "趋势延续", "高位派筹", "破位下跌", "筑底修复"},
    "破位下跌": _ALL,
    "恐慌投降": _ALL,
    "筑底修复": {"恐慌投降", "破位下跌", "筑底修复"},
}


def pctl(s):
    """序列在自身 252 日滚动窗口中的分位数（0-100）。"""
    def rank_last(x):
        return (x <= x[-1]).mean() * 100
    return s.rolling(PCTL_WINDOW, min_periods=PCTL_MIN).apply(rank_last, raw=True)


def score(df):
    """计算四族子分（0-100）与过热/恐慌双分及中间分位列。

    族内成员先做 252 日分位再取均值（共线指标同族平均），族间按
    FAMILY_W / CAPIT_W 加权；某族数据缺失（如无成交量）时剔除该族
    并按剩余权重归一。
    """
    out = df.copy()
    out["p_bias50"] = pctl(out["bias50"])
    out["p_bias200"] = pctl(out["bias200"])
    out["p_rsi"] = pctl(out["rsi14"])
    out["p_roc"] = pctl(out["roc20"])
    out["p_accel_diff"] = pctl(out["accel_diff"])
    out["p_atr"] = pctl(out["atr_pct"])
    out["p_hv"] = pctl(out["hv20"])
    out["p_bb"] = pctl(out["bb_width"])
    out["p_dd"] = pctl(-out["drawdown"])  # 回撤深度（正数越大越深）

    # 四族子分
    ma_align = (out["ma50"] > out["ma200"]).astype(float) * 100  # 多头排列记 100
    out["f_position"] = (out["p_bias50"] + out["p_bias200"]
                         + (100 - out["p_dd"]) + ma_align) / 4
    up_days_score = (out["up_days"] / UPDAYS_FULL).clip(0, 1) * 100
    out["f_momentum"] = (out["p_rsi"] + out["p_roc"]
                         + out["p_accel_diff"] + up_days_score) / 4
    out["f_volatility"] = (out["p_atr"] + out["p_hv"] + out["p_bb"]) / 3
    # 量能放大项：vol_z 截断到 [0, VOL_UP_FULL] 映射 0-100（无成交量则整列 NaN）
    out["f_volume"] = (out["vol_z"].clip(0, VOL_UP_FULL) / VOL_UP_FULL * 100)

    fam_parts = {k: out[f"f_{k}"] for k in FAMILY_W}
    if out["vol_z"].notna().sum() == 0:
        fam_parts.pop("volume")
    wsum = sum(FAMILY_W[k] for k in fam_parts)
    fam_weighted = sum(FAMILY_W[k] * v for k, v in fam_parts.items()) / wsum
    # 过热分取「四族加权」与「位置+动量组合」的较高者：
    # 波动/量能未确认时不压制位置与动量给出的极端读数（缩量阴跌式赶顶也能识别）
    out["posmom"] = (out["f_position"] + out["f_momentum"]) / 2
    out["overheat"] = pd.concat([fam_weighted, out["posmom"]], axis=1).max(axis=1)

    # 放量下跌：下跌日取 vol_z 截断分，上涨日记 0（成交量缺失则保持 NaN），再取近 5 日最大
    vz = out["vol_z"]
    vol_down = (vz.clip(0, VOL_DOWN_FULL) / VOL_DOWN_FULL * 100).where(
        out["close"].diff() < 0, 0).where(vz.notna())
    out["p_vol_down"] = vol_down.rolling(5, min_periods=1).max()
    capit_parts = {"drawdown": out["p_dd"],
                   "momentum_down": 100 - out["f_momentum"],
                   "volatility": out["f_volatility"],
                   "vol_down": out["p_vol_down"],
                   "oversold": 100 - out["p_rsi"]}
    # 缺成交量数据的品种：剔除 vol_down 项并按剩余权重归一
    if out["vol_z"].notna().sum() == 0:
        capit_parts.pop("vol_down")
    wsum = sum(CAPIT_W[k] for k in capit_parts)
    out["capit"] = sum(CAPIT_W[k] * v for k, v in capit_parts.items()) / wsum
    return out


def _candidate_state(row):
    """八阶段候选态判定（切换仍需确认与方向约束）。"""
    dd = row["drawdown"]
    if row["capit"] >= T_PANIC and not np.isnan(dd) and dd <= DD_GATE_PANIC:
        return "恐慌投降"
    if (row["close"] < row["ma50"] and row["p_roc"] <= BREAKDOWN_ROC_PCTL
            and row["capit"] >= T_BREAKDOWN):
        return "破位下跌"
    posmom = row["posmom"]
    if not np.isnan(dd) and dd >= DD_GATE_OVERHEAT and posmom >= T_ACCEL_POSMOM:
        return "加速/过热"
    # 派筹证据：背离/滞涨信号 与 动量走弱 需同时成立（单一动量回落噪声太大）
    dist_signals = ((row["div_bear_n"] or row["vol_stall_n"])
                    and row["f_momentum"] < T_DIST_MOM and row["f_mom_chg5"] < 0)
    # 替代前置（短历史品种、过热分未充分形成时）：
    # 高位 + 顶背离 + 放量滞涨三者齐备（无成交量品种不会走此路径）
    strong_recent = (row["recent_overheat"] >= T_DIST_RECENT_OH
                     or (row["f_position"] >= T_DIST_POS
                         and row["div_bear_n"] and row["vol_stall_n"]))
    if (not np.isnan(dd) and dd >= DD_GATE_DIST and strong_recent and dist_signals):
        return "高位派筹"
    # 主路径：近 60 日曾达恐慌分 +（缩量企稳 / 底背离 / 波动回落）
    # 辅路径：近 60 日曾达破位恐慌分 + 缩量企稳（案例库破位段高频信号）
    if ((row["recent_panic"] >= T_PANIC
         and (row["lowvol_stabil_n"] or row["div_bull_n"]
              or row["f_vol_chg5"] < 0))
            or (row["recent_panic"] >= T_REPAIR_VIA_STABIL
                and row["lowvol_stabil_n"])):
        return "筑底修复"
    if (not np.isnan(dd) and dd <= DD_BOTTOM
            and row["f_volatility"] < T_BOTTOM_VOL
            and (np.isnan(row["vol_z10"]) or row["vol_z10"] < 0)):
        return "底部盘整"
    if (row["close"] > row["ma50"] and row["bias50"] > 0
            and row["bias50_min30"] < 0 and row["f_momentum"] >= T_START_MOM):
        return "启动"
    if row["bias50"] > 0 and row["f_momentum"] >= T_TREND_MOM:
        return "趋势延续"
    # 兜底：无人认领的中间带按趋势方向归入最邻近阶段
    return "趋势延续" if row["bias50"] > 0 else "底部盘整"


def run_state_machine(sc):
    """逐日走状态机（2 日确认 + 方向约束），返回追加 state 列的 DataFrame。"""
    sc = sc.copy()
    sc["recent_overheat"] = sc["overheat"].rolling(RECENT_OVERHEAT_N, min_periods=1).max().shift()
    sc["recent_panic"] = sc["capit"].rolling(RECENT_PANIC_N, min_periods=1).max().shift()
    sc["bias50_min30"] = sc["bias50"].rolling(30, min_periods=1).min()
    sc["f_mom_chg5"] = sc["f_momentum"] - sc["f_momentum"].shift(5)
    sc["f_vol_chg5"] = sc["f_volatility"] - sc["f_volatility"].shift(5)
    sc["vol_z10"] = sc["vol_z"].rolling(10, min_periods=1).mean()
    for sig in ["div_bear", "div_bull", "vol_stall", "lowvol_stabil"]:
        sc[sig + "_n"] = sc[sig].rolling(DIVERGENCE_N, min_periods=1).max().fillna(False)

    states, current, pending, pending_n = [], None, None, 0
    for _, row in sc.iterrows():
        if np.isnan(row["overheat"]) or np.isnan(row["capit"]):
            states.append(current)  # 分位数尚未形成
            continue
        cand = _candidate_state(row)
        if current is None:
            current = cand  # 首个有效读数直接落位，无需确认
            states.append(current)
            continue
        # 恐慌一日跨越破位阈值直达时，先记 1 日「破位下跌」再确认「恐慌投降」，
        # 保证崩盘路径 …→破位下跌→恐慌投降 完整呈现
        if (cand == "恐慌投降" and current not in ("破位下跌", "恐慌投降")
                and row["capit"] >= T_BREAKDOWN):
            current = "破位下跌"
            states.append(current)
            pending, pending_n = "恐慌投降", 1
            continue
        if cand != current and current not in ALLOWED_FROM.get(cand, set()):
            # 方向约束：不允许的迁移直接忽略（各态总允许停留在自身）
            states.append(current)
            continue
        need = CONFIRM_DAYS_DOWN.get(cand, CONFIRM_DAYS)
        if cand == current:
            pending, pending_n = None, 0
        elif cand == pending:
            pending_n += 1
            if pending_n >= need - 1:
                current, pending, pending_n = cand, None, 0
        else:
            pending, pending_n = cand, 1
            if need <= 1:
                current, pending, pending_n = cand, None, 0
        states.append(current)
    sc["state"] = states
    return sc
