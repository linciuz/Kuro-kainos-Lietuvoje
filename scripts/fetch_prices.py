#!/usr/bin/env python3
"""
Kuro Kainos Lietuvoje - official price fetcher.

Source: Lietuvos energetikos agentūra (LEA), https://www.ena.lt/degalu-kainos-degalinese/
Stations must report 95 petrol / diesel / LPG prices to LEA every working day by 10:00.
LEA publishes a daily Excel with every station's prices (hosted on SharePoint).

This script:
  1. Reads the LEA page and finds the newest "Naujausios degalų kainos" Excel link.
  2. Downloads it anonymously via the OneDrive/SharePoint shares API.
  3. Parses it adaptively (matches Lithuanian header keywords; handles both
     "one column per fuel" and "one row per fuel" layouts).
  4. Writes data/stations.json (per-station rows + national summary).

It prints everything it detects (link, sheet, headers, column mapping, sample
rows) so the first GitHub Actions run shows exactly what came back. If a column
isn't mapped correctly, adjust the KEYWORDS below to match the real headers from
the log - no other code change needed.

NOTE: this has not been run against the live file from the dev environment
(that sandbox can't reach ena.lt/SharePoint). It is meant to run in GitHub
Actions, which has open internet. Treat the first run as a validation run.
"""

import base64
import datetime as dt
import json
import os
import re
import sys

import requests
from openpyxl import load_workbook

# Make stdout/stderr UTF-8 so Lithuanian text in the logs doesn't crash on a
# Windows console (cp1252). No-op where stdout is already UTF-8 (e.g. CI).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

PAGE_URL = "https://www.ena.lt/degalu-kainos-degalinese/"
OUT_PATH = os.path.join("data", "stations.json")
DEBUG_HEADERS_PATH = os.path.join("data", "_debug_headers.json")
UA = "Mozilla/5.0 (compatible; KuroKainosBot/1.0; +https://github.com/)"

# Keyword -> field. Matching is case-insensitive and accent-insensitive on the
# Excel header text. Extend these lists if the log shows unmapped columns.
KEYWORDS = {
    "network":      ["tinkl", "imone", "prekes zenkl", "operatorius", "brand"],
    # NB: no bare "vieta" here - the LEA file has two "Degalines vieta (...)"
    # columns (Savivaldybe and Gyvenviete, gatve); "vieta" would greedily grab
    # the savivaldybe column and steal it from `municipality`.
    "address":      ["gatve", "gyvenviet", "adres", "degalines pavadinim"],
    "municipality": ["savivaldyb"],
    "locality":     ["miest", "kaim"],
    "fuel":         ["degalu rus", "kuro rus", "produkt", "rusis"],   # long-format fuel column
    "price":        ["kaina"],                                        # long-format single price column
}

# Wide-format: a separate price column per fuel. Header keyword -> our fuel key.
FUEL_COLUMN_KEYWORDS = {
    "petrol95": ["95", "benzin", "e95", "a95"],
    "diesel":   ["dyzel", "disel", "d", "dt"],
    "lpg":      ["snd", "dujos", "lpg", "suskystint"],
}

# Long-format: map a fuel-cell value to our fuel key.
FUEL_VALUE_KEYWORDS = {
    "petrol95": ["95", "benzin"],
    "diesel":   ["dyzel", "diesel"],
    "lpg":      ["snd", "dujos", "lpg"],
}


def deaccent(s):
    repl = {"ą": "a", "č": "c", "ę": "e", "ė": "e", "į": "i", "š": "s",
            "ų": "u", "ū": "u", "ž": "z"}
    s = (s or "").lower()
    for a, b in repl.items():
        s = s.replace(a, b)
    return s


def find_latest_excel_link(html):
    """Return the first SharePoint link in the 'Naujausios degalų kainos' area."""
    links = re.findall(r'href="(https://[^"]*sharepoint\.com/[^"]+)"', html, re.I)
    if not links:
        return None
    # Prefer a link whose surrounding text mentions "kainos" (the data file),
    # otherwise just take the first SharePoint link.
    for lk in links:
        if "doc" in lk.lower() or ":x:" in lk.lower():
            return lk
    return links[0]


def _looks_like_xlsx(content):
    # .xlsx is a zip; it always starts with the PK signature.
    return content[:2] == b"PK"


