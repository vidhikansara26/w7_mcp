"""Score committed scenario traces against the graded decisions."""

from __future__ import annotations

from pathlib import Path

from run_scenarios import SCENARIOS

ROOT = Path(__file__).resolve().parent
SCENARIO_DIR = ROOT / "traces" / "scenarios"


def decision_in_trace(text: str) -> str | None:
    """Last Final Decision line. Draft lines use a different prefix."""
    found: str | None = None
    for line in text.splitlines():
        if line.startswith("Final Decision:"):
            found = line.removeprefix("Final Decision:").strip().lower()
    return found


def score_cases(
    found: dict[str, str | None],
    gold: list[dict] | None = None,
) -> tuple[int, list[str]]:
    """Compare scenario id → decision against the graded expectations."""
    cases = gold if gold is not None else SCENARIOS
    matched = 0
    misses: list[str] = []
    for scenario in cases:
        got = found.get(scenario["id"])
        if got == scenario["expected"]:
            matched += 1
            continue
        misses.append(
            scenario["id"] + ": expected " + scenario["expected"] + ", got " + str(got)
        )
    return matched, misses


def passes(matched: int, total: int) -> bool:
    """Graded traces pass only when every case matches."""
    return total > 0 and matched == total


def score_directory(directory: Path | None = None) -> tuple[int, list[str]]:
    folder = directory or SCENARIO_DIR
    found: dict[str, str | None] = {}
    for scenario in SCENARIOS:
        path = folder / f"{scenario['id']}.txt"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        found[scenario["id"]] = decision_in_trace(text)
    return score_cases(found)


def main() -> int:
    matched, misses = score_directory()
    total = len(SCENARIOS)
    print(f"{matched}/{total}")
    for miss in misses:
        print(miss)
    if passes(matched, total):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
