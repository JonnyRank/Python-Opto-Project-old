# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Persona & audience

From `project_rules.md`: act as a Principal Python Architect (data ingestion, analysis, optimization). Prioritize clean idiomatic Python, performance, and robust error handling. The user reads and understands code but does not write it — explain changes in terms of behavior and trade-offs, not line-by-line diffs.

## What this is

Standalone DraftKings DFS lineup optimizers. Each `.py` file at the repo root is a self-contained script — there is no package, no shared module, no test suite, and no build step. Common logic (data cleaning, diversity loop, table printing) is **duplicated across scripts by design**; a fix in one does not propagate, so when changing shared-looking behavior, decide explicitly whether the sibling scripts need the same change.

| Script | Sport / format | Roster | Solver | Input |
| --- | --- | --- | --- | --- |
| `NFL-Multi-Opto-v2.0.py` | NFL Classic, N lineups | 9 (QB/2RB/3WR/TE/FLEX/DST) | CBC (`PULP_CBC_CMD`) | CSV path as argv |
| `NFL-Single-Opto.py` | NFL Classic, 1 lineup | 9 | CBC | CSV path as argv |
| `NFL-SD-Multi-Opto-v1.0.py` | NFL Showdown (Captain Mode), N lineups | 6 (1 CPT + 5 FLEX) | HiGHS (`pulp.HiGHS`, needs `highspy`) | CSV path as argv |
| `NBA-Multi-Opto-v1.0.py` | NBA, N lineups | 8 | CBC | auto-globbed files |
| `NBA-Single-Opto-v1.0.py` | NBA, 1 lineup | 8 | CBC | auto-globbed files |

The NFL scripts are the modern generation: module docstring, typed helpers, `argparse`, `load_player_data()` / `main()` structure, everything wrapped in one `try/except` in `main()`. The NBA scripts are the older generation: top-level procedural code that runs at import, module-level config constants (`TARGET_DIRECTORY`, `NUMBER_OF_LINEUPS`, `MIN_UNIQUES`), and input files discovered by glob (`DKEntries*.csv`, `NBA-Projs-*.csv`) instead of CLI args. Follow the NFL style for new work.

## Running

```bash
venv/Scripts/python.exe NFL-Multi-Opto-v2.0.py "C:\path\to\projections.csv" -n 5 -u 2 -e -l "Josh Allen" -s -ndo
venv/Scripts/python.exe NFL-SD-Multi-Opto-v1.0.py "C:\path\to\showdown.csv" -n 5 -u 2 -e -l "Drake Maye:CPT" -ms 49800
venv/Scripts/python.exe NBA-Multi-Opto-v1.0.py   # no args; edit the constants at the top of the file
```

Local env is a plain `venv/` (Python 3.13) plus `requirements.txt` (`pandas`, `pulp`, `highspy`). There is no lint or test command. Verification means running a script against a real projections CSV and reading the printed lineups.

Note: `.claude/hooks/session-start.sh` bootstraps with `uv sync` and a `.python-version` pin — neither `pyproject.toml` nor `.python-version` exists here, so that hook only matters if the repo is migrated to uv (it is gated on `CLAUDE_CODE_REMOTE=true` and no-ops locally).

### CLI flags (NFL scripts)

`filepath` (required), `-n/--num-lineups`, `-u/--min-uniques`, `-e/--export`, `-l/--lock`, `-x/--exclude`, `-s/--stack N` (QB + N WR/TE same team), `-srb/--stack-rb`, `-te/--max-te`, `-ndo/--no-dst-opp`, `-ms/--max-salary` (Showdown only), `-ceiling/--c` and `-projceiling/--pj` (multi-lineup scripts only; mutually exclusive).

Showdown specifics: `-l`/`-x` accept an optional slot suffix (`"Drake Maye:CPT"`, `"Sam Darnold:FLEX"`); `-u` counts uniqueness by roster **spot**, so re-using six players with a different Captain is two uniques; `-s`, `-srb`, `-te`, `-ndo` do not apply.

Preserve the argument-reference comment block in each script's docstring — the user relies on it as the CLI cheat sheet:

```python
# Means: python <script> <projections file> -n <number of lineups> -u <min uniques> -e <export to CSV>
```

## Optimization model (the part worth knowing before editing)

All scripts share one pattern: **build the PuLP problem once, then solve it repeatedly, appending a diversity constraint after each solve.** Constraints are never rebuilt per lineup — the same `prob` object accumulates cuts.

