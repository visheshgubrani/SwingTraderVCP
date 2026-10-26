#!/usr/bin/env python3
"""Load a git ref's P10 prompt, schema and geometry for faithful replays.

Evaluation only. A replay of a past prompt version must send exactly what
that version sent: its own system prompt, its own response-schema field
order, and a candidate summary built by its own geometry module. Importing
only the old proposal_generator is not enough, because it pulls the current
working-tree schema and geometry through ``app.*`` imports.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]


def load_ref_module(ref: str, rel_path: str, name: str) -> Any:
    """Import ``rel_path`` as it exists at git ``ref`` under module ``name``."""
    text = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{ref}:{rel_path}"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    target = REPO / ".git" / f"{name}.py"
    target.write_text(text)
    spec = importlib.util.spec_from_file_location(name, target)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RefP10:
    """A git ref's proposal generator, response schema and geometry, wired together."""

    def __init__(self, ref: str) -> None:
        tag = "".join(ch if ch.isalnum() else "_" for ch in ref)
        self.ref = ref
        self.geometry = load_ref_module(
            ref, "server/app/domain/p10_geometry.py", f"p10_geo_{tag}"
        )
        schemas = load_ref_module(
            ref, "server/app/schemas/proposals.py", f"p10_schemas_{tag}"
        )
        self.generator = load_ref_module(
            ref, "server/app/services/proposal_generator.py", f"p10_gen_{tag}"
        )
        # The ref generator imported the working-tree schema; replay the ref's.
        self.generator.GeminiVcpProposalOutput = schemas.GeminiVcpProposalOutput

    @property
    def prompt_version(self) -> str:
        return str(self.generator.PROMPT_VERSION)

    def candidate_summary(self, candle_rows: list[dict], tick: Decimal) -> str:
        """The summary text the ref's worker built for this frozen window."""
        geo = self.geometry
        candles = [
            geo.CandleData(
                open=float(r["o"]),
                high=float(r["h"]),
                low=float(r["l"]),
                close=float(r["c"]),
                volume=int(r["v"]),
                date=str(r["date"]),
            )
            for r in candle_rows
        ]
        annotations = geo.derive_chart_geometry(candles, tick_size=tick)
        if not hasattr(geo, "compute_structural_facts"):
            return geo.format_candidate_summary(annotations.contractions)
        facts = geo.compute_structural_facts(
            candles, annotations.contractions, tick_size=tick
        )
        # Gates v2+ send the folded candidates; earlier versions sent raw swings.
        candidates = (
            facts.candidates
            if hasattr(geo, "fold_contraction_candidates")
            else annotations.contractions
        )
        return geo.format_candidate_summary(candidates, facts=facts)

    def prompt_hash(self, candle_rows: list[dict], tick: Decimal) -> str:
        return self.generator.proposal_prompt_hash(
            tick_size=tick,
            candidate_summary=self.candidate_summary(candle_rows, tick),
        )
