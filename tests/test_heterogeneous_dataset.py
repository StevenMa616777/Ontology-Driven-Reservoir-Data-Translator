"""Regress the actual heterogeneous inputs, including negative safety cases.

Fixtures below model reviewed semantic IR; they do not claim LLM extraction
accuracy. Real-provider evaluation uses the separate evaluation script.
"""
import csv
import copy
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from reservoir_data_translator.api.main import _default_services, create_app
from reservoir_data_translator.canonical.mapping_contract import normalize_semantic_identity, get_canonical_mapping_contract
from reservoir_data_translator.ingestion import RawBlock, RawDocument, parse_document
from reservoir_data_translator.semantic import SemanticMappingAgent, SemanticModelProvider, OntologyRetriever, UnitNormalizer, SemanticAgentContractError
from reservoir_data_translator.semantic.models import UnmappedSemanticMapping
from reservoir_data_translator.semantic.source_context import prepare_context, retrieval_block

ROOT = Path(__file__).parents[1]
DATA = ROOT / 'heterogeneous_reservoir_testset'


class IRProvider(SemanticModelProvider):
    def __init__(self, callback):
        self.callback = callback
        self.prompts = []

    async def structured_generate(self, prompt, response_model):
        self.prompts.append(prompt)
        payload = json.loads(prompt.split('INPUT:\n', 1)[1].split('\nCORRECTION REQUIRED:', 1)[0])
        return {'mappings': self.callback(payload)}


def draft(payload, legacy, value, unit=None, **selectors):
    concept, context = normalize_semantic_identity(legacy)
    return {'status': 'MAPPED', 'source_block_id': payload['raw_block']['block_id'],
            'ontology_concept': concept, 'context': context.model_dump(exclude_none=True),
            'selectors': selectors, 'value': value, 'source_unit': unit,
            'canonical_unit': {'MPa': 'bar', 'kPa': 'bar', 'psi': 'bar', 'Pa': 'bar',
                               '%': 'fraction', 'mPa_s': 'cP', 'year': 'day', 'quarter': 'day'}.get(unit, unit),
            'confidence': 0.99}


def scal_ir(payload, rows, pressure_unit, percent=False):
    identity = payload['table_identity']
    table = identity['technical_table_id']
    metadata = {'phase_system': ['oil', 'water']}
    if 'sample_id' in identity:
        metadata['sample_id'] = identity['sample_id']
    mappings = [draft(payload, 'scal.relative_permeability', metadata, table_id=table)]
    for i, row in enumerate(rows):
        for key, value, unit in zip(('water_saturation', 'krw', 'kro', 'pcow'), row,
                                   ('%' if percent else 'fraction', 'fraction', 'fraction', pressure_unit)):
            mappings.append(draft(payload, 'scal.relative_permeability.' + key, float(value), unit,
                                  table_id=table, point_index=i))
    return mappings


def well_ir(payload):
    content = payload['raw_block']['content']
    row = dict(zip(content['columns'], content['rows'][0]))
    if 'duration_years' in row:
        return [draft(payload, 'schedule.duration', row['duration_years'], 'year'),
                draft(payload, 'schedule.report_interval', 1, 'quarter')]
    well = row['wellId']
    injector = row['wellType'] == 'INJECTOR'
    control = 'water_injection_rate' if injector else 'liquid_rate'
    constraint = 'maximum_bhp' if injector else 'minimum_bhp'
    return [draft(payload, 'well', well, well_id=well),
            draft(payload, 'well.water_injector' if injector else 'well.producer',
                  'water_injector' if injector else 'producer', well_id=well),
            draft(payload, 'well.control.' + control, row['target'], row['unit'], well_id=well),
            draft(payload, 'well.constraint.' + constraint, row['maxBHP' if injector else 'minBHP'],
                  row['pressureUnit'], well_id=well, **({'control_type': control} if injector else {}))]


async def translate(number, provider):
    path = next(DATA.glob(f'{number:02d}_*'))
    app = create_app(provider=provider)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/translate', json={
            'source': {'file_name': path.name, 'content': path.read_text()}, 'target_platform': 'eclipse'})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize('number', [1, 2, 3, 10])
