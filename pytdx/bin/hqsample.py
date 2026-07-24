# coding=utf-8
"""
随机抽样行情测试工具

从指定市场随机获取 n 个标的的实时行情和各级别K线数据，
方便快速验证行情接口是否正常工作。

用法:
    python -m pytdx.bin.hqsample                               # 默认: 深市5只
    python -m pytdx.bin.hqsample -m 1 -n 3                     # 上海市场3只
    python -m pytdx.bin.hqsample -S 600000,000001,300750       # 指定股票代码
    python -m pytdx.bin.hqsample -S 600000.SH --l2             # 指定代码+L2逐笔
    python -m pytdx.bin.hqsample -s 218.75.126.9:7709          # 指定服务器
    python -m pytdx.bin.hqsample --kline 4,9                   # 只看日K线(4)和日K(9)
    python -m pytdx.bin.hqsample --all-kline                   # 查看全部12种K线
"""

from __future__ import unicode_literals

import os
import sys
import random
import click

if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))

from pytdx.hq import TdxHq_API
from pytdx.params import TDXParams
from pytdx.config.hosts import hq_hosts
from pytdx.util.click_util import split_comma_security
from pytdx.util.security_util import Security
import pandas as pd

pd.set_option('display.max_columns', None)
pd.set_option('display.max_rows', None)
pd.set_option('display.width', 200)

KLINE_NAMES = {
    0: "5分钟K线",
    1: "15分钟K线",
    2: "30分钟K线",
    3: "1小时K线",
    4: "日K线",
    5: "周K线",
    6: "月K线",
    7: "1分钟(exhq)",
    8: "1分钟K线",
    9: "日K线(alt)",
    10: "季K线",
    11: "年K线",
}

DEFAULT_KLINE_TYPES = [4, 0, 8]  # 日K、5分钟、1分钟


def find_server(timeout=3):
    """尝试连接服务器列表，返回第一个可用的"""
    api = TdxHq_API()
    for name, ip, port in hq_hosts:
        try:
            r = api.connect(ip, port, time_out=timeout)
            if r:
                api.disconnect()
                return ip, port, name
        except Exception:
            pass
    return None, None, None


def get_all_securities(api: TdxHq_API, market):
    """获取指定市场的全部证券列表"""
    result = []
    start = 0
    while True:
        data = api.get_security_list(market, start)
        if data is None or len(data) == 0:
            break
        result.extend(data)
        start += len(data)
    return result


@click.command()
@click.option('-m', '--market', default=0, type=click.INT,
              help="市场代码: 0=深圳, 1=上海 (默认: 0)")
@click.option('-n', '--count', default=5, type=click.INT,
              help="随机抽样数量 (默认: 5)")
@click.option('-s', '--server', default=None, type=click.STRING,
              help="服务器地址 ip:port，不指定则自动选择")
@click.option('--kline', default=None, type=click.STRING,
              help="K线类型列表，逗号分隔 (如: 4,0,8)。默认: 4,0,8")
@click.option('--all-kline', is_flag=True, default=False,
              help="查看全部12种K线级别")
@click.option('--kline-count', default=5, type=click.INT,
              help="每种K线获取的条数 (默认: 5)")
@click.option('--quote-only', is_flag=True, default=False,
              help="只获取实时行情，不获取K线")
@click.option('--kline-only', is_flag=True, default=False,
              help="只获取K线，不获取实时行情")
@click.option('--l2', 'show_l2', is_flag=True, default=False,
              help="显示L2逐笔成交数据 (Level 2 Tick)")
@click.option('--l2-count', default=15, type=click.INT,
              help="L2逐笔数据获取条数 (默认: 15)")
@click.option('--l2-flow', 'show_l2_flow', is_flag=True, default=False,
              help="显示L2资金流向分析 (按订单大小×方向聚合)")
@click.option('-S', '--security', 'securities', multiple=True,
              callback=split_comma_security,
              help="指定股票代码 (如 -S 600000,000001,300750.SZ)。"
                   "指定后忽略 -m/-n 随机抽样")
