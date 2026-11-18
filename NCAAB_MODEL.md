# College basketball (NCAAB) grading model and bandit for vegas.hu

Hand-off spec for a new agent. Script: `ncaab_grade.py` (one file; Python 3.10+, `numpy`, `pandas`, `tabulate`).
Boards come from the existing `capture_board.py`. The same layout works for any sport with few markets per game:
copy the script, change section 3's market table and section 5's results source.

**Goal of this phase: grade the book.** Collect many games fast, find out whether vegas.hu's college prices leak
anywhere, and let the bandit say when something is proven. No pick is "good" before that.

## 1. Principles (do not relax)

1. **The book is the anchor**: `p = p_book + w · lean`, `lean = clip(p_model − p_book, ±0.05)`; `w` starts at 0.05 on
   full-game lines, 0 elsewhere, and only moves after a refit on ≥30 graded games.
2. **Games are the unit of evidence.** 60 handicap lines on one game are one observation. Every number carries its n,
   and n is games.
3. **Negatives first**: every report opens with the verdict and with where the book beat the model.
4. **Find on old nights, confirm on new ones.** With dozens of arms some look good by luck.
5. **Verify before asserting.** Unknown is never zero; a game whose periods do not add up is left out, not guessed.
6. **Closing-line value (CLV) is the fastest honest signal**: capture twice (morning, about an hour before the first
   tip) and check whether your side is the one that shortens. NBA, one night: bets that shortened 3+ points won 58%
   against 44% implied in the morning.
7. Never call a multi above 3.0 odds good; each leg pays the margin again.
8. Keep a `CHANGELOG.md`. Secrets and the user's e-mail never go into logs or requests.

## 2. Why college basketball

- About 360 Division I teams, 30–150 games on a busy day: data arrives 5–10× faster than NBA.
- Books cannot watch every small-conference game; those lines are among the softest in major sports.
- Cost: few markets, blowouts (handicaps of 30+), big gaps in team quality, thin public data on small schools.

**What one vegas.hu board looks like** (Baylor–Jackson State, saved 2026-11-18): handicap ladder (31 line pairs),
total (25), each team's total (13 each); all "overtime included". The book's cut at the middle line: handicap
**5.3%**, total **5.0%**, team totals **9.3%**; median over each whole ladder 6.8% / 7.9% / 7.6%. That is the same
as NBA main lines, so the league is not more expensive to bet. There was no "to win" market on that game (a
35-point favourite). The user reports about 10 markets on other games: run `markets` on the first capture to see them.

## 3. Markets and settlement

College games are two 20-minute halves; overtime periods are 5 minutes. Full-game markets include overtime unless
the name says `nélkül` (without).

| Type | What | Settled on |
|---|---|---|
| `ML` | to win, 2-way | final score incl. overtime |
| `HANDICAP` | spread; scope G / H1 / H2 | margin + line; whole-number lines can push |
| `TOTAL` | points over/under; G / H1 / H2 | points in that scope (G includes overtime) |
| `TEAM_TOTAL` | one team's points | same |
| `1X2`, `DNB` | 3-way / draw-no-bet, mostly halves | regulation or that half; tie = draw / push |
| `ODD_EVEN` | odd/even points | counted points |
| `RACE` | first to N points: full game 2-way, first half 3-way with "neither" | ESPN play-by-play (`RACE` line in the results file); no play-by-play = not graded |
| `UNPARSED` | quarters (college has none), half/full-time doubles, bands, races, anything unknown | stored, never graded |

The script types a market by the site's own selection codes (home/draw/away, over/under, handicap, odd/even); the
Hungarian name only supplies scope (`1. félidő` = first half), stat (`pont`) and team. The second half (`H2`)
excludes overtime.

## 4. Nightly workflow

```
python ncaab_grade.py selftest                                         # once; must end with PASS
# on the user's machine (Hungarian IP), morning and ~1 h before the first tip:
python capture_board.py --all --league ncaab '<NCAAB league link or any NCAAB match link>'
python ncaab_grade.py markets ~/vegas-boards/ncaab/<date>              # first capture only: see section 8
python ncaab_grade.py price --date <date> --boards <folder> --capture 0900
python ncaab_grade.py price --date <date> --boards <folder> --capture 1730
# next day
python ncaab_grade.py results --date <date> --out results/<date>.txt
python ncaab_grade.py grade   --date <date> --results results/<date>.txt
python ncaab_grade.py report                                           # ncaab_report.md: verdict, tables, bandit
```

