"""Shared VCP definition for every Gemini VCP surface.

The P10 proposal reader and the advisory vision validator embed the same
rubric so a disagreement between them reflects different evidence, never a
different definition of the pattern. Changing this text changes both prompt
hashes; bump both prompt versions with it.
"""

from __future__ import annotations

VCP_RUBRIC = """VCP definition (Minervini / SEPA), used to classify:
- Prior advance: the base forms after a clear Stage 2 advance (price above a rising 200-day average, 50 > 150 > 200 averages). A base that follows a decline or sits below the key averages is not a VCP.
- Base high: the base starts at the highest high before the pullbacks (the left side of the base).
- Contractions: successive pullbacks from flat-to-descending swing highs. Each significant pullback is shallower than the one before and the lows hold or rise. A dip inside a rally whose highs keep rising is NOT a contraction — it is part of the advance off the prior low.
- Depth: a first contraction of roughly 10-35% is typical. A deeper base needs more time and more contractions before it is mature.
- Right side: the final contraction must form in the upper part of the base, near the base high, so a breakout clears overhead supply. A pivot in the lower or middle part of the base, with prior highs stacked above it, is an incomplete right side (forming, not valid). A V-shaped recovery straight off the base low, with no contraction near the highs yet, is also forming.
- Volume: volume dries up as the base tightens and is quiet in the final pullback. Heavy-volume distribution inside the base — wide down days, a climax reversal at the base high, or a high-volume breakdown — is a red flag. It disqualifies the base unless later price action has clearly absorbed it (price recovered to near the base high on quiet volume).
- Price action: tight, orderly closes near the pivot. Wide, choppy or widening swings are not a VCP.
- Two genuine contractions are enough for a mature VCP when everything above holds. Being below the pivot before a breakout is expected and is not by itself a reason for forming."""
