#!/usr/bin/env python3
"""College basketball (NCAAB) grading model + bandit for vegas.hu boards (Altenar .net.json captures made by
capture_board.py). One file: stdlib + numpy + pandas + tabulate. Spec: NCAAB_MODEL.md.

    python ncaab_grade.py selftest                                   # must print PASS first
    python ncaab_grade.py markets BOARDS_DIR                         # every market name and how it is typed
    python ncaab_grade.py price   --date 2026-11-18 --boards DIR [--capture 1730] [--teams teams.csv]
    python ncaab_grade.py results --date 2026-11-18 --out results/2026-11-18.txt     # ESPN (spec 5), UNTESTED
    python ncaab_grade.py grade   --date 2026-11-18 --results results/2026-11-18.txt
    python ncaab_grade.py report                                     # ledger report, open-vs-close, bandit

Stored in ncaab.sqlite next to this file (override: NCAAB_DB=path). Several captures of one night are kept apart
(--capture, default = the boards' file time HHMM); the LAST capture of a night is the one that is pooled, earlier
ones are only compared with it. Principles: the book is the anchor (p = p_book + w * lean), games are the unit of
evidence, every number carries its n, nothing is a bet until the gates pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DB = os.environ.get("NCAAB_DB", str(Path(__file__).with_name("ncaab.sqlite")))
MODEL_VERSION = "ncaab-0.1"
HALF = re.compile(r"(\d)\.\s*félidő", re.I)                    # '1. félidő' = first half
START_W = {"main": 0.05, "half": 0.0, "other": 0.0}
LEAN_CAP, MIN_GAMES, PRIOR_SD = 0.05, 30, 0.04
HCA, SD_MARGIN, SD_TOTAL = 3.2, 11.0, 17.0                     # unfitted starting values (spec 6)
HOME, DRAW, AWAY, OVER, UNDER, HCP_H, HCP_A, ODD, EVEN = 1, 2, 3, 12, 13, 1714, 1715, 70, 72   # site's own codes

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (night TEXT, capture TEXT, row_key TEXT, game TEXT, home TEXT, away TEXT,
  market TEXT, type TEXT, scope TEXT, ot INTEGER, subject TEXT, selection TEXT, side TEXT, line REAL, odds REAL,
  p_book REAL, p_model REAL, p REAL, w_used REAL, p_method TEXT, overround REAL, model_version TEXT,
  status TEXT DEFAULT 'open', result TEXT, actual REAL, PRIMARY KEY (night, capture, row_key));
CREATE TABLE IF NOT EXISTS weights (as_of TEXT, family TEXT, games INTEGER, w_fit REAL, w_se REAL, w_used REAL);
"""


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    return con


def norm_team(s: str) -> str:
    """One spelling per team: the board's full name, trimmed ('Baylor Bears'). Results must use the same."""
    return re.sub(r"\s+", " ", str(s)).strip()


def ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# ------------------------------------------------------------------ boards
def load_event(path) -> dict:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(d, dict) and "markets" in d:
        return d
    hits = [g["body"] for g in d if isinstance(g.get("body"), dict) and "markets" in g["body"] and "odds" in g["body"]]
    if not hits:
        raise ValueError(f"{path}: no GetEventDetails response in it")
    return max(hits, key=lambda b: len(b["odds"]))


def shown(price: float) -> float:
    return math.floor(price * 100 + 1e-6) / 100             # the site shows and pays prices floored to 2 decimals


def _line(o: dict):
    sv = str(o.get("sv") or "").split("|")[0].replace(",", ".")
    try:
        return float(sv)
    except ValueError:
        m = re.search(r"([+\-]?\d+(?:[.,]\d+)?)", o.get("name", ""))
        return float(m.group(1).replace(",", ".")) if m else None


def classify(name: str, tids: set, teams: tuple[str, str]) -> tuple[str, str, int, str | None]:
    """-> (type, scope, ot, team). scope G / H1 / H2. Decided by WHICH selection codes the market has; the name
    gives only scope, stat and team. Anything not recognised is UNPARSED: stored, never graded."""
    low = name.lower()
    bare = re.sub(r"\(.*?\)", "", low)
    m = HALF.search(low)
    scope = f"H{m.group(1)}" if m else "G"
    team = next((t for t in teams if t.lower() in low), None)
    ot = int(scope == "G" and "nélkül" not in low)             # full-game basketball markets include overtime
    if (scope == "G" and re.match(r"\s*\d\s*\.", low)) or scope not in ("G", "H1", "H2"):
        return "UNPARSED", scope, ot, team                     # quarters, numbered parts that are not halves
    if re.search(r" és | & |pontos|multi|tartomány|különbség|melyik|hányadik|első|utolsó|ki ér el|félidő/", bare):
        return "UNPARSED", scope, ot, team
    if tids == {HOME, AWAY}:
        return ("DNB" if scope != "G" or "döntetlennél" in low else "ML"), scope, ot, None
    if tids == {HOME, DRAW, AWAY}:
        return "1X2", scope, 0, None                           # 3-way: regulation only
    if tids and tids <= {HCP_H, HCP_A}:
        return "HANDICAP", scope, ot, None
    if tids and tids <= {OVER, UNDER}:
        if "pont" not in bare or re.search(r"tripla|hárompont|büntet|lepattan|gólpassz", bare):
            return "UNPARSED", scope, ot, team
        return ("TEAM_TOTAL" if team else "TOTAL"), scope, ot, team
    if tids == {ODD, EVEN}:
        return "ODD_EVEN", scope, ot, team
    return "UNPARSED", scope, ot, team