async def test_actual_scal_sources_keep_all_rows_and_convert_units(number):
    def respond(p):
        b = p['raw_block']
        if number == 1:
            return scal_ir(p, [row[1:] for row in b['content']['rows']], 'bar')
        if number == 2:
            assert any('X-12' in str(c['content']) for c in p['source_context'])
            rows = [line.split('|') for line in b['content'].splitlines() if line[:1].isdigit()]
            return scal_ir(p, rows, 'kPa', percent=True)
        if number == 3:
            rows = [line.split(',') for line in b['content'].splitlines() if line[:1].isdigit()]
            return scal_ir(p, rows, 'psi')
        if b['source_location'] == '$.schedule':
            return [draft(p, 'schedule.duration', 1825, 'day'), draft(p, 'schedule.report_interval', 91.25, 'day')]
        assert p['table_identity']['sample_id'] == 'X-12'
        return scal_ir(p, [row[:4] for row in b['content']['rows']], 'Pa')
    result = await translate(number, IRProvider(respond))
    assert result['status'] == 'success'
    table = result['canonical_model']['scal']['relative_permeability'][0]
    assert table['sample_id'] == 'X-12'
    assert len(table['points']) == 5
    expected = [[float(row[k]) for k in ('Sw', 'Krw', 'Krow', 'Pcow_bar')]
                for row in csv.DictReader((DATA / '01_lab_A_relperm.csv').open())]
    for point, gold in zip(table['points'], expected):
        assert [point[k]['value'] for k in ('sw', 'krw', 'kro', 'pcow')] == pytest.approx(gold, abs=0.00003)
    assert 'SWOF\n' in result['target']['content']


@pytest.mark.asyncio
async def test_actual_mixed_pvt_table_all_phases_all_properties():
    def respond(p):
        mappings = []
        for phase, label in [('oil', 'DEAD_OIL'), ('gas', 'GAS')]:
            mappings.append(draft(p, f'fluid.{phase}.pvt', {'model_type': 'table'}))
            rows = [r for r in p['raw_block']['content']['rows'] if r[0] == label]
            for i, row in enumerate(rows):
                for field, value, unit in zip(('pressure', 'formation_volume_factor', 'viscosity'), row[1:], ('MPa', 'rm3/sm3', 'mPa_s')):
                    mappings.append(draft(p, f'fluid.{phase}.pvt.{field}', float(value), unit, point_index=i))
        return mappings
    result = await translate(5, IRProvider(respond))
    assert result['status'] == 'success'
    for phase in ('oil', 'gas'):
        points = result['canonical_model']['fluids'][phase]['pvt']['points']
        assert len(points) == 4
        assert [p['pressure']['value'] for p in points] == [100, 200, 300, 400]
        assert all(p['formation_volume_factor'] and p['viscosity'] for p in points)
    assert 'PVDO\n' in result['target']['content'] and 'PVDG\n' in result['target']['content']


@pytest.mark.asyncio
async def test_actual_scada_injector_contract_and_metadata_consumption():
    def respond(p):
        rows = well_ir(p)
        if p['raw_block']['source_location'] == '$.controls[2]':
            schema = next(c['selector_contract'] for c in p['ontology_candidates'] if c['concept_id'] == 'well.constraint.maximum_bhp')
            assert 'water_injection_rate' in schema['properties']['control_type']['enum']
            assert 'INJECTION_RATE' not in schema['properties']['control_type']['enum']
            rows += [{'status': 'UNMAPPED', 'source_block_id': p['raw_block']['block_id'],
                      'source_field': field, 'confidence': 0} for field in ('fluid', 'controlMode', 'unit', 'pressureUnit')]
        return rows
    result = await translate(7, IRProvider(respond))
    assert result['status'] == 'success'
    assert [w['id'] for w in result['canonical_model']['wells']] == ['A15', 'B2', 'C1']
    assert len(result['semantic_mapping']['source_annotations']) == 5
    assert result['canonical_model']['schedule']['report_interval']['value'] == 91.25
    assert 'WCONINJE\n' in result['target']['content']


@pytest.mark.asyncio
async def test_swof_missing_unit_stays_review_without_model_guess():
    provider = IRProvider(lambda p: pytest.fail('Missing unit must be caught before the provider'))
    result = await translate(4, provider)
    assert result['status'] == 'review_required'
    assert result['canonical_model'] is None and result['target'] is None
    assert result['semantic_mapping']['mappings'][0]['source_field'] == 'pressure_unit'


