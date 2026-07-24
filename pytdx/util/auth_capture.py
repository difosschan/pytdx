# coding=utf-8
"""
7719 认证 blob 提取工具

从运行中的通达信客户端抓取其 7719 连接的认证 init blob，
并将其存入 .env 文件供 advhq.py 使用。

用法::

    # 自动模式: 抓包提取 auth blob
    python -m pytdx.util.auth_capture

    # 手动模式: 指定已知的 auth blob hex
    python -m pytdx.util.auth_capture --blob 0c01187b00011a...

    # 指定接口和输出文件
    python -m pytdx.util.auth_capture -i 5 -o .env

工作原理:
  1. 使用 tshark 抓取 7719 端口的 TCP 流量
  2. 等待通达信发送认证 init blob (cmd 0x7b18, 292 字节)
  3. 自动存入 .env 文件::

       ADVHQ_AUTH_BLOB=0c01187b00011a011a...

  4. advhq.py 在 connect() 时自动读取 .env 中的 auth blob
"""

from __future__ import annotations

import os
import sys
import re
import subprocess
import time
import click


# 认证 blob 特征: 以 0c01187b 开头, 292 字节
AUTH_BLOB_PATTERN = re.compile(r'^(0c01187b[0-9a-fA-F]{576})$')  # 292B = 584 hex chars

DEFAULT_ENV_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    '.env'
)

TSHARK_PATHS = [
    r'D:\Program Files\Wireshark\tshark.exe',
    r'C:\Program Files\Wireshark\tshark.exe',
    'tshark',
]


def find_tshark():
    """查找 tshark 可执行文件"""
    for p in TSHARK_PATHS:
        if os.path.exists(p):
            return p
    # Try PATH
    try:
        result = subprocess.run(['where', 'tshark'], capture_output=True, text=True, shell=True)
        if result.returncode == 0:
            return result.stdout.strip().split('\n')[0]
    except Exception:
        pass
    return None