def board_rows(path) -> list[dict]:
    ev = load_event(path)
    comp = ev["competitors"][:2]
    names = (norm_team(comp[0]["name"]), norm_team(comp[1]["name"]))
    home, away = names
    cid = {comp[0]["id"]: home, comp[1]["id"]: away}
    odds = {o["id"]: o for o in ev["odds"]}
    rows = []
    for m in ev["markets"]:
        if m.get("typeId") == 31046 or m.get("childMarketIds"):   # boosted container; player tables (none in NCAAB)
            continue
        live = [odds[i] for col in m.get("desktopOddIds", []) for i in col
                if i in odds and odds[i].get("oddStatus", 0) == 0 and shown(odds[i].get("price", 0)) > 1]
        if not live:
            continue
        name = m["name"].strip()
        typ, scope, ot, team = classify(name, {o.get("typeId") for o in live}, names)
        for o in live:
            t, nm, line, side, subject = o.get("typeId"), o.get("name", ""), _line(o), None, None
            if typ in ("ML", "DNB", "1X2"):
                side, subject = {HOME: "home", DRAW: "draw", AWAY: "away"}.get(t), cid.get(o.get("competitorId"))
                line = None                                    # no line here ('Philadelphia 76ers' is not a 76)
            elif typ == "HANDICAP":
                side = "home" if t == HCP_H else "away"
                subject = home if side == "home" else away
                own = re.search(r"\(?([+\-]\d+(?:[.,]\d+)?)\)?\s*$", nm)    # the selection's OWN line is in its name;
                if own:                                                     # the sv field is the home line for both
                    line = float(own.group(1).replace(",", "."))
                elif side == "away" and line is not None:
                    line = -line
            elif typ in ("TOTAL", "TEAM_TOTAL"):
                side, subject = ("over" if t == OVER else "under"), team
            elif typ == "ODD_EVEN":
                side, subject, line = ("odd" if t == ODD else "even"), team, None
            ty = typ if (side is not None or typ == "UNPARSED") else "UNPARSED"
            key = hashlib.md5(f"{away}@{home}|{ty}|{scope}|{subject}|{side}|{line}|{name}|{nm if ty == 'UNPARSED' else ''}".encode()).hexdigest()[:16]
            rows.append(dict(row_key=key, game=f"{away}@{home}", home=home, away=away, market=name, type=ty, scope=scope,
                             ot=ot, subject=subject, selection=nm, side=side, line=line, odds=shown(o["price"]), mid=m["id"]))
    return rows


def devig(B: pd.DataFrame) -> pd.DataFrame:
    """Margin removed. Groups that cover every outcome (2-way by market; over/under by line; handicap home L with
    away -L; 3-way) and whose implied chances add to 1.00-1.25 are normalised ('no_vig'). Everything else is divided
    by the board's median overround ('assumed_margin'): graded, reported, never pooled into calibration or arms."""
    B = B.copy()
    B["imp"] = 1 / B.odds
    ln = np.where((B.type == "HANDICAP") & (B.side == "away"), -B.line.fillna(0), B.line.fillna(0))
    lined = B.type.isin(["TOTAL", "TEAM_TOTAL", "HANDICAP"])
    B["grp"] = B.mid.astype(str) + np.where(lined, "|" + pd.Series(ln, index=B.index).round(2).astype(str), "")
    g = B.groupby("grp")
    B["overround"], n, sides = g.imp.transform("sum"), g.imp.transform("size"), g.side.transform("nunique")
    need = np.where(B.type == "1X2", 3, 2)
    ok = (n == need) & (sides == need) & B.overround.between(1.0, 1.25) & (B.type != "UNPARSED")
    med = B.loc[ok, "overround"].median() if ok.any() else 1.07
    B["p_book"] = np.where(ok, B.imp / B.overround, np.minimum(B.imp / med, 0.999))
    B["p_method"] = np.where(ok, "no_vig", f"assumed_margin_{med:.3f}")
    B.loc[~ok, "overround"] = med
    return B


