---
paths:
  - "backend/**/*.py"
---

# Models & conventions

Modern-style models and serializers. When editing an existing file, match that file — but never
newly introduce the anti-patterns called out below.

## Models

- Inherit `core.models.TimeStampedModel` for `created_at` / `updated_at`.
- For slug-addressable resources use `core.slug_mixins.SluggedResourceModelMixin` on the model plus
  `SlugLookupViewSetMixin` on the viewset; a numeric pk then 404s.
- PKs are `BigAutoField` (the Django default) — do not switch a table's PK to a UUID. UUIDs are
  opaque **secondary** identifiers (e.g. `embed_key`), never the PK.
- **No soft-delete mixin.** Use an explicit `is_active` / `is_archived` boolean, toggled inside a
  service function or `perform_destroy` — not a custom delete manager.
- Enums are `models.TextChoices` nested in the model class.
- **Always** define `class Meta` (`ordering`, constraints, `indexes` as appropriate) and `__str__`.
- Org-scoped models must be registered in `core/tenant_config.py::get_tenant_models()` — see
  `.claude/rules/multi-tenancy.md` for ordering and the migrate step.

## Serializers

- Explicit `fields = [...]`. **Never `fields = '__all__'`** — it silently exposes new columns.
- Serializers validate shape and coerce types only. DON'T: call service functions, trigger signals,
  or perform side effects inside `.save()` / `.create()` / `.update()`.

## Naming

| Thing | Convention | Example |
| --- | --- | --- |
| Django app dir | `snake_case` | `budget_approval/`, `client_communication/` |
| Model | `PascalCase`, singular | `BudgetRequest`, `QueueAgent` |
| Serializer | `<Model>Serializer` (+ purpose suffix), explicit `fields` | `TicketFormCreateSerializer` |
| ViewSet / View | `<Resource>ViewSet` (router) / `<Action>View` (APIView) | `QueueViewSet`, `LoginView` |
| Service function | verb-first, module-level in `services.py` | `reorder_work_types` |
| URL name | kebab-case | `name='project-queues'` |

## Related rules

- Layering (thin views → services → serializers), service-function conventions, and how/when to split
  `services.py`: `.claude/rules/architecture.md`.
- REST/URL/auth conventions for the surrounding views: `.claude/rules/api-conventions.md`.
