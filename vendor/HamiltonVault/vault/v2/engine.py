"""Hamilton owns dependencies; DuckDB owns set-based loading and atomicity."""
import csv
import hashlib
import json
import sys
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path
import duckdb
from hamilton import driver
from .metadata import Project
from .sql import stage_sql, model_plan, quality_checks, q, types_for, stage_checks, assertion_sql


ENGINE_VERSION = '2.0.0'


def read_project(path):
    return Project.model_validate_json(Path(path).read_text())


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Load timestamp requires an explicit timezone')
    return result.astimezone(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def run_checks(db, checks):
    results = []
    for check in checks:
        if db.execute(f'SELECT 1 FROM ({check.sql}) AS violations LIMIT 1').fetchone():
            raise ValueError(check.name)
        results.append({'name': check.name, 'status': 'pass'})
    return results


def validate_schema(db, project, m):
    info = db.execute(f'PRAGMA table_info({q(m.name)})').fetchall()
    actual = {row[1]: row[2] for row in info}
    expected = {c: ('TIMESTAMP WITH TIME ZONE' if t == 'TIMESTAMPTZ' else t)
                for c, t in types_for(project, m).items()}
    primary = {m.src_pk, m.src_ldts} if m.kind == 'sat' else {m.src_pk}
    required = {m.src_pk, m.src_ldts, m.src_source} | set(m.src_fk) | ({m.src_hashdiff} if m.src_hashdiff else set())
    if (actual != expected or {row[1] for row in info if row[5]} != primary
            or not required <= {row[1] for row in info if row[3]}):
        raise ValueError(f'{m.name}: physical schema drift requires migration')


class Context:
    def __init__(self, project, db, root, load_dts):
        self.project, self.db, self.root, self.load_dts = project, db, Path(root), load_dts

    def stage(self, name):
        spec = self.project.stages[name]
        path = (self.root / spec.path).resolve()
        with path.open(newline='', encoding='utf-8') as stream:
            if next(csv.reader(stream), None) != spec.columns:
                raise ValueError(f'{name}: CSV header differs from declared column order')
        self.db.execute(stage_sql(self.project, name, path, self.load_dts))
        run_checks(self.db, stage_checks(self.project, name))
        return {'stage': name, 'rows': self.db.execute(f'SELECT COUNT(*) FROM {q("_hv_stage_" + name)}').fetchone()[0]}

    def load(self, name):
        m = next(x for x in self.project.models if x.name == name)
        plan = model_plan(self.project, m)
        self.db.execute(plan.ddl)
        validate_schema(self.db, self.project, m)
        self.db.execute(plan.prepare)
        checks = run_checks(self.db, plan.checks)
        input_rows, skipped = self.db.execute(plan.metrics).fetchone()
        before = self.db.execute(f'SELECT COUNT(*) FROM {q(name)}').fetchone()[0]
        self.db.execute(plan.insert)
        after = self.db.execute(f'SELECT COUNT(*) FROM {q(name)}').fetchone()[0]
        return {'table': name, 'input_rows': input_rows, 'skipped_null_keys': skipped,
                'inserted': after - before, 'total_rows': after, 'checks': checks}


def compile_dag(project):
    lines = ['"""Generated SQL-first Hamilton Data Vault DAG."""', 'from vault.v2.engine import Context', '']
    for name in project.stages:
        lines += [f'def stage_{name}(context: Context) -> dict:', f'    return context.stage({name!r})', '']
    for m in project.models:
        args = ['context: Context'] + [f'stage_{s}: dict' for s in m.stages()] + [f'load_{d}: dict' for d in m.dependencies()]
        lines += [f'def load_{m.name}({", ".join(args)}) -> dict:', f'    return context.load({m.name!r})', '']
    return '\n'.join(lines)


def make_driver(project):
    module = types.ModuleType('hamilton_vault_' + uuid.uuid4().hex)
    sys.modules[module.__name__] = module
    try:
        exec(compile_dag(project), module.__dict__)
        return driver.Builder().with_modules(module).build()
    finally:
        del sys.modules[module.__name__]


def run(project_path, database, batch_id, load_dts):
    if not batch_id.strip():
        raise ValueError('Batch ID cannot be empty')
    project = read_project(project_path)
    root = Path(project_path).resolve().parent
    load_dts = timestamp(load_dts)
    model_hash = fingerprint(project.model_dump())
    files = {name: file_hash(root / s.path) for name, s in project.stages.items()}
    batch_hash = fingerprint([ENGINE_VERSION, model_hash, files, load_dts])
    dag = make_driver(project)
    with duckdb.connect(str(database)) as db:
        db.execute("SET TimeZone = 'UTC'")
        db.execute('BEGIN TRANSACTION')
        try:
            db.execute('CREATE TABLE IF NOT EXISTS _hv_project (project_name VARCHAR PRIMARY KEY, model_hash VARCHAR, engine_version VARCHAR)')
            previous = db.execute('SELECT project_name, model_hash, engine_version FROM _hv_project').fetchall()
            if previous and previous != [(project.name, model_hash, ENGINE_VERSION)]:
                raise ValueError('Project/metadata/engine changed: explicit migration or a new database required')
            if not previous:
                existing = {r[0].lower() for r in db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").fetchall()}
                if any(m.name.lower() in existing for m in project.models):
                    raise ValueError('Refusing to adopt pre-existing tables without an explicit migration')
                db.execute('INSERT INTO _hv_project VALUES (?, ?, ?)', [project.name, model_hash, ENGINE_VERSION])
            db.execute('CREATE TABLE IF NOT EXISTS _hv_runs (batch_id VARCHAR PRIMARY KEY, fingerprint VARCHAR, load_dts TIMESTAMPTZ, result JSON)')
            old = db.execute('SELECT fingerprint, result FROM _hv_runs WHERE batch_id = ?', [batch_id]).fetchone()
            if old:
                if old[0] != batch_hash:
                    raise ValueError('Batch ID reused with different input or load timestamp')
                # Replay still verifies the current persisted vault before returning.
                for model in project.models:
                    validate_schema(db, project, model)
                run_checks(db, quality_checks(project))
                db.execute('COMMIT')
                return {'status': 'already_loaded', 'batch_id': batch_id, 'original_result': json.loads(old[1])}
            tables = dag.execute([f'load_{m.name}' for m in project.models], inputs={'context': Context(project, db, root, load_dts)})
            checks = run_checks(db, quality_checks(project))
            if files != {name: file_hash(root / s.path) for name, s in project.stages.items()}:
                raise ValueError('Source files changed during execution')
            result = {'status': 'loaded', 'batch_id': batch_id, 'engine_version': ENGINE_VERSION,
                      'model_hash': model_hash, 'tables': tables, 'tests': checks}
            db.execute('INSERT INTO _hv_runs VALUES (?, ?, ?, ?)', [batch_id, batch_hash, load_dts, json.dumps(result)])
            db.execute('COMMIT')
            return result
        except Exception:
            db.execute('ROLLBACK')
            raise


def test_database(project_path, database):
    project = read_project(project_path)
    with duckdb.connect(str(database), read_only=True) as db:
        previous = db.execute('SELECT project_name, model_hash, engine_version FROM _hv_project').fetchall()
        if previous != [(project.name, fingerprint(project.model_dump()), ENGINE_VERSION)]:
            raise ValueError('Database and metadata do not match')
        for model in project.models:
            validate_schema(db, project, model)
        return {'status': 'pass', 'tests': run_checks(db, quality_checks(project))}


def build(project_path, output, load_dts):
    project = read_project(project_path)
    root = Path(project_path).resolve().parent
    output = Path(output)
    load_dts = timestamp(load_dts)
    (output / 'stages').mkdir(parents=True, exist_ok=True)
    (output / 'models').mkdir(exist_ok=True)
    (output / 'generated_dag.py').write_text(compile_dag(project))
    manifest = {'version': 2, 'engine_version': ENGINE_VERSION, 'backend': 'duckdb',
                'project': project.name, 'model_hash': fingerprint(project.model_dump()),
                'load_dts': load_dts, 'models': {}}
    statements = ['-- Reviewable SQL export; Hamilton run also enforces ownership, schema, source and audit checks.',
                  "SET TimeZone = 'UTC';", 'BEGIN TRANSACTION;']
    for name, stage in project.stages.items():
        sql = stage_sql(project, name, (root / stage.path).resolve(), load_dts)
        sql += '\n' + '\n'.join(assertion_sql(c) for c in stage_checks(project, name))
        (output / 'stages' / f'{name}.sql').write_text(sql + '\n')
        statements.append(sql)
    done = set()
    while len(done) < len(project.models):
        ready = [m for m in project.models if m.name not in done and set(m.dependencies()) <= done]
        if not ready:
            raise ValueError('Cyclic model dependencies')
        for m in ready:
            plan = model_plan(project, m)
            (output / 'models' / f'{m.name}.sql').write_text(plan.script() + '\n')
            statements.append(plan.script())
            manifest['models'][m.name] = {'kind': m.kind, 'sources': m.stages(), 'depends_on': m.dependencies(),
                                           'columns': types_for(project, m), 'checks': [c.name for c in plan.checks]}
            done.add(m.name)
    statements.extend(assertion_sql(c) for c in quality_checks(project))
    statements.append('COMMIT;')
    (output / 'run.sql').write_text('\n\n'.join(statements) + '\n')
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (output / 'project.schema.json').write_text(json.dumps(Project.model_json_schema(), indent=2) + '\n')
    return manifest