@pytest.mark.parametrize('number', [6, 8, 9, 10])
def test_original_wording_has_required_supported_candidates(number):
    services = _default_services(IRProvider(lambda p: []))
    retriever = OntologyRetriever(services.registry, source_mappings=[m for m in services._source_mappings.values() if m.automatic])
    agent = SemanticMappingAgent(services.registry, services.provider, retriever=retriever)
    doc = parse_document(next(DATA.glob(f'{number:02d}_*')))
    context, annotations = prepare_context(doc)
    candidates = [c for b in doc.blocks if b.block_id not in {a.source_block_id for a in annotations}
                  for c in agent._buildable_candidates(retrieval_block(b, context.get(b.block_id, []))) if c.context_supported]
    keys = {(c.concept_id, c.context.phase) for c in candidates}
    if number == 6:
        assert {(f'physical.{field}', phase) for field in ('density', 'pressure', 'formation_volume_factor', 'viscosity') for phase in ('oil', 'water', 'gas')} <= keys
        assert ('rock.compressibility', None) in keys
    if number in {8, 9}:
        assert {(concept, None) for concept in ('well.producer', 'well.water_injector', 'well.control.liquid_rate', 'well.control.water_injection_rate', 'well.constraint.minimum_bhp', 'well.constraint.maximum_bhp')} <= keys
    if number in {9, 10}:
        assert {('schedule.duration', None), ('schedule.report_interval', None)} <= keys


def test_metadata_cannot_hide_unknown_quantities_or_leak_between_tables():
    blocks = [RawBlock(block_id=str(i), block_type='key_value', content={'key': k, 'value': v}, source_location=loc)
              for i, (k, v, loc) in enumerate([
                  ('sourceSample', 'A', '$.tables[0].sourceSample'),
                  ('sourceSample', 'B', '$.tables[1].sourceSample'),
                  ('XYZ_COEFF', 12.5, '$.tables[0].XYZ_COEFF')])]
    blocks.append(RawBlock(block_id='rows', block_type='table', content={'columns': ['Sw'], 'rows': [[0.2]]}, source_location='$.tables[1].rows'))
    doc = RawDocument(source_id='negative', source_type='json', file_name='negative.json', blocks=blocks)
    ctx, annotations = prepare_context(doc)
    assert {a.source_block_id for a in annotations} == {'0', '1'}
    assert not any('A' == a.content.get('value') for a in ctx['rows'])
    assert any('B' == a.content.get('value') for a in ctx['rows'])


def test_units_preserve_explicit_time_policy():
    u = UnitNormalizer()
    assert u.normalize(15, '%', 'fraction') == .15
    assert u.normalize(15, 'percent', 'fraction') == .15
    assert u.normalize(35000, 'Pa', 'bar') == pytest.approx(.35)
    assert u.normalize(3, 'month', 'day') == 90
    assert u.normalize(1, 'quarter', 'day') == 91.25


def test_selector_contract_matches_resolver_patterns(registry):
    for binding in registry.scopes.bindings:
        contract = get_canonical_mapping_contract(binding.concept_id, binding.context)
        if contract:
            schema = contract.selector_schema()
            assert schema['required'] == list(contract.selector_names)
            assert schema['additionalProperties'] is False


@pytest.mark.asyncio
async def test_original_fluid_report_builds_every_expected_property():
    expected = json.loads((DATA / 'expected_results.json').read_text())['cases']['6']
    def respond(p):
        out = [draft(p, f'fluid.{phase}.pvt', {'model_type': 'table' if phase != 'water' else 'constant'})
               for phase in ('oil', 'water', 'gas')]
        for path, value in expected['quantities'].items():
            if path.startswith('fluids.'):
                parts = path.split('.')
                concept = 'fluid.' + parts[1] + ('.density' if parts[2] == 'density' else '.pvt.' + parts[-1])
                selectors = {'point_index': int(parts[3][7:-1])} if parts[2] == 'pvt' else {}
            else:
                concept, selectors = path, {}
            out.append(draft(p, concept, value['value'], value['unit'], **selectors))
        return out
    result = await translate(6, IRProvider(respond))
    assert result['status'] == 'success'
    from scripts.evaluate_heterogeneous import compare
    assert compare(result, expected) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('number', [8, 9])
