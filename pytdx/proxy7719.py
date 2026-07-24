# coding=utf-8
"""
7719 共享代理 + L2 交互查询

用法::

    一次性:  python -m pytdx.proxy7719 start       # 启动 SOCKS5 代理
    交互:    python -m pytdx.proxy7719             # 进入 REPL (反复查询)
    单次:    python -m pytdx.proxy7719 test        # 单次测试
    单次:    python -m pytdx.proxy7719 query -c 600000,000001
"""
import os, socket, struct, threading, time, random, zlib

import click
from rich.console import Console

_CONSOLE = Console()

SOCKS_PORT = 17719
CFG_PATH = r'D:\finance_tool_\new_tdx_hd_test\connect.cfg'
AUTH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.auth_blob')

SERVERS = ['139.9.211.54', '139.159.214.37', '116.205.239.160',
           '43.139.246.207', '139.9.1.206']

_SESSION = {}


# ============================================================
# SOCKS5 代理 (截获 auth blob)
# ============================================================

class L2Proxy:
    def __init__(self):
        self.auth_blob = None

    def socks5_handshake(self, client):
        ver, n = struct.unpack('!BB', client.recv(2))
        client.recv(n); client.send(b'\x05\x00')
        hdr = client.recv(4)
        _, _, _, atyp = struct.unpack('!BBBB', hdr)
        if atyp == 1: host = socket.inet_ntoa(client.recv(4))
        elif atyp == 3: host = client.recv(client.recv(1)[0]).decode()
        else: raise Exception(f'bad atyp={atyp}')
        port = struct.unpack('!H', client.recv(2))[0]
        remote = socket.socket(); remote.settimeout(5)
        remote.connect((host, port))
        client.send(b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')
        return remote, host

    def pipe(self, src, dst, capture_auth=False):
        captured = bytearray()
        try:
            while True:
                src.settimeout(30)
                data = src.recv(65536)
                if not data: break
                if capture_auth and not self.auth_blob:
                    captured.extend(data)
                    idx = captured.find(b'\x0c\x01\x18\x7b')
                    if idx >= 0 and len(captured) - idx >= 292:
                        self.auth_blob = bytes(captured[idx:idx + 292])
                        with open(AUTH_FILE, 'wb') as f:
                            f.write(self.auth_blob)
                        _CONSOLE.print(
                            f'[proxy] auth blob {len(self.auth_blob)}B → {AUTH_FILE}',
                            style='yellow')
                dst.send(data)
        except Exception:
            pass

    def handle(self, client):
        remote = None
        try:
            remote, host = self.socks5_handshake(client)
            t1 = threading.Thread(target=self.pipe, args=(client, remote, True), daemon=True)
            t2 = threading.Thread(target=self.pipe, args=(remote, client, False), daemon=True)
            t1.start(); t2.start()
            t1.join(); t2.join()
        except Exception:
            pass
        finally:
            try: client.close()
            except: pass
            try: remote.close()
            except: pass

    def start(self):
        self._stop = threading.Event()
        socks = socket.socket(); socks.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        socks.bind(('127.0.0.1', SOCKS_PORT)); socks.listen(10); socks.settimeout(1)
        _CONSOLE.print(f'SOCKS5 代理 127.0.0.1:{SOCKS_PORT}', style='green')
        _CONSOLE.print(f'auth → {AUTH_FILE}', style='dim')
        _CONSOLE.print('Ctrl+C 退出', style='yellow')

        def accept():
            while not self._stop.is_set():
                try:
                    c, a = socks.accept()
                    threading.Thread(target=self.handle, args=(c,), daemon=True).start()
                except socket.timeout: continue
                except Exception: break
        threading.Thread(target=accept, daemon=True).start()
        try:
            while not self._stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self._stop.set()
            try: socks.close()
            except: pass
            _CONSOLE.print('已关闭', style='green')


# ============================================================
# 7719 协议工具
# ============================================================

def _recv_exact(sock, size):
    buf = bytearray()
    while len(buf) < size:
        c = sock.recv(size - len(buf))
        if not c: raise ConnectionError()
        buf.extend(c)
    return bytes(buf)


def _recv_7719(sock, timeout=5):
    """读一个完整的 7719 响应"""
    sock.settimeout(timeout)
    h5 = _recv_exact(sock, 5)
    if h5[:4] == b'\xb1\xcb\x74\x00':
        need = 16 if h5[4:5] == b'\x0c' else 11
    else:
        need = 11
    ch = h5[4:5] + _recv_exact(sock, need)
    if h5[:4] == b'\xb1\xcb\x74\x00' and h5[4:5] == b'\x0c':
        _, _, _, zs, uzs = struct.unpack('<IIIHH', ch)
    else:
        _, _, _, zs, uzs = struct.unpack('<IHHHH', ch)
    if 0 < zs < 100000:
        body = _recv_exact(sock, zs)
        if zs != uzs and body[:2] == bytes.fromhex('789c'):
            body = zlib.decompress(body)
    else:
        body = b''
    return h5 + ch[:need] + body


def _load_auth():
    if not os.path.exists(AUTH_FILE):
        _CONSOLE.print(f'[!] {AUTH_FILE} 不存在, 请先启动代理并登录 TDX', style='red')
        return None
    with open(AUTH_FILE, 'rb') as f:
        return f.read()


def _connect_7719(auth_blob):
    """独立连接 7719 + auth + setup, 失败时关闭 socket 避免泄漏"""
    for ip in SERVERS:
        s = None
        try:
            s = socket.socket(); s.settimeout(5)
            s.connect((ip, 7719))
            s.send(auth_blob)
            _recv_7719(s)
            for cmd in [
                '0c0218940001030003000d000a',
                '0c031899000120002000db0f7464786c6576656c3200009a99f9400e5400000000000000000000000005',
                '0c0718930001380038000a00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
                '0c081871000102000200de0f',
            ]:
                s.send(bytes.fromhex(cmd))
                _recv_7719(s)
            time.sleep(0.3)
            return s
        except Exception:
            if s:
                try: s.close()
                except: pass
            continue
    return None


# ============================================================
# REPL / CLI 命令
# ============================================================

@click.command(context_settings={'help_option_names': ['-?', '--help', '-h']})
@click.pass_context
def start_cmd(ctx):
    """启动 SOCKS5 代理 (后台截获 auth blob)"""
    L2Proxy().start()


@click.command(context_settings={'help_option_names': ['-?', '--help', '-h']})
@click.pass_context
def test(ctx):
    """测试 L2 连通性"""
    s = _SESSION.get('sock')
    if not s:
        _CONSOLE.print('未连接', style='red'); return

    rid = struct.pack('<I', random.randint(0, 0xFFFFFFFF))
    sub = (b'\x0c' + rid + bytes.fromhex('011000100050050100') +
           b'600000' + bytes.fromhex('f1e000000000'))
    s.send(sub); time.sleep(0.3)
    try:
        r = _recv_7719(s)
        zs = r.find(b'x\x9c')
        if zs >= 0:
            d = zlib.decompress(r[zs:])
            _CONSOLE.print(f'{len(r)}B → {len(d)}B 解压', style='green')
        else:
            _CONSOLE.print(f'{len(r)}B', style='green')
    except Exception as e:
        _CONSOLE.print(f'超时: {e}', style='red')


@click.command(context_settings={'help_option_names': ['-?', '--help', '-h']})
@click.option('-c', '--codes', default='600000,000001', help='股票代码, 逗号分隔')
@click.pass_context
def query(ctx, codes):
    """查询 L2 批量资金流向"""
    from pytdx.helper import get_price

    s = _SESSION.get('sock')
    if not s:
        _CONSOLE.print('未连接', style='red'); return

    stocks = [c.strip() for c in codes.split(',') if c.strip()]
    rid = struct.pack('<I', random.randint(0, 0xFFFFFFFF))
    body = bytearray(b'\x00' * 7)
    body.extend(struct.pack('<H', len(stocks)))
    body.extend(b'\x00')
    for c in stocks:
        body.extend(b'\x01'); body.extend(c.encode()[:6])
    blen = struct.pack('<H', len(body))
    pkg = bytearray(b'\x0c')
    pkg.extend(rid); pkg.extend(blen); pkg.extend(blen)
    pkg.extend(struct.pack('<H', 0x0505)); pkg.extend(body)

    s.send(pkg); time.sleep(0.5)
    try:
        r = _recv_7719(s, timeout=10)
    except Exception as e:
        _CONSOLE.print(f'超时: {e}', style='red'); return

    zs = r.find(b'x\x9c')
    if zs < 0:
        _CONSOLE.print(f'无 zlib: {r[:80].hex()}', style='red'); return

    data = zlib.decompress(r[zs:])
    _CONSOLE.print(f'{len(data)}B', style='green')
    pos = 2
    count = struct.unpack('<H', data[pos:pos+2])[0]; pos += 3
    for _ in range(count):
        pos += 1; code = data[pos:pos+6].decode(); pos += 6
        start = pos; vals = []
        try:
            for _ in range(60): v, pos = get_price(data, pos); vals.append(v)
        except: pass
        _CONSOLE.print(f'  {code}: {len(vals)}v, {pos-start}B')


@click.command(context_settings={'help_option_names': ['-?', '--help', '-h']})
@click.pass_context
def status(ctx):
    """查看状态"""
    if os.path.exists(AUTH_FILE):
        _CONSOLE.print(f'auth: {AUTH_FILE} ({os.path.getsize(AUTH_FILE)}B)', style='green')
    else:
        _CONSOLE.print(f'auth: 无', style='red')
    if _SESSION.get('sock'):
        _CONSOLE.print(f'socket: 已连接', style='green')
    else:
        _CONSOLE.print(f'socket: 未连接', style='yellow')


# ============================================================
# 初始化 / 销毁
# ============================================================

def on_init(ctx: click.Context):
    """建立 7719 连接 (CLI 和 REPL 模式都调用)"""
    ctx.ensure_object(dict)
    ctx.obj['console'] = _CONSOLE

    # 关闭旧连接避免泄漏
    if _SESSION.get('sock'):
        try: _SESSION['sock'].close()
        except: pass
        _SESSION['sock'] = None

    auth = _load_auth()
    if not auth:
        _CONSOLE.print('[!] 无 auth blob, 请先在另一个终端执行: python -m pytdx.proxy7719 start',
                       style='yellow')
        _CONSOLE.print('    然后登录通达信 (SOCKS5 代理 127.0.0.1:17719)', style='yellow')
        return

    s = _connect_7719(auth)
    if s:
        _SESSION['sock'] = s
        _CONSOLE.print('7719 已连接', style='green')
    else:
        _CONSOLE.print('7719 连接失败', style='red')


def on_destroy(ctx: click.Context):
    if _SESSION.get('sock'):
        try: _SESSION['sock'].close()
        except: pass
        _SESSION['sock'] = None
    _CONSOLE.print('会话已关闭', style='yellow')


# ============================================================
# 入口
# ============================================================

if __name__ == '__main__':
    from difoss_stock_util.click_util import repl_cli_main

    repl_cli_main(
        doc='7719 L2 数据查询工具',
        prompt='7719> ',
        on_init=on_init,
        on_destroy=on_destroy,
        console=_CONSOLE,
    )
