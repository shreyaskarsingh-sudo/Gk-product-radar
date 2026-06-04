#!/usr/bin/env python3
"""
GoKwik Merchant Product Radar — server
Serves static files and proxies /api/chat to Anthropic.
Usage: python3 server.py
"""
import json
import os
import socket
import time
import threading
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

PORT      = int(os.environ.get('PORT', 8080))
CACHE_TTL = int(os.environ.get('CACHE_TTL_SECONDS', 43200))  # default: 12 hours (twice a day)

# Cards to pre-warm at startup. Each entry: (path, POST body).
WARM_CARDS = [
    ('/api/card/5824/query', b'{"constraints":null}'),  # Checkout features
    ('/api/card/5823/query', b'{"constraints":null}'),  # Payment features
]

_cache      = {}   # path → (fetched_at, bytes, content_type)
_cache_lock = threading.Lock()


def _cache_get(path):
    with _cache_lock:
        entry = _cache.get(path)
    if entry and (time.time() - entry[0]) < CACHE_TTL:
        return entry[1], entry[2]
    return None, None


def _cache_set(path, data, content_type):
    with _cache_lock:
        _cache[path] = (time.time(), data, content_type)


def _mb_fetch(path, body=None):
    """POST (or GET) a Metabase path directly — used by the background warmer."""
    mb_url  = os.environ.get('METABASE_URL', '').rstrip('/')
    api_key = os.environ.get('METABASE_API_KEY', '')
    if not mb_url or not api_key:
        raise RuntimeError('METABASE_URL / METABASE_API_KEY not set')
    method = 'POST' if body is not None else 'GET'
    req = urllib.request.Request(
        mb_url + path,
        data=body,
        headers={
            'Content-Type': 'application/json',
            'X-API-KEY':    api_key,
            'Authorization': f'Bearer {api_key}',
        },
        method=method
    )
    with urllib.request.urlopen(req) as resp:
        return resp.read(), resp.headers.get('Content-Type', 'application/json')


def _warm_once():
    """POST every WARM_CARD into the cache (runs in the background thread)."""
    for path, body in WARM_CARDS:
        try:
            data, ct = _mb_fetch(path, body)
            _cache_set(path, data, ct)
            print(f'[cache warm] {path} — {len(data) // 1024} KB ready')
        except Exception as exc:
            print(f'[cache warm] FAILED {path}: {exc}')


def _warmer_loop():
    """Background daemon: warm at startup, then refresh 60 s before TTL expires."""
    _warm_once()
    while True:
        time.sleep(max(CACHE_TTL - 60, 60))
        print('[cache warm] refreshing before TTL expiry…')
        _warm_once()


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
        """Forward /metabase/<path> to the real Metabase, injecting the API key.

        GET requests are cached in memory for CACHE_TTL seconds so repeated
        dashboard loads skip the Metabase round-trip entirely.
        """
        mb_url  = os.environ.get('METABASE_URL', '').rstrip('/')
        api_key = os.environ.get('METABASE_API_KEY', '')
        if not mb_url or not api_key:
            self._json(503, {'error': 'Metabase not configured on server (METABASE_URL / METABASE_API_KEY missing)'})
            return

        path = self.path[len('/metabase'):]

        # Cache card query POSTs (the heavy calls) and all GETs.
        # ?nocache=1 (sent by the Refresh button) bypasses and refreshes the cache.
        force_refresh = 'nocache=1' in path
        cache_key     = path.split('?')[0]  # cache by path only, ignore query string
        is_card_query = cache_key.startswith('/api/card/') and cache_key.endswith('/query')
        cacheable     = (method == 'GET' or is_card_query) and not force_refresh

        if cacheable:
            cached_data, cached_ct = _cache_get(cache_key)
            if cached_data is not None:
                print(f'[cache hit] {cache_key}')
                self.send_response(200)
                self.send_header('Content-Type', cached_ct)
                self.send_header('Content-Length', str(len(cached_data)))
                self.end_headers()
                self.wfile.write(cached_data)
                return

        # Strip nocache param before forwarding to Metabase
        clean_path = cache_key
        url = mb_url + clean_path
        headers = {
            'Content-Type': 'application/json',
            'X-API-KEY':    api_key,
            'Authorization': f'Bearer {api_key}',
        }
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                data = resp.read()
                ct   = resp.headers.get('Content-Type', 'application/json')
                if method == 'GET' or is_card_query:
                    _cache_set(cache_key, data, ct)
                    print(f'[cache set] {cache_key} ({len(data)} bytes, TTL {CACHE_TTL}s)')
                self.send_response(resp.status)
                self.send_header('Content-Type', ct)
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
    threading.Thread(target=_warmer_loop, daemon=True, name='cache-warmer').start()
    srv.serve_forever()
