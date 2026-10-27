#!/usr/bin/env python3
"""Start the interface or validate proposals written by an agent."""
from pathlib import Path
import sys
import argparse

sys.path.insert(0, str(Path(__file__).resolve().parent / 'app'))

from core import ReviewError
from workspace import Workspace

if __name__ == '__main__':
    try:
        if len(sys.argv) > 1 and sys.argv[1] == 'validate-proposals':
            parser = argparse.ArgumentParser(description='Validate proposals without changing decisions.')
            parser.add_argument('session_id')
            parser.add_argument('--simulate', action='store_true', help='Validate a simulation session.')
            parser.add_argument('--demo', action='store_true', help='Validate a demo session.')
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
                print('WARNING:', item['message'])
            if errors:
                sys.exit(1)
            print(f'OK: {len(proposals)} proposals, no critical errors and {len(warnings)} warnings. Return to the interface and click Load proposals.')
        else:
            from server import main
            main()
    except (ReviewError, OSError) as exc:
        sys.exit(f'ERROR: {exc}')