async def test_original_production_and_notes_preserve_quantities(number):
    def respond(p):
        out = []
        if number == 8:
            rows = p['raw_block']['content']['rows']
            assert any(profile['source_system'] == 'production_role_table' for profile in p['source_profiles'])
        else:
            rows = [['', '', w, role, rate, bhp, limit] for w, role, rate, bhp, limit in
                    [('A15', 'PROD', 500, 80, 'MIN'), ('B2', 'PROD', 500, 80, 'MIN'), ('C1', 'WINJ', 800, 420, 'MAX')]]
        for row in rows:
            well, injector = row[2], row[3] == 'WINJ'
            control = 'water_injection_rate' if injector else 'liquid_rate'
            constraint = 'maximum_bhp' if injector else 'minimum_bhp'
            out += [draft(p, 'well', well, well_id=well),
                    draft(p, 'well.water_injector' if injector else 'well.producer', 'water_injector' if injector else 'producer', well_id=well),
                    draft(p, 'well.control.' + control, float(row[4]), 'bbl/day' if number == 8 else 'm3/day', well_id=well),
                    draft(p, 'well.constraint.' + constraint, float(row[5]), 'psi' if number == 8 else 'bar', well_id=well,
                          **({'control_type': control} if injector else {}))]
        if number == 9:
            out += [draft(p, 'schedule.duration', 5, 'year'), draft(p, 'schedule.report_interval', 3, 'month')]
        return out
    # Explicit canonical units; source barrel/month magnitudes remain unchanged.
    original = respond
    def with_units(p):
        mappings = original(p)
        for m in mappings:
            if m['source_unit'] == 'bbl/day': m['canonical_unit'] = 'm3/day'
            if m['source_unit'] == 'month': m['canonical_unit'] = 'day'
        return mappings
    result = await translate(number, IRProvider(with_units))
    expected = json.loads((DATA / 'expected_results.json').read_text())['cases'][str(number)]
    from scripts.evaluate_heterogeneous import compare
    assert compare(result, expected) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,number,block_index', [('table_id', 2, 1), ('control_type', 7, 3)])
async def test_recorded_provider_failures_retry_with_complete_selector_contract(registry, kind, number, block_index):
    fixture = json.loads((ROOT / 'tests/fixtures/provider_contract_failures.json').read_text())[kind]['response']
    calls = []
    def respond(p):
        out = copy.deepcopy(fixture['mappings'])
        if calls:
            for m in out:
                if m['status'] == 'MAPPED' and kind in m.get('selectors', {}):
                    m['selectors'][kind] = p['table_identity']['technical_table_id'] if kind == 'table_id' else 'water_injection_rate'
        calls.append(p)
        return out
    provider = IRProvider(respond)
    doc = parse_document(next(DATA.glob(f'{number:02d}_*')))
    block = doc.blocks[block_index]
    contexts, _ = prepare_context(doc)
    result = await SemanticMappingAgent(registry, provider).map_block(doc, block, source_context=contexts.get(block.block_id, []))
    assert len(calls) == 2
    assert 'selector_contract' in provider.prompts[0]
    assert 'CANONICAL_PATH_OUTSIDE_CONTRACT' in provider.prompts[1]
    assert any(m.status == 'MAPPED' for m in result)


@pytest.mark.asyncio
async def test_empty_business_block_cannot_disappear_behind_valid_schedule(registry):
    doc = RawDocument(source_id='missing', source_type='txt', file_name='missing.txt', blocks=[
        RawBlock(block_id='a', block_type='text', content='well A15 liquid rate 500 m3/day'),
        RawBlock(block_id='b', block_type='text', content='duration 5 years')])
    provider = IRProvider(lambda p: [] if p['raw_block']['block_id'] == 'a' else [draft(p, 'schedule.duration', 5, 'year')])
    with pytest.raises(SemanticAgentContractError) as error:
        await SemanticMappingAgent(registry, provider).map_document(doc)
    assert error.value.code == 'INVALID_STRUCTURED_OUTPUT'
    assert error.value.source_block_id == 'a'


def test_unknown_saturation_basis_is_not_silently_treated_as_metadata():
    doc = RawDocument(source_id='basis', source_type='json', file_name='basis.json', blocks=[
        RawBlock(block_id='a', block_type='key_value', content={'key': 'saturationBasis', 'value': 'unknown'})])
    assert prepare_context(doc)[1] == []


