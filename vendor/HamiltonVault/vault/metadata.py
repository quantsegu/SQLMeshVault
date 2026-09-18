"""Strict, portable contract shared by modeling-tool adapters and the compiler."""
import json
import keyword
import re
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Strict):
    path: str
    record_source: str
    columns: list[str]


class Role(Strict):
    name: str
    hub: str
    columns: list[str]


class Entity(Strict):
    name: str
    kind: Literal["hub", "link", "satellite"]
    source: str
    keys: list[str] = []
    roles: list[Role] = []
    parent: str | None = None
    attributes: list[str] = []


class Model(Strict):
    version: Literal[1] = 1
    sources: dict[str, Source]
    entities: list[Entity]

    @model_validator(mode="after")
    def validate_graph(self):
        names = [e.name for e in self.entities]
        if not names or len(names) != len(set(names)):
            raise ValueError("Entity names must be nonempty and unique")
        by_name = {e.name: e for e in self.entities}
        identifiers = list(self.sources) + names
        for source in self.sources.values():
            identifiers += source.columns
            if len(source.columns) != len(set(source.columns)):
                raise ValueError("Duplicate source columns")
        reserved = {"hk", "load_dts", "record_source", "hashdiff"}
        for e in self.entities:
            identifiers += e.keys + e.attributes + [r.name for r in e.roles]
            if e.source not in self.sources:
                raise ValueError(f"Unknown source: {e.source}")
            cols = set(self.sources[e.source].columns)
            used = e.keys + e.attributes + [c for r in e.roles for c in r.columns]
            if not set(used) <= cols:
                raise ValueError(f"Unknown columns in {e.name}")
            if len(used) != len(set(used)) and e.kind != "link":
                raise ValueError(f"Duplicate columns in {e.name}")
            if reserved.intersection(e.keys + e.attributes):
                raise ValueError("Payload/key column collides with technical column")
            if e.kind == "hub" and (not e.keys or e.roles or e.parent or e.attributes):
                raise ValueError("Hub requires keys only")
            if e.kind == "link":
                if len(e.roles) < 2 or e.keys or e.parent or e.attributes:
                    raise ValueError("Link requires at least two roles only")
                if len({r.name for r in e.roles}) != len(e.roles):
                    raise ValueError("Duplicate link role")
                for r in e.roles:
                    h = by_name.get(r.hub)
                    if h is None or h.kind != "hub" or len(r.columns) != len(h.keys):
                        raise ValueError("Link roles must map all hub keys in order")
            if e.kind == "satellite":
                p = by_name.get(e.parent)
                if p is None or p.kind == "satellite" or p.source != e.source:
                    raise ValueError("Satellite requires a hub/link parent with the same source")
                if not e.attributes or e.keys or e.roles:
                    raise ValueError("Satellite requires attributes only")
        if any(not re.fullmatch(r"[a-z][a-z0-9_]*", n) or keyword.iskeyword(n) for n in identifiers):
            raise ValueError("Identifiers must be lowercase Python/SQL identifiers")
        return self


def read_model(path: str | Path) -> Model:
    return Model.model_validate_json(Path(path).read_text())


def import_hackolade(export: dict, mapping: dict) -> Model:
    """Adapt an explicit entities array. Never infer DV semantics from table names.

    mapping contains canonical sources and entities keyed by exported entity name.
    Each entity mapping supplies kind/source/keys/roles/parent/attributes.
    Native export layout varies by target: normalize its entity array first.
    """
    entities = export.get("entities")
    if not isinstance(entities, list):
        raise ValueError("Expected normalized Hackolade export with an entities array")
    names = [e["name"] for e in entities]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate exported entity names")
    missing = set(mapping["entities"]) - set(names)
    if missing:
        raise ValueError(f"Mapped entities missing from export: {sorted(missing)}")
    result = []
    for e in entities:
        if e["name"] in mapping["entities"]:
            result.append({"name": e["name"], **mapping["entities"][e["name"]]})
    return Model(sources=mapping["sources"], entities=result)
