"""Replay saved real-provider evidence without network access or approval changes.

Use PYTHONPATH pointing at src or an isolated installed wheel. Per-file source
hashes and prompts must match the recorded run. This checks deterministic replay,
not a new measurement of model extraction accuracy.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from httpx import ASGITransport, AsyncClient
import reservoir_data_translator
from reservoir_data_translator.api.main import create_app
from reservoir_data_translator.semantic import SemanticModelProvider
from evaluate_heterogeneous import ROOT, compare


class RecordedProvider(SemanticModelProvider):
    def __init__(self, calls):
        self.calls = list(calls)
        self.used = 0

    @property
    def provider_name(self):
        return 'deepseek:deepseek-v4-flash'

    async def structured_generate(self, prompt, response_model):
        if self.used >= len(self.calls):
            raise AssertionError('Unexpected provider call during offline replay')
        call = self.calls[self.used]
        # Match the actual prompt, not merely the block index or file name.
        assert prompt == call['request_payload']['input'], 'Prompt differs from recorded evidence'
        self.used += 1
        return response_model.model_validate(call['validated_output'])


async def verify(evidence, output):
    summary = json.loads((evidence / 'summary.json').read_text())
    expected = json.loads((ROOT / 'heterogeneous_reservoir_testset/expected_results.json').read_text())['cases']
    report = {'mode': 'offline_recorded_response_replay',
              'import_path': str(Path(reservoir_data_translator.__file__).resolve()),
              'evidence': str(evidence.resolve()), 'cases': []}
    for entry in summary['cases']:
        case_number = int(entry['file'].split('_', 1)[0])
        source = ROOT / 'heterogeneous_reservoir_testset' / entry['file']
        assert hashlib.sha256(source.read_bytes()).hexdigest() == entry['source_sha256']
        saved = json.loads((evidence / f'{case_number:02d}-result.json').read_text())
        usage = entry.get('usage')
        calls = []
        if usage:
            trace_id = usage['trace_url'].rsplit('/', 1)[1]
            calls = json.loads((evidence / 'provider-traces' / f'{trace_id}.json').read_text())['calls']
        provider = RecordedProvider(calls)
        app = create_app(provider=provider)
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            submitted = await client.post('/translation-jobs', json={
                'source': {'file_name': source.name, 'content': source.read_text()},
                'target_platform': 'eclipse'})
            assert submitted.status_code == 202, submitted.text
            for _ in range(1000):
                job = (await client.get(submitted.json()['status_url'])).json()
                if job['status'] in {'completed', 'failed'}:
                    break
                await asyncio.sleep(.01)
            assert job['status'] == 'completed', job.get('error')
            result = job['result']
            for field in ('status', 'source', 'semantic_mapping', 'canonical_model', 'validation', 'export_validation', 'target', 'trace'):
                assert result.get(field) == saved.get(field), f'{source.name}: {field} differs from real run'
            assert provider.used == len(calls), f'{source.name}: unconsumed responses'
            differences = compare(result, expected[str(case_number)])
            report['cases'].append({'file': source.name, 'status': result['status'],
                                    'matches_recorded_result': True, 'provider_responses': provider.used,
                                    'meets_original_expectation': not differences,
                                    'expectation_difference_count': len(differences)})
            for route in ('/', '/ontology', '/ocr-lab', '/ui/app.js', '/ui/ontology.js', '/ui/ocr-lab.js', '/api/ontology/graph'):
                response = await client.get(route)
                assert response.status_code == 200, (route, response.status_code)
            assert 'scope2-sun-v5' in (await client.get('/')).headers['x-reservoir-ui-version']
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'replay_passed': len(report['cases']),
                      'original_expectations_passed': sum(c['meets_original_expectation'] for c in report['cases']),
                      'import_path': report['import_path']}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.evidence_dir, args.output))
