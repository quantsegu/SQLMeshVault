"""DuckDB SQL compiler. No user-supplied SQL expressions are evaluated."""
from dataclasses import dataclass
from .metadata import Project, HashColumn


def q(value):
    return '"' + value.replace('"', '""') + '"'


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def normalized(expr, config):
    result = f'TRIM(CAST({expr} AS VARCHAR))'
    if config.casing == 'UPPER':
        result = f'UPPER({result})'
    return f"NULLIF({result}, '')"


def hash_sql(spec: HashColumn, config):
    names = spec.names()
    if spec.is_hashdiff:
        names = sorted(names, key=str.lower)
    expressions = [normalized(q(n), config) for n in names]
    if isinstance(spec.columns, str):
        content = expressions[0]
    else:
        parts = [f'COALESCE({e}, {literal(config.null_placeholder_string)})' for e in expressions]
        content = f"CONCAT_WS({literal(config.concat_string)}, {', '.join(parts)})"
        if not spec.is_hashdiff:
            content = f'NULLIF({content}, {literal(config.concat_string.join([config.null_placeholder_string] * len(names)))})'
    function = 'MD5' if config.algorithm == 'MD5' else 'SHA256'
    result = f'{function}({content})'
    return f'FROM_HEX({result})' if config.storage == 'binary' else result


def stage_sql(project, name, path, timestamp):
    s = project.stages[name]
    csv_types = ', '.join(f'{literal(c)}: \'VARCHAR\'' for c in s.columns)
    raw = f"SELECT * FROM read_csv({literal(path)}, header=true, auto_detect=false, delim=',', quote='\"', escape='\"', columns={{{csv_types}}}, nullstr='', strict_mode=true)"
    derived = []
    for alias, d in s.derived_columns.items():
        expr = q(d.column) if d.column else literal(timestamp if d.load_timestamp else d.literal)
        if d.transform != 'identity':
            expr = f'{d.transform.upper()}({expr})'
        derived.append(f'{expr} AS {q(alias)}')
    additions = ', ' + ', '.join(derived) if derived else ''
    nulls = []
    for c in s.columns + list(s.derived_columns):
        expr = f"COALESCE(NULLIF(TRIM({q(c)}), ''), {literal(s.null_columns[c])})" if c in s.null_columns else q(c)
        nulls.append(f'{expr} AS {q(c)}')
    hashes = ', '.join(f'{hash_sql(h, project.hashing)} AS {q(c)}' for c, h in s.hashed_columns.items())
    suffix = ', ' + hashes if hashes else ''
    return (f'CREATE OR REPLACE TEMP TABLE {q("_hv_stage_" + name)} AS\n'
            f'WITH raw AS ({raw}),\n'
            f'derived AS (SELECT *{additions} FROM raw),\n'
            f'null_replaced AS (SELECT {", ".join(nulls)} FROM derived)\n'
            f'SELECT *{suffix} FROM null_replaced;')


@dataclass
class Check:
    name: str
    sql: str


def stage_checks(project, name):
    temporal = set()
    for m in project.models:
        if name in m.stages():
            temporal.add(m.src_ldts)
            if m.src_eff:
                temporal.add(m.src_eff)
    checks = []
    for col in sorted(temporal):
        value = q(col)
        sql = f"""SELECT 1 FROM {q('_hv_stage_' + name)}
WHERE {value} IS NOT NULL AND
(NOT regexp_matches(CAST({value} AS VARCHAR), '(Z|[+-][0-9]{{2}}:[0-9]{{2}})$', 'i')
 OR TRY_CAST({value} AS TIMESTAMPTZ) IS NULL)"""
        checks.append(Check(f'{name}.{col}: use ISO timestamps with explicit timezone', sql))
    return checks


def assertion_sql(check):
    return f"-- {check.name}\nSELECT CASE WHEN EXISTS ({check.sql}) THEN error({literal(check.name)}) ELSE 'ok' END;"


@dataclass
class Plan:
    name: str
    ddl: str
    prepare: str
    checks: list[Check]
    insert: str
    metrics: str

    def script(self):
        checks = '\n'.join(assertion_sql(c) for c in self.checks)
        return '\n\n'.join([self.ddl, self.prepare, checks, self.insert])


