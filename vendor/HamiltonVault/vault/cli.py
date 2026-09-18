"""Command line for legacy v1 and the SQL-first v2 replacement core."""
import argparse
import json
from pathlib import Path
from .metadata import Model, read_model, import_hackolade
from .compiler import compile_model
from .runner import run as run_v1
from .v2 import engine
from .v2.metadata import Project


def main():
    parser = argparse.ArgumentParser(description='Metadata-driven Hamilton Data Vault')
    commands = parser.add_subparsers(dest='command', required=True)
    for action in ('validate', 'compile', 'run', 'build', 'test'):
        p = commands.add_parser(action)
        p.add_argument('model')
        if action in ('compile', 'build'):
            p.add_argument('--output', required=True)
        if action in ('run', 'test'):
            p.add_argument('--database', required=True)
        if action == 'run':
            p.add_argument('--batch-id', required=True)
            p.add_argument('--result-file')
        if action in ('run', 'build'):
            p.add_argument('--load-dts', required=True)
    p = commands.add_parser('schema')
    p.add_argument('--version', choices=['1', '2'], default='2')
    p.add_argument('--output', required=True)
    p = commands.add_parser('import-hackolade')
    p.add_argument('export')
    p.add_argument('--mapping', required=True)
    p.add_argument('--output', required=True)
    p = commands.add_parser('import-automatedv')
    p.add_argument('export')
    p.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'schema':
            spec = Project if args.version == '2' else Model
            Path(args.output).write_text(json.dumps(spec.model_json_schema(), indent=2))
            return
        if args.command == 'import-hackolade':
            mapping = json.loads(Path(args.mapping).read_text())
            export = json.loads(Path(args.export).read_text())
            if mapping.get('version') == 2:
                from .v2.importers import hackolade
                model = hackolade(export, mapping)
            else:
                model = import_hackolade(export, mapping)
            Path(args.output).write_text(model.model_dump_json(indent=2))
            return
        if args.command == 'import-automatedv':
            from .v2.importers import automatedv
            project = automatedv(json.loads(Path(args.export).read_text()))
            Path(args.output).write_text(project.model_dump_json(indent=2))
            return
        is_v2 = json.loads(Path(args.model).read_text()).get('version') == 2
        if args.command == 'validate':
            model = engine.read_project(args.model) if is_v2 else read_model(args.model)
            count = len(model.models) if is_v2 else len(model.entities)
            print(f'Valid v{model.version}: {count} models')
        elif args.command == 'compile':
            code = engine.compile_dag(engine.read_project(args.model)) if is_v2 else compile_model(read_model(args.model))
            Path(args.output).write_text(code)
        elif args.command == 'run':
            result = (engine.run if is_v2 else run_v1)(args.model, args.database, args.batch_id, args.load_dts)
            if args.result_file:
                Path(args.result_file).write_text(json.dumps(result, indent=2) + '\n')
            print(json.dumps(result, indent=2))
        elif not is_v2:
            raise ValueError('build/test require a version 2 project')
        elif args.command == 'build':
            manifest = engine.build(args.model, args.output, args.load_dts)
            print(json.dumps({'status': 'built', 'models': list(manifest['models']), 'output': args.output}, indent=2))
        else:
            print(json.dumps(engine.test_database(args.model, args.database), indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(1, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
