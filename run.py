#!/usr/bin/env python3
"""Iniciar la interfaz o validar las propuestas escritas por un agente."""
from pathlib import Path
import sys
import argparse

sys.path.insert(0, str(Path(__file__).resolve().parent / 'app'))

from core import ReviewError
from workspace import Workspace

if __name__ == '__main__':
    try:
        if len(sys.argv) > 1 and sys.argv[1] == 'validate-proposals':
            parser = argparse.ArgumentParser(description='Validar propuestas sin cambiar decisiones.')
            parser.add_argument('session_id')
            parser.add_argument('--simulate', action='store_true', help='Validar una sesión de simulación.')
            parser.add_argument('--demo', action='store_true', help='Validar una sesión de demostración.')
            parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parent / 'data')
            args = parser.parse_args(sys.argv[2:])
            from demo import DemoClient
            from simulation import SimulationClient
            workspace = Workspace(args.data_dir, client=DemoClient() if args.demo else SimulationClient(Path(__file__).resolve().parent / '.env') if args.simulate else None)
            workspace.open(args.session_id, persist=False)
            proposals = workspace.validated_proposals()
            errors, warnings = workspace.proposal_checks()
            for item in errors:
                print('ERROR:', item['message'])
            for item in warnings:
                print('ADVERTENCIA:', item['message'])
            if errors:
                sys.exit(1)
            print(f'OK: {len(proposals)} propuestas, sin errores críticos y con {len(warnings)} advertencias. Volvé a la interfaz y pulsá Cargar propuestas.')
        else:
            from server import main
            main()
    except (ReviewError, OSError) as exc:
        sys.exit(f'ERROR: {exc}')