def types_for(project, m):
    hash_type = 'BLOB' if project.hashing.storage == 'binary' else 'VARCHAR'
    hashes = set([m.src_pk] + m.src_fk + ([m.src_hashdiff] if m.src_hashdiff else []))
    timestamps = {m.src_ldts} | ({m.src_eff} if m.src_eff else set())
    return {c: hash_type if c in hashes else 'TIMESTAMPTZ' if c in timestamps else 'VARCHAR' for c in m.output_columns()}


def model_plan(project: Project, m) -> Plan:
    target = q(m.name)
    raw, candidate = q('_hv_raw_' + m.name), q('_hv_input_' + m.name)
    cols = m.output_columns()
    pk, ldts = q(m.src_pk), q(m.src_ldts)
    types = types_for(project, m)
    required = {m.src_pk, m.src_ldts, m.src_source} | set(m.src_fk) | ({m.src_hashdiff} if m.src_hashdiff else set())
    definition = ',\n  '.join(f'{q(c)} {t}' + (' NOT NULL' if c in required else '') for c, t in types.items())
    primary = f'{pk}, {ldts}' if m.kind == 'sat' else pk
    ddl = f'CREATE TABLE IF NOT EXISTS {target} (\n  {definition},\n  PRIMARY KEY ({primary})\n);'
    sources = []
    for priority, name in enumerate(m.stages()):
        selected = ', '.join(q(c) for c in cols)
        sources.append(f'SELECT {selected}, {priority} AS "__priority" FROM {q("_hv_stage_" + name)}')
    valid = ' AND '.join(f'{q(c)} IS NOT NULL' for c in [m.src_pk] + m.src_fk)
    casted = ', '.join(f'CAST({q(c)} AS {types[c]}) AS {q(c)}' for c in cols)
    # Cast only eligible records; timestamp format validation is performed in stage nodes.
    prepare = f'CREATE OR REPLACE TEMP TABLE {raw} AS ' + '\nUNION ALL\n'.join(sources) + ';\n'
    prepare += f'CREATE OR REPLACE TEMP TABLE {candidate} AS SELECT {casted}, "__priority" FROM {raw} WHERE {valid};'
    checks = [Check(f'{m.name}: null load timestamp or record source',
                    f'SELECT 1 FROM {candidate} WHERE {ldts} IS NULL OR NULLIF(TRIM({q(m.src_source)}), \'\') IS NULL')]
    by_name = {x.name: x for x in project.models}
    refs = m.references if m.kind == 'link' else ({m.src_pk: m.parent} if m.parent else {})
    for fk, parent in refs.items():
        parent_pk = q(by_name[parent].src_pk)
        checks.append(Check(f'{m.name}: orphan key {fk} -> {parent}',
                            f'SELECT 1 FROM {candidate} s WHERE NOT EXISTS (SELECT 1 FROM {q(parent)} p WHERE p.{parent_pk} = s.{q(fk)})'))
    selected = ', '.join(q(c) for c in cols)
    if m.kind != 'sat':
        identity = m.src_nk if m.kind == 'hub' else m.src_fk
        canon = [normalized(q(c), project.hashing) if m.kind == 'hub' else q(c) for c in identity]
        checks.append(Check(f'{m.name}: key collision within batch',
                            f'SELECT {pk} FROM (SELECT DISTINCT {pk}, {", ".join(canon)} FROM {candidate}) x GROUP BY {pk} HAVING COUNT(*) > 1'))
        comparisons = []
        for c in identity:
            left = normalized('s.' + q(c), project.hashing) if m.kind == 'hub' else 's.' + q(c)
            right = normalized('t.' + q(c), project.hashing) if m.kind == 'hub' else 't.' + q(c)
            comparisons.append(f'{left} IS DISTINCT FROM {right}')
        checks.append(Check(f'{m.name}: key collision with target',
                            f'SELECT 1 FROM {candidate} s JOIN {target} t ON s.{pk} = t.{pk} WHERE ' + ' OR '.join(comparisons)))
        order = ', '.join([ldts, '"__priority"'] + [q(c) for c in cols if c not in (m.src_pk, m.src_ldts)])
        insert = f'''INSERT INTO {target} ({selected})
SELECT {selected} FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY {pk} ORDER BY {order}) AS "__rank"
  FROM {candidate}
) s WHERE "__rank" = 1
AND NOT EXISTS (SELECT 1 FROM {target} t WHERE t.{pk} = s.{pk});'''
    else:
        hd = q(m.src_hashdiff)
        checks += [Check(f'{m.name}: null hashdiff', f'SELECT 1 FROM {candidate} WHERE {hd} IS NULL'),
                   Check(f'{m.name}: conflicting states at the same load timestamp',
                         f'SELECT {pk}, {ldts} FROM (SELECT DISTINCT {selected} FROM {candidate}) d GROUP BY {pk}, {ldts} HAVING COUNT(*) > 1')]
        if m.late_arriving == 'error':
            checks.append(Check(f'{m.name}: late-arriving change requires explicit reconciliation', f'''
SELECT 1 FROM {candidate} s
WHERE s.{ldts} <= (SELECT MAX(t.{ldts}) FROM {target} t WHERE t.{pk} = s.{pk})
AND s.{hd} IS DISTINCT FROM (
  SELECT t.{hd} FROM {target} t WHERE t.{pk} = s.{pk} AND t.{ldts} <= s.{ldts}
  ORDER BY t.{ldts} DESC LIMIT 1
)'''))
        insert = f'''INSERT INTO {target} ({selected})
WITH latest AS (
  SELECT {pk}, {hd} AS "__last_hash", {ldts} AS "__last_ldts" FROM {target}
  QUALIFY ROW_NUMBER() OVER (PARTITION BY {pk} ORDER BY {ldts} DESC) = 1
), fresh AS (
  SELECT DISTINCT {", ".join('s.' + q(c) for c in cols)}, t."__last_hash"
  FROM {candidate} s LEFT JOIN latest t ON s.{pk} = t.{pk}
  WHERE t.{pk} IS NULL OR s.{ldts} > t."__last_ldts"
), ordered AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY {pk} ORDER BY {ldts}) AS "__seq",
  LAG({hd}) OVER (PARTITION BY {pk} ORDER BY {ldts}) AS "__prev_hash"
  FROM fresh
)
SELECT {selected} FROM ordered
WHERE {hd} IS DISTINCT FROM CASE WHEN "__seq" = 1 THEN "__last_hash" ELSE "__prev_hash" END;'''
    metrics = f'SELECT COUNT(*) AS input_rows, COUNT(*) FILTER (WHERE NOT ({valid})) AS skipped_null_keys FROM {raw}'
    return Plan(m.name, ddl, prepare, checks, insert, metrics)