# ------------------------------------------------------------------ model: tempo-free ratings -> normal margin / total
def expected_points(teams_csv: str):
    """teams.csv: team,adj_o,adj_d,tempo (points per 100 possessions for / against, possessions per game - the
    layout of Bart Torvik's and KenPom's ratings). `team` must be spelled as on the board; aliases.csv (board,ratings)
    maps the rest. Returns f(home, away) -> (home points, away points) or None."""
    t = pd.read_csv(teams_csv)
    ali = Path(teams_csv).with_name("aliases.csv")
    alias = dict(pd.read_csv(ali).values) if ali.exists() else {}
    R = {norm_team(r.team): r for r in t.itertuples()}
    eff, tempo = t.adj_o.mean(), t.tempo.mean()

    def f(home, away):
        h, a = R.get(alias.get(home, home)), R.get(alias.get(away, away))
        if h is None or a is None:
            return None
        poss = h.tempo * a.tempo / tempo
        return h.adj_o * a.adj_d / eff * poss / 100 + HCA / 2, a.adj_o * h.adj_d / eff * poss / 100 - HCA / 2
    return f


def model_p(r, ph: float, pa: float) -> float | None:
    k = 1.0 if r.scope == "G" else 0.5
    m, tot, sdm, sdt = (ph - pa) * k, (ph + pa) * k, SD_MARGIN * math.sqrt(k), SD_TOTAL * math.sqrt(k)
    sd_team = math.sqrt(sdm ** 2 + sdt ** 2) / 2
    t, s, L = r.type, r.side, r.line
    if t in ("ML", "DNB"):
        p = ncdf(m / sdm)
        return p if s == "home" else 1 - p
    if L is None or L % 1 != 0.5:
        return None                                            # whole-number lines can push: left to the book
    if t == "HANDICAP":
        return ncdf(((m if s == "home" else -m) + L) / sdm)
    if t == "TOTAL":
        p = 1 - ncdf((L - tot) / sdt)
        return p if s == "over" else 1 - p
    if t == "TEAM_TOTAL":
        p = 1 - ncdf((L - (ph if r.subject == r.home else pa) * k) / sd_team)
        return p if s == "over" else 1 - p
    return None


def family(t: str, scope: str) -> str:
    return "half" if scope != "G" else "main" if t in ("ML", "HANDICAP", "TOTAL", "TEAM_TOTAL") else "other"


def price(night: str, boards: str, capture: str | None, teams_csv: str | None, con) -> pd.DataFrame:
    files = sorted(Path(boards).glob("*.json"))
    if not files:
        sys.exit(f"no .json boards in {boards}")
    capture = capture or pd.Timestamp(max(os.path.getmtime(f) for f in files), unit="s").strftime("%H%M")
    frames, skipped = [], []
    for f in files:
        try:
            rows = board_rows(f)
            if rows:
                frames.append(devig(pd.DataFrame(rows)))
        except Exception as e:
            skipped.append(f"{f.name}: {e}")
    B = pd.concat(frames, ignore_index=True).drop_duplicates("row_key", keep="last")   # the key holds the game
    B["p_model"] = np.nan
    if teams_csv:
        f, miss = expected_points(teams_csv), set()
        for g, x in B.groupby("game"):
            e = f(x.home.iloc[0], x.away.iloc[0])
            if e is None:
                miss.add(g)
                continue
            B.loc[x.index, "p_model"] = [np.nan if (v := model_p(r, *e)) is None else v for r in x.itertuples()]
        print(f"model priced {B.game.nunique() - len(miss)} of {B.game.nunique()} games; no rating for: {sorted(miss)[:8]}")
    w = dict(START_W)
    for fam, wu in con.execute("SELECT family, w_used FROM weights WHERE as_of = (SELECT max(as_of) FROM weights)"):
        w[fam] = wu
    B["w_used"] = [w[family(t, s)] for t, s in zip(B.type, B.scope)]
    lean = (B.p_model - B.p_book).clip(-LEAN_CAP, LEAN_CAP)
    B["p"] = np.where(B.p_model.notna(), B.p_book + B.w_used * lean, B.p_book)
    bad = B[(B.p <= 0) | (B.p >= 1) | (B.odds <= 1) | ((B.p_method == "no_vig") & ~B.overround.between(1.0, 1.25))]
    cols = ["row_key", "game", "home", "away", "market", "type", "scope", "ot", "subject", "selection", "side", "line",
            "odds", "p_book", "p_model", "p", "w_used", "p_method", "overround"]
    con.execute("DELETE FROM predictions WHERE night = ? AND capture = ?", [night, capture])
    con.executemany(f"INSERT INTO predictions (night, capture, model_version, {','.join(cols)}) VALUES "
                    f"(?, ?, ?, {','.join('?' * len(cols))})",
                    [(night, capture, MODEL_VERSION, *[None if isinstance(v, float) and math.isnan(v) else v for v in r])
                     for r in B[cols].astype(object).values.tolist()])
    con.commit()
    nv = B[B.p_method == "no_vig"]
    print(f"{night} capture {capture}: {B.game.nunique()} games, {len(B):,} rows, price audit {len(bad)} broken; "
          f"skipped files {len(skipped)}")
    print(nv.groupby(["type", "scope"]).agg(rows=("odds", "size"), games=("game", "nunique"),
                                            cut=("overround", lambda s: s.median() - 1)).round(3).to_string())
    print(f"UNPARSED rows: {(B.type == 'UNPARSED').sum()} in {B[B.type == 'UNPARSED'].market.nunique()} market names")
    return B