- Objective target (NFL multi-lineup scripts only): `OPTIMIZATION_TARGETS` maps a target key to `(label, projection weight, ceiling weight)` and `target_value()` folds those weights into one per-player coefficient, so switching targets changes only the objective — never a constraint. Default is projection-only (`1.0, 0.0`), `-ceiling` is `0.0, 1.0`, `-projceiling` is `0.5, 0.5`. Showdown evaluates the same weights against `CptProjection`/`CptCeiling` for the Captain var and `Projection`/`Ceiling` for the FLEX var. `validate_target_data()` raises when a ceiling-weighted target meets a missing or all-zero `Ceiling` column. The single-lineup and NBA scripts do not carry this — adding it there means porting all four pieces (constants, the two helpers, the argparse group, the objective).

- Classic NFL: one binary var per player. Roster constraints are `QB == 1`, `RB >= 2`, `WR >= 3`, `TE >= 1`, `DST == 1`, `FLEX-eligible == 7`, total `== 9` — the FLEX slot is expressed as that count identity rather than a separate variable. `game_id` is a `frozenset({team, opp})` so both rows of a game map to one id; linking vars enforce "at least two games".
- Showdown: **two** binary vars per player (`cpt_vars[i]`, `flex_vars[i]`) with `cpt + flex <= 1` per player, `sum(cpt) == 1`, `sum(flex) == 5`, and `>= 1` rostered player from each of the two teams. Captain salary/projection come from the file's `CPT Salary` / `CPT Proj` columns when present, else derived at 1.5x.
- Diversity: `sum(vars of the just-solved lineup) <= ROSTER_SIZE - min_uniques`. Showdown's version is slot-aware — it sums `cpt_vars[captain]` plus the five `flex_vars`, which is why promoting a FLEX to Captain counts as two uniques.
- Display slot assignment is post-hoc, not part of the model: `_assign_flex_positions()` fills RB/WR/TE slots by descending salary and drops the leftover FLEX-eligible player into FLEX.
- A non-`Optimal` status breaks the loop; lineup #1 failing means the base constraints are infeasible, later failures just mean the pool is exhausted.

## Input CSV expectations

Classic NFL: `ID`, `Player`, `Position`, `Team`, `Opp`, `Salary`, `Proj`→`Projection`, `Own`→`Ownership`, `Ceiling` (optional; cleaned like the other numerics and defaulted to 0 with a note, so only the ceiling-weighted targets actually require it). `Opp` may be `"@BUF"` or `"BUF"`. Salary strings like `"$6,000 "` and ownership like `"11.70%"` are stripped to numerics; rows missing critical columns are dropped.

Showdown: aliases are applied via `COLUMN_ALIASES` (`Pos`, `Proj`, `Total Own`, `Own`, `CPT Own`, `CPT Salary`, `CPT Proj`), first alias wins so the rename can't create duplicate columns. `Ceiling`/`Ownership`/`CptOwnership` are optional and default to 0. Ownership is slot-aware: `FlexOwnership = Total Own - CPT Own`, so the printed total is true product ownership. The file must contain exactly two teams or loading raises.

Exports (`-e`) go to `EXPORT_DIR = r"G:\My Drive\Documents\NFL-DFS\csv-exports"` (NBA scripts use their own OneDrive paths), filename timestamped and tagged with the optimization target (`_ceiling` / `_projceiling`) on the NFL multi-lineup scripts. Export columns are unchanged by the target. Each lineup writes one row per roster spot, then a `TOTAL` row, then a DraftKings upload row holding only `Name + ID` values positioned into `EXPORT_COLUMNS[1:]`.

The upload row is built from `DK_ENTRIES_PATH` (`~/Downloads/DKEntries.csv`), whose player pool starts at row 8 behind the jagged entry-list columns. `load_dk_name_ids()` indexes it three ways — slot+name, name alone (ambiguous names map to `None`), and slot+team for defenses — because Showdown assigns a player **different IDs at CPT and FLEX**. Anything unresolvable (missing file, unmatched player) drops just the upload row; it is never a fatal error. The two NFL multi-lineup scripts carry their own identical copy of these helpers — `patch` both or neither.

## PR workflow

`.github/workflows/claude-auto-pr-once.yml` runs a Claude review automatically when a PR opens, and on repo-owner comments containing `@claude`. In remote/cloud sessions, a `@claude` review-request comment on a subscribed PR is a trigger for **that** workflow, not a task for the session — wait for the workflow's review and act on its findings (this rule is injected by `.claude/hooks/pr-review-posture.sh`).