- `<date>` is the US Eastern date of the games. `capture_board.py` puts each game in the folder of its own date.
- `--capture` is only a label (HHMM). Captures are ordered by when they were taken (`captured_utc`: the boards'
  file time, or `--captured-at 2026-11-19T00:30+01:00`), so a capture after midnight follows the evening one.
- For every **game**, the last capture taken before its tip is the one pooled; earlier ones are only compared with
  it (open vs close). Games tip from noon to 11pm ET, so an evening capture no longer holds the afternoon games:
  those are pooled from the morning capture. A game that had already tipped when the boards were pulled is not stored.
- One capture a night is enough to run; two give CLV. Run `pack` on the machine that made the capture (copied files
  can lose their time; then pass `--captured-at`).
- Other leagues default to 8 days ahead in `capture_board.py`; add `--hours 30` for one night.

Results file (team names exactly as on the board):
```
GAME Jackson State Tigers@Baylor Bears      # away@home
H Baylor Bears: 48 51                       # first half, second half, then one number per overtime
H Jackson State Tigers: 30 28
```
Rejected, with nothing graded: a tie without overtime, overtime without a regulation tie, unequal period counts,
a half outside 5–95 points, a team not in the game.

### 4a. Current split (2026-11-18): GitHub scrapes, grading is done from a clone

`.github/workflows/ncaab.yml` only fetches ESPN `results/<date>.txt` and `slate/<date>.txt` (daily 12:23 UTC, missing
days backfilled from 2025-11-03, last 3 days refetched) and goes red if ESPN does not answer. The user pushes packed
boards to `boards/<date>/<HHMM>.rows.csv.gz`. Grading: clone the repo, `NCAAB_DB=/tmp/ncaab.sqlite python
ncaab_grade.py sync --root <clone>` rebuilds the ledger from every pack and results file in ONE run (since 0.7:
prices first, then grades every finished night that has a results file, whatever its age). `sync` only fetches files
that are missing and never overwrites one, so the play-by-play lines scraped by the job are kept. 4b below is the older all-on-GitHub
design; `sync` is the same command.

### 4b. Hands-off route: GitHub repo + `sync` (added 2026-11-18)

Repo layout (everything except the raw boards and the sqlite ledger is committed):
```
ncaab_grade.py  NCAAB_MODEL.md  CHANGELOG.md  .github/workflows/ncaab.yml
boards/<date>/<HHMM>.rows.csv.gz     # made by `pack` on the user's machine, pushed by the user
results/<date>.txt  slate/<date>.txt # fetched from ESPN by the job
ratings/teams.csv  ratings/model.json  ncaab_report.md  ncaab_picks.md   # written by the job
```
On the user's machine after each capture:
```
python ncaab_grade.py pack --boards ~/vegas-boards/ncaab/<date> --out boards/<date>/<HHMM>.rows.csv.gz
git add boards && git commit -m "boards <date> <HHMM>" && git push
```
The workflow runs `python ncaab_grade.py sync` on every push to `boards/` and once a day. `sync`, in order: fetch
missing results; grade finished nights; price every pack not priced yet, with ratings fitted **only on games before
that date** and neutral courts taken from ESPN's slate; write `ncaab_picks.md`; refit ratings; write the report. The
ledger (`ncaab.sqlite`, about 5 MB a night, too big for git) is kept as the asset of a release tagged `ledger`.
First run: start the workflow by hand with `backfill_from = 2025-11-03` so last season is in the ratings.

## 5. Where the data comes from

Results (need only the score by half; no player data, because the boards have no player markets):

| Source | What | Key | Notes |
|---|---|---|---|
| **ESPN public scoreboard** `site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates=YYYYMMDD&groups=50&limit=500` | every D-I game of a date, score by half and overtime | none | what `results` uses. Unofficial and undocumented: can change without notice. `groups=50` = all Division I (without it only ranked teams come back) |
| **ncaa.com via henrygd/ncaa-api** `ncaa-api.henrygd.me` | scoreboard, box score, play-by-play from ncaa.com | none | official source behind a free wrapper; good cross-check; can be self-hosted |
| **CollegeBasketballData.com** `api.collegebasketballdata.com` | games, team/player box scores, **historical betting lines**, ratings | free key, 1,000 calls/month | best for a backfill of past seasons with closing lines |
| sports-reference.com/cbb | box scores, schedules | none | rate-limited; scrape slowly, by hand |

