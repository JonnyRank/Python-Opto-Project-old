# NFL DFS Optimizers

Python optimizers that build DraftKings NFL lineups from a projections CSV. Each script
models the contest as a mixed-integer linear program with [PuLP](https://coin-or.github.io/pulp/)
and maximizes total projected points subject to the salary cap and DraftKings roster rules.

| Script | Format | Roster | Lineups | Solver |
| --- | --- | --- | --- | --- |
| `NFL-Multi-Opto-v2.0.py` | Classic | QB, 2 RB, 3 WR, TE, FLEX, DST (9) | Many | CBC |
| `NFL-Single-Opto.py` | Classic | QB, 2 RB, 3 WR, TE, FLEX, DST (9) | One | CBC |
| `NFL-SD-Multi-Opto-v1.0.py` | Showdown (Captain Mode) | 1 CPT + 5 FLEX (6) | Many | HiGHS |

(The repo also contains two older NBA scripts. They are configured by editing constants at
the top of the file rather than from the command line and are not covered here.)

## Setup

```bash
python -m venv venv
venv/Scripts/python.exe -m pip install -r requirements.txt
```

Requirements: `pandas`, `pulp`, `highspy`. The Showdown optimizer needs `highspy`; the two
Classic optimizers use the CBC solver that ships with PuLP.

## Running

All three scripts take the path to a projections CSV as the first argument.

```bash
# Single best Classic lineup
venv/Scripts/python.exe NFL-Single-Opto.py "C:\path\to\projections.csv" -e

# 20 Classic lineups, at least 2 players different between any two, QB stacked with a WR/TE,
# no DST opposite one of your own offensive players
venv/Scripts/python.exe NFL-Multi-Opto-v2.0.py "C:\path\to\projections.csv" -n 20 -u 2 -s -ndo -e

# 20 Showdown lineups with a locked Captain and a little salary left on the table
venv/Scripts/python.exe NFL-SD-Multi-Opto-v1.0.py "C:\path\to\showdown.csv" -n 20 -u 2 -l "Drake Maye:CPT" -ms 49800 -e
```

Lineups print to the terminal as a formatted table with total projection, ownership, ceiling,
and salary. Nothing is written to disk unless you pass `-e`.

## Options

### `NFL-Multi-Opto-v2.0.py` (Classic, multi-lineup)

| Flag | Meaning |
| --- | --- |
| `filepath` | Path to the projections CSV (required) |
| `-n`, `--num-lineups` | Number of lineups to generate (default: 1) |
| `-u`, `--min-uniques` | Minimum players that must differ between any two lineups (default: 1) |
| `-e`, `--export` | Write the lineups to a timestamped CSV |
| `-l`, `--lock` | Player names to force into every lineup, e.g. `-l "Josh Allen" "Puka Nacua"` |
| `-x`, `--exclude` | Player names to keep out of every lineup |
| `-s`, `--stack [N]` | Require the QB to be paired with at least N WR/TE from his own team (N defaults to 1 when the flag is given without a number) |
| `-srb`, `--stack-rb` | Require the QB to be paired with an RB from his own team |
| `-te`, `--max-te` | Cap the number of TEs, e.g. `-te 1` to keep a TE out of the FLEX |
| `-ndo`, `--no-dst-opp` | Never roster a DST alongside a QB/RB/WR/TE from the opposing team |

Name matching for `-l` and `-x` is case-insensitive. A name that isn't in the projections file
prints a warning and is skipped rather than failing the run.

### `NFL-Single-Opto.py` (Classic, one lineup)

Takes only `filepath` and `-e`, `--export`. It applies the same salary cap, roster, and
two-games constraints as the multi-lineup version, without locks, stacking, or exclusions.

### `NFL-SD-Multi-Opto-v1.0.py` (Showdown, multi-lineup)

| Flag | Meaning |
| --- | --- |
| `filepath` | Path to the Showdown projections CSV (required) |
| `-n`, `--num-lineups` | Number of lineups to generate (default: 1) |
| `-u`, `--min-uniques` | Minimum roster spots that must differ between any two lineups (default: 1) |
| `-e`, `--export` | Write the lineups to a timestamped CSV |
| `-l`, `--lock` | Players to force into every lineup |
| `-x`, `--exclude` | Players to keep out of every lineup |
| `-ms`, `--max-salary` | Cap total lineup salary below $50,000 (values above the cap are clamped) |

Showdown notes:

* The roster is 1 Captain + 5 FLEX. The Captain scores 1.5x points and costs 1.5x salary, and
  a player can fill the Captain slot or a FLEX slot but never both.
* Every lineup must include at least one player from each of the two teams.
* `-l` and `-x` accept an optional slot suffix: `-l "Drake Maye:CPT"` locks him at Captain,
  `-x "Sam Darnold:CPT"` bans him from Captain but still allows him in the FLEX. Without a
  suffix the lock or exclusion applies to both slots.
* `-u` counts uniqueness by roster spot, so the same six players with a different Captain
  counts as two uniques.
* `-s`, `-srb`, `-te`, and `-ndo` don't apply to Showdown.

## Projections CSV format

### Classic

Expected columns: `ID`, `Player`, `Position`, `Team`, `Opp`, `Salary`, `Proj`, `Own`, `Ceiling`.

* `Salary` may be formatted (`$6,000`) and `Own` may carry a `%` — both are cleaned on load.
* `Opp` may be written `@BUF` or `BUF`; the `@` is stripped when pairing teams into games.
* Rows missing `ID`, `Salary`, `Proj`, or `Position` are dropped before optimizing.
* Every lineup is required to use players from at least two different games.

### Showdown

Expected columns: `Player`, `Pos`, `Team`, `Salary`, `Proj`, plus the optional
`Ceiling`, `Total Own`, `CPT Own`, `CPT Salary`, and `CPT Proj`.

* The file must contain exactly two teams — filter it down to the single game first, or
  loading fails with an explanatory error.
* `CPT Salary` and `CPT Proj` are used when present; otherwise the standard 1.5x multiplier
  is applied to the FLEX values.
* Ownership is slot-aware: the Captain contributes its `CPT Own` and each FLEX contributes
  `Total Own - CPT Own`, so the printed total is true product ownership for the exact lineup.
* Missing `Ceiling` / `Total Own` / `CPT Own` columns default to zero and display as `0.00`.

## Exports

`-e` writes a timestamped file (`nfl_classic_multi_lineups_<timestamp>.csv`,
`nfl_showdown_multi_lineups_<timestamp>.csv`) to the directory set by the `EXPORT_DIR`
constant near the top of each script — currently `G:\My Drive\Documents\NFL-DFS\csv-exports`.
Change that constant to export somewhere else.

Each lineup is written as three blocks, all sharing a `Lineup_ID`:

1. **One row per roster spot**, in slot order.
2. **A `TOTAL` row** with the lineup's salary, projection, ownership, and ceiling.
3. **A DraftKings upload row** — the same lineup laid out horizontally, holding only the
   players' `Name + ID` values in slot order, ready to paste into a DraftKings entries file.

```
1,CPT,Drake Maye,QB,NE,15000,28.95,3.63,49.2
1,FLEX,Jaxon Smith-Njigba,WR,SEA,10600,17.9,12.92,30.4
...
1,TOTAL,,,,50000,97.65,78.51,165.9
1,Drake Maye (43782098),Jaxon Smith-Njigba (43782034),...
```

### The upload row and DKEntries.csv

The `Name + ID` values are read from `C:\Users\jrank\Downloads\DKEntries.csv` (the
`DK_ENTRIES_PATH` constant). That file is jagged: contest entries come first and the player
pool section starts at row 8 with its own header, listing `Position`, `Name + ID`, `Name`,
`ID`, `Roster Position`, `Salary`, `Game Info`, and `TeamAbbrev`.

* Showdown players appear twice — once at `CPT` and once at `FLEX` — with **different IDs**,
  so the Captain row and FLEX rows are looked up by slot and get the correct ID for each.
* Names are matched case-insensitively, ignoring punctuation and generational suffixes, so
  `AJ Brown` in your projections still finds `A.J. Brown` in the DraftKings file. Defenses
  fall back to matching on team abbreviation, since DraftKings names them by nickname.
* **If the file isn't there, the upload row is simply skipped** and the export continues from
  the `TOTAL` row to the next lineup. The same happens for an individual lineup if any of its
  players can't be matched — the script prints which player it couldn't resolve.

## When fewer lineups come back than you asked for

Each solved lineup adds a constraint forbidding it from reappearing, so the pool shrinks as
the run goes on. If the first lineup can't be built at all, the constraints are contradictory —
usually conflicting locks, too many exclusions, or a `-ms` value that's too low. If the run
stops partway through, the slate simply has no more lineups that satisfy your `-u` setting;
lower `-u` or loosen the stacking flags.
