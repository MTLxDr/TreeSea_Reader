"""Generic downstream example. Uses the shared service over Python, CLI or HTTP."""
import argparse
import json
from pathlib import Path
import sys
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reader.calculation_client import calculate_request
from reader.calculation_api import parse_json, MAX_BYTES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--transport', choices=('python', 'cli', 'http'), default='python')
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    try:
        with args.input.open('rb') as stream: raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES: raise ValueError('Input exceeds size limit')
        result = calculate_request(parse_json(raw.decode('utf-8-sig')), args.transport, url=args.url)
    except (ValueError, OSError, TimeoutError, subprocess.SubprocessError) as error:
        result = {'error': {'code': 'client_error', 'message': str(error)}}
    body = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
    if args.output:
        try: args.output.write_text(body, encoding='utf-8')
        except OSError as error:
            print(json.dumps({'error': {'code': 'output_error', 'message': str(error)}})); return 2
    else: sys.stdout.write(body)
    return 2 if 'error' in result else 0


if __name__ == '__main__': raise SystemExit(main())
