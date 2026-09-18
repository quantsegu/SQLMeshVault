"""Explicit export adapters. Never evaluate a dbt project's Jinja or SQL."""
from .metadata import Project, Derived, HashColumn, Stage, Table, Hashing


def automatedv(export):
    """Import resolved macro arguments from an exported JSON manifest.

    This manifest is a documented interchange format, NOT dbt's manifest.json.
    Physical CSV sources and relationships are supplied explicitly.
    Unsupported macros, variables and stage arguments fail instead of disappearing.
    """
    allowed = {'name', 'vars', 'sources', 'stages', 'models'}
    if set(export) - allowed:
        raise ValueError(f'Unsupported manifest keys: {set(export) - allowed}')
    variables = export.get('vars', {})
    if set(variables) - {'hash', 'hash_content_casing', 'concat_string', 'null_placeholder_string', 'hash_storage'}:
        raise ValueError('Unsupported AutomateDV variables; no silent compatibility fallback')
    algorithm = variables.get('hash', 'MD5').upper()
    if algorithm == 'SHA':
        algorithm = 'SHA256'
    hashing = Hashing(algorithm=algorithm, casing=variables.get('hash_content_casing', 'UPPER'),
                      concat_string=variables.get('concat_string', '||'),
                      null_placeholder_string=variables.get('null_placeholder_string', '^^'),
                      storage=variables.get('hash_storage', 'binary'))
    stages = {}
    for name, config in export['stages'].items():
        config = dict(config)
        source = export['sources'][config.pop('source_model')]
        if set(source) != {'path', 'columns'}:
            raise ValueError('Physical source requires path and columns only')
        if config.pop('include_source_columns', True) is not True:
            raise ValueError('include_source_columns=false is not supported')
        derived = {}
        for col, expr in config.pop('derived_columns', {}).items():
            if not isinstance(expr, str):
                derived[col] = Derived.model_validate(expr)
            elif expr.startswith('!'):
                derived[col] = Derived(literal=expr[1:])
            elif expr == '@load_dts':
                derived[col] = Derived(load_timestamp=True)
            else:
                # Identifier validation rejects SQL expressions and unresolved Jinja.
                derived[col] = Derived(column=expr)
        hashes = {}
        for col, spec in config.pop('hashed_columns', {}).items():
            hashes[col] = HashColumn.model_validate(spec if isinstance(spec, dict) else {'columns': spec})
        nulls = config.pop('null_columns', {})
        if any(not isinstance(v, str) for v in nulls.values()):
            raise ValueError('Use explicit per-column null replacement strings')
        if config:
            raise ValueError(f'Unsupported stage arguments: {sorted(config)}')
        stages[name] = Stage(**source, derived_columns=derived, hashed_columns=hashes, null_columns=nulls)
    models = []
    for config in export['models']:
        config = dict(config)
        macro = config.pop('macro')
        prefix, sep, kind = macro.rpartition('.')
        if not sep or prefix not in ('dbtvault', 'automate_dv') or kind not in ('hub', 'link', 'sat'):
            raise ValueError(f'Unsupported macro: {macro}')
        for field in ('src_nk', 'src_fk', 'src_payload', 'src_extra_columns'):
            if isinstance(config.get(field), str):
                config[field] = [config[field]]
        models.append(Table(kind=kind, **config))
    return Project(name=export['name'], hashing=hashing, stages=stages, models=models)


def hackolade(export, mapping):
    """Names from normalized modeling-tool export, semantics from v2 sidecar."""
    if not isinstance(export.get('entities'), list):
        raise ValueError('Expected normalized export with entities array')
    names = [e['name'] for e in export['entities']]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate exported entity names')
    project = Project.model_validate(mapping)
    missing = {m.name for m in project.models} - set(names)
    if missing:
        raise ValueError(f'Mapped entities missing from export: {sorted(missing)}')
    return project
