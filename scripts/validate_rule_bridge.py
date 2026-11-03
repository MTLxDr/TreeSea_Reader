"""Validate rule compilation, read-only session integration and three transports."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reader.effect_rules import RuleCatalog
from reader.calculation_client import calculate_request
from reader.calculation_api import start_server


def main():
    folder = ROOT / 'reports/calculation/rule_bridge'; folder.mkdir(exist_ok=True)
    protected = ('data/reader.sqlite3', 'data/sessions/current.json', 'data/effects/catalog.json')
    before = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in protected}
    catalog = RuleCatalog()
    request = json.loads((ROOT / 'docs/examples/session_strong_strike.request.json').read_text(encoding='utf-8'))
    partial = deepcopy(request); partial['session']['inventories']['relics']['complete'] = False
    blocked = deepcopy(partial); partial['allow_partial'] = True
    mismatch = deepcopy(request); mismatch['session']['inventories']['relics']['items'][0]['id'] = 'rogue_6_relic_book_7'
    dynamic = deepcopy(request); dynamic['session']['inventories']['relics']['items'][0]['id'] = 'rogue_6_relic_artifact_7'
    cases = [('strong_strike', request, 648.945, 'complete_for_supported_scope'),
             ('profession_mismatch', mismatch, 564.3, 'complete_for_supported_scope'),
             ('incomplete_inventory', blocked, None, 'incomplete'),
             ('explicit_partial', partial, 648.945, 'incomplete'),
             ('unknown_dynamic_mechanic', dynamic, None, 'incomplete')]
    server, thread = start_server(0)
    url = 'http://127.0.0.1:' + str(server.server_port)
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'cases': []}
    def write(name, value): (folder / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    try:
        for name, envelope, expected, status in cases:
            values = [calculate_request(envelope, t, url=url) for t in ('python', 'cli', 'http')]
            if values[0] != values[1] or values[1] != values[2]: raise AssertionError('Interface mismatch: ' + name)
            value = values[0]
            actual = value['result']['hp_damage'] if value['result'] else None
            if value['status'] != status or ((actual is None) != (expected is None)) or (expected is not None and abs(actual - expected) > 1e-8):
                raise AssertionError((name, status, value['status'], expected, actual))
            write(name + '.request.json', envelope); write(name + '.result.json', value)
            report['cases'].append({'name': name, 'status': status, 'expected': expected, 'actual': actual,
                                    'python_cli_http_equal': True})
        with urlopen(url + '/v1/rules/coverage', timeout=30) as response: coverage = json.load(response)
        if coverage != catalog.coverage(): raise AssertionError('Coverage endpoint differs')
        write('coverage.json', coverage)
        report['coverage'] = {k: coverage[k] for k in ('active_source_entries', 'entry_status', 'rule_count')}
        report['rules_http_coverage_equal'] = True
    finally: server.shutdown(); server.server_close(); thread.join(5)
    current = json.loads((ROOT / 'data/sessions/current.json').read_text(encoding='utf-8'))
    current_result = calculate_request({'operation': 'session_damage', 'session': current, 'selection': {}})
    write('current_session_audit.json', {'status': current_result['status'], 'result': current_result['result'],
                                        'missing': current_result['audit']['missing']})
    if current_result['result'] is not None: raise AssertionError('Unselected current session produced damage')
    after = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in before}
    if before != after: raise AssertionError('Protected session/database/catalog changed')
    report['protected_files_unchanged'] = after
    write('validation.json', report)
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
