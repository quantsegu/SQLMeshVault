"""Compile validated metadata to inspectable, ordinary Hamilton Python functions."""
from .metadata import Model


def compile_model(model: Model) -> str:
    lines = ['"""Generated Hamilton DAG. Regenerate after metadata changes."""',
             'from vault.runtime import Context', '']
    for name in model.sources:
        lines += [f'def stage_{name}(context: Context) -> list:',
                  f'    return context.stage({name!r})', '']
    for e in model.entities:
        deps = list(dict.fromkeys(r.hub for r in e.roles)) if e.kind == 'link' else ([e.parent] if e.parent else [])
        args = ["context: Context", f"stage_{e.source}: list"] + [f"load_{d}: dict" for d in deps]
        lines += [f'def load_{e.name}({", ".join(args)}) -> dict:',
                  f'    return context.load({e.name!r}, stage_{e.source})', '']
    return '\n'.join(lines)
