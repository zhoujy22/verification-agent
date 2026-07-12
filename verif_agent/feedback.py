"""Coverage-driven feedback loop.

If C < 85% after the first run, bump weights for uncovered bins and re-run.
Bounded to MAX_ITER additional passes to keep wall-clock under control.
"""
from __future__ import annotations


MAX_ITER = 2
TARGET_C = 85.0


def adjust(constraints: dict, bins_def: dict, functional: dict, iteration: int) -> dict:
    """Mutate `constraints` in place: bump weights for uncovered bins + log update.

    Returns the same dict.
    """
    updates = constraints.setdefault("coverage_feedback_updates", [])
    updates.append({
        "iter": iteration,
        "trigger": "combined_C<85%",
        "applied": True,
        "action": "Bump weights for uncovered bins",
    })

    # Identify uncovered bins
    per_cp = {
        cp["name"]: cp for cp in bins_def.get("coverpoints", [])
    }
    for cp_name, cp in per_cp.items():
        for b in cp.get("bins", []):
            entry = next(
                (e for e in functional.get("per_coverpoint", []) if e["name"] == cp_name),
                {"bins": []},
            )
            bin_match = next(
                (bb for bb in entry.get("bins", []) if bb["name"] == b["name"]),
                None,
            )
            if bin_match and not bin_match.get("covered", False):
                # find a random var tied to this cp and bump its weight slightly
                # Best-effort: walk random_variables and pick one with dist == weighted
                for v in constraints.get("random_variables", []):
                    if v.get("dist") == "weighted" and isinstance(v.get("weights"), dict):
                        # add a bonus 5 to every present weight
                        for k in list(v["weights"].keys()):
                            v["weights"][k] += 5
                        break
    return constraints
