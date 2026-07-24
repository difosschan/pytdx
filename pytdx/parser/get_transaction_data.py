# coding=utf-8

from pytdx.parser.base import BaseParser
from pytdx.helper import get_datetime, get_volume, get_price, get_time
from collections import OrderedDict
import struct
import six

class GetTransactionData(BaseParser):

    def setParams(self, market, code, start, count):
        if type(code) is six.text_type:
            code = code.encode("utf-8")
        pkg = bytearray.fromhex(u'0c 17 08 01 01 01 0e 00 0e 00 c5 0f')
        pkg.extend(struct.pack("<H6sHH", market, code, start, count))
        self.send_pkg = pkg

    def parseResponse(self, body_buf):
        pos = 0
        (num, ) = struct.unpack("<H", body_buf[:2])
        pos += 2
        ticks = []
        last_price = 0
        for i in range(num):
            ### ?? get_time
            # \x80\x03 = 14:56

            hour, minute, pos = get_time(body_buf, pos)

            price_raw, pos = get_price(body_buf, pos)
            vol, pos = get_price(body_buf, pos)
            num, pos = get_price(body_buf, pos)
            buyorsell, pos = get_price(body_buf, pos)
            _, pos = get_price(body_buf, pos)

            last_price = last_price + price_raw
            price = float(last_price) / 100

            tick = OrderedDict(
                [
                    ("time", "%02d:%02d" % (hour, minute)),
                    ("price", price),
                    ("vol", vol),        # L2_VOL: 每笔成交量(手)
                    ("num", num),        # L2_VOLNUM: 每笔成交笔数(订单数)
                    ("amount", int(price * vol * 100)),  # L2_AMO: 每笔成交金额(元)
                    ("buyorsell", buyorsell),  # 0=买 1=卖 2=中性
                ]
            )

            ticks.append(tick)

        return ticks


