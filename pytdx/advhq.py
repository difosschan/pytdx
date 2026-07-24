# coding=utf-8
"""
高级行情服务器接口 (Advanced Market Data API — port 7719)
===========================================================

通达信高级行情服务器 (TCP 7719) 提供预聚合的 L2 资金流向分档统计数据，
对应公式函数 ``L2_VOL(N,M)`` / ``L2_AMO(N,M)`` / ``L2_VOLNUM(N,M)``。

.. attention::
   7719 的数据查询需要 TDX 客户端级别的认证 token。
   当前 ``AdvHq_API`` 可完成完整的协议握手和命令交互，
   但数据查询需要有效的认证 token（嵌入在 init blob 中）。

   对于不需要认证的场景，请使用 ``pytdx.l2_flow`` 模块，
   它从标准行情 (7709) 的逐笔成交数据本地计算同等的 L2 分档统计。

协议概要 (基于 Wireshark 抓包逆向，2026-07):
  - 传输层: TCP 长连接
  - 握手: 12 字节 ``0c0000000000020002001500``
  - 响应前缀: ``b1cb7400`` (4 字节会话标记)
  - 响应头: 紧凑格式(12B) 或 标准 ``<IIIHH`` (16B)
  - 压缩: zlib (magic ``789c``)

已识别的命令:
  ====== ====== ====================
  命令    方向   用途
  ====== ====== ====================
  0x1500  C→S   握手 / 服务器探测
  0x7b18  C→S   认证 init blob
  0x9418  C→S   SetupCmd2
  0x9918  C→S   SetupCmd3 (含 "tdxlevel2")
  0x6918  C→S   请求配置文件
  0x9318  C→S   SetupCmd1
  0x7118  C→S   二次握手
  0x0105  C→S   订阅单股
  0x0505  C→S   批量 L2 资金流向查询
  0x0503  C→S   单股详细 L2 查询
  0x0507  C→S   指数数据查询
  0x0fcc  C→S   大块数据 (日级 L2 明细)
  ====== ====== ====================

用法::

    # 方案 A: 本地 L2 聚合 (推荐，无需认证)
    from pytdx.l2_flow import compute_l2_flow_from_ticks
    from pytdx.hq import TdxHq_API
    api = TdxHq_API(); api.connect('180.153.18.170', 7709)
    ticks = api.get_history_transaction_data(1, '600000', 0, 50000, 20260713)
    result = compute_l2_flow_from_ticks(ticks)
    print(result['formatted'])

    # 方案 B: 7719 直连 (需 auth token)
    from pytdx.advhq import AdvHq_API
    api = AdvHq_API()
    api.connect('139.159.214.37', 7719)
    api.setup_session()          # 握手 + SetupCmd1/2/3
    if api.auth_token:            # 需从 TDX 客户端获取
        api.authenticate(api.auth_token)
        data = api.get_l2_flow_batch(['600000', '000001'])
"""

from __future__ import absolute_import

import socket
import struct
import zlib
import time
import random
from collections import OrderedDict

from pytdx.base_socket_client import BaseSocketClient, CONNECT_TIMEOUT
from pytdx.log import DEBUG, log

# ============================================================
# 协议常量
# ============================================================

ADVHQ_DEFAULT_HOST = '139.159.214.37'
ADVHQ_DEFAULT_PORT = 7719

# 响应前缀
RESP_PREFIX = b'\xb1\xcb\x74\x00'  # b1cb7400

# 握手命令 (探测/会话初始化)
CMD_HANDSHAKE = bytes.fromhex('0c0000000000020002001500')

# ---- 命令模板 (从抓包提取) ----
TMPL_SETUPCMD2 = '0c0218940001030003000d000a'
TMPL_SETUPCMD3 = (
    '0c031899000120002000db0f7464786c6576656c3200'
    '009a99f9400e5400000000000000000000000005'
)
TMPL_SETUPCMD1 = (
    '0c0718930001380038000a00'
    + '00' * 56  # 56 字节零填充
)
TMPL_HANDSHAKE2 = '0c081871000102000200de0f'

TMPL_CFG_SPEC = (
    '0c04186900012a002a00c502696e666f686172626f725f'
    '737065632e636667' + '00' * 27
)
TMPL_CFG_BIGDATA = (
    '0c05186900012a002a00c50262692f626967646174615f'
    '302e7a6970' + '00' * 26
)
TMPL_CFG_CUSTOM = (
    '0c06186900012a002a00c502637573746f6d6366675f'
    '746478746573742e7a6970' + '00' * 24
)

