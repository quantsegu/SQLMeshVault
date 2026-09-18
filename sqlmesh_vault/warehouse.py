"""Shared warehouse contracts; build/validate never connect or resolve credentials."""

from __future__ import annotations

import os
import re
from pathlib import Path

import sqlglot
from sqlglot import ErrorLevel, exp

ENGINES = {"duckdb", "databricks", "snowflake", "clickhouse"}
FIELDS = {
    "duckdb": {"database"},
    "databricks": {
        "server_hostname",
        "http_path",
        "access_token",
        "catalog",
        "schema",
        "auth_type",
        "oauth_client_id",
        "oauth_client_secret",
    },
    "snowflake": {
        "account",
        "user",
        "password",
        "warehouse",
        "database",
        "schema",
        "role",
        "authenticator",
        "token",
        "private_key_file",
        "private_key_file_pwd",
    },
    "clickhouse": {
        "host",
        "port",
        "username",
        "password",
        "database",
        "secure",
        "verify",
    },
}
SECRETS = {
    "access_token",
    "password",
    "token",
    "oauth_client_secret",
    "private_key_file_pwd",
}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Invalid identifier: {value!r}")
    return value


def relation(value, engine, *, schema=False):
    parts = value.split(".")
    allowed = (
        {1}
        if schema and engine == "clickhouse"
        else ({1, 2} if schema else ({2} if engine == "clickhouse" else {2, 3}))
    )
    if len(parts) not in allowed:
        raise ValueError(f"Invalid {engine} relation depth: {value}")
    for part in parts:
        identifier(part)
    return value


def table(value):
    return exp.to_table(".".join('"' + identifier(p) + '"' for p in value.split(".")))


def validate(spec):
    if set(spec) != {"type", "connection"} or spec["type"] not in ENGINES:
        raise ValueError("warehouse requires type and connection")
    if set(spec["connection"]) - FIELDS[spec["type"]]:
        raise ValueError("Unknown warehouse connection fields")
    for key, value in spec["connection"].items():
        if isinstance(value, dict):
            if set(value) != {"env"} or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*", value["env"]
            ):
                raise ValueError('Environment references must be {"env": "NAME"}')
        elif key in SECRETS:
            raise ValueError(
                f"{key} must use an environment reference, not a literal secret"
            )
        elif not isinstance(value, (str, int, bool)):
            raise ValueError(
                "Connection options must be scalar values or env references"
            )
    return spec


def options(spec):
    validate(spec)
    result = {}
    for key, value in spec["connection"].items():
        if isinstance(value, dict):
            env = value["env"]
            if env not in os.environ:
                raise ValueError(f"Missing environment variable: {env}")
            value = os.environ[env]
        if key == "port":
            value = int(value)
        if key in {"secure", "verify"} and isinstance(value, str):
            if value.lower() not in {"true", "false"}:
                raise ValueError(f"{key} must be true or false")
            value = value.lower() == "true"
        result[key] = value
    return result


def render(sql, engine, read="duckdb"):
    """Strict AST translation plus explicit vault hash/null/timestamp differences."""
    if engine not in ENGINES:
        raise ValueError("Unsupported engine")
    tree = sqlglot.parse_one(sql, read=read)

    def fix(node):
        if isinstance(node, exp.Anonymous) and node.name.upper() == "FROM_HEX":
            return exp.Unhex(this=node.expressions[0].copy())
        if engine == "clickhouse":
            if isinstance(node, exp.SHA2):
                return exp.Anonymous(
                    this="lower",
                    expressions=[
                        exp.Anonymous(
                            this="hex",
                            expressions=[
                                exp.Anonymous(
                                    this="SHA256", expressions=[node.this.copy()]
                                )
                            ],
                        )
                    ],
                )
            if isinstance(node, exp.NullSafeNEQ):
                a, b = node.this.copy(), node.expression.copy()
                return exp.or_(
                    exp.NEQ(
                        this=exp.Paren(
                            this=exp.Is(this=a.copy(), expression=exp.Null())
                        ),
                        expression=exp.Paren(
                            this=exp.Is(this=b.copy(), expression=exp.Null())
                        ),
                    ),
                    exp.Coalesce(
                        this=exp.NEQ(this=a, expression=b), expressions=[exp.false()]
                    ),
                )
            if isinstance(node, (exp.Cast, exp.TryCast)) and node.to.is_type(
                exp.DataType.Type.TIMESTAMPTZ, exp.DataType.Type.TIMESTAMP
            ):
                return exp.Anonymous(
                    this="parseDateTime64BestEffortOrNull"
                    if isinstance(node, exp.TryCast)
                    else "parseDateTime64BestEffort",
                    expressions=[
                        exp.Cast(
                            this=node.this.copy(),
                            to=exp.DataType.build("String", dialect="clickhouse"),
                        ),
                        exp.Literal.number(6),
                        exp.Literal.string("UTC"),
                    ],
                )
        return node

    # Bottom-up traversal preserves nested hash conversions inside FROM_HEX.
    for original in reversed(list(tree.walk())):
        replacement = fix(original)
        if replacement is not original:
            if original is tree:
                tree = replacement
            else:
                original.replace(replacement)
    rendered = tree.sql(dialect=engine, unsupported_level=ErrorLevel.RAISE)
    sqlglot.parse_one(rendered, read=engine)
    return rendered


