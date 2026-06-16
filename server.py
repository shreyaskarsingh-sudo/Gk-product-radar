#!/usr/bin/env python3
"""
GoKwik Merchant Product Radar — server
Serves static files and proxies /api/chat to Anthropic.
Usage: python3 server.py
"""
import json
import os
import socket
import http.server
import urllib.request
import urllib.error
from http.server import HTTPServer, SimpleHTTPRequestHandler

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
CSV_PATH  = os.path.join(DATA_DIR, 'latest.csv')
META_PATH = os.path.join(DATA_DIR, 'latest.meta.json')
os.makedirs(DATA_DIR, exist_ok=True)


def _load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                val = val.strip().strip('"').strip("'")
                os.environ.setdefault(key.strip(), val)
        print(f'Loaded env from {env_path}')
    except FileNotFoundError:
        print(f'No .env found at {env_path}, relying on system env vars')

_load_dotenv()

PORT = int(os.environ.get('PORT', 8080))

BLOCKED = {
    '.env', '.env.example', '.git', '.gitignore',
    'setup.sh', 'CLAUDE.md', 'DEPLOY.md', 'data',
}


class DualStackServer(HTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(fmt % args)

    def do_GET(self):
        if self.path in ('/', ''):
            self.path = '/merchant-product-radar.html'

        if self.path == '/api/csv':
            self._serve_csv()
            return

        parts = [p for p in self.path.lstrip('/').split('/') if p]
        name = parts[0] if parts else ''
        if name in BLOCKED or name.startswith('.'):
            self.send_error(403, 'Forbidden')
            return

        super().do_GET()

    def _serve_csv(self):
        if not os.path.exists(CSV_PATH):
            self._json(404, {'error': 'no_csv'})
            return
        try:
            with open(CSV_PATH, encoding='utf-8') as f:
                csv_text = f.read()
            meta = {}
            if os.path.exists(META_PATH):
                with open(META_PATH) as f:
                    meta = json.load(f)
            self._json(200, {'csv': csv_text, 'fileName': meta.get('fileName', 'latest.csv')})
        except Exception as e:
            self._json(500, {'error': str(e)})

    def do_HEAD(self):
        parts = [p for p in self.path.lstrip('/').split('/') if p]
        name = parts[0] if parts else ''
        if name in BLOCKED or name.startswith('.'):
            self.send_error(403, 'Forbidden')
            return
        super().do_HEAD()

    def do_DELETE(self):
        if self.path != '/api/csv':
            self.send_error(404)
            return
        for p in (CSV_PATH, META_PATH):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        self._json(200, {'ok': True})

    def do_POST(self):
        if self.path == '/api/csv':
            self._save_csv()
            return
        if self.path != '/api/chat':
            self.send_error(404)
            return

        length = int(self.headers.get('Content-Length', 0))
        body   = json.loads(self.rfile.read(length))

        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            self._json(400, {'error': 'ANTHROPIC_API_KEY not configured on server'})
            return

        payload = json.dumps({
            'model': 'claude-haiku-4-5-20251001',
            'max_tokens': 1024,
            'system': body.get('system', ''),
            'messages': [{'role': 'user', 'content': body.get('message', '')}]
        }).encode()

        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=payload,
            headers={
                'Content-Type':      'application/json',
                'x-api-key':         api_key,
                'anthropic-version': '2023-06-01'
            },
            method='POST'
        )
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read())
            content = data.get('content', [{}])[0].get('text', '')
            self._json(200, {'content': content})
        except urllib.error.HTTPError as e:
            err = e.read().decode()
            self._json(e.code, {'error': err})

    def _save_csv(self):
        length = int(self.headers.get('Content-Length', 0))
        body   = json.loads(self.rfile.read(length))
        csv_text  = body.get('csv', '')
        file_name = body.get('fileName', 'latest.csv')
        if not csv_text:
            self._json(400, {'error': 'empty csv'})
            return
        try:
            with open(CSV_PATH, 'w', encoding='utf-8') as f:
                f.write(csv_text)
            with open(META_PATH, 'w') as f:
                json.dump({'fileName': file_name}, f)
            self._json(200, {'ok': True})
        except Exception as e:
            self._json(500, {'error': str(e)})

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    try:
        srv = DualStackServer(('::', PORT), Handler)
    except OSError:
        srv = HTTPServer(('0.0.0.0', PORT), Handler)
    print(f'Serving on http://localhost:{PORT}')
    srv.serve_forever()
