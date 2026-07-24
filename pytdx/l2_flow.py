# coding=utf-8
"""
L2 资金流向分析模块 (Level 2 Flow Analysis)

基于逐笔成交数据 (GetTransactionData / GetHistoryTransactionData)，
按订单大小和成交方向聚合成 L2 资金流向统计数据。

对应通达信公式函数:
  - L2_VOL(N, M):  成交量分档  N=0-3(超大/大/中/小) M=0-3(买/卖/主买/主卖)
  - L2_AMO(N, M):  成交额分档  N=0-3(超大/大/中/小) M=0-3(买/卖/主买/主卖)
  - L2_VOLNUM(N, M): 单数分档  N=0-1(大单/小单)   M=0-1(买/卖)

注意:
  - TDX 公式中的 L2_VOL/L2_AMO/L2_VOLNUM 是日线级别的聚合统计量
  - 本模块通过原始逐笔成交数据计算得到，与 TDX 客户端可能有微小差异
  - 历史逐笔数据不含 L2_VOLNUM (逐笔订单数)，因此只能基于成交量/成交额分档
"""

from collections import OrderedDict, defaultdict


# ---- 订单大小分档阈值 (可根据需要调整) ----
# 默认阈值：基于成交金额(元)判断
DEFAULT_AMOUNT_THRESHOLDS = {
    'super_large': 1000000,   # >= 100万元 → 超大单
    'large':        200000,   # >=  20万元 → 大单
    'medium':         40000,  # >=   4万元 → 中单
    'small':              0,  # <    4万元 → 小单
}

# 基于成交量(手)判断的阈值
DEFAULT_VOL_THRESHOLDS = {
    'super_large': 500,  # >= 500手 → 超大单
    'large':       100,  # >= 100手 → 大单
    'medium':       20,  # >=  20手 → 中单
    'small':         0,  # <   20手 → 小单
}

# ---- 方向定义 ----
DIRECTION_MAP = {
    0: 'buy',       # 买入 (外盘/主动买)
    1: 'sell',      # 卖出 (内盘/主动卖)
    2: 'neutral',   # 中性
}

# 分档索引 N → 中文名
SIZE_NAMES = {
    0: '超大单',
    1: '大单',
    2: '中单',
    3: '小单',
}

# 方向索引 M → 中文名
DIR_NAMES = {
    0: '买入',
    1: '卖出',
    2: '主买',
    3: '主卖',
}


def classify_order_size(amount, vol, thresholds=None):
    """
    根据成交金额和成交量判断订单大小档位

    :param amount: 成交金额 (元)
    :param vol: 成交量 (手)
    :param thresholds: 自定义阈值 dict, 键为 'super_large'/'large'/'medium'/'small'
    :return: 档位索引 (0=超大, 1=大, 2=中, 3=小)
    """
    if thresholds is None:
        thresholds = DEFAULT_AMOUNT_THRESHOLDS

    if amount >= thresholds['super_large'] or vol >= DEFAULT_VOL_THRESHOLDS['super_large']:
        return 0  # 超大单
    elif amount >= thresholds['large'] or vol >= DEFAULT_VOL_THRESHOLDS['large']:
        return 1  # 大单
    elif amount >= thresholds['medium'] or vol >= DEFAULT_VOL_THRESHOLDS['medium']:
        return 2  # 中单
    else:
        return 3  # 小单


