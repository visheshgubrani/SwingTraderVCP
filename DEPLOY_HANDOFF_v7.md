# P10 audit v7 — deploy handoff (runbook)

Everything below is implemented, unit-tested (**638 passed**, incl. new
structural-gate/no-lookahead/schema-consistency/lifecycle tests) and
evaluated on the 75 unique deployed charts. No DB migration is needed —
only JSONB payloads and version strings change.

## 1. Change inventory (single commit)

| Area | Files | Effect |
|---|---|---|
| Geometry + gates | `server/app/domain/p10_geometry.py` | StructuralFacts on RAW geometry; structural gates (maturity 15s → forming, undercut > max(tick, 0.10×ATR), tightening ratio ≤0.90 or ≥0.75pp, pullback distribution, climax-fade); facts-aware candidate summary |
| Contract | `server/app/schemas/proposals.py` | schema v7: `pattern_type`, `primary_reason`; consistency validator (valid coherent only; not_vcp ≠ immature/mature base) |
| Prompt/flow | `server/app/services/proposal_generator.py` | prompt v7, `p10_vcp_proposal_v7` / `gemini_vcp_proposal_output_v7` / `GEOMETRY_VERSION=p10_python_owned_levels_v6`; structural verdict enforcement; provider taxonomy (`proposal_provider_timeout|upstream_error|rate_limited`, `proposal_schema_inconsistent` retryable); readable rejection messages |
| Worker lifecycle | `server/app/workers/proposal_worker.py` | dispositions proposal/forming/invalid/existing/failed; hard-invalid skips provider; immature-valid → forming watch; `existing` no-op outcome; watch not blanket-closed |
| UI | `client/src/features/proposals/ProposalGenerationResults.tsx` | ledger Existing/Rejected counts consistent with new counters |
| Docs | `AGENTS.md` | §4/§5.1–5.5/§12.1 amended to v7 semantics |
| Tests | `server/tests/test_p10_structural_gates.py` (new), `test_p10_proposal_generator.py`, `test_p10_proposal_batch.py` | gate/schema/taxonomy/lifecycle coverage |

## 2. Deploy (no migrations)

Normal flow: commit → push to `main` (or `workflow_dispatch` "Build and
deploy") → Actions builds GHCR images and runs on the VPS:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod pull
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d postgres redis
docker compose -f docker-compose.prod.yml --env-file .env.prod run --rm --no-deps -T api python scripts/apply_pending_migrations.py   # no-op for v7
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
```

Verify after deploy:
- `docker ps` shows `swingtradervcp-proposal-worker-1` healthy on the new image.
- `server/.env` on the VPS: `VCP_VISION_MODEL=google/gemini-3.7-flash` unchanged (model stays flash).

## 3. First v7 batch — inspection run with auto-arm OFF

1. On the VPS, in `~/.env.prod` (deploy dir), set `PAPER_AUTO_ARM_PROPOSALS=false`,
   then `docker compose ... up -d proposal-worker` to recreate the worker with
   the new env (no other service needs a restart for this flag).
2. Let the next EOD scan/batch run (or trigger a manual batch) and inspect:
   - Generation ledger rows now carry readable deterministic reasons, e.g.
     `proposal_structural_forming: base has only 12 completed sessions (<15)`,
     `proposal_structural_invalid: lower low … undercuts the prior pullback floor`,
     `proposal_structural_invalid: pullbacks are not progressively shallower (flat / high-tight shelf shape)`.
   - `Existing` rows are separate from `Rejected`; rejected counts exclude them.
   - Structurally immature rows appear as `Partial` and land in the forming-watch
     list (developing) instead of `broken_down`.
   - Attempt audit rows record `prompt_version=p10_vcp_proposal_v7`,
     `schema_version=gemini_vcp_proposal_output_v7`,
     `geometry_version=p10_python_owned_levels_v6`, plus `structural_facts`
     and `structural_gate_details` in `error_details`/`geometry`.
3. Compare a few ledger charts against your own read (the seven regression
   anchors from the plan behave as: FLUOROCHEM/PPLPHARMA forming-watch,
   HONASA/NEULANDLAB/SYRMA/LALPATHLAB rejected with structural codes,
   TORNTPHARM-class valid only when geometry gates pass).
4. If clean: restore `PAPER_AUTO_ARM_PROPOSALS=true` and recreate the worker.
   If anything is wrong: rollback = revert the commit, redeploy (no data
   migration involved).

## 4. Notes

- Existing v6 proposals/positions are untouched (immutable rows keep v6
  versions); only future runs use v7. The three paper-armed rows from 09-02
  (FLUOROCHEM/HONASA/NEULANDLAB) remain governed by their existing approved
  versions until their D1 windows expire — review them in the UI if desired.
- Eval reference: `evidence/p10/FINAL_REPORT.md` (75-chart v6→v7 delta,
  flag/calibration deltas, anchor outcomes, cost/latency). Evidence dir is
  gitignored; `scripts/p10_reaudit_ab.py` runs were the only OpenRouter
  spend (~$0.93 total; the strong-model arm was dropped).