Ratings for the statistics model (`teams.csv`: `team,adj_o,adj_d,tempo`):

| Source | What | Cost |
|---|---|---|
| **Bart Torvik (barttorvik.com)** | adjusted offence/defence per 100 possessions, tempo, daily | free; no official API, table download / the `cbbdata` package |
| KenPom (kenpom.com) | the reference ratings | paid subscription |
| ESPN BPI, Haslametrics, EvanMiya | alternatives | free / mixed |

Reference prices (to check vegas.hu against a sharper market): **the-odds-api.com**, sport key
`basketball_ncaab`, free tier; key in the env var `ODDS_API_KEY`, never logged.

Injuries and news: college teams publish no reliable injury report before games (availability reports exist only
in some conferences). Treat every line move as possible news you cannot see. That is one more reason for two
captures.

**Team names are the main chore.** The board says `Baylor Bears`; ESPN's `displayName` usually matches; ratings
sites say `Baylor`. Keep two small files next to the script and extend them whenever `grade` lists a board without
a result: `results_aliases.csv` (`espn,board`) and, beside `teams.csv`, `aliases.csv` (`board,ratings`).

## 6. The statistics model (optional; grading works without it)

v0 in the script: expected points from tempo-free ratings,
`possessions = tempo_home × tempo_away ÷ league tempo`,
`points_home = adj_o_home × adj_d_away ÷ league efficiency × possessions ÷ 100 + 1.6`, the away side mirrored with
−1.6 (home court 3.2 in total). Margin and total are treated as normal with SD 11 and 17 (halves: half the mean,
SD ÷ √2). The constants are **starting guesses, not fitted**; early-season ratings lean on preseason priors.

**v1 (added 2026-11-18): own ratings, `fit`.** No outside ratings site needed. From the results files:
`log(points) = league + offence[team] + defence[opponent] ± home/2`, ridge-shrunk toward the league average
(`--ridge 0.15`), recent games weighted more (`--half-life 150` days,
so last season still counts in November), overtime games scaled to 40 minutes, neutral courts without home edge. Output:
`ratings/teams.csv` in the layout above (tempo fixed at 68: the fit is on points, not possessions) with a `games`
column, and `ratings/model.json` with the fitted home court, SDs and a walk-forward test (each of the last 21 game
days predicted from earlier games only: winner hit rate, margin error, Brier). A team with fewer than 4 games is not
priced. `picks` lists the predicted winner of every game with odds, book / model / blended chance, break-even and
expected return. The blend still follows section 1: the model moves the price by at most 0.25 points until a refit.

Add later, only if grading says a model is worth it, each tested against the book on games it was not fitted on:
neutral-site flag (no home court), rest and travel, conference vs non-conference, minutes-weighted roster
continuity, a fitted spread-to-win curve for blowouts.

## 7. The bandit and the gates

`report` runs the bandit over every bet type × scope × side and these hypotheses fixed in advance: favourite /
underdog to win; home / away side on the handicap; big favourites (−15 or more) / big underdogs (+15 or more);
short legs (book ≥70%) / longshots (<30%).

Added 2026-11-18, before any NCAAB game was graded: handicap at the line nearest to even (favourite / underdog);
first-half handicap (favourite / underdog); handicap where the model differs from the book by 5+ points (for /
against); race favourite / underdog, split at targets ≤25 and ≥30; first-half race "neither"; race where the model
likes a side by 5+ points. The "model" arms are the test of whether prediction pays: they need positive `belief`,
not just a good-looking `seen`.

- Start belief: the book's price is right (prior mean = the return its own no-vig price implies, SD 4 points).
- Evidence: mean return per game, with its standard error.
- Output: `p_beats` = chance the arm's true return per bet is above zero; `clv` = how far the book moved the arm's
  bets between a game's first and last capture (+ = they shortened), over `clv_games` games.
