# org_customization registry — key contract and naming spec

This directory is the **contract layer** for org-level module/surface
customization (MED-453). It contains no tables and no business logic: the
registry lives in code, and the B1/B2 tables store per-organization /
per-project overrides keyed by the strings declared here. When a module is
removed from the registry, orphaned config rows are simply ignored by the
B4 resolver.

The public entry points are the collectors in `org_customization/registry`:

```python
from org_customization.registry import get_module_registry, get_surface_registry

get_module_registry()   # Mapping[str, ModuleSpec]  — the 23 frozen modules
get_surface_registry()  # Mapping[str, SurfaceSpec] — registered surfaces
```

Both validate on every call and run at Django startup (`apps.py.ready()`):
**duplicate keys and malformed key formats raise `ImproperlyConfigured` and
fail startup.**

---

## Key naming spec (FROZEN)

> Any change to this spec must be mirrored in A2 —
> `frontend/src/lib/orgCustomization/keys.ts` — and agreed with its owner.
> The two key lists must match **character for character**.

### Surface keys — `<module>.<type>.<name>`

```
nav.group.manage              nav.item.tasks       nav.item.ads_draft.facebook_meta
tasks.tab.gantt               overview.card.meetings
integrations.provider.slack   shell.usermenu.subscription
```

* Every segment is snake_case (`^[a-z][a-z0-9_]*$`).
* `<name>` may itself contain further dot-separated segments
  (`nav.item.ads_draft.facebook_meta` → name = `ads_draft.facebook_meta`).
* `<module>` must be one of the 23 module keys below, or the reserved
  `nav` namespace (global navigation surfaces). `nav.*` surfaces are owned
  by the `shell` module for config purposes: rows denormalize
  `module_key="shell"` (see `module_key_for_surface()`).
* `<type>` is fixed for now as: `group` / `item` / `tab` / `card` / `panel` /
  `provider` / `filter` / `section` — plus `usermenu`, which is not in the
  original eight but appears in the frozen example
  `shell.usermenu.subscription` and must stay registrable.

### Module keys

A module key is a single snake_case segment (`^[a-z][a-z0-9_]*$`, no dots).

---

## The 23 module keys (complete frozen list)

| # | key | label | default_order | route_prefixes (backend enforcement) |
|---|-----|-------|---------------|--------------------------------------|
| 1 | `overview` | Overview | 10 | `/api/dashboard/` |
| 2 | `tasks` | Tasks | 20 | `/api/tasks/` |
| 3 | `campaigns` | Campaigns | 30 | `/api/campaigns/` |
| 4 | `meta_ads` | Meta Ads | 40 | `/api/meta_ads/` |
| 5 | `decisions` | Decisions | 50 | `/api/decisions/` |
| 6 | `budget_pools` | Budget Pools | 60 | `/api/budgets/`, `/budgets/` |
| 7 | `spreadsheets` | Spreadsheets | 70 | `/api/spreadsheet/` |
| 8 | `variations_studio` | Variations Studio | 80 | `/api/ad_copy_variation/` |
| 9 | `ads_draft` | Ads Draft | 90 | `/api/facebook_meta/`, `/api/google_ads/`, `/api/tiktok/` |
| 10 | `email_draft` | Email Draft | 100 | `/api/klaviyo/`, `/api/mailchimp/` |
| 11 | `notion` | Notion | 110 | `/api/notion/` |
| 12 | `meetings` | Meetings | 120 | `/api/projects/{project_id}/meetings` |
| 13 | `calendar` | Calendar | 130 | `/api/calendars/`, `/api/events/` |
| 14 | `messages` | Messages | 140 | `/api/chat/` |
| 15 | `miro` | Miro | 150 | `/api/miro/` |
| 16 | `workflows` | Workflows | 160 | `/api/workflows/` |
| 17 | `timeline` | Timeline | 170 | `/api/tasks/gantt/` |
| 18 | `csm` | CSM | 180 | `/api/csm/` |
| 19 | `admin` | Admin | 190 | `/api/access_control/`, `/api/audit/`, `/api/policy/` |
| 20 | `integrations` | Integrations | 200 | `/api/slack/`, `/api/v1/zoom/`, `/api/v1/linear/`, `/api/google-calendar/`, `/api/google-docs/`, `/api/facebook_integration/` |
| 21 | `agent` | Agent | 210 | `/api/agent/` |
| 22 | `notifications` | Notifications | 220 | `/api/notifications/` |
| 23 | `shell` | Shell | 230 | `/api/stripe/`, `/users/` |

`route_prefixes` matching rules (backend enforcement):

* A prefix matches by **path-prefix**, with `{param}` matching exactly one
  path segment (e.g. `/api/projects/{project_id}/meetings`).
* When two modules' prefixes overlap (e.g. `tasks` `/api/tasks/` vs
  `timeline` `/api/tasks/gantt/`), **the most specific (longest) prefix
  wins**.
* Prefixes are the module's initial enforcement namespace. A module issue in
  M4 may extend its own list; changing another module's prefixes requires an
  agreement between owners.

---

## Adding a module — the 4 files to touch

1. **`backend/org_customization/registry/__init__.py`**
   Add a `ModuleSpec(...)` to `MODULE_SPECS` (the key is the module key;
   declare `route_prefixes`) and the module's `SurfaceSpec(...)` entries to
   `SURFACE_SPECS`. Duplicate keys or malformed keys will fail Django startup
   with `ImproperlyConfigured`.
2. **`frontend/src/lib/orgCustomization/keys.ts`**
   Mirror the exact keys in `MODULE_KEYS` / `SURFACE_KEYS` (`as const`, so
   the exported union types pick them up). Must match character for character.
3. **`frontend/public/msw/orgCustomization.fixtures.ts`**
   Cover the new module/surfaces in the MSW fixtures used by
   `GET /api/org-customization/effective/` and `/registry/` mocks.
4. **`backend/org_customization/tests/test_registry.py`**
   Extend the expected key lists / counts so the contract stays pinned by
   tests.

Surface keys must follow the naming spec above. The individual surfaces of a
module are registered by its module issue (M4); this README's frozen example
list and `SURFACE_SPECS` show the expected shape.

---

## Contract endpoint

`GET /api/org-customization/registry/` returns the registry as JSON
(`{"modules": [...], "surfaces": [...]}`). It is a read-only projection of
the code registry — the effective per-org config API (`/effective/`) lands
with B4 under the same `api/org-customization/` prefix.