# ------------------------------------------------------------------ results
def read_results(path: str) -> tuple[dict, list[str]]:
    """GAME <away>@<home>   (team names exactly as on the board)
       H <team>: h1 h2 [ot1 ot2 ...]      one line per team
    Rejected: a tie without overtime, overtime without a regulation tie, unequal period counts, unknown team."""
    games, errs, g = {}, [], None
    for n, ln in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        ln = ln.split("#")[0].strip()
        if not ln:
            continue
        try:
            if ln.startswith("GAME "):
                g = ln[5:].strip()
                a, h = (norm_team(x) for x in g.split("@"))
                g = f"{a}@{h}"
                games[g] = dict(home=h, away=a, per={})
            elif ln.startswith("H "):
                t, v = ln[2:].rsplit(":", 1)
                t = norm_team(t)
                assert t in (games[g]["home"], games[g]["away"]), "team is not in this game"
                games[g]["per"][t] = [int(x) for x in v.split()]
            else:
                raise ValueError("unknown line")
        except Exception as e:
            errs.append(f"line {n}: {ln!r} ({e})")
    for g, d in games.items():
        if set(d["per"]) != {d["home"], d["away"]}:
            errs.append(f"{g}: needs one H line per team"); continue
        h, a = d["per"][d["home"]], d["per"][d["away"]]
        if len(h) != len(a) or len(h) < 2:
            errs.append(f"{g}: both teams need the same number of periods (2 halves + overtimes)"); continue
        if len(h) == 2 and sum(h) == sum(a):
            errs.append(f"{g}: tied with no overtime")
        if len(h) > 2 and sum(h[:2]) != sum(a[:2]):
            errs.append(f"{g}: overtime listed but regulation was not tied")
        if len(h) > 2 and sum(h) == sum(a):
            errs.append(f"{g}: still tied after the listed overtimes")
        if not all(15 <= x <= 80 for x in h[:2] + a[:2]):
            errs.append(f"{g}: a half outside 15-80 points - check the numbers")
    return games, errs


def settle(r, d: dict) -> tuple[str | None, float | None]:
    h, a = d["per"][d["home"]], d["per"][d["away"]]
    if r.scope == "G":
        hg, ag = (sum(h), sum(a)) if r.ot else (sum(h[:2]), sum(a[:2]))
    else:
        i = int(r.scope[1]) - 1
        hg, ag = h[i], a[i]
    t, s, L = r.type, r.side, r.line
    yn = lambda c: "won" if c else "lost"
    if t in ("ML", "1X2"):
        return yn((hg > ag) if s == "home" else (hg < ag) if s == "away" else (hg == ag)), hg - ag
    if t == "DNB":
        return ("push" if hg == ag else yn((hg > ag) == (s == "home"))), hg - ag
    if t in ("TOTAL", "TEAM_TOTAL"):
        x = hg + ag if t == "TOTAL" else (hg if r.subject == d["home"] else ag)
        return ("push" if x == L else yn((x > L) == (s == "over"))), x
    if t == "HANDICAP":
        m = (hg - ag) if s == "home" else (ag - hg)
        return ("push" if m + L == 0 else yn(m + L > 0)), m
    if t == "ODD_EVEN":
        x = hg + ag if not r.subject else (hg if r.subject == d["home"] else ag)
        return yn((x % 2 == 1) == (s == "odd")), x
    return None, None


def grade(night: str, results: str, con) -> None:
    games, errs = read_results(results)
    for e in errs:
        print("ERROR", e)
    if errs:
        sys.exit("results file rejected: fix it, nothing was graded")
    P = pd.read_sql("SELECT * FROM predictions WHERE night = ?", con, params=[night])
    if P.empty:
        sys.exit(f"no predictions stored for {night}")
    out = []
    for r in P.itertuples():
        d = games.get(r.game)
        res, act = (None, None) if (d is None or r.type == "UNPARSED") else settle(r, d)
        st = "unparsed" if r.type == "UNPARSED" else "graded" if res else "no_result"
        out.append((st, res, act, night, r.capture, r.row_key))
    con.executemany("UPDATE predictions SET status=?, result=?, actual=? WHERE night=? AND capture=? AND row_key=?", out)
    con.commit()
    miss = sorted(set(P.game) - set(games))
    print(f"{night}: {len(games)} games in results, {P.game.nunique()} on the boards, {len(miss)} boards without a result")
    for m in miss[:15]:
        print("   no result:", m)
    extra = sorted(set(games) - set(P.game))
    if extra:
        print(f"   {len(extra)} results match no board (name spelling?): {extra[:8]}")
    print(pd.Series([o[0] + (":" + o[1] if o[1] else "") for o in out]).value_counts().to_string())


