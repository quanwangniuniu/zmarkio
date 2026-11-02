# Org customization registry

The registry is the single list of modules, surface types and surfaces that org and project
configuration can refer to. It is code, not database rows. Django validates it on startup and
refuses to boot with `ImproperlyConfigured` if a key is duplicated or malformed.

The frontend reads it from `GET /api/org-customization/registry/`, so dropdowns and settings
screens are built from this list instead of hardcoded options.

## Entities

| Entity | File | Fields |
|---|---|---|
| `ModuleSpec` | `modules.py` | `key`, `label`, `default_visible`, `default_order`, `route_prefixes` |
| `SurfaceTypeSpec` | `surfaces.py` (`SURFACE_TYPES`) | `key`, `label` |
| `SurfaceSpec` | `surfaces.py` (`SURFACES`) | `key`, `label`, `default_visible`, `default_order`, `route_prefixes`, `parent` |

Read them through `get_module_registry()`, `get_surface_type_registry()` and
`get_surface_registry()`. Each returns a dict of key to spec, in declaration order.

## Naming spec

Module and surface type keys are a single `snake_case` word: `[a-z][a-z0-9_]*`.

Surface keys have exactly three parts:

```
<module>.<type>.<name>
```

- `<module>` must be a key in `MODULES`.
- `<type>` must be a key in `SURFACE_TYPES`: `group`, `item`, `tab`, `card`, `panel`,
  `provider`, `filter`, `section`.
- `<name>` is `snake_case`. Existing ids with hyphens are converted (`my-actions` becomes
  `my_actions`).

Examples:

```
tasks.tab.gantt
overview.card.meetings
integrations.provider.slack
shell.group.manage
shell.item.tasks
```

A nested surface keeps a three-part key and points to its parent:

```python
SurfaceSpec(key="shell.item.ads_draft", label="Ads Draft")
SurfaceSpec(key="shell.item.facebook_meta", label="Facebook Meta", parent="shell.item.ads_draft")
```

The parent must be registered, and parents cannot form a cycle.

A new surface type is one entry in `SURFACE_TYPES`, for example
`SurfaceTypeSpec(key="usermenu", label="User menu")`. It must be mirrored in the frontend.

Keys are a contract with the frontend. Do not rename a key once it ships; add a new one instead.

## `route_prefixes`

Backend API paths owned by a module or surface, used by backend enforcement. Each entry starts
with `/api/` and ends with `/`. Path parameters are written the Django way, for example
`/api/projects/<project_id>/meetings/`.

Some modules are not fully covered by Django: `decisions` is served by `services/decision-service`
and `variations_studio` by `variations-studio-api` (both routed by nginx), and `timeline` has no
API of its own because it reads `/api/tasks/`.

## Adding a module: the 4 files to touch

1. `backend/org_customization/registry/modules.py`: add a `ModuleSpec` with its `route_prefixes`.
   Its position in the list sets `default_order`.
2. `backend/org_customization/registry/surfaces.py`: add the module's surfaces to `SURFACES`.
3. `frontend/src/lib/orgCustomization/keys.ts`: add the key to `MODULE_KEYS`, spelled exactly the
   same.
4. `frontend/src/lib/orgCustomization/mocks/fixtures.ts`: add the module to the registry fixture.

Also add the key to `MODULE_KEYS` in `backend/org_customization/tests/test_registry.py` and
update the frontend snapshot with `npx jest -u`. Both pin the full module list, so a key missing or
misspelled on either side fails CI.
