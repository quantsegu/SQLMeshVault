"""Declarative stage/hub/link/sat contract using familiar AutomateDV arguments."""
import keyword
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

IDENTIFIER = re.compile(r'[A-Za-z][A-Za-z0-9_]*\Z')


def identifier(value):
    if not IDENTIFIER.fullmatch(value) or keyword.iskeyword(value):
        raise ValueError(f'Invalid identifier: {value!r}')
    return value


def unique(values, label):
    if len(values) != len({v.lower() for v in values}):
        raise ValueError(f'Duplicate/case-colliding {label}')
    for value in values:
        identifier(value)


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Hashing(Strict):
    algorithm: Literal['MD5', 'SHA256'] = 'MD5'
    casing: Literal['UPPER', 'DISABLED'] = 'UPPER'
    concat_string: str = Field(default='||', min_length=1)
    null_placeholder_string: str = Field(default='^^', min_length=1)
    storage: Literal['binary', 'hex'] = 'binary'


class Derived(Strict):
    column: str | None = None
    literal: str | None = None
    load_timestamp: bool = False
    transform: Literal['identity', 'trim', 'upper', 'lower'] = 'identity'

    @model_validator(mode='after')
    def check(self):
        if sum([self.column is not None, self.literal is not None, self.load_timestamp]) != 1:
            raise ValueError('Derived column needs exactly one of column/literal/load_timestamp')
        if self.column:
            identifier(self.column)
        return self


class HashColumn(Strict):
    columns: str | list[str]
    is_hashdiff: bool = False

    def names(self):
        return [self.columns] if isinstance(self.columns, str) else self.columns

    @model_validator(mode='after')
    def check(self):
        if not self.names():
            raise ValueError('Empty hash columns')
        unique(self.names(), 'hash inputs')
        return self


class Stage(Strict):
    path: str
    columns: list[str]
    derived_columns: dict[str, Derived] = Field(default_factory=dict)
    null_columns: dict[str, str] = Field(default_factory=dict)
    hashed_columns: dict[str, HashColumn]

    def available(self):
        return self.columns + list(self.derived_columns) + list(self.hashed_columns)

    @model_validator(mode='after')
    def check(self):
        unique(self.available(), 'stage columns')
        if not self.columns:
            raise ValueError('Stage columns cannot be empty')
        for d in self.derived_columns.values():
            if d.column and d.column not in self.columns:
                raise ValueError('Derived columns must reference raw input columns')
        available = self.columns + list(self.derived_columns)
        if not set(self.null_columns) <= set(available):
            raise ValueError('Null replacement refers to unknown column')
        for h in self.hashed_columns.values():
            if not set(h.names()) <= set(available):
                raise ValueError('Hash refers to unknown/unavailable column')
        return self


class Table(Strict):
    name: str
    kind: Literal['hub', 'link', 'sat']
    source_model: str | list[str]
    src_pk: str
    src_ldts: str
    src_source: str
    src_nk: list[str] = Field(default_factory=list)
    src_fk: list[str] = Field(default_factory=list)
    src_hashdiff: str | None = None
    src_payload: list[str] = Field(default_factory=list)
    src_eff: str | None = None
    src_extra_columns: list[str] = Field(default_factory=list)
    # Explicit relationships replace ref()-based inference.
    references: dict[str, str] = Field(default_factory=dict)
    parent: str | None = None
    # Reject unknown late changes, or explicitly opt into AutomateDV-style filtering.
    late_arriving: Literal['error', 'ignore'] = 'error'

    def stages(self):
        return [self.source_model] if isinstance(self.source_model, str) else self.source_model

    def output_columns(self):
        return ([self.src_pk] + self.src_nk + self.src_fk +
                ([self.src_hashdiff] if self.src_hashdiff else []) + self.src_payload +
                ([self.src_eff] if self.src_eff else []) + self.src_extra_columns +
                [self.src_ldts, self.src_source])

    def dependencies(self):
        return list(dict.fromkeys(self.references.values())) if self.kind == 'link' else ([self.parent] if self.parent else [])


class Project(Strict):
    version: Literal[2] = 2
    name: str
    hashing: Hashing = Field(default_factory=Hashing)
    stages: dict[str, Stage]
    models: list[Table]

    @model_validator(mode='after')
    def check(self):
        identifier(self.name)
        unique(list(self.stages), 'stage names')
        unique([m.name for m in self.models], 'model names')
        if not self.models:
            raise ValueError('At least one model required')
        by_name = {m.name: m for m in self.models}
        for m in self.models:
            unique(m.output_columns(), f'output columns for {m.name}')
            if not m.stages() or len(set(m.stages())) != len(m.stages()):
                raise ValueError('Source models must be nonempty and unique')
            for s in m.stages():
                if s not in self.stages:
                    raise ValueError(f'Unknown stage {s}')
                stage = self.stages[s]
                if not set(m.output_columns()) <= set(stage.available()):
                    raise ValueError(f'{m.name}: missing stage columns in {s}')
                for col in [m.src_pk] + m.src_fk:
                    if col not in stage.hashed_columns or stage.hashed_columns[col].is_hashdiff:
                        raise ValueError(f'{col}: primary/foreign keys must be declared key hashes')
                if m.src_hashdiff:
                    if m.src_hashdiff not in stage.hashed_columns or not stage.hashed_columns[m.src_hashdiff].is_hashdiff:
                        raise ValueError('Satellite hashdiff must have is_hashdiff=true')
                technical = [m.src_ldts, m.src_source] + ([m.src_eff] if m.src_eff else [])
                if any(c in stage.hashed_columns for c in technical + m.src_nk + m.src_payload + m.src_extra_columns):
                    raise ValueError('Only key/hashdiff output columns may be hashes')
            if m.kind == 'hub':
                if not m.src_nk or m.src_fk or m.src_hashdiff or m.src_payload or m.src_eff or m.parent or m.references:
                    raise ValueError('Hub requires src_nk and no link/satellite fields')
            elif m.kind == 'link':
                if len(m.src_fk) < 2 or set(m.references) != set(m.src_fk):
                    raise ValueError('Link requires at least two src_fk and references for each')
                if m.src_nk or m.src_hashdiff or m.src_payload or m.src_eff or m.parent:
                    raise ValueError('Link cannot contain hub/satellite fields')
                for parent in m.references.values():
                    if parent not in by_name or by_name[parent].kind != 'hub':
                        raise ValueError('Link references must name hubs')
            else:
                if len(m.stages()) != 1 or not m.src_hashdiff or not m.src_payload:
                    raise ValueError('Satellite requires one source, hashdiff and payload')
                if m.src_nk or m.src_fk or m.references:
                    raise ValueError('Satellite cannot contain hub/link fields')
                if m.parent not in by_name or by_name[m.parent].kind == 'sat':
                    raise ValueError('Satellite requires a hub/link parent')
        return self
