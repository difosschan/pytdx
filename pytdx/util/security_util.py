# coding=utf-8
"""
证券代码抽象类

将 A 股 6 位数字代码自动识别市场并构建完整证券代码 (带 .SH/.SZ/.BJ 后缀)，
同时提供 pytdx 协议所需的 market 整型值。

用法::

    from pytdx.util.security_util import Security

    s = Security('600000')
    s.code         # '600000'      纯数字代码
    s.full_code    # '600000.SH'   完整代码
    s.market       # 1             pytdx 市场代码 (0=SZ, 1=SH, 2=BJ)
    s.exchange     # 'SH'          交易所后缀
    s.suffix       # '.SH'         带点的后缀
    s.market_name  # '上海'        中文市场名

    # 也支持已有后缀的代码
    Security('000001.SZ').code   # '000001'
    Security('430047.BJ').market # 2

    # 可迭代 / 可比较
    str(Security('600000'))   # '600000.SH'
    Security('600000') == Security('600000.SH')  # True
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pytdx.params import TDXParams


# ---- 交易所后缀 → market 映射 ----
_SUFFIX_TO_MARKET = {
    'SH': TDXParams.MARKET_SH,   # 上海
    'SZ': TDXParams.MARKET_SZ,   # 深圳
    'BJ': TDXParams.MARKET_BJ,   # 北京（北交所）
    'NQ': TDXParams.MARKET_BJ,   # 新三板 → 按北京处理（需根据实际场景调整）
}

_MARKET_TO_SUFFIX = {v: k for k, v in _SUFFIX_TO_MARKET.items()}

_MARKET_NAMES = {
    TDXParams.MARKET_SH: '上海',
    TDXParams.MARKET_SZ: '深圳',
    TDXParams.MARKET_BJ: '北京',
}

# ---- A 股 6 位数字代码规则 ----
_A_SHARE_RULES = [
    # (前缀集合, 市场后缀)
    (('60',),            'SH'),   # 上海主板   600xxx-609xxx
    (('68',),            'SH'),   # 上海科创板 688xxx-689xxx
    (('00', '30'),       'SZ'),   # 深圳主板+创业板
    (('43', '8', '83', '87', '88'), 'BJ'),  # 北京所
]

# 带后缀的代码正则: 600000.SH, 000001.sz
_RE_FULL_CODE = re.compile(r'^(\d{4,6})\.([A-Za-z]{2,4})$')


def _deduce_exchange(code: str) -> str:
    """根据 6 位纯数字代码推导交易所后缀"""
    if len(code) == 6 and code.isdigit():
        for prefixes, suffix in _A_SHARE_RULES:
            for prefix in prefixes:
                if code.startswith(prefix):
                    return suffix
    return 'SZ'  # fallback


@dataclass(frozen=True)
class Security:
    """
    证券代码抽象

    支持:
    - 6 位纯数字代码 (自动识别市场): ``Security('600000')``
    - 带后缀的完整代码: ``Security('000001.SZ')``
    - 大小写不敏感: ``Security('600000.sh')``
    """

    _code: str       # 纯 6 位数字代码
    _suffix: str     # 交易所后缀 (SH/SZ/BJ, 不含点号)
    _market: int     # pytdx 市场代码

    def __init__(self, raw: str):
        if not raw or not isinstance(raw, str):
            raise ValueError(f"无效的证券代码: {raw!r}")

        raw = raw.strip().upper()

        m = _RE_FULL_CODE.match(raw)
        if m:
            code, suffix = m.group(1), m.group(2).upper()
        elif len(raw) == 6 and raw.isdigit():
            code = raw
            suffix = _deduce_exchange(raw)
        else:
            raise ValueError(
                f"无法识别的证券代码格式: {raw!r}，"
                f"期望 6 位数字 (如 600000) 或带后缀 (如 000001.SZ)"
            )

        market = _SUFFIX_TO_MARKET.get(suffix)
        if market is None:
            raise ValueError(f"不支持的市场后缀: .{suffix} (支持: {list(_SUFFIX_TO_MARKET)})")

        # dataclass frozen → 用 object.__setattr__
        object.__setattr__(self, '_code', code)
        object.__setattr__(self, '_suffix', suffix)
        object.__setattr__(self, '_market', market)

    # ---- 属性 ----
    @property
    def code(self) -> str:
        """纯 6 位数字代码，如 '600000'"""
        return self._code

    @property
    def exchange(self) -> str:
        """交易所后缀，如 'SH'"""
        return self._suffix

    @property
    def suffix(self) -> str:
        """带点号的后缀，如 '.SH'"""
        return f'.{self._suffix}'

    @property
    def full_code(self) -> str:
        """完整代码，如 '600000.SH'"""
        return f'{self._code}.{self._suffix}'

    @property
    def market(self) -> int:
        """pytdx 市场代码 (0=深圳, 1=上海, 2=北京)"""
        return self._market

    @property
    def market_name(self) -> str:
        """中文市场名"""
        return _MARKET_NAMES.get(self._market, '未知')

    @property
    def is_shanghai(self) -> bool:
        return self._market == TDXParams.MARKET_SH

    @property
    def is_shenzhen(self) -> bool:
        return self._market == TDXParams.MARKET_SZ

    @property
    def is_beijing(self) -> bool:
        return self._market == TDXParams.MARKET_BJ

    # ---- 协议层常用值 ----
    @property
    def encoded(self) -> bytes:
        """6 位代码的 UTF-8 bytes，供 parser 层直接使用"""
        return self._code.encode('utf-8')

    @property
    def market_tuple(self) -> tuple[int, str]:
        """返回 (market, code) 元组，用于 get_security_quotes 等接口"""
        return (self._market, self._code)

    # ---- dunder ----
    def __str__(self) -> str:
        return self.full_code

    def __repr__(self) -> str:
        return f"Security('{self.full_code}')"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Security):
            return self.full_code == other.full_code
        if isinstance(other, str):
            return self.full_code == other or self.code == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.full_code)

    def __lt__(self, other: Security) -> bool:
        return self.full_code < other.full_code

    # ---- 工厂方法 ----
    @classmethod
    def from_tuple(cls, market: int, code: str) -> Security:
        """从 (market, code) 元组构建"""
        suffix = _MARKET_TO_SUFFIX.get(market)
        if suffix is None:
            raise ValueError(f"未知市场代码: {market}")
        return cls(f'{code}.{suffix}')

    @classmethod
    def parse_list(cls, raw_codes: list[str]) -> list[Security]:
        """批量解析"""
        return [cls(c) for c in raw_codes]


# ============================================================
# 演示 & 测试
# ============================================================
if __name__ == '__main__':
    # 基础构建
    for raw in ['600000', '000001', '300750', '688981', '430047',
                '600000.SH', '000001.sz', '430047.BJ']:
        s = Security(raw)
        print(f'{raw:<14} → code={s.code:<8} full={s.full_code:<12} '
              f'market={s.market} ({s.market_name}) exchange={s.exchange}')

    print()

    # 属性
    s = Security('600519')
    print(f'code:        {s.code}')
    print(f'full_code:   {s.full_code}')
    print(f'market:      {s.market}')
    print(f'exchange:    {s.exchange}')
    print(f'suffix:      {s.suffix}')
    print(f'market_name: {s.market_name}')
    print(f'is_shanghai: {s.is_shanghai}')
    print(f'is_shenzhen: {s.is_shenzhen}')
    print(f'encoded:     {s.encoded}')
    print(f'market_tuple:{s.market_tuple}')

    print()

    # 相等性
    assert Security('600000') == Security('600000.SH')
    assert Security('000001') == Security('000001.SZ')
    assert Security('430047') == Security('430047.BJ')
    print('相等性测试通过 ✓')

    # 批量
    codes = ['600000', '000001', '300750', '688981', '600519.SH']
    secs = Security.parse_list(codes)
    print(f'批量解析: {[str(s) for s in secs]}')

    # 排序
    sorted_secs = sorted(secs)
    print(f'排序: {[s.full_code for s in sorted_secs]}')

    # from_tuple
    s2 = Security.from_tuple(TDXParams.MARKET_SH, '600000')
    assert s2 == Security('600000.SH')
    print('from_tuple 测试通过 ✓')

    # 异常
    for bad in ['', 'abc', '12', 'abcdef']:
        try:
            Security(bad)
        except ValueError as e:
            print(f'正确拒绝: {bad!r} → {e}')

    print()
    print('全部测试通过 ✓')