- Tiers: **watch** ≥65%; **SMALL STAKE** ≥80% and ≥100 games; **CANDIDATE** ≥95% and ≥100 games.
- A real +5% edge reaches 80% in roughly 150–200 games, +3% in about 300; no edge never does. At 40+ games a
  night that is days to weeks, not months.

Before real stakes also require: positive CLV for that arm on two-capture nights, and (if the model is used) blend
Brier ≤ book Brier. Until then the verdict line says NO BET and every slip is quoted with its negative expected
return.

## 8. Tested and not tested (2026-11-18)

Tested:
- `selftest` PASS: a synthetic overtime game priced at two captures; 12 settlements (overtime winner, spread, total
  with overtime, team total, first-half lines, 3-way), de-vig sums, handicap pairing, "only the last capture is
  pooled", open-vs-close section, rejection of a tie without overtime.
- On 8 real vegas.hu boards (NBA, 2026-11-16) the reader produced exactly the row counts of the established NBA
  parser for to-win, handicap, total, team total, first-half handicap and total (16 / 496 / 496 / 416 / 162 / 176),
  and every one of those 1,762 bets graded to the same result with the same price.

Added 2026-11-18 (`ncaab-0.2`), tested: selftest PASS with 7 new checks (fit recovers known strengths on a
synthetic 40-team league, walk-forward, home court, neutral court, packed boards price identically, picks output);
`sync` dry-run offline end to end; ESPN field layout (`linescores[].value`, `neutralSite`, `status.type.completed`,
`displayName`) read off the live feed for 2026-11-17. **Not tested:** any real ESPN download through the script
(host blocked where it was written), the fit on real seasons (ridge and half-life are untuned), the GitHub workflow.

Real data, 2026-11-18 (`ncaab-0.4`): 380 ESPN days (7,205 games) scraped by the GitHub job; 59 real boards read,
all 59 matched to ESPN's card and priced. See CHANGELOG for the four bugs this exposed. **Not yet done: no night has
been graded**, so settlement on real boards is still unverified. First graded night: hand-check ten games across
every market type.

Review fixes, 2026-11-18 (`ncaab-0.7`, see CHANGELOG): captures per game and by time, tip time stored, one-run
rebuild, results files never overwritten, `clv` per arm. selftest 25 checks PASS; identical pricing and grading to 0.5
on 6,704 real rows. The model's margins were checked against results (slope 0.96 on 2,691 walk-forward games): it is
not short on blowouts in reality, only against the book's lines, so no correction was added.

Review, 2026-11-18 (`ncaab-0.8`): 0.7 checked function by function and on the 59 real boards (identical pricing);
slips merged back and saved as `slips/<night>.csv`; copied-capture warning added. selftest 29 checks PASS.

Still not tested, by risk:
1. **No real night has been graded.** Settlement rules are proven only on the synthetic game and on NBA boards.
   First graded night: hand-check ten games across every market type, races included.
2. **ESPN play-by-play (`results --pbp`) has never run.** Its field layout was read off one live game. Until a
   results file carries `RACE` lines, race markets stay ungraded.
3. The model: ridge / half-life were chosen on the same games they are reported on; totals and the race model are
   weak (4-6 points from the book's price). Its weight stays at 0.05 / 0 until a refit on graded games.
4. Capture times rely on the files' own time: run `pack` where the capture was made, or zip with a tool that keeps
   UTC file times; heed the "look copied" warning.
5. `capture_board.py --all` for NCAAB and `push_boards.py` against the real GitHub API are untested from here.

### Paper slips (`slips`, since 0.6)

`python ncaab_grade.py slips --date <date>` builds ten multis at about 3x / 7x / 10x from one capture by the fixed
`SLIP_PLAN` (legs: to-win, handicap at .5 lines, first-half draw-no-bet, races; odds 1.15-2.60; one leg per game,
no game in two slips). They are paper only and are settled in `report`. A slip result is weak evidence (one win or
loss per slip); the legs are graded individually and feed the bandit, which is what decides.

## 9. Reporting to the user

Terse, plain words, worst news first, n in games. Say what ran and what did not. One night is never evidence.
Asked for picks before a tier is reached: give the highest-probability two-way lines and the cheapest route to the
target odds, each with win chance, break-even and expected return, and say they lose on average.
