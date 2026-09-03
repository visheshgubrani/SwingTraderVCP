# React UI migration port guide (OpenDesign "VCP Trader Core Terminal")

Reference artifact: `docs/design/vcp-trader-dashboard.html` (open in a browser for visuals).
Authoritative theme + component classes live in `client/src/index.css`:

- Design tokens are CSS vars on `:root` (`--bg`, `--surface`, `--surface-warm`, `--fg`,
  `--fg-2`, `--muted-text`, `--border`, `--border-soft`, `--accent`, `--accent-on`,
  `--success`, `--warn`, `--danger`, derived `--hl`, `--hl-2`, `--field`, `--rule`,
  `--mask`, soft tones `--acc-soft/--ok-soft/--ko-soft/--wa-soft`).
- Tailwind semantic colors keep shadcn names working: `bg-background`, `bg-card`,
  `bg-surface`, `bg-field`, `bg-hl`, `bg-accent-soft`, `bg-ok-soft`, `bg-ko-soft`,
  `bg-wa-soft`, `text-foreground`, `text-fg2`, `text-muted-foreground`,
  `text-muted-text`, `border-border`, `border-border-soft`, `text-ok`, `text-ko`,
  `text-wa`, `text-accent`, `bg-muted` (subtle surface), `ring-ring`.
- Ported component classes (use them, do not redefine): frame `.app .tb .tape .main
  .rail .watch .stage`; `.vhead .sub .vmeta .vhead-right .note-demo .netp .lbl .val`;
  buttons `.btn .btn-primary .btn-ghost .btn-line .ibtn .link-act(.danger)`;
  segmented `.seg (.seg-side) button.on/.on-buy/.on-sell/.dim`; chips `.chip(.chip-acc)
  .sc-chip{wait,fill,work,rej,off} .gchip .g-A/.g-B/.g-C/.g-D .qchip(.sc) .note-demo`;
  tables `.tscroll .tbl th/td .l .srt .symlink .rank .act .rowflash`; chart `.qbar
  .qsym .qpx .qmini .qchips .qside .ctool .tf .smatg(.c20/.c50/.c200/.on) .csep
  .cmeta .chartbox .cstat`; forms `.inp .stepper .step .grid2 .tk-*`; overlays `.ovl
  .ticket .toasts .toast(.ok,.warn,.bad,.info) .srch-pop`.
- Helpers: `src/lib/format.ts` (fmtNum/fmtAmount/fmtPct/toneCls…), terminal bits in
  `src/components/terminal/bits.tsx` (Seg, StatusChip w/ dot, GradeChip),
  toasts: `useToast()` from `src/components/terminal/toast.tsx`
  (`toast(tone, {title?, text?, mono?})`).
- Up/down semantic classes: `up` (green), `down` (red), `flat` (muted). Numbers in
  data tables are `font-mono tabular-nums` by default under `.tbl td`; use
  en-IN grouping via fmtNum (₹5,08,080 style). Fonts Inter + Roboto Mono.

RULES for page re-skins (visual-only migration):
1. Never change product behavior, API calls, TanStack keys, mutation flows,
   poll intervals, or exported prop/type contracts of shared components.
2. Never modify: `src/index.css`, `src/App.tsx`, `src/components/layout/*`,
   `src/components/terminal/*` (except adding a NEW small component),
   `src/features/chart/ChartWorkspace.tsx` + `TradingChart.tsx`,
   `src/features/watchlist/*`, `src/features/positions/PositionsTable.tsx`,
   `src/features/orders/OrderBookTable.tsx`, `src/features/tradebook/TradebookView.tsx`,
   `src/lib/*`, `src/features/dashboard/app-context.tsx`.
3. Structure each module page as a full-height column:
   `<section className="view h-full"> → <div className="vhead">(title h2 + span.sub +
   p.vmeta | right .vhead-right) → filters row → <div className="tscroll"><table
   className="tbl">…`; empty/error states as a centered muted paragraph row inside the
   table or a `.tscroll`-free padded block. Keep sticky headers native to `.tbl th`.
4. Replace old "card + banner" chrome with the design equivalents above. Preserve
   accessibility labels/titles, keyboard handlers and test-relevant text only if a
   test asserts it — when a visual copy change breaks a test, update the test to the
   new copy in the same change.
5. Verify with `npx tsc -b` (whole project) and run vitest for the files you touched
   (`pnpm exec vitest run src/features/<your-area>`) — all must pass. Do not run the
   dev server or backend.
6. Finish by reporting files changed + any copy/string changes you made.