# ------------------------------------------------------------------ report + bandit
def decided(con, last_only: bool = True) -> pd.DataFrame:
    d = pd.read_sql("SELECT * FROM predictions WHERE status = 'graded' AND result IN ('won', 'lost')", con)
    if d.empty:
        return d
    if last_only:
        d = d[d.capture == d.groupby("night").capture.transform("max")]
    d = d.copy()
    d["y"] = (d.result == "won").astype(float)
    d["ret"] = d.y * d.odds - 1
    d["book_ret"] = d.p_book * d.odds - 1
    d["cluster"] = d.night + "|" + d.game
    d["family"] = [family(t, s) for t, s in zip(d.type, d.scope)]
    return d


def cstat(x: pd.DataFrame) -> dict:
    g = x.groupby("cluster").ret.mean()
    n = len(g)
    return dict(bets=len(x), games=n, hit=x.y.mean(), need=(1 / x.odds).mean(), ret=g.mean(),
                se=g.std(ddof=1) / math.sqrt(n) if n > 1 else float("nan"))


def cbrier(p, y, cl) -> float:
    return float(((p - y) ** 2).groupby(cl).mean().mean()) if len(y) else float("nan")


def bandit(d: pd.DataFrame) -> pd.DataFrame:
    """P(arm's true return per bet > 0), starting from 'the book's price is right' (prior mean = the return the
    book's own no-vig price implies, sd PRIOR_SD), updated with the per-game mean return. Tiers need 100 games."""
    x = d[(d.p_method == "no_vig") & d.odds.between(1.15, 6.0)].copy()
    fav = x.p_book >= 0.5
    arms = {f"{t} {sc} {s}": g for (t, sc, s), g in x.groupby(["type", "scope", "side"])}
    ml = x[x.type == "ML"]
    arms.update({"* Favourite to win": ml[ml.p_book >= 0.5], "* Underdog to win": ml[ml.p_book < 0.5],
                 "* Home side, handicap": x[(x.type == "HANDICAP") & (x.side == "home") & (x.scope == "G")],
                 "* Away side, handicap": x[(x.type == "HANDICAP") & (x.side == "away") & (x.scope == "G")],
                 "* Big favourite -15 or more, handicap": x[(x.type == "HANDICAP") & (x.scope == "G") & (x.line <= -15)],
                 "* Big underdog +15 or more, handicap": x[(x.type == "HANDICAP") & (x.scope == "G") & (x.line >= 15)],
                 "* Short legs (book 70%+)": x[x.p_book >= 0.7], "* Longshot legs (book under 30%)": x[x.p_book < 0.3],
                 "* Favourite side, any market": x[fav & x.type.isin(["ML", "HANDICAP"])]})
    rows = []
    for name, a in arms.items():
        g = a.groupby("cluster").ret.mean()
        n = len(g)
        if n < 12 or len(a) < 30:
            continue
        se = max(g.std(ddof=1) / math.sqrt(n) * (1 + 2 / n), 0.005)      # widened for few games
        mu0 = a.groupby("cluster").book_ret.mean().mean()
        w = (1 / se ** 2) / (1 / se ** 2 + 1 / PRIOR_SD ** 2)
        pm, ps = w * g.mean() + (1 - w) * mu0, math.sqrt(1 / (1 / se ** 2 + 1 / PRIOR_SD ** 2))
        pb = ncdf(pm / ps)
        rows.append(dict(arm=name, bets=len(a), games=n, hit=a.y.mean(), need=(1 / a.odds).mean(), seen=g.mean(),
                         seen_se=se, book_says=mu0, belief=pm, p_beats=pb,
                         status="CANDIDATE" if pb >= 0.95 and n >= 100 else "SMALL STAKE" if pb >= 0.80 and n >= 100
                         else "watch" if pb >= 0.65 else "-"))
    return pd.DataFrame(rows).sort_values("p_beats", ascending=False) if rows else pd.DataFrame()


def refit(d: pd.DataFrame, con) -> None:
    rows, now = [], pd.Timestamp.now("UTC").isoformat()
    for fam, x in d.dropna(subset=["p_model"]).groupby("family"):
        nc = x.cluster.nunique()
        if nc < MIN_GAMES:
            print(f"  {fam}: {nc} games with a model price (< {MIN_GAMES}): weight stays {START_W[fam]}")
            continue
        k = 1 / x.groupby("cluster").cluster.transform("size")
        dx, e0 = x.p_model - x.p_book, x.y - x.p_book
        sxx = float((k * dx * dx).sum())
        w = float((k * dx * e0).sum() / sxx)
        gs = (k * dx * (e0 - w * dx)).groupby(x.cluster).sum()
        se = max(math.sqrt(float((gs ** 2).sum()) * nc / (nc - 1)) / sxx, 0.03)
        wu = float(np.clip((w / se ** 2 + START_W[fam] / 0.15 ** 2) / (1 / se ** 2 + 1 / 0.15 ** 2), 0, 1.2))
        rows.append((now, fam, nc, w, se, round(wu, 3)))
        print(f"  {fam}: {nc} games, fitted weight {w:+.2f} +- {se:.2f} -> used {wu:.3f}")
    if rows:
        con.executemany("INSERT INTO weights VALUES (?,?,?,?,?,?)", rows)
        con.commit()