def quality_checks(project):
    """Post-load checks, also usable independently through the test command."""
    result = []
    by_name = {m.name: m for m in project.models}
    for m in project.models:
        pk, ldts = q(m.src_pk), q(m.src_ldts)
        keys = f'{pk}, {ldts}' if m.kind == 'sat' else pk
        result.append(Check(f'{m.name}: unique key', f'SELECT {keys} FROM {q(m.name)} GROUP BY {keys} HAVING COUNT(*) > 1'))
        required = [m.src_pk, m.src_ldts, m.src_source] + m.src_fk + ([m.src_hashdiff] if m.src_hashdiff else [])
        result.append(Check(f'{m.name}: required columns', f'SELECT 1 FROM {q(m.name)} WHERE ' + ' OR '.join(f'{q(c)} IS NULL' for c in required)))
        refs = m.references if m.kind == 'link' else ({m.src_pk: m.parent} if m.parent else {})
        for fk, parent in refs.items():
            result.append(Check(f'{m.name}: relationship {fk}', f'SELECT 1 FROM {q(m.name)} s WHERE NOT EXISTS (SELECT 1 FROM {q(parent)} p WHERE p.{q(by_name[parent].src_pk)} = s.{q(fk)})'))
        if m.kind == 'sat':
            hd = q(m.src_hashdiff)
            result.append(Check(f'{m.name}: consecutive change history', f'SELECT 1 FROM (SELECT {hd}, LAG({hd}) OVER (PARTITION BY {pk} ORDER BY {ldts}) AS "__prev" FROM {q(m.name)}) s WHERE {hd} = "__prev"'))
    return result