def download_shared_xlsx(share_url):
    """Download an anonymously-shared SharePoint/OneDrive file.

    Primary method (works for SharePoint Online tenant share links like
    ltenergagen.sharepoint.com/:x:/...): append download=1 to the share URL,
    which makes SharePoint stream the raw file and follow redirects to it.
    Falls back to the consumer OneDrive shares API for personal-OneDrive links.
    """
    headers = {"User-Agent": UA}

    # Primary: download=1 on the share link itself (keeps the ?e= access token).
    sep = "&" if "?" in share_url else "?"
    direct = share_url + sep + "download=1"
    try:
        r = requests.get(direct, headers=headers, allow_redirects=True, timeout=60)
        if r.status_code == 200 and _looks_like_xlsx(r.content):
            return r.content
        print(f"[warn] download=1 returned status={r.status_code} "
              f"ct={r.headers.get('content-type','')[:40]} - trying fallback")
    except requests.RequestException as e:
        print(f"[warn] download=1 request failed: {e} - trying fallback")

    # Fallback: consumer OneDrive shares API (u! base64 of the share URL).
    enc = base64.urlsafe_b64encode(share_url.encode()).decode().rstrip("=")
    api = f"https://api.onedrive.com/v1.0/shares/u!{enc}/driveItem/content"
    r = requests.get(api, headers=headers, allow_redirects=True, timeout=60)
    r.raise_for_status()
    return r.content


def header_row_index(ws, max_scan=15):
    """Find the first row that looks like a header (>=3 non-empty text cells)."""
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=max_scan, values_only=True)):
        nonempty = [c for c in row if c not in (None, "")]
        text = [c for c in nonempty if isinstance(c, str)]
        if len(nonempty) >= 3 and len(text) >= 3:
            return i
    return 0


def map_columns(headers):
    """Map column index -> our field name, using KEYWORDS.

    Each column is assigned to at most one role (first match wins) so a single
    column can't satisfy two fields - e.g. the savivaldybe column being read as
    both `address` and `municipality`, which would corrupt the per-station key
    and silently merge distinct stations.
    """
    mapping = {}
    fuel_cols = {}
    used = set()

    # Pass 1: wide-format fuel price columns (only if "kaina" or a fuel word present).
    for idx, h in enumerate(headers):
        hd = deaccent(str(h))
        if not hd.strip():
            continue
        for fuel, kws in FUEL_COLUMN_KEYWORDS.items():
            if fuel in fuel_cols:
                continue
            if any(k in hd for k in kws) and ("kaina" in hd or any(
                    f in hd for f in ["benzin", "dyzel", "dujos", "snd", "lpg"])):
                fuel_cols[fuel] = idx
                used.add(idx)

    # Pass 2: generic text fields, each column claimed by at most one field.
    for idx, h in enumerate(headers):
        if idx in used:
            continue
        hd = deaccent(str(h))
        if not hd.strip():
            continue
        for field, kws in KEYWORDS.items():
            if field in mapping:
                continue
            if any(k in hd for k in kws):
                mapping[field] = idx
                used.add(idx)
                break
    return mapping, fuel_cols


def to_float(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 3)
    s = str(v).strip().replace("€", "").replace("eur", "").replace("/l", "")
    s = s.replace(",", ".")
    m = re.search(r"\d+\.?\d*", s)
    return round(float(m.group()), 3) if m else None


