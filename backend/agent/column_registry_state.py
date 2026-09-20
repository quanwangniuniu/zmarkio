"""Guarded, process-local schema state, shared across registry module reloads."""
import os
import re
from collections.abc import Mapping
from threading import RLock
from types import MappingProxyType

TEST_MODE_ENV = 'AGENT_COLUMN_REGISTRY_TEST_MODE'


def test_mode_enabled():
    return os.environ.get(TEST_MODE_ENV, '').strip().lower() in {'1', 'true', 'yes', 'on'}


def normalise(name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError('Column names and aliases must be non-empty strings.')
    return re.sub(r'[\s_]+', ' ', name.strip().lower())


class ColumnRegistryCollisionError(ValueError):
    code = 'COLUMN_REGISTRY_COLLISION'

    def __init__(self, collisions):
        self.collisions = tuple(collisions)
        super().__init__('Column registry name collision: ' + '; '.join(
            f"{item['name']!r} ({', '.join(item['owners'])})" for item in collisions
        ))


def _claim(index, name, owner, scope):
    key = normalise(name)
    if key in index and index[key] != owner and not test_mode_enabled():
        raise ColumnRegistryCollisionError([{
            'name': key, 'owners': [f'{scope}.{index[key]}', f'{scope}.{owner}'],
        }])
    index[key] = owner


def build_columns(definitions, scope):
    """Consume pairs before dictionary construction can erase duplicate keys."""
    columns, index, canonical_names = {}, {}, {}
    for name, spec in definitions:
        key = normalise(name)
        if key in canonical_names and not test_mode_enabled():
            raise ColumnRegistryCollisionError([{
                'name': key,
                'owners': [f'{scope}.{canonical_names[key]}', f'{scope}.{name} (new registration)'],
            }])
        if not isinstance(spec, Mapping):
            raise ValueError(f'{scope}.{name}: column specification must be an object.')
        aliases = spec.get('aliases', [])
        if not isinstance(aliases, (list, tuple)):
            raise ValueError(f'{scope}.{name}: aliases must be a list.')
        canonical_names[key] = name
        for alias in [name, *aliases]:
            _claim(index, alias, name, scope)
        columns[name] = {**spec, 'aliases': list(aliases), 'category': spec.get('category', 'unknown')}
    # Rebuild from final definitions so test-only replacements drop old aliases.
    index = {}
    for name, spec in columns.items():
        for alias in [name, *spec['aliases']]:
            _claim(index, alias, name, scope)
    return columns, index


def validate_column_definitions(definitions, scope='template'):
    if not isinstance(definitions, list):
        raise ValueError('Column definitions must be a list.')
    pairs = []
    for spec in definitions:
        if not isinstance(spec, dict):
            raise ValueError('Each column definition must be an object.')
        pairs.append((spec.get('canonical_name'), spec))
    return build_columns(pairs, scope)


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


class ColumnRegistry(Mapping):
    """Read-only definitions; all writes publish validated snapshots under a lock."""

    def __init__(self):
        self._lock = RLock()
        self._state = (MappingProxyType({}), MappingProxyType({}))
        self._error = None
        self._initialized = False

    def __getitem__(self, key):
        return self._state[0][key]

    def __iter__(self):
        return iter(self._state[0])

    def __len__(self):
        return len(self._state[0])

    def snapshot(self):
        with self._lock:
            self.check()
            return self._state

    def check(self):
        with self._lock:
            if self._error and not test_mode_enabled():
                raise self._error

    def _publish(self, entries):
        schemas, indexes = {}, {}
        for key, schema in entries:
            if not isinstance(key, str) or not key.strip():
                raise ValueError('Schema keys must be non-empty strings.')
            if key in schemas and not test_mode_enabled():
                raise ColumnRegistryCollisionError([{'name': key, 'owners': [key, f'{key} (new registration)']}])
            if not isinstance(schema, Mapping) or not isinstance(schema.get('name'), str) or 'columns' not in schema:
                raise ValueError(f'{key}: schema requires a name and columns.')
            definitions = schema['columns']
            pairs = definitions.items() if isinstance(definitions, Mapping) else definitions
            columns, index = build_columns(pairs, key)
            schemas[key] = {**schema, 'columns': columns}
            indexes[key] = index
        # Both definitions and indexes are constructed before publishing either.
        self._state = (_freeze(schemas), _freeze(indexes))

    def initialize(self, entries):
        with self._lock:
            if self._initialized:
                return
            try:
                self._publish(entries)
            except ColumnRegistryCollisionError as exc:
                self._error = exc
            self._initialized = True

    def register_schema(self, key, schema):
        with self._lock:
            try:
                self._publish([*self.items(), (key, schema)])
            except ColumnRegistryCollisionError as exc:
                self._error = exc
                raise
            return self[key]

    def register_column(self, key, name, spec):
        with self._lock:
            schema = self[key]
            candidate = {**schema, 'columns': [*schema['columns'].items(), (name, spec)]}
            try:
                self._publish((k, candidate if k == key else v) for k, v in self.items())
            except ColumnRegistryCollisionError as exc:
                self._error = exc
                raise
            return self[key]['columns'][name]


# Keep definitions and failure state stable if column_registry itself is reloaded.
registry = ColumnRegistry()
