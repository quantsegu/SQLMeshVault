from __future__ import annotations

import argparse
import json

from .compiler import build
from .runtime import apply


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile and execute native SQLMesh Data Vault models"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build")
    p.add_argument("metadata")
    p.add_argument("--output", required=True)
    p = sub.add_parser("build-warehouse")
    p.add_argument("metadata")
    p.add_argument("--warehouse", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("apply")
    p.add_argument("project")
    p.add_argument("--execution-time", default="2026-01-05T00:00:00Z")
    args = parser.parse_args()
    if args.command == "build-warehouse":
        from .remote import build as build_remote

        print(
            json.dumps(
                build_remote(args.metadata, args.warehouse, args.output), indent=2
            )
        )
        return
    result = (
        build(args.metadata, args.output)
        if args.command == "build"
        else apply(args.project, args.execution_time)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
