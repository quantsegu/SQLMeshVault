import hashlib
import json
import types
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
import duckdb
from hamilton import driver
from .compiler import compile_model
from .metadata import read_model
from .runtime import Context


def run(model_path, database, batch_id, load_dts):
    model = read_model(model_path)
    timestamp = datetime.fromisoformat(load_dts.replace('Z', '+00:00'))
    if timestamp.tzinfo is None:
        raise ValueError('load_dts requires a timezone')
    timestamp = timestamp.astimezone(timezone.utc)
    root = Path(model_path).resolve().parent
    model_hash = hashlib.sha256(model.model_dump_json().encode()).hexdigest()
    fingerprints = {n: hashlib.sha256((root / s.path).read_bytes()).hexdigest() for n, s in model.sources.items()}
    batch_hash = hashlib.sha256(json.dumps([model_hash, timestamp.isoformat(), fingerprints], sort_keys=True).encode()).hexdigest()
    module = types.ModuleType('generated_vault_' + uuid.uuid4().hex)
    sys.modules[module.__name__] = module
    try:
        exec(compile_model(model), module.__dict__)
        dag = driver.Builder().with_modules(module).build()
    finally:
        del sys.modules[module.__name__]
    with duckdb.connect(str(database)) as db:
        db.execute('BEGIN TRANSACTION')
        try:
            db.execute('CREATE TABLE IF NOT EXISTS _vault_batches (batch_id VARCHAR PRIMARY KEY, fingerprint VARCHAR, model_hash VARCHAR, load_dts TIMESTAMPTZ, result VARCHAR)')
            old = db.execute('SELECT fingerprint, result FROM _vault_batches WHERE batch_id = ?', [batch_id]).fetchone()
            if old:
                if old[0] != batch_hash:
                    raise ValueError('Batch ID reused with different input, metadata, or timestamp')
                db.execute('COMMIT')
                return {'status': 'already_loaded', 'batch_id': batch_id, 'original_result': json.loads(old[1])}
            previous = db.execute('SELECT model_hash, max(load_dts) FROM _vault_batches GROUP BY model_hash').fetchall()
            if any(h != model_hash for h, _ in previous):
                raise ValueError('Model changed: explicit migration or a fresh database required')
            if any(timestamp <= t for _, t in previous):
                raise ValueError('New batches require strictly increasing load timestamps')
            result = dag.execute([f'load_{e.name}' for e in model.entities], inputs={'context': Context(model, db, root, timestamp)})
            # Detect source modifications during the run before committing.
            if any(hashlib.sha256((root / model.sources[n].path).read_bytes()).hexdigest() != h for n, h in fingerprints.items()):
                raise ValueError('Source changed during execution')
            db.execute('INSERT INTO _vault_batches VALUES (?, ?, ?, ?, ?)', [batch_id, batch_hash, model_hash, timestamp, json.dumps(result)])
            db.execute('COMMIT')
            return {'status': 'loaded', 'batch_id': batch_id, 'tables': result}
        except Exception:
            db.execute('ROLLBACK')
            raise