def report(con) -> None:
    d = decided(con)
    if d.empty:
        sys.exit("nothing graded yet")
    pct = lambda c: lambda v: f"{v:+.1%}" if c else f"{v:.1%}"
    L = [f"# NCAAB ledger report\n\n{len(d):,} decided bets, **{d.cluster.nunique()} games**, nights {d.night.min()} .. "
         f"{d.night.max()} (last capture of each night). Games are the unit of evidence.\n"]
    B = bandit(d)
    best = f"{B.p_beats.max():.0%}" if len(B) else "n/a"
    L += ["## Verdict\n", f"- Real-money verdict: **{'NO BET' if not len(B) or not B.status.isin(['CANDIDATE', 'SMALL STAKE']).any() else 'see tiers below'}**"
          f" (best arm at {best}; SMALL STAKE needs 80%, CANDIDATE 95%, both 100 games).\n"]
    m = d.dropna(subset=["p_model"])
    L.append("## Negatives first: book vs model vs blend (Brier, lower is better)\n")
    if len(m):
        T = pd.DataFrame([dict(type=t, scope=s, n=len(x), games=x.cluster.nunique(), book=cbrier(x.p_book, x.y, x.cluster),
                               model=cbrier(x.p_model, x.y, x.cluster), blend=cbrier(x.p, x.y, x.cluster))
                          for (t, s), x in m.groupby(["type", "scope"])])
        L += [T.assign(model_minus_book=T.model - T.book).sort_values("model_minus_book", ascending=False).round(4).to_markdown(index=False), ""]
    else:
        L.append("No model prices graded (price with --teams).\n")
    nv = d[d.p_method == "no_vig"]
    C = nv.groupby(pd.cut(nv.p_book, np.arange(0, 1.01, 0.1), include_lowest=True), observed=True).agg(
        n=("y", "size"), games=("cluster", "nunique"), p_book=("p_book", "mean"), hit=("y", "mean"))
    L += ["## Is the book's price right? (two-way priced rows)\n", C.assign(gap=C.hit - C.p_book).round(3).to_markdown(), ""]
    R = pd.DataFrame([dict(type=t, scope=sc, side=s, **cstat(x)) for (t, sc, s), x in nv.groupby(["type", "scope", "side"])])
    L += ["## Return by bet type and side\n", R.sort_values("ret").round(3).to_markdown(index=False), ""]

    A = pd.read_sql("SELECT * FROM predictions WHERE status = 'graded' AND result IN ('won','lost') AND p_method = 'no_vig'", con)
    A["first"], A["last"] = A.groupby("night").capture.transform("min"), A.groupby("night").capture.transform("max")
    two = A[A["first"] != A["last"]]
    L.append("## Open vs close (nights with two or more captures)\n")
    if len(two):
        e, l = two[two.capture == two["first"]], two[two.capture == two["last"]]
        mm = e.merge(l, on=["night", "game", "row_key"], suffixes=("_open", "_close"))
        mm["y"] = (mm.result_close == "won").astype(float)
        mm["cl"] = mm.night + "|" + mm.game
        mv = mm.p_book_close - mm.p_book_open
        rows = [dict(group="all", bets=len(mm), games=mm.cl.nunique(), brier_open=cbrier(mm.p_book_open, mm.y, mm.cl),
                     brier_close=cbrier(mm.p_book_close, mm.y, mm.cl), ret_at_open=(mm.y * mm.odds_open - 1).mean())]
        for nm, x in (("price shortened 3+ pts", mm[mv >= 0.03]), ("price drifted 3+ pts", mm[mv <= -0.03])):
            if len(x):
                rows.append(dict(group=nm, bets=len(x), games=x.cl.nunique(), brier_open=cbrier(x.p_book_open, x.y, x.cl),
                                 brier_close=cbrier(x.p_book_close, x.y, x.cl), ret_at_open=(x.y * x.odds_open - 1).mean()))
        L += [pd.DataFrame(rows).round(4).to_markdown(index=False),
              "\nCLV of an arm = how often its bets are on the side that shortens. Positive CLV is the fastest honest signal.\n"]
    else:
        L.append("None yet: price the same night twice (`--capture 0900`, then `--capture 1730`).\n")
    L.append("## Bandit (arms marked * are hypotheses set before any data)\n")
    if len(B):
        T = B.copy()
        for c in ("hit", "need", "p_beats"):
            T[c] = T[c].map(pct(False))
        for c in ("seen", "seen_se", "book_says", "belief"):
            T[c] = T[c].map(pct(True))
        L += [T.to_markdown(index=False), "",
              "`seen` = raw return so far; `belief` = what is left after starting from 'the book is right'. With dozens of "
              "arms some will look good by chance: only p_beats with 100+ games counts."]
    else:
        L.append("No arm has 12 games yet.")
    out = Path(DB).with_name("ncaab_report.md")
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"{d.cluster.nunique()} games. Best arm P(beats) {best}. Weight refit:")
    refit(d, con)
    print(out)


