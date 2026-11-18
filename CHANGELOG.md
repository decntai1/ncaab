# Changelog

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
