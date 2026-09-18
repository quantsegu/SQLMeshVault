"""Behavioral tests for SQL-first core and documented AutomateDV hash rules."""
import copy
import hashlib
import json
import shutil
from pathlib import Path
import duckdb
import pytest
from vault.v2 import engine
from vault.v2.metadata import Project, HashColumn, Hashing
from vault.v2.sql import hash_sql
from vault.v2.importers import automatedv, hackolade

EXAMPLE = Path(__file__).parents[1] / 'examples' / 'replacement'


@pytest.fixture
def env(tmp_path):
    for file in EXAMPLE.glob('*'):
        if file.is_file():
            shutil.copy(file, tmp_path)
    return tmp_path / 'project.json', tmp_path / 'vault.duckdb'


def run(env, batch='b1', when='2026-01-05T00:00:00Z'):
    return engine.run(*env, batch, when)


def query(env, sql, params=None):
    with duckdb.connect(str(env[1])) as db:
        db.execute("SET TimeZone = 'UTC'")
        return db.execute(sql, params or []).fetchall()


def change_project(env, fn):
    data = json.loads(env[0].read_text())
    fn(data)
    env[0].write_text(json.dumps(data))


def append(env, file, value):
    path = env[0].parent / file
    path.write_text(path.read_text() + value)


@pytest.mark.parametrize('columns,is_diff,values,expected', [
    ('A', False, [' abc ', None], 'ABC'),
    ('A', False, ['', None], None),
    (['A', 'B'], False, [None, None], None),
    (['A', 'B'], False, ['abc', None], 'ABC||^^'),
    (['B', 'A'], False, ['abc', 'def'], 'DEF||ABC'),
    (['B', 'A'], True, ['abc', 'def'], 'ABC||DEF'),
    (['A', 'B'], True, [None, None], '^^||^^'),
    (['A'], True, [None, None], '^^'),
    ('A', True, [None, None], None),
])
@pytest.mark.parametrize('algorithm', ['MD5', 'SHA256'])
@pytest.mark.parametrize('storage', ['binary', 'hex'])
def test_documented_hash_vectors(columns, is_diff, values, expected, algorithm, storage):
    config = Hashing(algorithm=algorithm, storage=storage)
    sql = hash_sql(HashColumn(columns=columns, is_hashdiff=is_diff), config)
    with duckdb.connect() as db:
        actual = db.execute(f'SELECT {sql} FROM (SELECT ?::VARCHAR AS A, ?::VARCHAR AS B)', values).fetchone()[0]
    expected = None if expected is None else hashlib.new(algorithm.lower(), expected.encode()).digest()
    if expected is not None and storage == 'hex':
        expected = expected.hex()
    assert actual == expected


def test_custom_hash_options():
    config = Hashing(casing='DISABLED', concat_string='!!', null_placeholder_string='##')
    sql = hash_sql(HashColumn(columns=['A','B']), config)
    with duckdb.connect() as db:
        actual = db.execute(f"SELECT {sql} FROM (SELECT 'abc' AS A, NULL::VARCHAR AS B)").fetchone()[0]
    assert actual == hashlib.md5(b'abc!!##').digest()


def test_multisource_history_replay_and_incremental(env):
    result = run(env)
    assert [result['tables']['load_' + m]['inserted'] for m in ['hub_customer','hub_order','link_customer_order','sat_customer','sat_order']] == [3,2,2,4,3]
    assert query(env, "SELECT CITY FROM sat_customer WHERE CUSTOMER_HK=from_hex(md5('C001')) ORDER BY LOAD_DTS") == [('Zurich',),('Bern',),('Zurich',)]
    assert query(env, "SELECT RECORD_SOURCE FROM hub_customer WHERE CUSTOMER_ID='C001'") == [('CRM',)]
    assert query(env, "SELECT RECORD_SOURCE FROM hub_customer WHERE CUSTOMER_ID='C003'") == [('ERP',)]
    assert run(env)['status'] == 'already_loaded'
    unchanged = run(env, 'b2')
    assert all(t['inserted'] == 0 for t in unchanged['tables'].values())
    append(env,'crm.csv','C001,Alice,Geneva,2026-01-06T00:00:00Z\n')
    later = run(env,'b3','2026-01-07T00:00:00Z')
    assert later['tables']['load_sat_customer']['inserted'] == 1
    assert engine.test_database(*env)['status'] == 'pass'


def test_source_values_preserved_while_keys_normalized(env):
    path = env[0].parent/'crm.csv'
    path.write_text(path.read_text().replace('C001',' c001 '))
    result = run(env)
    assert result['tables']['load_hub_customer']['inserted'] == 3
    assert query(env,"SELECT CUSTOMER_ID FROM hub_customer WHERE CUSTOMER_HK=from_hex(md5('C001'))") == [(' c001 ',)]


def test_null_keys_filtered_and_audited(env):
    append(env,'crm.csv',',Unknown,Unknown,2026-01-01T00:00:00Z\n')
    result = run(env)
    assert result['tables']['load_hub_customer']['skipped_null_keys'] == 1
    assert result['tables']['load_sat_customer']['skipped_null_keys'] == 1