def parse_workbook(xbytes):
    tmp = "_lea_tmp.xlsx"
    with open(tmp, "wb") as f:
        f.write(xbytes)
    wb = load_workbook(tmp, data_only=True, read_only=True)
    # pick the sheet with the most rows
    ws = max(wb.worksheets, key=lambda s: (s.max_row or 0))
    print(f"[info] sheets: {[s.title for s in wb.worksheets]}; using '{ws.title}' "
          f"({ws.max_row} rows x {ws.max_column} cols)")

    rows = list(ws.iter_rows(values_only=True))
    hidx = header_row_index(ws)
    headers = [str(c) if c is not None else "" for c in rows[hidx]]
    print(f"[info] header row #{hidx+1}: {headers}")

    mapping, fuel_cols = map_columns(headers)
    print(f"[info] field mapping: {mapping}")
    print(f"[info] wide fuel columns: {fuel_cols}")

    # save headers for debugging/validation
    os.makedirs("data", exist_ok=True)
    with open(DEBUG_HEADERS_PATH, "w", encoding="utf-8") as f:
        json.dump({"sheet": ws.title, "headers": headers,
                   "mapping": mapping, "fuel_cols": fuel_cols}, f,
                  ensure_ascii=False, indent=2)

    data_rows = rows[hidx + 1:]
    stations = {}

    def key(net, addr, muni):
        return f"{net}|{addr}|{muni}"

    def cell(r, field):
        """Value of `field`'s column for row r, as a clean string ('' if blank).
        Blank Excel cells are None; without this they'd become the literal
        string 'None' and leak in as fake stations (e.g. footer rows)."""
        idx = mapping.get(field)
        if idx is None or idx >= len(r):
            return ""
        v = r[idx]
        return "" if v is None else str(v).strip()

    if fuel_cols:
        # WIDE format: one row per station, a price column per fuel
        for r in data_rows:
            if not r or all(c in (None, "") for c in r):
                continue
            net = cell(r, "network")
            addr = cell(r, "address")
            muni = cell(r, "municipality")
            loc = cell(r, "locality")
            # A real station always has a company; blank-company rows are the
            # spreadsheet's footer (national average / "Duomenys: N degalines").
            if not net:
                continue
            st = stations.setdefault(key(net, addr, muni), {
                "network": net, "address": addr, "municipality": muni,
                "locality": loc, "petrol95": None, "diesel": None, "lpg": None})
            for fuel, ci in fuel_cols.items():
                if ci < len(r):
                    val = to_float(r[ci])
                    if val:
                        st[fuel] = val
    else:
        # LONG format: one row per (station, fuel); needs fuel + price columns
        if "fuel" not in mapping or "price" not in mapping:
            print("[error] Could not identify fuel/price columns. "
                  "Check the header log above and update KEYWORDS.")
            sys.exit(2)
        for r in data_rows:
            if not r or all(c in (None, "") for c in r):
                continue
            net = cell(r, "network")
            addr = cell(r, "address")
            muni = cell(r, "municipality")
            loc = cell(r, "locality")
            fuel_raw = deaccent(cell(r, "fuel"))
            price = to_float(r[mapping["price"]]) if mapping["price"] < len(r) else None
            if not net or price is None:
                continue
            fuel = None
            for fk, kws in FUEL_VALUE_KEYWORDS.items():
                if any(k in fuel_raw for k in kws):
                    fuel = fk
                    break
            if not fuel:
                continue
            st = stations.setdefault(key(net, addr, muni), {
                "network": net, "address": addr, "municipality": muni,
                "locality": loc, "petrol95": None, "diesel": None, "lpg": None})
            st[fuel] = price

    return list(stations.values())


def summarize(stations):
    out = {}
    for fuel in ("petrol95", "diesel", "lpg"):
        vals = [s[fuel] for s in stations if s.get(fuel)]
        if vals:
            out[fuel] = {"min": round(min(vals), 3),
                         "avg": round(sum(vals) / len(vals), 3),
                         "max": round(max(vals), 3),
                         "count": len(vals)}
    return out


def main():
    print(f"[info] fetching page: {PAGE_URL}")
    html = requests.get(PAGE_URL, headers={"User-Agent": UA}, timeout=60).text
    link = find_latest_excel_link(html)
    if not link:
        print("[error] No SharePoint Excel link found on the page.")
        sys.exit(1)
    print(f"[info] latest Excel link: {link}")

    xbytes = download_shared_xlsx(link)
    print(f"[info] downloaded {len(xbytes)} bytes")

    stations = parse_workbook(xbytes)
    print(f"[info] parsed {len(stations)} stations")
    for s in stations[:5]:
        print("   sample:", s)

    if not stations:
        print("[error] 0 stations parsed - aborting so we don't overwrite good data.")
        sys.exit(3)

    payload = {
        "updated": dt.date.today().isoformat(),
        "source": "Lietuvos energetikos agentūra (ena.lt)",
        "source_url": PAGE_URL,
        "summary": summarize(stations),
        "stations": sorted(stations, key=lambda s: (s.get("municipality") or "", s.get("network") or "")),
    }
    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[ok] wrote {OUT_PATH} ({len(stations)} stations)")


if __name__ == "__main__":
    main()
