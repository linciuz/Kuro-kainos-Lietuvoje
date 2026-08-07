#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Is the staged diff MEANINGFUL, or just clock noise?

WHY THIS EXISTS
---------------
The pipeline runs every ~15 minutes and committed EVERY time, because several
outputs carry a wall-clock stamp that moves whether or not any price did:

    data/_price_source_debug.json   checked_utc, fetched_utc
    data/stations.json              price_engine.resolved_utc, sources[].fetched_utc
    data/discrepancies.json         generated
    data/sources/*.json             generated
    api/prices.json                 generated_utc

Every commit triggers a GitHub Pages build. On 2026-08-06 that was ~25 builds
in a day, which is how a GitHub Pages incident turned into a mailbox full of
red X's: with builds that frequent, any upstream wobble is guaranteed to catch
several of them.

So: strip timestamp-valued fields, and if NOTHING else differs, skip the commit.

WHAT COUNTS AS NOISE — deliberately narrow
------------------------------------------
A key is ignored only when BOTH hold:
  1. its name is a known build/fetch stamp (ends with `_utc`, or is one of
     generated / generated_at / checked_at / fetched_at), AND
  2. its value parses as an ISO-8601 timestamp.
Condition 2 is what makes this safe: a key that merely LOOKS like a stamp but
carries real data is never dropped.

Deliberately NOT ignored:
  * `updated` in stations.json — that is LEA's PRICE DATE, the single most
    important field in the repo.
  * `price_updated` per station — drives the "🕒 updated 12:39" line users see.
  * data/oil.json — Brent genuinely moves during the day. Real data, real commit.
  * any non-JSON file (HTML, XML, CSV): if it changed at all, it changed.

FAIL-SAFE. Any error, any unparseable file, any git hiccup -> exit 0 (COMMIT).
A bug in this script must never be able to silently stop publishing prices.
Skipping is only ever chosen when we positively PROVED the diff is noise.

  exit 0 -> commit    exit 1 -> nothing meaningful, skip
  --audit N           replay the last N commits and report what WOULD have been
                      skipped (no repo changes; used to measure the real benefit)
"""

import json
import re
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

STAMP_NAMES = {"generated", "generated_at", "checked_at", "fetched_at"}

# MEASURED 2026-08-06: 0 of 39 automated commits that day were pure timestamp
# noise — the stamp-stripping above saves nothing on its own. The real driver is
# these two: Brent and the electricity spot price genuinely move on almost every
# 15-minute run, and each move published a commit and burned a Pages build.
# 29 of those 39 commits (74%) changed NOTHING ELSE.
#
# They are CONTEXT, not the product: a Brent reference in the footer, not a pump
# price. So they ride along with the next real commit instead of causing one.
#
# Why this does not weaken any guarantee:
#   * verify_sources.py's oil/electricity freshness gates run AFTER the commit
#     step and read the freshly fetched file from the WORKSPACE, not from git —
#     so a skipped commit cannot hide a broken fetch. A silent failure still
#     reds the run.
#   * `updated` below forces at least one publish per day, so these can never
#     drift more than a day behind on the live site.
#   * ~10 real commits still happen per day, so in practice they refresh far
#     more often than that.
CONTEXT_ONLY = {"data/oil.json", "data/electricity.json"}
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}")
# Accepts "2026-08-06T15:00:47+00:00Z", "2026-08-06T15:01:12Z", "2026-08-06 15:01:12"
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")


def is_stamp(key, value):
    return (isinstance(value, str)
            and (key.endswith("_utc") or key in STAMP_NAMES)
            and bool(ISO.match(value.strip())))


def strip_stamps(obj):
    if isinstance(obj, dict):
        return {k: strip_stamps(v) for k, v in obj.items() if not is_stamp(k, v)}
    if isinstance(obj, list):
        return [strip_stamps(v) for v in obj]
    return obj


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout


def blob(rev, path):
    """File content at a revision. b'' for a path that doesn't exist there."""
    try:
        return git("show", f"{rev}:{path}")
    except subprocess.CalledProcessError:
        return b""


def meaningful(old_bytes, new_bytes, path):
    """True if this file changed for a reason other than a moved clock."""
    if old_bytes == new_bytes:
        return False
    if not path.endswith(".json"):
        return True                     # HTML/XML/CSV: changed is changed
    try:
        a = strip_stamps(json.loads(old_bytes.decode("utf-8")))
        b = strip_stamps(json.loads(new_bytes.decode("utf-8")))
    except Exception:
        return True                     # unparseable -> assume real
    return json.dumps(a, sort_keys=True, ensure_ascii=False) != \
           json.dumps(b, sort_keys=True, ensure_ascii=False)


def changed_files(old_rev, new_rev=None):
    """(status, path) pairs. new_rev=None means the staged index."""
    args = ["diff", "--name-status", old_rev] + ([new_rev] if new_rev else ["--cached"])
    out = git(*args).decode("utf-8", "replace").splitlines()
    rows = []
    for line in out:
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0][0], parts[-1]))
    return rows


def day_changed(old_bytes, new_bytes):
    """Did this file's `updated` roll over to a new DAY? Forces a daily publish
    for the context feeds so they can never sit more than a day stale."""
    try:
        a = json.loads(old_bytes.decode("utf-8")).get("updated") or ""
        b = json.loads(new_bytes.decode("utf-8")).get("updated") or ""
    except Exception:
        return True                     # can't tell -> publish
    ma, mb = DAY.match(str(a)), DAY.match(str(b))
    return (ma.group(0) if ma else a) != (mb.group(0) if mb else b)


def decide(old_rev, new_rev=None):
    """(should_commit, reasons). Anything added/deleted/renamed is meaningful."""
    reasons, context = [], []
    for status, path in changed_files(old_rev, new_rev):
        if status in ("A", "D", "R", "C"):
            reasons.append(f"{path} ({status})")
            continue
        old = blob(old_rev, path)
        new = blob(new_rev, path) if new_rev else git("show", f":{path}")
        if not meaningful(old, new, path):
            continue
        if path in CONTEXT_ONLY and not day_changed(old, new):
            context.append(path)        # real, but not worth a publish by itself
        else:
            reasons.append(path)
    if reasons:
        return True, reasons + [f"(+context: {', '.join(context)})"] * bool(context)
    return False, context


def audit(n):
    """Replay recent history: how many commits were pure clock noise?"""
    revs = git("log", f"-{n}", "--format=%H %s").decode("utf-8", "replace").splitlines()
    noise = real = 0
    for line in revs:
        sha, _, subject = line.partition(" ")
        if not subject.startswith(("Update prices", "Update fuel prices")):
            continue                    # only judge the automated data commits
        keep, reasons = decide(sha + "^", sha)
        if keep:
            real += 1
            print(f"  COMMIT {sha[:7]} {subject[:34]:<36} <- {', '.join(reasons)[:70]}")
        else:
            noise += 1
            print(f"  skip   {sha[:7]} {subject[:34]:<36} <- {', '.join(reasons) or 'timestamps'} only")
    total = noise + real
    if total:
        print(f"\n{noise}/{total} automated commits would have been skipped "
              f"({noise * 100 // total}%) — that many Pages builds saved.")
    return 0


def main():
    if "--audit" in sys.argv:
        i = sys.argv.index("--audit")
        return audit(int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 40)

    # --range OLD NEW: is NEW meaningfully different from OLD?
    #
    # Used after the publish rebase. MEASURED 2026-08-07T04:48:04Z: two runs
    # started in the SAME SECOND from the SAME base (d52cba9) — GitHub's
    # concurrency group does not reliably serialise simultaneous triggers. Each
    # honestly saw real changes against its own base, so both committed; the
    # loser rebased on top and pushed a second commit carrying nothing but a
    # moved clock and an electricity tick.
    #
    # Re-asking AFTER the rebase is what catches it: once our commit sits on top
    # of the winner's, the question becomes "is anything of ours still worth
    # publishing?" — and for a duplicate poke the answer is no.
    if "--range" in sys.argv:
        i = sys.argv.index("--range")
        old, new = sys.argv[i + 1], sys.argv[i + 2]
        keep, reasons = decide(old, new)
        if keep:
            print(f"[commit] still worth publishing vs {old}: {', '.join(reasons[:6])}")
            return 0
        print(f"[commit] another run already published this data — nothing left "
              f"vs {old}{' (only ' + ', '.join(reasons) + ')' if reasons else ''}.")
        return 1

    keep, reasons = decide("HEAD")
    if keep:
        print(f"[commit] real changes: {', '.join(reasons[:6])}"
              + (f" (+{len(reasons) - 6} more)" if len(reasons) > 6 else ""))
        return 0
    # Say WHICH kind of no-op it was. A log line that claims "timestamps only"
    # when it actually skipped a real Brent move is how the next misdiagnosis
    # starts.
    what = f"context only ({', '.join(reasons)})" if reasons else "timestamps only"
    print(f"[commit] staged diff is {what} — skipping commit. Saves a Pages "
          f"build; pump prices are unchanged and the freshness gates still run "
          f"against the freshly fetched files.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # FAIL-SAFE: never let a bug here stop the pipeline from publishing.
        print(f"::warning::[commit] should_commit.py failed ({type(e).__name__}: {e}) "
              f"— committing anyway, which is the safe direction.")
        sys.exit(0)