def capture_auth_blob(interface=5, timeout=60):
    """
    使用 tshark 抓取 7719 端口的认证 blob

    :param interface: 网卡接口编号 (默认 5=WLAN)
    :param timeout: 最大等待时间 (秒)
    :return: auth blob hex 字符串, 或 None
    """
    tshark = find_tshark()
    if not tshark:
        click.secho("未找到 tshark/Wireshark, 请安装后重试", fg="red")
        click.secho("下载: https://www.wireshark.org/download.html", fg="yellow")
        return None

    click.secho(f"使用 tshark: {tshark}", fg="green")
    click.secho(f"在接口 {interface} 上监听 7719 端口 (最长 {timeout}s)...", fg="yellow")
    click.secho("等待通达信发送认证 blob...", fg="yellow")
    click.secho("(如果超时, 请在 TDX 中切换股票触发数据请求)", fg="white")

    # tshark 命令: 抓取 7719 端口的客户端数据帧
    cmd = [
        tshark, '-i', str(interface),
        '-f', 'tcp port 7719',
        '-T', 'fields', '-e', 'tcp.payload',
        '-Y', 'tcp.port==7719 and tcp.len>0 and tcp.payload matches "^0c01187b"',
        '-a', f'duration:{timeout}',
        '-Q',  # quiet
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
        if result.returncode != 0 and result.returncode != 1:  # 1 = no packets (normal)
            click.secho(f"tshark 警告: {result.stderr}", fg="yellow")

        output = result.stdout.strip()
        if output:
            # 取第一行匹配
            for line in output.split('\n'):
                line = line.strip()
                if AUTH_BLOB_PATTERN.match(line):
                    click.secho(f"✓ 捕获到认证 blob ({len(line)} hex chars = {len(line)//2} bytes)", fg="green")
                    return line
        else:
            click.secho("未捕获到认证 blob", fg="red")
            click.secho("提示:", fg="yellow")
            click.secho("  1. 确保通达信已登录且连接到 7719 服务器", fg="yellow")
            click.secho("  2. 在通达信中切换股票触发数据刷新", fg="yellow")
            click.secho("  3. 或用 --blob 参数手动指定", fg="yellow")
            return None

    except subprocess.TimeoutExpired:
        click.secho("抓包超时", fg="red")
        return None
    except Exception as e:
        click.secho(f"错误: {e}", fg="red")
        return None

    return None


def save_to_env(blob_hex, env_file=DEFAULT_ENV_FILE):
    """将认证 blob 存入 .env 文件"""
    # 读取现有内容
    existing = {}
    if os.path.exists(env_file):
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    existing[k.strip()] = v.strip()

    # 更新
    existing['ADVHQ_AUTH_BLOB'] = blob_hex

    # 写回
    with open(env_file, 'w', encoding='utf-8') as f:
        f.write("# pytdx 高级行情 (7719) 认证配置\n")
        f.write(f"ADVHQ_AUTH_BLOB={blob_hex}\n")
        # 保留其他配置
        for k, v in existing.items():
            if k != 'ADVHQ_AUTH_BLOB':
                f.write(f"{k}={v}\n")

    click.secho(f"✓ 已保存到 {env_file}", fg="green")
    return env_file


def load_from_env(env_file=DEFAULT_ENV_FILE):
    """从 .env 文件读取 auth blob"""
    if not os.path.exists(env_file):
        return None
    with open(env_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line.startswith('ADVHQ_AUTH_BLOB='):
                val = line.split('=', 1)[1].strip()
                if AUTH_BLOB_PATTERN.match(val):
                    return val
    return None


# ============================================================
# CLI
# ============================================================

@click.command()
@click.option('-i', '--interface', default=5, type=click.INT,
              help="网卡接口编号 (默认: 5=WLAN)")
@click.option('-t', '--timeout', default=60, type=click.INT,
              help="最长等待时间 (默认: 60s)")
@click.option('-o', '--output', default=None, type=click.STRING,
              help="输出 .env 文件路径")
@click.option('--blob', default=None, type=click.STRING,
              help="手动指定 auth blob hex (跳过抓包)")
@click.option('--show', 'show_blob', is_flag=True, default=False,
              help="仅显示已保存的 auth blob")
@click.option('--clear', 'clear_blob', is_flag=True, default=False,
              help="清除已保存的 auth blob")
def main(interface, timeout, output, blob, show_blob, clear_blob):
    """
    7719 认证 blob 提取工具

    从运行中的通达信客户端自动抓取其 7719 连接的认证 init blob，
    存入 .env 文件供 pytdx.advhq 使用。
    """
    env_file = output or DEFAULT_ENV_FILE

    if show_blob:
        existing = load_from_env(env_file)
        if existing:
            click.secho(f"当前 auth blob ({len(existing)//2} bytes):", fg="green")
            click.echo(f"  {existing[:80]}...")
        else:
            click.secho("未找到已保存的 auth blob", fg="yellow")
        return

    if clear_blob:
        if os.path.exists(env_file):
            os.remove(env_file)
            click.secho("✓ 已清除 .env 文件", fg="green")
        return

    # 获取 blob
    blob_hex = None
    if blob:
        blob_hex = blob.strip()
        if not AUTH_BLOB_PATTERN.match(blob_hex):
            click.secho("无效的 auth blob 格式 (应以 0c01187b 开头, 292 字节)", fg="red")
            return
    else:
        blob_hex = capture_auth_blob(interface, timeout)

    if blob_hex:
        save_to_env(blob_hex, env_file)
        click.secho("", fg="green")
        click.secho("现在可以在 advhq.py 中使用认证连接:", fg="green")
        click.secho("  from pytdx.advhq import AdvHq_API", fg="bright")
        click.secho("  api = AdvHq_API()", fg="bright")
        click.secho("  api.connect()  # 自动读取 .env 中的 ADVHQ_AUTH_BLOB", fg="bright")


if __name__ == '__main__':
    main()
