#!/usr/bin/env python3
"""仅监听回环地址的控制接口，凭据与模型生成文件分开保存。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import threading
import time

from release import atomic, decision_lock

BASE = Path('/srv/game-agent')
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, fmt, *args):
        # 不记录认证头或反馈正文。
        print('Control request:', self.command, self.path, flush=True)

    def response(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        expected = 'Bearer ' + (BASE / 'control/api-key').read_text().strip()
        return hmac.compare_digest(self.headers.get('Authorization', '').encode(), expected.encode())

    def do_GET(self):
        if self.path == '/api/state':
            p = BASE / 'public/state.json'
            return self.response(200, json.loads(p.read_text()) if p.exists() else {'phase': 'preparing'})
        if self.path == '/api/control':
            if not self.authorized():
                return self.response(401, {'error': '需要控制密钥'})
            return self.response(200, json.loads((BASE / 'control/commands.json').read_text()))
        self.response(404, {'error': 'Not found'})

    def do_POST(self):
        if self.path != '/api/control':
            return self.response(404, {'error': 'Not found'})
        if not self.authorized():
            return self.response(401, {'error': '需要控制密钥'})
        if self.headers.get('Origin') not in (None, 'https://lab.doufei.eu.org'):
            return self.response(403, {'error': 'Origin rejected'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.response(415, {'error': 'JSON required'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192:
                return self.response(413, {'error': 'Request too large'})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict) or data.get('action') not in ['pause', 'resume', 'stop', 'feedback']:
                raise ValueError('Unknown action')
            if data['action'] == 'feedback' and (not isinstance(data.get('text'), str) or not 0 < len(data['text'].strip()) <= 2000):
                raise ValueError('Feedback must contain 1–2000 characters')
        except (ValueError, UnicodeError):
            return self.response(400, {'error': '参数无效'})
        with LOCK, decision_lock(BASE):
            config = json.loads((BASE / 'config.json').read_text())
            state_path = BASE / 'control/commands.json'
            state = json.loads(state_path.read_text())
            if data['action'] in ('pause', 'resume') and (state['mode'] == 'stopped' or time.time() >= config['deadline']):
                return self.response(409, {'error': '任务已结束，需要另行安排新的运行周期'})
            if data['action'] == 'feedback':
                state['feedback'] = data['text'].strip()
            else:
                state['mode'] = {'pause': 'paused', 'resume': 'running', 'stop': 'stopped'}[data['action']]
            state['updated_at'] = time.time()
            atomic(state_path, state, 0o600)
        self.response(200, {'ok': True, 'mode': state['mode'], 'feedback_saved': data['action'] == 'feedback'})


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 18106), Handler).serve_forever()
