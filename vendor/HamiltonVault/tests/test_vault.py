import json
import shutil
from pathlib import Path
import duckdb
import pytest
from vault.runner import run
from vault.runtime import digest
from vault.metadata import Model, import_hackolade

EXAMPLE = Path(__file__).parents[1] / 'examples'


@pytest.fixture
def setup(tmp_path):
    shutil.copy(EXAMPLE / 'model.json', tmp_path)
    shutil.copy(EXAMPLE / 'sales.csv', tmp_path)
    return tmp_path / 'model.json', tmp_path / 'vault.duckdb'


def load(setup, batch='b1', day=1):
    return run(*setup, batch, f'2026-01-{day:02d}T00:00:00Z')


def rows(db, table):
    with duckdb.connect(str(db)) as con:
        return con.execute(f'SELECT * FROM {table}').fetchall()


def test_initial_replay_unchanged_and_reversion(setup):
    result = load(setup)
    assert result['tables']['load_hub_customer']['inserted'] == 2
    assert len(rows(setup[1], 'link_customer_order')) == 3
    assert load(setup)['status'] == 'already_loaded'
    assert all(v['inserted'] == 0 for v in load(setup, 'b2', 2)['tables'].values())
    csv = setup[0].parent / 'sales.csv'
    original = csv.read_text()
    csv.write_text(original.replace('Zurich', 'Bern'))
    load(setup, 'b3', 3)
    csv.write_text(original)
    load(setup, 'b4', 4)
    assert len(rows(setup[1], 'sat_customer')) == 4
    assert len(rows(setup[1], 'hub_customer')) == 2


def test_changed_replay_rejected(setup):
    load(setup)
    with pytest.raises(ValueError, match='Batch ID reused'):
        load(setup, day=2)


def test_rollback_after_conflicting_satellite(setup):
    load(setup)
    csv = setup[0].parent / 'sales.csv'
    csv.write_text(csv.read_text() + 'C001,Alice,Geneva,O004,88.00\n')
    with pytest.raises(ValueError, match='conflicting states'):
        load(setup, 'bad', 2)
    assert len(rows(setup[1], 'hub_order')) == 3
    assert len(rows(setup[1], '_vault_batches')) == 1


def test_null_key_rejected(setup):
    csv = setup[0].parent / 'sales.csv'
    csv.write_text(csv.read_text().replace('C002', ''))
    with pytest.raises(ValueError, match='null/blank'):
        load(setup)


def test_chronology_and_schema_drift(setup):
    load(setup, day=3)
    with pytest.raises(ValueError, match='increasing'):
        load(setup, 'b2', 2)
    model = json.loads(setup[0].read_text())
    model['entities'][-1]['attributes'] = ['city']
    setup[0].write_text(json.dumps(model))
    with pytest.raises(ValueError, match='Model changed'):
        load(setup, 'b4', 4)


def test_hash_framing_and_case():
    assert digest(['a||b', 'c']) != digest(['a', 'b||c'])
    assert digest([None]) != digest(['^^'])
    assert digest([' A ']) == digest(['A'])
    assert digest(['a']) != digest(['A'])


def test_invalid_metadata(setup):
    model = json.loads(setup[0].read_text())
    model['entities'][0]['name'] = 'bad-name'
    with pytest.raises(ValueError):
        Model.model_validate(model)


def test_adapter(setup):
    model = json.loads(setup[0].read_text())
    mapping = {'sources': model['sources'], 'entities': {e['name']: {k: v for k, v in e.items() if k != 'name'} for e in model['entities']}}
    export = {'entities': [{'name': e['name']} for e in model['entities']]}
    assert import_hackolade(export, mapping) == Model.model_validate(model)
    with pytest.raises(ValueError, match='missing'):
        import_hackolade({'entities': []}, mapping)
