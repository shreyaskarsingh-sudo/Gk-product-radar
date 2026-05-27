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


def _load_dotenv():
    """Load .env from the same directory as this script into os.environ."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                # Strip optional quotes around the value
                val = val.strip().strip('"').strip("'")
                os.environ.setdefault(key.strip(), val)
        print(f'Loaded env from {env_path}')
    except FileNotFoundError:
        print(f'No .env found at {env_path}, relying on system env vars')

_load_dotenv()

PORT = int(os.environ.get('PORT', 8080))


class DualStackServer(HTTPServer):
    """Binds on IPv6 wildcard (::) which also accepts IPv4 on most OS."""
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


BLOCKED = {
    '.env', '.env.example', '.git', '.gitignore',
    'setup.sh', 'CLAUDE.md', 'DEPLOY.md',
}


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(fmt % args)

    def do_GET(self):
        # Serve the app directly at / so the URL stays clean
        if self.path in ('/', ''):
            self.path = '/merchant-product-radar.html'

        # Metabase proxy — GET (e.g. /metabase/api/database)
        if self.path.startswith('/metabase/'):
            self._proxy_metabase('GET', None)
            return

        # Block sensitive files — check every path segment
        parts = [p for p in self.path.lstrip('/').split('/') if p]
        name = parts[0] if parts else ''
        if name in BLOCKED or name.startswith('.'):
            self.send_error(403, 'Forbidden')
            return

        super().do_GET()

    def do_HEAD(self):
        parts = [p for p in self.path.lstrip('/').split('/') if p]
        name = parts[0] if parts else ''
        if name in BLOCKED or name.startswith('.'):
            self.send_error(403, 'Forbidden')
            return
        super().do_HEAD()

    def do_POST(self):
        # Metabase proxy — POST (e.g. /metabase/api/card/5824/query)
        if self.path.startswith('/metabase/'):
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length) if length else b'{}'
            self._proxy_metabase('POST', body)
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

    def _proxy_metabase(self, method, body):
        """Forward /metabase/<path> to the real Metabase, injecting the API key."""
        mb_url = os.environ.get('METABASE_URL', '').rstrip('/')
        api_key = os.environ.get('METABASE_API_KEY', '')
        if not mb_url or not api_key:
            self._json(503, {'error': 'Metabase not configured on server (METABASE_URL / METABASE_API_KEY missing)'})
            return

        # Strip /metabase prefix to get the real Metabase path
        path = self.path[len('/metabase'):]
        url  = mb_url + path

        headers = {
            'Content-Type': 'application/json',
            'X-API-KEY':    api_key,
            'Authorization': f'Bearer {api_key}',
        }
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                data = resp.read()
                self.send_response(resp.status)
                self.send_header('Content-Type', resp.headers.get('Content-Type', 'application/json'))
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

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
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
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