# ------------------------------------------------------------------ results from ESPN's public scoreboard (UNTESTED)
def fetch_results(date: str, out: str) -> None:
    """Never run where this file was written (host unreachable there). Check the first output against a few box
    scores by hand. Team names are ESPN's 'displayName' ('Baylor Bears'); where the board spells a team differently,
    add a line to results_aliases.csv (espn,board) next to this file. read_results() rejects inconsistent games."""
    import urllib.request
    url = ("https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
           f"?dates={date.replace('-', '')}&groups=50&limit=500")
    js = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read())
    ali = Path(__file__).with_name("results_aliases.csv")
    alias = dict(pd.read_csv(ali).values) if ali.exists() else {}
    L, n = [f"# NCAAB results {date} (US Eastern date) from ESPN scoreboard, fetched {pd.Timestamp.now('UTC'):%Y-%m-%d %H:%M}Z"], 0
    for ev in js.get("events", []):
        c = ev["competitions"][0]
        if not c.get("status", {}).get("type", {}).get("completed"):
            continue
        t = {x["homeAway"]: x for x in c["competitors"]}
        nm = lambda x: alias.get(x["team"]["displayName"], x["team"]["displayName"])
        per = lambda x: " ".join(str(int(float(p.get("value", p.get("displayValue", 0))))) for p in x.get("linescores", []))
        L += ["", f"GAME {nm(t['away'])}@{nm(t['home'])}", f"H {nm(t['home'])}: {per(t['home'])}", f"H {nm(t['away'])}: {per(t['away'])}"]
        n += 1
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text("\n".join(L) + "\n", encoding="utf-8")
    games, errs = read_results(out)
    print(f"{out}: {n} finished games written, {len(errs)} errors")
    for e in errs[:20]:
        print("ERROR", e)


