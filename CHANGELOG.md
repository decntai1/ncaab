# Changelog

## ncaab-0.4 (2026-11-18)
First real NCAAB boards (59 games, capture 2026-11-18 11:31). Found and fixed:
- **Silent wrong type:** `1.félidő - verseny a(z) N pontért` (race to N points) with two selections was read as a
  first-half draw-no-bet and would have been graded wrongly. Now UNPARSED.
- **Duplicate markets:** 48 of 59 boards list every market twice; the doubled rows broke the two-way pairing, so
  only 10 games had a usable to-win price. Markets are now de-duplicated by id (49 games with a to-win price).
- **Names:** 20 of 59 games did not match ESPN (`UConn` vs `Connecticut`, ...). `results_aliases.csv` (espn,board)
  is now applied when results / slate files are read, so already-scraped files are covered. 21 names added.
- **Home/away order:** on neutral courts the board and ESPN can disagree (2 of 59). Results, slate and grading now
  match a game by its two teams, and grading uses the board's home/away.
- `sync` fits once before pricing so the fitted SDs are used; `picks` verdict counts only odds 1.15-6.0.
- Check against the book's own lines, 59 games: model margin vs middle handicap correlation 0.96, mean gap 3.7
  points, same favourite 90%; totals correlation 0.87, mean gap 4.1. The model underrates blowouts (28-point book
  favourites come out at 19-22).
- Still UNPARSED by design: winning-margin bands, double result, races, "will there be overtime", "which half has
  more points", player multis, team stat totals (threes, blocks, assists, steals).

## ncaab-0.3 (2026-11-18)
- First real data: 380 days from ESPN via the GitHub job, 7,205 games (56 skipped by the half-range rule).
- `fit` defaults changed from ridge 3 / half-life 60 to **ridge 0.15 / half-life 150**. The old values shrank every
  team to average in November (last season weighed almost nothing) and pushed the difference into "home court"
  (+14.8 points, nonsense). Walk-forward on real games, old -> new: November 2026 margin error 13.7 -> 10.6 points,
  Jan-Feb 2026 9.9 -> 8.9, home court now 3.1-3.7. Chosen on the same games it is reported on: mildly optimistic.
- Half-range sanity rule widened from 15-80 to 5-95 points: real halves of 10-14 and 60 were being thrown out.
- Workflow is scrape-only (results + slate); pricing and grading run from a clone.

## ncaab-0.2 (2026-11-18)
- `fit`: own team ratings from the results files (ridge, time-weighted, neutral courts, overtime scaled), with a
  walk-forward test; writes ratings/teams.csv and ratings/model.json (fitted home court and SDs replace the guesses).
- `picks`: predicted winner per game with odds, book / model / blended chance, break-even, expected return.
- `slate`: ESPN card of a date with neutral courts; `price --slate` drops home court for those games.
- `results --to`: backfill a date range. A game whose numbers do not add up is written as `# SKIPPED`, so one bad
  box score no longer blocks a night. `NEUTRAL` line added to the results format.
- `pack`: boards folder -> one small .rows.csv.gz; `price --boards` accepts it.
- `sync` + .github/workflows/ncaab.yml: fetch, grade, price, refit, report in one run.
- Teams with fewer than 4 games behind their rating are not priced by the model.
- Fix: the weight of a family not included in the latest refit fell back to its starting value.

## ncaab-0.1 (2026-11-18)
- First version: board reader, de-vig, v0 model, grading, report, bandit, selftest.