# 已知服务器池
ADVHQ_HOSTS = [
    ("高级行情主站1", "139.159.214.37", 7719),
    ("高级行情主站2", "116.205.239.160", 7719),
    ("高级行情主站3", "43.139.246.207", 7719),
]


# ============================================================
# 底层通信
# ============================================================

class AdvHqConnection(object):
    """7719 TCP 连接管理"""

    def __init__(self, timeout=10):
        self.sock = None
        self.timeout = timeout
        self.session_prefix = RESP_PREFIX

    def connect(self, host, port=ADVHQ_DEFAULT_PORT):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect((host, port))
        return self

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def send(self, data):
        self.sock.send(data)

    def recv_exact(self, size):
        buf = bytearray()
        while len(buf) < size:
            chunk = self.sock.recv(size - len(buf))
            if not chunk:
                raise ConnectionError('服务器断开')
            buf.extend(chunk)
        return bytes(buf)

    def recv_response(self):
        """
        接收一个 7719 响应。

        响应格式分三种:
          1. 紧凑头: b1cb7400 + 12B(<IHHHH) + zlib body (握手响应)
          2. 标准头: b1cb7400 + 0c + 16B(<IIIHH) + zlib body
          3. 无前缀: 16B(<IIIHH) + zlib body (极少数情况)
        """
        head4 = self.recv_exact(4)

        if head4 == RESP_PREFIX:
            nxt = self.recv_exact(1)
            if nxt == b'\x0c':
                # 标准格式
                hdr = self.recv_exact(16)
                _, _, _, zs, uzs = struct.unpack('<IIIHH', hdr)
            else:
                # 紧凑格式: nxt + 11 more = 12 bytes
                ch = nxt + self.recv_exact(11)
                _, _, _, zs, uzs = struct.unpack('<IHHHH', ch)
        else:
            # 无前缀: head4 + 12 more = 16 bytes
            hdr = head4 + self.recv_exact(12)
            _, _, _, zs, uzs = struct.unpack('<IIIHH', hdr)

        if zs == 0:
            return b''

        body = self.recv_exact(zs)
        if zs != uzs and body[:2] == bytes.fromhex('789c'):
            body = zlib.decompress(body)
        return body

    def cmd(self, hexstr):
        """发送命令并接收响应 (一步完成)"""
        self.send(bytes.fromhex(hexstr))
        time.sleep(0.1)
        return self.recv_response()


# ============================================================
# API 主类
# ============================================================