# ------------------------------------------------------------------ selftest
def selftest() -> None:
    import tempfile
    od, mk, n = [], [], [0]

    def M(name, sels):
        ids = []
        for nm, tid, pr, cid, sv in sels:
            n[0] += 1
            od.append(dict(id=n[0], name=nm, typeId=tid, price=pr, oddStatus=0, competitorId=cid, sv=sv))
            ids.append(n[0])
        mk.append(dict(id=1000 + len(mk), name=name, typeId=len(mk), desktopOddIds=[ids]))

    H, A = "Baylor Bears", "Jackson State Tigers"
    M("Győztes (hosszabbítást beleértve)", [(H, 1, 1.20, 1, None), (A, 3, 4.60, 2, None)])
    M("Hendikep (hosszabbítást beleértve)", [(f"{H} -9,5", 1714, 1.90, 1, "-9.5"), (f"{A} +9,5", 1715, 1.90, 2, "-9.5"),
                                            (f"{H} -12,5", 1714, 2.30, 1, "-12.5"), (f"{A} +12,5", 1715, 1.60, 2, "-12.5")])
    M("Pontok száma (hosszabbítást beleértve)", [("150,5 felett", 12, 1.86, None, "150.5"), ("150,5 alatt", 13, 1.95, None, "150.5")])
    M(f"{H} pontok száma (hosszabbítást beleértve)", [("80,5 felett", 12, 1.83, None, "80.5"), ("80,5 alatt", 13, 1.83, None, "80.5")])
    M("1. félidő - Hendikep", [(f"{H} -5,5", 1714, 1.90, 1, "-5.5"), (f"{A} +5,5", 1715, 1.90, 2, "-5.5")])
    M("1. félidő - pontok száma", [("70,5 felett", 12, 1.90, None, "70.5"), ("70,5 alatt", 13, 1.90, None, "70.5")])
    M("1. félidő - 1X2", [(H, 1, 1.50, 1, None), ("Döntetlen", 2, 12.0, None, None), (A, 3, 3.20, 2, None)])
    M("Félidő/végeredmény", [("x/y", 900, 5.0, None, None)])
    M("1. negyed - pontok száma", [("30,5 felett", 12, 1.9, None, "30.5"), ("30,5 alatt", 13, 1.9, None, "30.5")])
    ev = dict(markets=mk, odds=od, competitors=[dict(id=1, name=H), dict(id=2, name=A)])
    tmp = Path(tempfile.mkdtemp())
    for cap, bump in (("0900", 0.0), ("1730", 0.10)):                       # second capture: home favourite shortens
        e2 = json.loads(json.dumps(ev))
        e2["odds"][0]["price"], e2["odds"][1]["price"] = 1.20 - bump / 2, 4.60 + bump * 8
        (tmp / cap).mkdir()
        (tmp / cap / "BAY-JKST.net.json").write_text(json.dumps([dict(url="GetEventDetails", body=e2)]))
    (tmp / "teams.csv").write_text(f"team,adj_o,adj_d,tempo\n{H},118,95,68\n{A},100,108,70\nOther,108,104,68\n")
    # Baylor 40+38 = 78, Jackson State 36+42 = 78 -> overtime 11:6 -> final 89:84
    (tmp / "res.txt").write_text(f"GAME {A}@{H}\nH {H}: 40 38 11\nH {A}: 36 42 6\n")
    global DB
    DB = str(tmp / "t.sqlite")
    con = connect()
    for cap in ("0900", "1730"):
        price("2026-01-01", str(tmp / cap), cap, str(tmp / "teams.csv"), con)
    grade("2026-01-01", str(tmp / "res.txt"), con)
    g = pd.read_sql("SELECT * FROM predictions WHERE capture = '1730'", con)
    R = {(r.type, r.scope, r.side, None if pd.isna(r.line) else r.line): r.result for r in g.itertuples()}
    want = {("ML", "G", "home", None): "won", ("ML", "G", "away", None): "lost",
            ("HANDICAP", "G", "home", -9.5): "lost", ("HANDICAP", "G", "away", 9.5): "won",       # won by 5
            ("HANDICAP", "G", "away", 12.5): "won",
            ("TOTAL", "G", "over", 150.5): "won",                                                 # 173 with overtime
            ("TEAM_TOTAL", "G", "over", 80.5): "won",                                             # Baylor 89
            ("HANDICAP", "H1", "home", -5.5): "lost", ("HANDICAP", "H1", "away", 5.5): "won",     # 40:36
            ("TOTAL", "H1", "over", 70.5): "won", ("1X2", "H1", "home", None): "won", ("1X2", "H1", "draw", None): "lost"}
    bad = [(k, v, R.get(k)) for k, v in want.items() if R.get(k) != v]
    d = decided(con)
    (tmp / "bad.txt").write_text(f"GAME {A}@{H}\nH {H}: 40 38\nH {A}: 36 42\n")
    checks = [("settlement", not bad, bad),
              ("quarters and combos unparsed", (g.type == "UNPARSED").sum() == 3 and set(g[g.type == "UNPARSED"].status) == {"unparsed"}, None),
              ("no-vig pair sums to 1", abs(g[(g.type == "ML")].p_book.sum() - 1) < 1e-9, None),
              ("handicap lines pair up", (g[g.type == "HANDICAP"].p_method == "no_vig").all(), None),
              ("model favours Baylor", float(g[(g.type == "ML") & (g.side == "home")].p_model.iloc[0]) > 0.8, None),
              ("only the last capture is pooled", set(d.capture) == {"1730"} and d.cluster.nunique() == 1, None),
              ("tie without overtime rejected", bool(read_results(str(tmp / "bad.txt"))[1]), None)]
    report(con)
    txt = Path(DB).with_name("ncaab_report.md").read_text()
    checks.append(("open-vs-close section filled", "price shortened" in txt, None))
    for nm, ok, info in checks:
        print(("ok   " if ok else "FAIL ") + nm + (f"  {info}" if info and not ok else ""))
    print("PASS" if all(c[1] for c in checks) else "FAIL")
    sys.exit(0 if all(c[1] for c in checks) else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    sub.add_parser("report")
    s = sub.add_parser("markets"); s.add_argument("boards")
    s = sub.add_parser("price"); s.add_argument("--date", required=True); s.add_argument("--boards", required=True)
    s.add_argument("--capture"); s.add_argument("--teams")
    s = sub.add_parser("results"); s.add_argument("--date", required=True); s.add_argument("--out", required=True)
    s = sub.add_parser("grade"); s.add_argument("--date", required=True); s.add_argument("--results", required=True)
    a = ap.parse_args()
    if a.cmd == "selftest":
        selftest()
    elif a.cmd == "markets":
        B = pd.concat([pd.DataFrame(board_rows(f)) for f in sorted(Path(a.boards).glob("*.json"))])
        pd.set_option("display.width", 220, "display.max_colwidth", 90, "display.max_rows", 2000)
        print(B.groupby(["type", "scope", "market"]).agg(rows=("odds", "size"), games=("game", "nunique")).reset_index()
              .sort_values(["type", "rows"], ascending=[True, False]).to_string(index=False))
        print(f"\n{B.game.nunique()} games; {(B.type != 'UNPARSED').mean():.0%} of {len(B):,} rows typed")
    elif a.cmd == "price":
        price(a.date, a.boards, a.capture, a.teams, connect())
    elif a.cmd == "results":
        fetch_results(a.date, a.out)
    elif a.cmd == "grade":
        grade(a.date, a.results, connect())
    elif a.cmd == "report":
        report(connect())


if __name__ == "__main__":
    main()