def test_transaction_rollback_on_conflicting_history(env):
    run(env)
    append(env,'crm.csv','C004,New,Basel,2026-01-06T00:00:00Z\nC001,Alice,Geneva,2026-01-04T00:00:00Z\n')
    with pytest.raises(ValueError,match='conflicting states'):
        run(env,'bad')
    assert query(env,'SELECT COUNT(*) FROM hub_customer') == [(3,)]
    assert query(env,'SELECT COUNT(*) FROM _hv_runs') == [(1,)]


def test_late_change_rejected(env):
    run(env)
    append(env,'crm.csv','C001,Alice,Geneva,2026-01-02T12:00:00Z\n')
    with pytest.raises(ValueError,match='late-arriving'):
        run(env,'bad')


def test_explicit_source_filter(env):
    change_project(env,lambda p:p['models'][3].update(late_arriving='ignore'))
    run(env)
    append(env,'crm.csv','C001,Alice,Geneva,2026-01-02T12:00:00Z\n')
    assert run(env,'b2')['tables']['load_sat_customer']['inserted'] == 0


def test_batch_id_reuse(env):
    run(env)
    with pytest.raises(ValueError,match='Batch ID reused'):
        run(env,when='2026-01-06T00:00:00Z')


def test_timestamp_timezone_required(env):
    path=env[0].parent/'crm.csv'
    path.write_text(path.read_text().replace('T00:00:00Z','T00:00:00'))
    with pytest.raises(ValueError,match='explicit timezone'):
        run(env)
    assert query(env,"SELECT table_name FROM information_schema.tables WHERE table_schema='main'") == []


def test_orphan_rolls_back(env):
    # Keep C003 in the link source but deliberately omit ERP from the customer hub.
    change_project(env,lambda p:p['models'][0].update(source_model='crm'))
    with pytest.raises(ValueError,match='orphan key'):
        run(env)
    assert query(env,"SELECT table_name FROM information_schema.tables WHERE table_schema='main'") == []


def test_model_drift_and_existing_table_adoption(env):
    query(env,'CREATE TABLE hub_customer (x INTEGER)')
    with pytest.raises(ValueError,match='pre-existing'):
        run(env)
    query(env,'DROP TABLE hub_customer')
    run(env)
    change_project(env,lambda p:p['hashing'].update(algorithm='SHA256'))
    with pytest.raises(ValueError,match='explicit migration'):
        run(env,'b2')


def test_physical_schema_drift(env):
    run(env)
    query(env,'ALTER TABLE sat_customer ADD COLUMN drift VARCHAR')
    with pytest.raises(ValueError,match='physical schema drift'):
        run(env,'b2')


def test_sql_artifact_executes_same_data(env,tmp_path):
    output=tmp_path/'compiled'
    manifest=engine.build(env[0],output,'2026-01-05T00:00:00Z')
    assert manifest['models']['sat_order']['depends_on'] == ['link_customer_order']
    run(env)
    with duckdb.connect() as db:
        db.execute((output/'run.sql').read_text())
        for table in manifest['models']:
            # Compare multisets of JSON rows; binary columns are serialized by DuckDB.
            actual=sorted(row[0] for row in db.execute(f'SELECT to_json(t) FROM {table} t').fetchall())
            expected=sorted(row[0] for row in query(env,f'SELECT to_json(t) FROM {table} t'))
            assert actual == expected
        db.execute((output/'run.sql').read_text())
        assert db.execute('SELECT COUNT(*) FROM sat_customer').fetchone()[0] == 4


def test_importers_and_strict_unsupported_options(env):
    export=json.loads((env[0].parent/'automatedv.export.json').read_text())
    project=engine.read_project(env[0])
    assert automatedv(export) == project
    assert hackolade(json.loads((env[0].parent/'hackolade.normalized.json').read_text()),project.model_dump()) == project
    bad=copy.deepcopy(export)
    bad['models'][0]['macro']='automate_dv.ma_sat'
    with pytest.raises(ValueError,match='Unsupported macro'):
        automatedv(bad)
    bad=copy.deepcopy(export)
    bad['stages']['crm']['derived_columns']['LOAD_DTS']='CURRENT_TIMESTAMP()'
    with pytest.raises(ValueError,match='Invalid identifier'):
        automatedv(bad)


def test_unsafe_identifiers_and_missing_hashdiff(env):
    p=json.loads(env[0].read_text())
    p['models'][0]['name']='x; DROP TABLE y'
    with pytest.raises(ValueError):
        Project.model_validate(p)
    p=json.loads(env[0].read_text())
    p['stages']['crm']['hashed_columns']['CUSTOMER_HASHDIFF']['is_hashdiff']=False
    with pytest.raises(ValueError,match='is_hashdiff'):
        Project.model_validate(p)


def test_hub_key_collision_rejected(env):
    # Misconfigured key hash collapses distinct business keys: reject instead of losing data.
    change_project(env,lambda p:p['stages']['crm']['hashed_columns']['CUSTOMER_HK'].update(columns='CITY'))
    append(env,'crm.csv','C999,Other,Basel,2026-01-01T00:00:00Z\n')
    with pytest.raises(ValueError,match='key collision'):
        run(env)


@pytest.mark.parametrize('algorithm,storage',[('SHA256','binary'),('MD5','hex')])
def test_hash_variants_end_to_end(env,algorithm,storage):
    change_project(env,lambda p:p['hashing'].update(algorithm=algorithm,storage=storage))
    assert run(env)['tables']['load_sat_customer']['inserted'] == 4
    assert run(env,'b2')['tables']['load_sat_customer']['inserted'] == 0