class Connection:
    def __init__(self, spec):
        self.spec = validate(spec)
        self.engine = spec["type"]
        self.handle = None

    def connect(self):
        if self.handle is not None:
            return self
        opts = options(self.spec)
        if self.engine == "duckdb":
            import duckdb

            self.handle = duckdb.connect(**opts)
        elif self.engine == "databricks":
            from databricks import sql

            if opts.get("oauth_client_id") and opts.get("oauth_client_secret"):
                from databricks.sdk.core import Config, oauth_service_principal

                auth = Config(
                    host="https://" + opts["server_hostname"],
                    client_id=opts.pop("oauth_client_id"),
                    client_secret=opts.pop("oauth_client_secret"),
                )
                opts.pop("auth_type", None)
                opts["credentials_provider"] = lambda: oauth_service_principal(auth)
            self.handle = sql.connect(
                **opts, session_configuration={"spark.sql.session.timeZone": "UTC"}
            )
        elif self.engine == "snowflake":
            import snowflake.connector

            self.handle = snowflake.connector.connect(
                **opts, session_parameters={"TIMEZONE": "UTC"}
            )
        else:
            import clickhouse_connect

            self.handle = clickhouse_connect.get_client(
                **opts,
                settings={"join_use_nulls": 1, "date_time_input_format": "best_effort"},
            )
        return self

    def query(self, sql):
        self.connect()
        if self.engine == "clickhouse":
            result = self.handle.query(sql)
            return list(result.column_names), list(result.result_rows)
        cursor = self.handle.cursor()
        try:
            cursor.execute(sql)
            return [d[0] for d in cursor.description], cursor.fetchall()
        finally:
            cursor.close()

    def execute(self, sql):
        self.connect()
        if self.engine == "clickhouse":
            self.handle.command(sql)
            return
        cursor = self.handle.cursor()
        try:
            cursor.execute(sql)
        finally:
            cursor.close()

    def close(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None


def sqlmesh_config(spec, root):
    """Native SQLMesh gateway, with a local durable state store for single-host use."""
    from sqlmesh.core.config import Config, GatewayConfig, ModelDefaultsConfig
    from sqlmesh.core.config.connection import (
        ClickhouseConnectionConfig,
        DatabricksConnectionConfig,
        DuckDBConnectionConfig,
        SnowflakeConnectionConfig,
    )

    engine = spec["type"]
    opts = options(spec)
    opts.pop("schema", None)
    if engine == "databricks":
        opts["disable_databricks_connect"] = True
        opts["disable_spark_session"] = True
        opts["session_configuration"] = {"spark.sql.session.timeZone": "UTC"}
    if engine == "snowflake":
        if "private_key_file" in opts:
            opts["private_key_path"] = opts.pop("private_key_file")
        if "private_key_file_pwd" in opts:
            opts["private_key_passphrase"] = opts.pop("private_key_file_pwd")
        opts["session_parameters"] = {"TIMEZONE": "UTC"}
    if engine == "clickhouse":
        opts.pop(
            "database", None
        )  # ClickHouse's SQLMesh schema is its native database.
        opts["connection_settings"] = {
            "join_use_nulls": 1,
            "date_time_input_format": "best_effort",
        }
    cls = {
        "duckdb": DuckDBConnectionConfig,
        "databricks": DatabricksConnectionConfig,
        "snowflake": SnowflakeConnectionConfig,
        "clickhouse": ClickhouseConnectionConfig,
    }[engine]
    gateway = GatewayConfig(
        connection=cls(**opts),
        state_connection=DuckDBConnectionConfig(
            database=str(Path(root) / "sqlmesh_state.duckdb")
        ),
    )
    return Config(
        gateways={"warehouse": gateway},
        default_gateway="warehouse",
        model_defaults=ModelDefaultsConfig(dialect=engine, start="2026-01-01"),
    )