def aggregate_l2_ticks(ticks, amount_thresholds=None):
    """
    将逐笔成交数据按订单大小 × 方向聚合

    :param ticks: 逐笔成交列表 (来自 get_transaction_data / get_history_transaction_data)
    :param amount_thresholds: 自定义金额阈值
    :return: dict {
        'summary': { 总计 },
        'by_size_dir': { (size_idx, dir_idx): {vol, amount, num, count} },
        'by_size': { size_idx: {vol, amount, num, count} },
        'by_dir': { dir_idx: {vol, amount, num, count} },
        'raw_ticks': ticks,
    }
    """
    result = {
        'summary': {'vol': 0, 'amount': 0, 'num': 0, 'count': 0},
        'by_size_dir': defaultdict(lambda: {'vol': 0, 'amount': 0, 'num': 0, 'count': 0}),
        'by_size': defaultdict(lambda: {'vol': 0, 'amount': 0, 'num': 0, 'count': 0}),
        'by_dir': defaultdict(lambda: {'vol': 0, 'amount': 0, 'num': 0, 'count': 0}),
        'ticks_analyzed': [],
    }

    for tick in ticks:
        vol = tick.get('vol', 0)
        amount = tick.get('amount', 0)
        num = tick.get('num', 0)  # L2_VOLNUM (may be 0 in history data)
        buyorsell = tick.get('buyorsell', 2)

        if vol == 0 and amount == 0:
            continue  # skip empty ticks (集合竞价标记等)

        # 分类
        size_idx = classify_order_size(amount, vol, amount_thresholds)

        # 方向: 简化映射 buy=0, sell=1
        if buyorsell == 0:
            dir_idx = 0  # 买入
        elif buyorsell == 1:
            dir_idx = 1  # 卖出
        else:
            dir_idx = 2  # 中性/其他

        # 聚合
        result['summary']['vol'] += vol
        result['summary']['amount'] += amount
        result['summary']['num'] += num
        result['summary']['count'] += 1

        key = (size_idx, dir_idx)
        result['by_size_dir'][key]['vol'] += vol
        result['by_size_dir'][key]['amount'] += amount
        result['by_size_dir'][key]['num'] += num
        result['by_size_dir'][key]['count'] += 1

        result['by_size'][size_idx]['vol'] += vol
        result['by_size'][size_idx]['amount'] += amount
        result['by_size'][size_idx]['num'] += num
        result['by_size'][size_idx]['count'] += 1

        result['by_dir'][dir_idx]['vol'] += vol
        result['by_dir'][dir_idx]['amount'] += amount
        result['by_dir'][dir_idx]['num'] += num
        result['by_dir'][dir_idx]['count'] += 1

        result['ticks_analyzed'].append({
            'time': tick.get('time', ''),
            'price': tick.get('price', 0),
            'vol': vol,
            'amount': amount,
            'num': num,
            'buyorsell': buyorsell,
            'size': size_idx,
            'size_name': SIZE_NAMES[size_idx],
            'dir': dir_idx,
            'dir_name': DIR_NAMES[dir_idx] if dir_idx <= 1 else '中性',
        })

    return result


def get_l2_flow_matrix(result):
    """
    从聚合结果生成 L2 分档矩阵，对应公式 L2_VOL(N,M) / L2_AMO(N,M) / L2_VOLNUM(N,M)

    :param result: aggregate_l2_ticks() 的返回值
    :return: dict {
        'L2_VOL':    [[N0M0, N0M1, N0M2, N0M3], [N1M0, ...], ...],   # 4x4 成交量矩阵
        'L2_AMO':    [[...], ...],                                      # 4x4 成交额矩阵
        'L2_VOLNUM': [[...], ...],                                      # 4x4 笔数矩阵
        'labels': { 'size': [...], 'dir': [...] },
    }
    """
    matrix_vol = [[0]*4 for _ in range(4)]
    matrix_amo = [[0]*4 for _ in range(4)]
    matrix_num = [[0]*4 for _ in range(4)]

    for (size_idx, dir_idx), data in result['by_size_dir'].items():
        if size_idx < 4 and dir_idx < 4:
            matrix_vol[size_idx][dir_idx] = data['vol']
            matrix_amo[size_idx][dir_idx] = data['amount']
            matrix_num[size_idx][dir_idx] = data['num']

    return {
        'L2_VOL': matrix_vol,
        'L2_AMO': matrix_amo,
        'L2_VOLNUM': matrix_num,
        'labels': {
            'size': [SIZE_NAMES[i] for i in range(4)],
            'dir': [DIR_NAMES[i] for i in range(4)],
        },
    }


def format_l2_matrix(matrix_data):
    """
    格式化输出 L2 分档矩阵

    :param matrix_data: get_l2_flow_matrix() 的返回值
    :return: str 格式化的表格字符串
    """
    labels = matrix_data['labels']
    header = f"{'订单大小':<8} {'方向':<6} {'成交量(手)':>12} {'成交额(万元)':>14} {'笔数':>10}"
    sep = '-' * 56
    lines = [header, sep]

    for n in range(4):
        for m in range(2):  # 只显示买/卖两个方向
            vol = matrix_data['L2_VOL'][n][m]
            amo = matrix_data['L2_AMO'][n][m]
            num = matrix_data['L2_VOLNUM'][n][m]
            if vol > 0 or amo > 0:
                lines.append(
                    f"{labels['size'][n]:<8} {labels['dir'][m]:<6} "
                    f"{vol:>12d} {amo/10000:>14.2f} {num:>10d}"
                )

    # 总计
    total_vol = sum(sum(row) for row in matrix_data['L2_VOL'])
    total_amo = sum(sum(row) for row in matrix_data['L2_AMO'])
    total_num = sum(sum(row) for row in matrix_data['L2_VOLNUM'])
    lines.append(sep)
    lines.append(
        f"{'合计':<8} {'':<6} "
        f"{total_vol:>12d} {total_amo/10000:>14.2f} {total_num:>10d}"
    )

    return '\n'.join(lines)