def main(market, count, server, kline, all_kline, kline_count,
         quote_only, kline_only, show_l2, l2_count, show_l2_flow,
         securities: list[Security]):
    """
    随机抽样行情测试工具 —— 从指定市场随机获取标的行情和K线数据。
    """
    # 确定K线类型
    if all_kline:
        kline_types = list(range(12))
    elif kline:
        kline_types = [int(x.strip()) for x in kline.split(',')]
    else:
        kline_types = DEFAULT_KLINE_TYPES

    # 连接服务器
    api = TdxHq_API()
    if server:
        ip, port = server.split(':')
        port = int(port)
        server_name = server
    else:
        click.secho("自动选择服务器...", fg="yellow")
        ip, port, server_name = find_server()
        if ip is None:
            click.secho("无法连接任何服务器", fg="red")
            sys.exit(1)

    click.secho(f"连接 {server_name} ({ip}:{port}) ...", fg="yellow")
    r = api.connect(ip, port, time_out=30)
    if not r:
        click.secho("连接失败", fg="red")
        sys.exit(1)
    click.secho("连接成功!", fg="green")

    market_name = "深圳" if market == 0 else "上海"

    try:
        # ---- 确定测试标的 ----
        if securities:
            # 明确指定股票代码 → 直接使用，跳过随机抽样
            samples = [{'code': s.code, 'name': ''} for s in securities]
            codes = [s.market_tuple for s in securities]
            click.secho("=" * 60, fg="cyan")
            click.secho("指定标的", fg="cyan", bold=True)
            click.secho("=" * 60, fg="cyan")
            for s in securities:
                click.echo(f"  {s.full_code}")
        else:
            # 随机抽样模式
            click.secho(f"\n获取{market_name}市场证券列表...", fg="yellow")
            all_secs = get_all_securities(api, market)
            if not all_secs:
                click.secho("获取证券列表失败", fg="red")
                return

            click.secho(f"共 {len(all_secs)} 只证券，随机抽取 {count} 只\n", fg="green")

            samples = random.sample(all_secs, min(count, len(all_secs)))
            codes = [(market, s['code']) for s in samples]

            click.secho("=" * 60, fg="cyan")
            click.secho(f"抽样标的 ({market_name}市场)", fg="cyan", bold=True)
            click.secho("=" * 60, fg="cyan")
            for s in samples:
                click.echo(f"  {s['code']}  {s.get('name', '')}")

        # 实时行情
        if not kline_only:
            click.secho(f"\n{'=' * 60}", fg="cyan")
            click.secho("实时行情 (Realtime Quotes)", fg="cyan", bold=True)
            click.secho("=" * 60, fg="cyan")
            quotes = api.get_security_quotes(codes)
            if quotes:
                df = api.to_df(quotes)
                cols = [c for c in ['code', 'name', 'price', 'last_close',
                                    'open', 'high', 'low', 'vol', 'amount',
                                    'bid1', 'ask1', 'bid_vol1', 'ask_vol1']
                        if c in df.columns]
                click.echo(df[cols].to_string(index=False))
            else:
                click.secho("  获取行情失败", fg="red")

        # K线数据
        if not quote_only:
            for cat in kline_types:
                cat_name = KLINE_NAMES.get(cat, f"类型{cat}")
                click.secho(f"\n{'=' * 60}", fg="cyan")
                click.secho(f"K线: {cat_name} (category={cat}, "
                            f"最近{kline_count}条)", fg="cyan", bold=True)
                click.secho("=" * 60, fg="cyan")

                for i, s in enumerate(samples):
                    code = s['code']
                    name = s.get('name', '')
                    mkt = codes[i][0]
                    bars = api.get_security_bars(cat, mkt, code,
                                                 0, kline_count)
                    if bars and len(bars) > 0:
                        df = api.to_df(bars)
                        cols = [c for c in ['datetime', 'open', 'high',
                                            'low', 'close', 'vol', 'amount']
                                if c in df.columns]
                        click.secho(f"\n  [{code} {name}]", fg="white",
                                    bold=True)
                        click.echo(df[cols].to_string(index=False))
                    else:
                        click.secho(f"  [{code} {name}] 无数据", fg="yellow")

        # L2逐笔成交数据
        if show_l2:
            click.secho(f"\n{'=' * 80}", fg="cyan")
            click.secho("L2逐笔成交数据 (Level 2 Tick: L2_VOL / L2_VOLNUM / L2_AMO)",
                        fg="cyan", bold=True)
            click.secho("=" * 80, fg="cyan")

            for i, s in enumerate(samples):
                code = s['code']
                name = s.get('name', '')
                mkt = codes[i][0]
                ticks = api.get_l2_transaction_data(mkt, code, 0, l2_count)
                if ticks and len(ticks) > 0:
                    # Calculate statistics
                    total_vol = sum(t['vol'] for t in ticks)
                    total_amount = sum(t['amount'] for t in ticks)
                    total_num = sum(t['num'] for t in ticks)
                    click.secho(f"\n  [{code} {name}] "
                                f"(最近{l2_count}笔, 总成交量={total_vol}手, "
                                f"总笔数={total_num}, 总金额={total_amount}元)",
                                fg="white", bold=True)

                    # Display as table
                    click.echo(f"  {'时间':<8} {'价格':<8} "
                               f"{'L2_VOL(量)':>10} {'L2_VOLNUM(笔)':>14} "
                               f"{'L2_AMO(额)':>14} {'方向':>6}")
                    click.echo(f"  {'-'*60}")
                    for t in ticks[:15]:
                        bs_str = {0: '买', 1: '卖', 2: '中性'}.get(
                            t['buyorsell'], str(t['buyorsell']))
                        click.echo(f"  {t['time']:<8} {t['price']:<8.2f} "
                                   f"{t['vol']:>10d} {t['num']:>14d} "
                                   f"{t['amount']:>14d} {bs_str:>6}")
                else:
                    click.secho(f"  [{code} {name}] 无L2数据", fg="yellow")

        # L2资金流向分析
        if show_l2_flow:
            from pytdx.l2_flow import fetch_and_analyze_l2_flow

            for i, s in enumerate(samples):
                code = s['code']
                name = s.get('name', '')
                mkt = codes[i][0]
                click.secho(f"\n{'=' * 70}", fg="cyan")
                click.secho(f"L2资金流向: [{code} {name}] "
                            f"(按订单大小×方向聚合)", fg="cyan", bold=True)
                click.secho(f"对应公式: L2_VOL(N,M) / L2_AMO(N,M) / L2_VOLNUM(N,M)",
                            fg="cyan")
                click.secho("=" * 70, fg="cyan")

                try:
                    result = api.analyze_l2_flow(mkt, code)
                    click.echo(result['formatted'])
                except Exception as e:
                    click.secho(f"  分析失败: {e}", fg="red")

    finally:
        api.disconnect()
        click.secho("\n断开连接", fg="green")


if __name__ == '__main__':
    main()