class AdvHq_API(object):
    """
    高级行情 API 客户端 (端口 7719)

    认证方式:
      1. .env 文件: 设置 ``ADVHQ_AUTH_BLOB=0c01187b...``
      2. 代码传参: ``api.auth_token = '0c01187b...'``
      3. 自动提取: ``python -m pytdx.util.auth_capture``

    用法::

        # 带认证的完整连接
        api = AdvHq_API()
        api.auth_token = '0c01187b00011a...'  # 或放入 .env
        api.connect('139.9.211.54', 7719)
        api.setup_session()
        data = api.subscribe('600000')
    """

    def __init__(self):
        self.conn = None
        self._auth_token = None

    # ---- auth token ----
    @property
    def auth_token(self):
        """认证 blob (292 字节 hex)"""
        if self._auth_token:
            return self._auth_token
        # 尝试从 .env 加载
        try:
            from pytdx.util.auth_capture import load_from_env
            self._auth_token = load_from_env()
        except ImportError:
            pass
        return self._auth_token

    @auth_token.setter
    def auth_token(self, value):
        self._auth_token = value

    def connect(self, host=ADVHQ_DEFAULT_HOST, port=ADVHQ_DEFAULT_PORT, timeout=10):
        """建立 TCP 连接。若有 auth_token, 自动发送认证 blob"""
        self.conn = AdvHqConnection(timeout=timeout)
        self.conn.connect(host, port)

        # 自动认证
        if self.auth_token:
            self.conn.cmd(self.auth_token)
        return self

    def close(self):
        if self.conn:
            self.conn.close()

    def handshake(self):
        """发送握手命令，获取服务器信息"""
        body = self.conn.cmd(CMD_HANDSHAKE.hex())
        info = {}
        for key in [b'Level2', b'Win', b'/tdx/']:
            idx = body.find(key)
            if idx >= 0:
                end = body.find(b'\x00', idx)
                info[key.decode('ascii', errors='ignore')] = \
                    body[idx:end].decode('latin-1', errors='ignore').strip('\x00').strip()
        return info

    def setup_session(self, with_config=True):
        """执行完整的会话初始化序列"""
        results = OrderedDict()

        # 握手
        results['handshake'] = self.handshake()

        # SetupCmd2
        results['setup2'] = len(self.conn.cmd(TMPL_SETUPCMD2))

        # SetupCmd3 ("tdxlevel2")
        results['setup3'] = len(self.conn.cmd(TMPL_SETUPCMD3))

        # 配置请求 (可选)
        if with_config:
            self.conn.cmd(TMPL_CFG_SPEC)
            self.conn.cmd(TMPL_CFG_BIGDATA)
            self.conn.cmd(TMPL_CFG_CUSTOM)

        # SetupCmd1
        results['setup1'] = len(self.conn.cmd(TMPL_SETUPCMD1))

        # 二次握手
        results['handshake2'] = len(self.conn.cmd(TMPL_HANDSHAKE2))

        return results

    def authenticate(self, token_hex):
        """
        发送认证 init blob (需从运行的 TDX 客户端抓包获取)

        :param token_hex: 292 字节认证数据的 hex 字符串
        """
        self.auth_token = token_hex
        return len(self.conn.cmd(token_hex))

    def subscribe(self, code):
        """订阅股票 (cmd 0x0105)"""
        rid = struct.pack('<I', random.randint(0, 0xFFFFFFFF))
        pkg = bytearray(b'\x0c')
        pkg.extend(rid)
        pkg.extend(bytes.fromhex('011000100050050100'))
        pkg.extend(code.encode()[:6])
        pkg.extend(bytes.fromhex('f1e000000000'))
        self.conn.send(pkg)
        return self.conn.recv_response()

    def get_l2_flow_batch(self, codes):
        """批量查询 L2 资金流向 (cmd 0x0505)"""
        rid = struct.pack('<I', random.randint(0, 0xFFFFFFFF))
        body = bytearray(b'\x00' * 8)
        body.extend(struct.pack('<H', len(codes)))
        for c in codes:
            body.extend(b'\x00')
            body.extend(c.encode()[:6])
        blen = struct.pack('<H', len(body))
        pkg = bytearray(b'\x0c')
        pkg.extend(rid)
        pkg.extend(blen)
        pkg.extend(blen)
        pkg.extend(struct.pack('<H', 0x0505))
        pkg.extend(body)
        self.conn.send(pkg)
        return self.conn.recv_response()

    def get_l2_detail(self, code):
        """单股 L2 详细数据 (cmd 0x0503, 含分钟级)"""
        rid = struct.pack('<I', random.randint(0, 0xFFFFFFFF))
        # 模板: 从 frame 80 提取
        pkg = bytearray(b'\x0c')
        pkg.extend(rid)
        pkg.extend(bytes.fromhex('01250025004705030001'))
        pkg.extend(code.encode()[:6])
        pkg.extend(bytes.fromhex('1f56020001383831333836f649020000303032343833a8550200'))
        self.conn.send(pkg)
        return self.conn.recv_response()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ============================================================
# 演示: 协议握手 (不需认证的部分)
# ============================================================
if __name__ == '__main__':
    print("=" * 60)
    print("高级行情服务器 (7719) 协议测试")
    print("=" * 60)

    for name, host, port in ADVHQ_HOSTS[:3]:
        print(f"\n尝试 {name} ({host}:{port})...")
        try:
            api = AdvHq_API()
            api.connect(host, port, timeout=5)
            info = api.handshake()
            print(f"  服务器: {info}")
            # Test full setup
            result = api.setup_session(with_config=False)
            print(f"  Setup: {dict(result)}")
            api.close()
        except Exception as e:
            print(f"  失败: {e}")

    print()
    print("=" * 60)
    print("7719 协议状态:")
    print("  握手+Setup:     ✅ 已打通")
    print("  数据查询:       ⚠️ 需会话级认证token")
    print()
    print("生产环境推荐: pytdx.l2_flow (从7709本地聚合)")
    print("  from pytdx.l2_flow import compute_l2_flow_from_ticks")
    print("=" * 60)
