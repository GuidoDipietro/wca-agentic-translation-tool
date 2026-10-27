"""Sign in with ChatGPT for the local client, with PKCE and OIDC validation."""
import base64
import hashlib
import json
from pathlib import Path
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import jwt

from core import ReviewError, atomic_write

ISSUER = 'https://auth.openai.com'
RESOURCE = 'https://api.openai.com/v1'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ReviewError('Unexpected AI provider redirect.')


def http_json(url, body=None, form=False, headers=None):
    if not (url.startswith(ISSUER + '/') or url.startswith(RESOURCE + '/')):
        raise ReviewError('Authentication destination not allowed.')
    payload = None
    request_headers = {'Accept': 'application/json'}
    if headers:
        request_headers.update(headers)
    if body is not None:
        payload = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
        request_headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
    req = urllib.request.Request(url, data=payload, headers=request_headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=35) as response:
            raw = response.read(4 * 1024 * 1024)
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ReviewError(f'OpenAI returned HTTP {code}. Check your connection and account access or sign in again.') from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise ReviewError('Could not complete the OpenAI connection. Try again.') from None


class ChatGPTAuth:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.pending = {}
        self.data = {'version': 1, 'host_id': 'urn:uuid:' + str(uuid.uuid4()), 'accounts': {}, 'active': None}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding='utf-8'))
                if self.data.get('version') != 1 or not isinstance(self.data.get('accounts'), dict):
                    raise ValueError()
            except (ValueError, OSError):
                raise ReviewError('Could not read the ChatGPT connection. Back up the file before resetting it.') from None
        self.discovery_cache = None
        self.last_error = None

    def save(self):
        atomic_write(self.path, json.dumps(self.data, ensure_ascii=False, indent=2) + '\n')
        self.path.chmod(0o600)

    def discovery(self):
        if not self.discovery_cache:
            value = http_json(ISSUER + '/.well-known/openid-configuration')
            if value.get('issuer') != ISSUER or not str(value.get('jwks_uri', '')).startswith(ISSUER + '/'):
                raise ReviewError('The provider OIDC configuration is invalid.')
            self.discovery_cache = value
        return self.discovery_cache

    def status(self):
        with self.lock:
            accounts = [{'id': identifier, 'email': item.get('email') or 'ChatGPT account', 'connected': bool(item.get('refresh_token') or item.get('access_token')), 'can_translate': {'chatgpt.tokens.use.direct', 'resource.invoke'}.issubset(item.get('scopes', [])) and bool(item.get('access_token'))} for identifier, item in self.data['accounts'].items()]
            return {'active': self.data.get('active'), 'accounts': accounts, 'error': self.last_error}

    def start(self, port, account_id=None):
        with self.lock:
            if account_id is not None and account_id not in self.data['accounts']:
                raise ReviewError('Unknown ChatGPT account.')
            self.save()  # Persistir el host antes del primer intento.
            state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
            callback = f'http://127.0.0.1:{port}/auth/callback'
            saved = self.data['accounts'].get(account_id, {})
            client_id = saved.get('client_id', 'dynamic_agent_client')
            params = {'client_id': client_id, 'ext_agent_host_id': self.data['host_id'], 'response_type': 'code', 'redirect_uri': callback, 'scope': SCOPES, 'resource': RESOURCE, 'state': state, 'nonce': nonce, 'code_challenge_method': 'S256', 'code_challenge': challenge}
            if client_id == 'dynamic_agent_client':
                params['agent_name_hint'] = 'WCA Translation Studio'
            else:
                if saved.get('id_token'):
                    params['id_token_hint'] = saved['id_token']
                if saved.get('email'):
                    params['login_hint'] = saved['email']
            self.pending = {k: v for k, v in self.pending.items() if time.time() - v['created'] < 600}
            self.pending[state] = {'nonce': nonce, 'verifier': verifier, 'redirect_uri': callback, 'client_id': client_id, 'account_id': account_id, 'created': time.time()}
            self.last_error = None
            return ISSUER + '/api/accounts/authorize?' + urllib.parse.urlencode(params)

    def verify_identity(self, token, client_id, nonce=None):
        try:
            header = jwt.get_unverified_header(token)
            if header.get('alg') not in ('RS256', 'ES256'):
                raise ValueError()
            config = self.discovery()
            keys = jwt.PyJWKSet.from_dict(http_json(config['jwks_uri']))
            signing = next(key for key in keys.keys if key.key_id == header.get('kid'))
            claims = jwt.decode(token, signing.key, algorithms=['RS256', 'ES256'], audience=client_id, issuer=ISSUER, leeway=30, options={'require': ['exp', 'iat', 'sub', 'iss', 'aud']})
            if nonce is not None and not secrets.compare_digest(str(claims.get('nonce', '')), nonce):
                raise ValueError()
            return claims
        except ReviewError:
            raise
        except (jwt.PyJWTError, ValueError, StopIteration, KeyError, TypeError):
            raise ReviewError('Could not verify the ChatGPT identity. Start the connection again.') from None

    def complete(self, query):
        with self.lock:
            state = query.get('state', '')
            attempt = self.pending.get(state)
            if not attempt or time.time() - attempt['created'] > 600:
                raise ReviewError('The connection attempt expired or its state is invalid.')
            del self.pending[state]
            if query.get('error'):
                raise ReviewError('The ChatGPT connection was not authorized.')
            client_id = query.get('client_id', attempt['client_id'])
            if not client_id or client_id == 'dynamic_agent_client' or (attempt['client_id'] != 'dynamic_agent_client' and client_id != attempt['client_id']):
                raise ReviewError('ChatGPT did not return the expected client registration.')
            if not query.get('code'):
                raise ReviewError('The connection did not return an authorization code.')
            tokens = http_json(ISSUER + '/api/accounts/oauth/token', {'grant_type': 'authorization_code', 'client_id': client_id, 'code': query['code'], 'code_verifier': attempt['verifier'], 'redirect_uri': attempt['redirect_uri'], 'resource': RESOURCE}, form=True)
            claims = self.verify_identity(tokens.get('id_token', ''), client_id, attempt['nonce'])
            saved = self.data['accounts'].get(attempt['account_id'])
            if saved and saved['subject'] != claims['sub']:
                raise ReviewError('The received account does not match the selected account.')
            if not tokens.get('access_token') or str(tokens.get('token_type', '')).lower() != 'bearer':
                raise ReviewError('The connection did not return usable credentials.')
            identifier = attempt['account_id'] or next((k for k, v in self.data['accounts'].items() if v['client_id'] == client_id and v['subject'] == claims['sub']), str(uuid.uuid4()))
            self.data['accounts'][identifier] = {'client_id': client_id, 'subject': claims['sub'], 'email': claims.get('email', ''), 'access_token': tokens['access_token'], 'refresh_token': tokens.get('refresh_token'), 'id_token': tokens['id_token'], 'scopes': str(tokens.get('scope', '')).split(), 'expires_at': time.time() + int(tokens.get('expires_in', 3600))}
            self.data['active'] = identifier
            self.save()
            self.last_error = None
            return identifier

    def select(self, identifier):
        with self.lock:
            if identifier not in self.data['accounts']:
                raise ReviewError('Cuenta desconocida.')
            self.data['active'] = identifier
            self.save()

    def access_token(self):
        with self.lock:
            item = self.data['accounts'].get(self.data.get('active'))
            if not item or not item.get('access_token') or 'chatgpt.tokens.use.direct' not in item.get('scopes', []) or 'resource.invoke' not in item.get('scopes', []):
                raise ReviewError('Connect ChatGPT and authorize plan usage, or choose the API key option.')
            if item.get('expires_at', 0) < time.time() + 60:
                if not item.get('refresh_token'):
                    raise ReviewError('The ChatGPT connection expired. Sign in again.')
                replacement = http_json(ISSUER + '/api/accounts/oauth/token', {'grant_type': 'refresh_token', 'client_id': item['client_id'], 'refresh_token': item['refresh_token'], 'resource': RESOURCE}, form=True)
                if not replacement.get('access_token'):
                    raise ReviewError('Could not refresh the ChatGPT connection.')
                if replacement.get('id_token'):
                    claims = self.verify_identity(replacement['id_token'], item['client_id'])
                    if claims['sub'] != item['subject']:
                        raise ReviewError('Identity changed during session refresh.')
                # Replace the complete set only after validating the response.
                updated = dict(item, access_token=replacement['access_token'], expires_at=time.time() + int(replacement.get('expires_in', 3600)), refresh_token=replacement.get('refresh_token', item.get('refresh_token')), id_token=replacement.get('id_token', item.get('id_token')), scopes=str(replacement.get('scope', ' '.join(item['scopes']))).split())
                self.data['accounts'][self.data['active']] = updated
                self.save()
                item = updated
            if not {'chatgpt.tokens.use.direct', 'resource.invoke'}.issubset(item.get('scopes', [])):
                raise ReviewError('Refresh did not authorize plan usage. Reconnect your account.')
            return item['access_token']

    def disconnect(self):
        with self.lock:
            item = self.data['accounts'].get(self.data.get('active'))
            if not item:
                return
            failed = False
            try:
                endpoint = self.discovery().get('revocation_endpoint')
                if item.get('refresh_token') and not endpoint:
                    failed = True
                if endpoint and item.get('refresh_token'):
                    http_json(endpoint, {'token': item['refresh_token'], 'token_type_hint': 'refresh_token', 'client_id': item['client_id']}, form=True)
            except ReviewError:
                failed = True
            for name in ('access_token', 'refresh_token', 'id_token'):
                item.pop(name, None)
            self.save()
            self.last_error = 'Disconnected locally. Remote revocation could not be confirmed; you can revoke access in ChatGPT Settings.' if failed else None
