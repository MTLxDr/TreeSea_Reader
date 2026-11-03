"""Standard-library clients for the same calculation service; no copied formula."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .calculation import CalculationError
from .calculation_api import parse_json, MAX_BYTES
from .session_calculation import dispatch

ROOT = Path(__file__).resolve().parents[1]


def calculate_request(request, transport='python', *, url='http://127.0.0.1:8765', timeout=30):
    raw = json.dumps(request, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(raw) > MAX_BYTES: raise CalculationError('Input exceeds size limit')
    if transport == 'python': return dispatch(parse_json(raw))
    if transport == 'cli':
        process = subprocess.run([sys.executable, str(ROOT / 'scripts/calculate.py')], input=raw,
                                 capture_output=True, timeout=timeout, cwd=ROOT)
        result = parse_json(process.stdout)
        if process.returncode and 'error' not in result: raise CalculationError(process.stderr.decode('utf-8', 'replace'))
        return result
    if transport == 'http':
        parsed = urlparse(url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.username or parsed.password or parsed.path not in {'', '/'}:
            raise CalculationError('Client URL must be a local http://127.0.0.1:port base URL')
        call = Request(url.rstrip('/') + '/v1/calculate', data=raw,
                       headers={'Content-Type': 'application/json; charset=utf-8'}, method='POST')
        try:
            with urlopen(call, timeout=timeout) as response: return parse_json(response.read())
        except HTTPError as error:
            with error: return parse_json(error.read())
    raise CalculationError('Unknown transport: ' + str(transport))
