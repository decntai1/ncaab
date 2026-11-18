# Changelog

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
