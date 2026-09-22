"""Explicitly opt-in real-provider regression against per-file expected facts.

Run with PYTHONPATH=src and --real-provider. Prints no credentials. Stores the
normal application's safe traces, complete API results and field comparisons.
This tests translation to demo INCLUDEs, not simulator execution.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from reservoir_data_translator.api.main import create_app
from reservoir_data_translator.semantic import DeepSeekProvider

ROOT = Path(__file__).resolve().parents[1]


def flatten(value, path=''):
    output = {}
    if isinstance(value, dict):
        if 'value' in value and 'unit' in value:
            output[path] = {'value': value['value'], 'unit': value['unit']}
        else:
            for key, child in value.items():
                if key not in {'provenance', 'confidence'}:
                    output.update(flatten(child, f'{path}.{key}' if path else key))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            output.update(flatten(child, f'{path}[{i}]'))
    elif value is not None:
        output[path] = value
    return output


def compare(result, case):
    errors = []
    if result.get('status') != case['status']:
        errors.append({'kind': 'status', 'expected': case['status'], 'actual': result.get('status')})
    if case['status'] != 'success':
        if result.get('canonical_model') or result.get('target'):
            errors.append({'kind': 'review_gate_bypassed'})
        return errors
    actual = flatten(result.get('canonical_model') or {})
    for path, expected in case['quantities'].items():
        observed = actual.get(path)
        if (not isinstance(observed, dict) or observed['unit'] != expected['unit']
                or not math.isclose(observed['value'], expected['value'], rel_tol=1e-10,
                                    abs_tol=expected['absolute_tolerance'])):
            errors.append({'kind': 'quantity', 'path': path, 'expected': expected, 'actual': observed})
    for path, expected in case['categorical'].items():
        if actual.get(path) != expected:
            errors.append({'kind': 'categorical', 'path': path, 'expected': expected, 'actual': actual.get(path)})
    for path, value in actual.items():
        if isinstance(value, dict) and 'unit' in value and path not in case['quantities']:
            errors.append({'kind': 'unexpected_quantity', 'path': path, 'actual': value})
    return errors


async def run(args):
    dataset = ROOT / 'heterogeneous_reservoir_testset'
    expectations = json.loads((dataset / 'expected_results.json').read_text())['cases']
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ['DEEPSEEK_TRACE_DIR'] = str(output / 'provider-traces')
    provider = DeepSeekProvider.from_environment(api_key_file=ROOT / 'LLM/DeepSeek/api_key',
                                               model='deepseek-v4-flash')
    app = create_app(provider=provider)
    summary = {'requested_model': provider.model, 'cases': []}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test', timeout=180) as client:
        for number in args.files:
            case = expectations[str(number)]
            source = dataset / case['file']
            response = await client.post('/translate', json={
                'source': {'file_name': source.name, 'content': source.read_text()}, 'target_platform': 'eclipse'})
            result = response.json()
            (output / f'{number:02d}-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
            errors = compare(result, case)
            entry = {'file': source.name, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                     'http_status': response.status_code, 'status': result.get('status'),
                     'passed': response.status_code == 200 and not errors, 'differences': errors,
                     'failure': result.get('detail'), 'usage': result.get('deepseek_trace') or
                     (result.get('detail') or {}).get('diagnostics', {}).get('deepseek_trace')}
            summary['cases'].append(entry)
            (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps({'file': source.name, 'status': result.get('status'), 'passed': entry['passed'],
                              'difference_count': len(errors), 'failure_code': (result.get('detail') or {}).get('code')}, ensure_ascii=False), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real-provider', action='store_true', help='Explicitly enable billable provider calls.')
    parser.add_argument('--files', nargs='+', type=int, default=list(range(1, 11)))
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'artifacts/heterogeneous-regression')
    args = parser.parse_args()
    if not args.real_provider:
        parser.error('Real model evaluation requires --real-provider; ordinary pytest stays offline.')
    summary = asyncio.run(run(args))
    raise SystemExit(0 if all(c['passed'] for c in summary['cases']) else 1)


if __name__ == '__main__':
    main()
