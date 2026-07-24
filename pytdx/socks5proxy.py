# coding=utf-8
"""
极简 SOCKS5 代理 — 验证 TDX 代理连通性

用法::

    # 启动 (默认 127.0.0.1:1080)
    python -m pytdx.socks5proxy

    # 指定端口
    python -m pytdx.socks5proxy -p 2080
"""
import socket
import struct
import threading

import click


def pipe(src, dst, label):
    n = 0
    try:
        while True:
            src.settimeout(30)
            data = src.recv(65536)
            if not data:
                break
            dst.send(data)
            n += len(data)
    except Exception:
        pass
    click.echo(f'  [{label}] {n} 字节')


def handle(client, addr):
    click.secho(f'\n连接: {addr}', fg='yellow')
    remote = None
    try:
        ver, nmethods = struct.unpack('!BB', client.recv(2))
        methods = client.recv(nmethods)
        click.echo(f'  SOCKS5: ver={ver} methods={methods.hex()}')
        client.send(b'\x05\x00')

        hdr = client.recv(4)
        ver, cmd, rsv, atyp = struct.unpack('!BBBB', hdr)
        if atyp == 1:
            host = socket.inet_ntoa(client.recv(4))
        elif atyp == 3:
            host = client.recv(client.recv(1)[0]).decode()
        else:
            click.secho(f'  不支持的地址类型: {atyp}', fg='red')
            client.close()
            return
        port = struct.unpack('!H', client.recv(2))[0]
        click.echo(f'  目标: {host}:{port}')

        remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        remote.settimeout(5)
        remote.connect((host, port))
        click.secho(f'  已连接', fg='green')

        client.send(b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')

        t1 = threading.Thread(target=pipe, args=(client, remote, f'{addr}→{host}:{port}'), daemon=True)
        t2 = threading.Thread(target=pipe, args=(remote, client, f'{host}:{port}→{addr}'), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        click.secho(f'  关闭', fg='yellow')
    except Exception as e:
        click.secho(f'  错误: {e}', fg='red')
    finally:
        try:
            client.close()
        except Exception:
            pass
        if remote:
            try:
                remote.close()
            except Exception:
                pass


@click.command()
@click.option('-p', '--port', default=1080, type=click.INT, help='监听端口 (默认: 1080)')
@click.option('-h', '--host', default='127.0.0.1', type=click.STRING, help='监听地址 (默认: 127.0.0.1)')
def main(host, port):
    """极简 SOCKS5 代理 — 验证 TDX 代理连通性"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(10)
    click.secho(f'SOCKS5 代理 {host}:{port}', fg='green')
    click.secho('等待连接 ...', fg='yellow')
    while True:
        client, addr = srv.accept()
        threading.Thread(target=handle, args=(client, addr), daemon=True).start()


if __name__ == '__main__':
    main()