@pytest.mark.asyncio
async def test_conflicting_samples_require_review_before_provider(registry):
    block = RawBlock(block_id='samples', block_type='table',
                     content={'columns': ['Sample_ID', 'Sw', 'Krw', 'Krow', 'Pcow_bar'],
                              'rows': [['A', .2, 0, .9, .4], ['B', .3, .1, .8, .3]]})
    doc = RawDocument(source_id='samples', source_type='csv', file_name='samples.csv', blocks=[block])
    provider = IRProvider(lambda p: pytest.fail('Conflicting sample identities need review'))
    result = await SemanticMappingAgent(registry, provider).map_document(doc)
    assert len(result.mappings) == 1
    assert result.mappings[0].status == 'UNMAPPED'
    assert result.mappings[0].source_field == 'sample_id'
    assert '冲突' in result.mappings[0].reason


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,number,block_index', [('quarterly', 7, 4), ('constraint_target', 8, 0)])
async def test_live_failures_are_rejected_and_retried_with_precise_contract(kind, number, block_index):
    fixture = json.loads((ROOT / 'tests/fixtures/provider_relationship_failures.json').read_text())[kind]['response']
    calls = []
    def respond(p):
        out = copy.deepcopy(fixture['mappings'])
        if calls:
            for m in out:
                if kind == 'quarterly' and m['ontology_concept'] == 'schedule.report_interval':
                    m.update(value=1, source_unit='quarter', canonical_unit='day')
                if kind == 'constraint_target' and m['ontology_concept'] == 'well.constraint.maximum_bhp':
                    m['selectors']['control_type'] = 'water_injection_rate'
        calls.append(p)
        return out
    provider = IRProvider(respond)
    services = _default_services(provider)
    retriever = OntologyRetriever(services.registry, source_mappings=[m for m in services._source_mappings.values() if m.automatic])
    agent = SemanticMappingAgent(services.registry, provider, retriever=retriever)
    doc = parse_document(next(DATA.glob(f'{number:02d}_*')))
    result = await agent.map_block(doc, doc.blocks[block_index])
    assert len(calls) == 2
    assert all(m.status == 'MAPPED' for m in result)
    if kind == 'quarterly':
        candidate = next(c for c in calls[0]['ontology_candidates'] if c['concept_id'] == 'schedule.report_interval')
        assert candidate['value_contract']['example']['source_unit'] == 'quarter'
        assert 'CANONICAL_UNIT_OUTSIDE_CONTRACT' in provider.prompts[1]
    else:
        assert 'CONSTRAINT_CONTROL_TARGET_MISMATCH' in provider.prompts[1]
        assert 'never fabricate a target' in provider.prompts[1]


@pytest.mark.asyncio
async def test_real_fluid_report_response_preserves_source_inverse_pressure_unit():
    fixture = json.loads((ROOT / 'tests/fixtures/provider_fluid_report_response.json').read_text())['response']
    assert any(m.get('source_unit') == '/bar' for m in fixture['mappings'])
    result = await translate(6, IRProvider(lambda p: fixture['mappings']))
    expected = json.loads((DATA / 'expected_results.json').read_text())['cases']['6']
    from scripts.evaluate_heterogeneous import compare
    assert compare(result, expected) == []


@pytest.mark.asyncio
async def test_missing_pvt_coordinate_retries_before_canonical_build():
    fixture = json.loads((ROOT / 'tests/fixtures/provider_missing_pvt_pressure.json').read_text())['response']
    calls = []
    def respond(p):
        out = copy.deepcopy(fixture['mappings'])
        if calls:
            out.append(draft(p, 'fluid.water.pvt.pressure', 300, 'bar', point_index=0))
        calls.append(p)
        return out
    provider = IRProvider(respond)
    result = await translate(6, provider)
    assert len(calls) == 2
    assert 'PVT_POINT_PRESSURE_MISSING' in provider.prompts[1]
    expected = json.loads((DATA / 'expected_results.json').read_text())['cases']['6']
    from scripts.evaluate_heterogeneous import compare
    assert compare(result, expected) == []


@pytest.mark.asyncio
async def test_pvt_pressure_absent_in_source_remains_unmapped():
    provider = IRProvider(lambda p: [
        draft(p, 'fluid.water.pvt', {'model_type':'constant'}),
        draft(p, 'fluid.water.pvt.formation_volume_factor', 1.02, 'rm3/sm3', point_index=0),
        {'status':'UNMAPPED', 'source_block_id':p['raw_block']['block_id'],
         'source_field':'PVT pressure not specified', 'confidence':0}])
    app = create_app(provider=provider)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/translate', json={
            'source': {'file_name':'missing.txt', 'content':'water PVT Bw=1.02 rm3/sm3'},
            'target_platform':'eclipse'})
    result = response.json()
    assert result['status'] == 'review_required'
    assert result['canonical_model'] is None and result['target'] is None
