"""DuckDB reference loader: one atomic snapshot batch, one state per key."""
import csv
import hashlib
import json
from pathlib import Path


def normalized(value):
    if value is None:
        return None
    return str(value).strip() or None


def digest(values):
    # JSON array framing prevents delimiter/null-token collisions.
    data = json.dumps([normalized(v) for v in values], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def q(name):
    return '"' + name.replace('"', '""') + '"'


class Context:
    def __init__(self, model, connection, root, timestamp):
        self.model, self.db = model, connection
        self.root, self.timestamp = Path(root), timestamp
        self.entities = {e.name: e for e in model.entities}

    def stage(self, name):
        source = self.model.sources[name]
        with (self.root / source.path).open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != source.columns:
                raise ValueError(f"{name}: CSV header must match declared columns in order")
            rows = list(reader)
        for row in rows:
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"{name}: malformed CSV row")
        return [{k: normalized(v) for k, v in row.items()} for row in rows]

    def key(self, entity, row):
        columns = entity.keys if entity.kind == "hub" else [c for r in entity.roles for c in r.columns]
        if any(row[c] is None for c in columns):
            raise ValueError(f"{entity.name}: null/blank business key")
        if entity.kind == "hub":
            return digest([row[c] for c in columns])
        return digest([digest([row[c] for c in role.columns]) for role in entity.roles])

    def load(self, name, rows):
        e = self.entities[name]
        parent = self.entities[e.parent] if e.parent else e
        payload_cols = (e.keys if e.kind == "hub" else
                        [r.name + "_hk" for r in e.roles] if e.kind == "link" else
                        ["hashdiff"] + e.attributes)
        cols = ["hk", "load_dts", "record_source"] + payload_cols
        types = ["VARCHAR", "TIMESTAMPTZ", "VARCHAR"] + ["VARCHAR"] * len(payload_cols)
        pk = 'PRIMARY KEY ("hk", "load_dts")' if e.kind == "satellite" else 'PRIMARY KEY ("hk")'
        definition = ", ".join(f"{q(c)} {t}" for c, t in zip(cols, types))
        self.db.execute(f"CREATE TABLE IF NOT EXISTS {q(name)} ({definition}, {pk})")
        staged = {}
        for row in rows:
            hk = self.key(parent, row)
            if e.kind == "hub":
                payload = [row[c] for c in e.keys]
            elif e.kind == "link":
                payload = [digest([row[c] for c in role.columns]) for role in e.roles]
                for role, key in zip(e.roles, payload):
                    if not self.db.execute(f"SELECT 1 FROM {q(role.hub)} WHERE hk = ?", [key]).fetchone():
                        raise ValueError(f"{name}: missing parent hub key in {role.hub}")
            else:
                if not self.db.execute(f"SELECT 1 FROM {q(e.parent)} WHERE hk = ?", [hk]).fetchone():
                    raise ValueError(f"{name}: missing parent")
                values = [row[c] for c in e.attributes]
                payload = [digest(values)] + values
            if hk in staged and staged[hk] != payload:
                raise ValueError(f"{name}: conflicting states for one key in a snapshot batch")
            staged[hk] = payload
        inserted = 0
        for hk, payload in staged.items():
            if e.kind == "satellite":
                latest = self.db.execute(f"SELECT hashdiff, load_dts FROM {q(name)} WHERE hk = ? ORDER BY load_dts DESC LIMIT 1", [hk]).fetchone()
                if latest and self.timestamp < latest[1]:
                    raise ValueError("Out-of-order load timestamp; use chronological snapshot batches")
                if latest and latest[0] == payload[0]:
                    continue
                if latest and self.timestamp == latest[1]:
                    raise ValueError("Different state at an existing load timestamp")
            elif self.db.execute(f"SELECT 1 FROM {q(name)} WHERE hk = ?", [hk]).fetchone():
                continue
            values = [hk, self.timestamp, self.model.sources[e.source].record_source] + payload
            placeholders = ",".join("?" for _ in values)
            self.db.execute(f"INSERT INTO {q(name)} VALUES ({placeholders})", values)
            inserted += 1
        return {"table": name, "input_rows": len(rows), "inserted": inserted}
