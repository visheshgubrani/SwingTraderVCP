# Personal app UI guide (VCP Trader terminal)

Reference artifact: `docs/design/vcp-trader-dashboard.html` (original OpenDesign look).
Theme tokens and terminal classes live in `client/src/index.css`.

## Layout

- One navigation: the labelled module tabs docked at the bottom (`StageNav`). Each
  tab reopens its module at the last URL you left it on (symbol + list, scanner
  filters, proposal tab…), remembered per session in `App.tsx`.
- The list sidebar (`features/chart/ChartSidebar.tsx`: Scan · watchlists 1–4) belongs
  to the Chart route only; table pages get the full width.
- Pages are `<section className="view h-full">` → `.vhead` (h2 + `.vmeta`, actions in
  `.vhead-right`) → optional `.ptabs` sub-tabs → scrolling body.

## State belongs in the URL

Use `useUrlState` (`src/lib/useUrlState.ts`) for tabs, filters, sort, paging and the
selected row/drawer, so navigating away and back (or back/forward) restores the view.
Filter changes replace the history entry; opening a detail drawer pushes one so Back
closes it. The chart walks the sidebar list with ↑/↓ or J/K (`useChartListNav`); the
scan list's order is shared with the Scanner through `features/screener/scanList.ts`.

## Building blocks

- Panels and tiles: `Panel`, `Stat`, `EmptyState`, `SectionLabel` from
  `src/components/terminal/panel.tsx`. Do not hand-roll `rounded-lg border bg-card` boxes.
- Chips: `StatusChip`, `GradeChip` (`components/terminal/bits.tsx`); proposal-specific
  chips in `features/proposals/proposalUi.tsx`.
- Tables: `.tbl` inside `.tscroll` (or a `Panel flush`); `.srt` headers for sortable
  columns, `tr.clickable` / `tr.sel` for openable rows.
- Primitives: shadcn on `@base-ui/react` — `Button`, `Sheet` (detail drawers),
  `AlertDialog` (every state-changing control asks for confirmation + reason),
  `Tooltip` (wrap icon buttons), `Tabs`, `Skeleton`.

## Formatting (`src/lib/format.ts`)

`fmtMoney` (₹5,08,080.00 · -₹2,000.00, `sign` for P&L), `fmtNum`, `fmtPct`, `fmtR`,
`fmtDate` / `fmtDateTime` / `fmtTime` (IST), `fmtRelative`, `toNum` for API decimal
strings. No local `toFixed`/`toLocaleString` money formatting.

## Type and colour

- Nothing below 11px: `text-2xs` (11px) for labels/chips, `text-xs` (12px) for table and
  body copy, `text-sm` for prose. No `text-[9px]`-style arbitrary sizes.
- Colours come from tokens only: `text-ok/ko/wa/accent`, `bg-ok-soft/ko-soft/wa-soft/
  accent-soft`, `bg-surface`, `bg-field`, `border-border(-soft)`, `text-fg2`,
  `text-muted-text`. No raw Tailwind palette (`green-400`, …) or hex classes; canvas
  chart options are the only place hex values remain.

## Money-path rules in the UI (AGENTS.md)

The client displays server values and sends decisions; it never computes trading
numbers. Proposal detail shows Python's levels and R values as returned, offers only
approve/reject with the expected hash (+ optional notes), shows confidence as display
only, and never derives a share quantity.

## Checks

`pnpm exec tsc -b && pnpm lint && pnpm test` from `client/`.
