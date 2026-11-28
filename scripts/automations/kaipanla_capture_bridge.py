"""Save excerpts copied from 开盘啦 via a short-lived local form."""

from __future__ import annotations

import argparse
import html
import json
import secrets
import signal
import subprocess
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs


MAX_BODY = 500_000
CHECKPOINT = Path(__file__).resolve().with_name('kaipanla_checkpoint.py')
FORM = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>开盘啦本地采集</title>
<main>
{message}
<form method="post">
<label for="batch">批次编号</label><input id="batch" name="batch" maxlength="80" required>
<label for="page">采集批次</label><textarea id="page" name="page" required></textarea>
<button type="submit">保存批次</button>
</form></main></html>'''


def make_handler(token: str, day: Path, fact: str, actor: str, input_format: str):
    class CaptureHandler(BaseHTTPRequestHandler):
        timeout = 5

        def log_message(self, *_):
            pass

        def response(self, code: int, message: str = '') -> None:
            content = FORM.format(message=f'<output>{html.escape(message)}</output>' if message else '').encode()
            self.send_response(code)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header("Content-Security-Policy", "default-src 'none'; form-action 'self'; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def authorized(self) -> bool:
            host = f'127.0.0.1:{self.server.server_port}'
            return (
                self.path == token and self.headers.get('Host') == host
                and self.headers.get('Origin') in {None, f'http://{host}'}
            )

        def do_GET(self) -> None:
            if not self.authorized():
                self.send_error(404)
                return
            self.response(200)

        def do_POST(self) -> None:
            if not self.authorized():
                self.send_error(404)
                return
            if self.headers.get_content_type() != 'application/x-www-form-urlencoded':
                self.send_error(415)
                return
            length = self.headers.get('Content-Length', '')
            if not length.isascii() or not length.isdigit() or len(length) > 6 or not 0 < int(length) <= MAX_BODY:
                self.send_error(413)
                return
            try:
                raw = self.rfile.read(int(length))
                if len(raw) != int(length):
                    raise ValueError('采集批次未收全')
                pairs = parse_qs(raw.decode('ascii'), errors='strict', strict_parsing=True, max_num_fields=2)
                batch, = pairs['batch']
                page, = pairs['page']
            except (ValueError, KeyError, TimeoutError):
                self.response(400, '采集批次格式无效')
                return
            command = [
                sys.executable, str(CHECKPOINT), 'record', '--day-dir', str(day), '--date', fact,
                '--input-by', actor, '--batch-id', batch, '--input', '-', '--format', input_format,
            ]
            try:
                result = subprocess.run(command, input=page, text=True, encoding='utf-8',
                                        capture_output=True, timeout=12, check=False)
            except subprocess.TimeoutExpired:
                self.response(503, '保存超时，先用 status 核对批次是否已落盘')
                return
            if result.returncode:
                self.response(422, result.stderr.strip()[-500:] or '批次保存失败')
                return
            state = json.loads(result.stdout)
            self.response(200, (
                f"批次 {batch} 已保存，已验收 {len(state['completed_slots'])} 个视图，"
                f"待采 {len(state['remaining_slots'])} 个视图，待核商品 {len(state['pending_commodity'])} 项。"
            ))

    return CaptureHandler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day-dir', required=True, type=Path)
    parser.add_argument('--date', required=True)
    parser.add_argument('--input-by', required=True)
    parser.add_argument('--format', choices=('json', 'pipe'), default='pipe')
    parser.add_argument('--ttl', type=int, default=2400, help='表单有效秒数，最长 2700 秒')
    args = parser.parse_args()
    try:
        fact = date.fromisoformat(args.date).isoformat()
    except ValueError:
        fact = ''
    if fact != args.date or not args.input_by.strip() or not 1 <= args.ttl <= 2700:
        parser.error('须提供 YYYY-MM-DD 事实日、请求者和 1～2700 秒有效期')
    token = '/' + secrets.token_urlsafe(24)
    handler = make_handler(token, args.day_dir.resolve(), fact, args.input_by, args.format)

    def expire(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGALRM, expire)
    with HTTPServer(('127.0.0.1', 0), handler) as server:
        signal.alarm(args.ttl)
        print(f'http://127.0.0.1:{server.server_port}{token}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            signal.alarm(0)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
