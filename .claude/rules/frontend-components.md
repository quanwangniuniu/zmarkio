---
paths:
  - "frontend/**/*.{ts,tsx}"
---

# Frontend components, styling & types

Principle: **Tailwind + Radix/shadcn only.** Compose UIs from the `components/ui` primitives —
don't pull in another component library or hand-roll styled elements.

## Primitives (`components/ui`)

shadcn/ui (new-york style) + CVA. Canonical pattern (reference `src/components/ui/button.tsx`):

- `cva()` for variants, exposed via `VariantProps<typeof ...>`.
- `React.forwardRef`, `Slot` from `@radix-ui/react-slot` for an `asChild` prop, `cn()` from
  `@/lib/utils` to merge classes, set `displayName`, use a **named export**.

## App components

- Local `interface <Name>Props`; defaults set in destructuring (`type = 'warning'`); function
  components with `export default function`.
- Icons from `lucide-react`; toasts via `react-hot-toast`.
- Keep components under ~200–300 lines — split into feature components + hooks past that (see
  `.claude/rules/code-style.md`).

## Styling

- **Use Tailwind design tokens**: `bg-primary`, `text-muted-foreground`, `bg-background`,
  `border-border`, and the `brand.*` palette defined in `tailwind.config.js`. `darkMode` is
  class-based, so tokens (HSL CSS vars) give you dark mode for free.
- DON'T hardcode hex like `bg-[#3CCED7]` or `from-[#3CCED7] to-[#A6E661]` — this exists in older
  files and should not spread; it breaks theming/dark mode.

## Types (`src/types/<domain>.ts`)

- `interface` (PascalCase) for object shapes; **field names are `snake_case`, mirroring the DRF
  serializer** (`project_id`, `due_date`, `created_by`).
- Enums as string-literal unions (`status?: 'DRAFT' | 'SUBMITTED' | ...`).
- Cite the backend serializer / Jira ticket in a comment (`// aligned with TaskDetailSerializer
  (MED-240)`). Reference: `src/types/task.ts`.
- A request/response type used by one module may be colocated in its `*Api.ts`; shared domain
  types go in `src/types/`.

## Related rules

- `.claude/rules/frontend-architecture.md` (where components live),
  `.claude/rules/code-style.md` (naming table, `@/` alias, component size).