def compute_l2_flow_from_ticks(ticks, thresholds=None):
    """
    一站式函数: 从逐笔成交数据计算 L2 资金流向

    :param ticks: 逐笔成交列表
    :param thresholds: 自定义金额阈值
    :return: 包含聚合结果和分档矩阵的完整 dict
    """
    aggregated = aggregate_l2_ticks(ticks, thresholds)
    matrix = get_l2_flow_matrix(aggregated)
    return {
        'aggregated': aggregated,
        'matrix': matrix,
        'formatted': format_l2_matrix(matrix),
    }


# ============================================================
# 便捷函数: 与 TdxHq_API 集成
# ============================================================

def fetch_and_analyze_l2_flow(api, market, code, date=None,
                               max_ticks=200000, thresholds=None):
    """
    从行情接口获取逐笔数据并分析 L2 资金流向

    :param api: TdxHq_API 实例 (需已连接)
    :param market: 市场代码 (0=深圳, 1=上海)
    :param code: 股票代码
    :param date: 日期 (如 20260713), None 表示实时数据
    :param max_ticks: 最大获取 tick 数
    :param thresholds: 自定义阈值
    :return: compute_l2_flow_from_ticks 的结果

    注意: 日线级别的全量 L2 分析需要获取当日所有逐笔成交数据。
          对于活跃股票，一天可能有数万笔成交，需要分批获取。
    """
    all_ticks = []
    start = 0
    batch_size = 1000

    while len(all_ticks) < max_ticks:
        if date is not None:
            batch = api.get_history_transaction_data(
                market, code, start, batch_size, date)
        else:
            batch = api.get_transaction_data(market, code, start, batch_size)

        if not batch or len(batch) == 0:
            break

        all_ticks.extend(batch)

        if len(batch) < batch_size:
            break

        start += len(batch)

    return compute_l2_flow_from_ticks(all_ticks, thresholds)


# ============================================================
# 演示 & 测试
# ============================================================
if __name__ == '__main__':
    from pytdx.hq import TdxHq_API

    api = TdxHq_API()
    if api.connect('180.153.18.170', 7709):
        print("=" * 60)
        print("L2 资金流向分析示例 — 600000 浦发银行 (昨日)")
        print("=" * 60)

        # 获取昨日逐笔数据
        ticks = []
        start = 0
        while True:
            batch = api.get_history_transaction_data(
                1, '600000', start, 1000, 20260713)
            if not batch or len(batch) == 0:
                break
            ticks.extend(batch)
            if len(batch) < 1000:
                break
            start += len(batch)
            print(f"  已获取 {len(ticks)} 笔...")

        print(f"\n共获取 {len(ticks)} 笔逐笔成交")

        # 分析
        result = compute_l2_flow_from_ticks(ticks)
        print()
        print(result['formatted'])

        # 对应公式: L2_VOL(0,0) = 超大单买入量, L2_AMO(2,1) = 中单卖出额
        mat = result['matrix']
        print(f"\n公式示例:")
        print(f"  L2_VOL(0,0) = 超大单买入量   = {mat['L2_VOL'][0][0]} 手")
        print(f"  L2_VOL(0,1) = 超大单卖出量   = {mat['L2_VOL'][0][1]} 手")
        print(f"  L2_AMO(1,0) = 大单买入额     = {mat['L2_AMO'][1][0]/10000:.2f} 万元")
        print(f"  L2_AMO(1,1) = 大单卖出额     = {mat['L2_AMO'][1][1]/10000:.2f} 万元")
        print(f"  L2_VOLNUM(0,0) = 大单买入笔数 = {mat['L2_VOLNUM'][0][0]}")
        print(f"  L2_VOLNUM(0,1) = 大单卖出笔数 = {mat['L2_VOLNUM'][0][1]}")

        api.disconnect()
