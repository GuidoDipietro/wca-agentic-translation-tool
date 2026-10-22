#!/usr/bin/env python3
"""Traducción WCA: revisión en localhost y sincronización de solo lectura con Weblate."""
import argparse
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit, parse_qs

from core import ReviewError
from workspace import Workspace
from weblate import Client
from connections import Connections
from openai_auth import ChatGPTAuth
from translation import OpenAITranslator, generate_proposals

ROOT = Path(__file__).resolve().parent


def make_handler(controller, connections=None, auth=None, translator=None):
    demo = isinstance(controller, Workspace) and getattr(controller.client, 'is_demo', False) is True
    connections = connections or Connections(demo=demo)
    auth = auth or ChatGPTAuth(controller.data_dir / 'auth' / 'chatgpt.json')
    translator = translator or OpenAITranslator(connections, auth)
    if isinstance(controller, Workspace) and isinstance(controller.client, Client):
        controller.client.token_getter = lambda: connections.get('WEBLATE_TOKEN')
    operation_lock = threading.Lock()
    catalog_lock = threading.Lock()
    cached_state = None

    def snapshot():
        nonlocal cached_state
        cached_state = controller.snapshot()
        return cached_state

    def active_review(data):
        if isinstance(controller, Workspace):
            controller.assert_session(data)
            if not controller.review:
                raise ReviewError("Primero sincronizá con Weblate.")
            return controller.review
        return controller

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, body, content_type='application/json; charset=utf-8', extra=None):
            data = body.encode('utf-8') if isinstance(body, str) else body
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if extra:
                for name, value in extra.items():
                    self.send_header(name, value)
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                # Una recarga puede cancelar una respuesta que ya estaba en curso.
                self.close_connection = True

        def json_response(self, value, status=200):
            self.respond(status, json.dumps(value, ensure_ascii=False))

        def trusted(self):
            port = self.server.server_port
            hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
            if self.headers.get('Host') not in hosts:
                self.json_response({'error': 'Host no permitido. Usá la dirección localhost indicada en la terminal.'}, 403)
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in {'http://' + host for host in hosts}:
                self.json_response({'error': 'Origen no permitido.'}, 403)
                return False
            # Una redirección OAuth mantiene cross-site hasta llegar a la página
            # final. Se puede abrir el documento raíz sin habilitar API ni recursos.
            document_navigation = (
                self.command == 'GET'
                and urlsplit(self.path).path == '/'
                and self.headers.get('Sec-Fetch-Mode') == 'navigate'
                and self.headers.get('Sec-Fetch-Dest') == 'document'
            )
            if self.headers.get('Sec-Fetch-Site') == 'cross-site' and not document_navigation:
                self.json_response({'error': 'Solicitud externa bloqueada.'}, 403)
                return False
            return True

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/auth/callback':
                # La redirección OAuth llega desde otro sitio; state/nonce/PKCE reemplazan
                # la comprobación de origen para esta única ruta y el Host sigue restringido.
                if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                    self.json_response({'error': 'Callback local inválido.'}, 403)
                    return
                if not operation_lock.acquire(blocking=False):
                    self.json_response({'error': 'Esperá a que termine la operación e iniciá sesión otra vez.'}, 409)
                    return
                try:
                    query = parse_qs(urlsplit(self.path).query)
                    if any(len(values) != 1 for values in query.values()):
                        raise ReviewError('Callback OAuth ambiguo.')
                    auth.complete({k: v[0] for k, v in query.items()})
                except (ReviewError, ValueError, TypeError, KeyError):
                    auth.last_error = 'No se pudo completar el inicio de sesión con ChatGPT. Volvé a conectar tu cuenta.'
                finally:
                    operation_lock.release()
                self.respond(303, b'', extra={'Location': '/'})
                return
            if not self.trusted():
                return
            path = urlsplit(self.path).path
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path == '/api/connections':
                self.json_response({'config': connections.status(), 'chatgpt': auth.status(), 'demo': demo})
                return
            if path in ('/api/languages', '/api/models', '/api/project-status'):
                # Una consulta de opciones no debe bloquear la revisión local.
                with catalog_lock:
                    try:
                        query = parse_qs(urlsplit(self.path).query)
                        if path == '/api/project-status':
                            result = None
                            if isinstance(controller, Workspace) and isinstance(controller.client, Client) and not demo:
                                client = Client(controller.client.env_path)
                                client.token_getter = controller.client.token
                                result = client.project_status(query.get('language', ['es_419'])[0])
                            self.json_response({'status': result})
                            return
                        if path == '/api/languages':
                            if not isinstance(controller, Workspace):
                                raise ReviewError('Se requiere una sesión de trabajo.')
                            if demo:
                                result = [{'code': 'es_419', 'name': 'Español latinoamericano (demo)', 'total': 4, 'translated': 1}]
                            else:
                                client = controller.client
                                if isinstance(client, Client):
                                    client = Client(controller.client.env_path)
                                    client.token_getter = controller.client.token
                                result = client.translations(query.get('component', ['locales'])[0])
                        else:
                            result = [] if demo else translator.models(query.get('provider', ['chatgpt'])[0])
                        self.json_response({'items': result})
                    except (ReviewError, ValueError, TypeError, KeyError) as exc:
                        self.json_response({'error': str(exc)}, 400)
                return
            if path == '/api/state':
                try:
                    if operation_lock.acquire(blocking=False):
                        try:
                            self.json_response(snapshot())
                        finally:
                            operation_lock.release()
                    elif cached_state is not None:
                        self.json_response(cached_state)
                    else:
                        self.json_response({'error': 'Hay una operación en curso. Esperá a que termine.'}, 409)
                except (ReviewError, OSError) as exc:
                    self.json_response({'error': str(exc)}, 400)
            elif path in files:
                name, kind = files[path]
                self.respond(200, (ROOT / 'static' / name).read_bytes(), kind)
            else:
                self.json_response({'error': 'Recurso inexistente.'}, 404)

        def do_POST(self):
            if not operation_lock.acquire(blocking=False):
                self.json_response({'error': 'Hay una operación en curso. Esperá a que termine.'}, 409)
                return
            try:
                self.handle_post()
            finally:
                operation_lock.release()

        def stream_sync(self, component, language='es_419', provider='manual', model='', translate_only=False):
            nonlocal cached_state
            cached_state = snapshot()
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            connected = True

            def emit(event):
                nonlocal connected
                if connected:
                    try:
                        self.wfile.write((json.dumps(event, ensure_ascii=False) + '\n').encode('utf-8'))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        connected = False

            try:
                emit({'stage': 0, 'total': 7, 'message': 'Iniciando sincronización'})
                if not translate_only:
                    controller.sync(component, progress=emit, language=language)
                if (translate_only or provider != 'manual') and controller.work and controller.work['entries']:
                    generate_proposals(controller, translator, provider, model, progress=emit)
                cached_state = snapshot()
                emit({'result': cached_state})
            except (ReviewError, ValueError, KeyError, TypeError) as exc:
                emit({'error': str(exc), 'recovery': snapshot()})
            except OSError:
                emit({'error': 'No se pudo guardar la sesión. Revisá permisos y espacio disponible.'})
            except Exception:
                emit({'error': 'Error interno al sincronizar. Las sesiones anteriores se conservan.'})
            finally:
                controller.client.progress = None

        def handle_post(self):
            if not self.trusted():
                return
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                self.json_response({'error': 'Se requiere application/json.'}, 415)
                return
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 8 * 1024 * 1024:
                    raise ReviewError('Solicitud vacía o demasiado grande (máximo 8 MB).')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ReviewError('Solicitud inválida.')
                path = urlsplit(self.path).path
                if path == '/api/connections':
                    connections.configure(data.get('values', {}))
                    self.json_response({'config': connections.status(), 'chatgpt': auth.status(), 'demo': demo})
                    return
                if path in ('/api/chatgpt/start', '/api/chatgpt/select', '/api/chatgpt/disconnect'):
                    if demo:
                        raise ReviewError('La demo no usa cuentas reales.')
                    if path.endswith('/start'):
                        self.json_response({'url': auth.start(self.server.server_port, data.get('account_id'))})
                    elif path.endswith('/select'):
                        auth.select(data.get('account_id'))
                        self.json_response({'chatgpt': auth.status()})
                    else:
                        auth.disconnect()
                        self.json_response({'chatgpt': auth.status()})
                    return
                if path in ('/api/sync', '/api/open', '/api/load-proposals', '/api/generate'):
                    if not isinstance(controller, Workspace):
                        raise ReviewError('Esta operación requiere una sesión de trabajo.')
                    controller.assert_session(data)
                    if path == '/api/sync':
                        self.stream_sync(data.get('component', 'locales'), data.get('language', 'es_419'), data.get('provider', 'manual'), data.get('model', ''))
                        return
                    elif path == '/api/generate':
                        self.stream_sync(controller.manifest.get('component', 'locales') if controller.manifest else 'locales', provider=data.get('provider', 'manual'), model=data.get('model', ''), translate_only=True)
                        return
                    elif path == '/api/open':
                        controller.open(data['target_id'])
                    else:
                        controller.load_proposals()
                    self.json_response(snapshot())
                    return
                review = active_review(data)
                if path == '/api/decision':
                    review.decide(data['key'], data['decision'], data['revision'])
                    self.json_response(snapshot())
                elif path == '/api/dismiss-check':
                    review.dismiss_check(data.get('issue_id'), data['revision'], data.get('dismissed', True))
                    self.json_response(snapshot())
                elif path == '/api/validate-export':
                    self.json_response(review.validate_export(data['revision'], data.get('mode', 'changes')))
                elif path == '/api/recent':
                    text = data.get('text')
                    if text is not None and not isinstance(text, str):
                        raise ReviewError('El archivo debe ser texto UTF-8.')
                    review.set_recent(text, data['revision'])
                    self.json_response(snapshot())
                elif path == '/api/export':
                    if isinstance(controller, Workspace) and controller.work['entries'] and not snapshot()['workspace']['ready']:
                        raise ReviewError('Cargá las propuestas antes de exportar esta sesión.')
                    options = {'mode': data.get('mode', 'full')}
                    result = review.export(data['revision'], data.get('confirm_pending') is True, data.get('confirm_warnings') is True, **options)
                    self.json_response(result)
                else:
                    self.json_response({'error': 'Recurso inexistente.'}, 404)
            except (ReviewError, ValueError, KeyError, TypeError) as exc:
                self.json_response({'error': str(exc)}, 400)
            except OSError:
                self.json_response({'error': 'No se pudo guardar el archivo. Revisá permisos y espacio disponible; la decisión no se confirmó.'}, 500)

        def log_message(self, fmt, *args):
            # No registrar cuerpos, traducciones ni decisiones en la terminal.
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--simulate', action='store_true', help='Descargar una copia real y vaciar tres entradas solo en una sesión de prueba separada.')
    group.add_argument('--demo', action='store_true', help='Probar sin red ni token, con datos y propuestas ficticios.')
    parser.add_argument('--port', type=int, default=None)
    parser.add_argument('--data-dir', type=Path, default=None)
    args = parser.parse_args()
    if args.port is None:
        args.port = 8766 if args.demo else 8767 if args.simulate else 8765
    args.data_dir = args.data_dir or ROOT.parent / ('data/demo' if args.demo else 'data/simulation' if args.simulate else 'data')
    if (args.demo or args.simulate) and args.data_dir.resolve() == (ROOT.parent / 'data').resolve():
        parser.error('La demo necesita una carpeta distinta de data para separar las sesiones reales.')
    if not 1 <= args.port <= 65535:
        parser.error('El puerto debe estar entre 1 y 65535.')
    args.data_dir.mkdir(parents=True, exist_ok=True)
    # Impide que dos servidores sobrescriban el mismo progreso.
    with (args.data_dir / '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit('Ya hay otra instancia usando esta carpeta de datos.')
        from demo import DemoClient
        from simulation import SimulationClient
        workspace = Workspace(args.data_dir, client=DemoClient() if args.demo else SimulationClient(ROOT.parent / '.env') if args.simulate else None)
        if args.simulate:
            print('SIMULACIÓN: copias locales con huecos de prueba. No subir exportaciones.', flush=True)
        if args.demo:
            print('DEMO: datos ficticios, sin conexión a Weblate. No subir exportaciones.', flush=True)
        with ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(workspace)) as server:
            server.timeout = 1
            print(f'Revisión local: http://127.0.0.1:{args.port}', flush=True)
            print(f'Sesiones y exportaciones: {args.data_dir.resolve()}', flush=True)
            print('Para detener: Ctrl+C. Las decisiones guardadas se conservan.', flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print('\nServidor detenido.')


if __name__ == '__main__':
    try:
        main()
    except (ReviewError, OSError) as exc:
        sys.exit(f'ERROR: {exc}')
