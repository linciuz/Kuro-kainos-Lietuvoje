#!/usr/bin/env python3
"""
Full LEA station list from the Power BI report embedded on ena.lt.

The daily SharePoint Excel only lists stations that reported a PRICE that day
(~733). LEA's Power BI holds the complete ~820-station universe, including
regional operators (Saurida, Trevena, Savicko, ...) that don't report prices.
This adds those missing stations to data/stations.json as PRICE-LESS location
pins (we know which fuels they sell, but not a retail price — the Power BI's
own "Kaina" is a pre-tax figure, ~25-30% below pump, so it is NOT used as a
price).

Flow: scrape the ?r= embed token from ena.lt -> resourceKey; POST a
SemanticQuery to the public Power BI querydata API; decode the compressed DSR
(dict-indexed groups + repeat-bitmask carry-forward, incremental value dicts);
merge stations whose address we don't already have. Resilient: on any fetch
failure it falls back to the last committed data/sources/lea_powerbi.json so the
extra pins persist. Non-fatal by design.
"""

import base64
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request

STATIONS = os.path.join("data", "stations.json")
CACHE = os.path.join("data", "sources", "lea_powerbi.json")
ENA_PAGE = "https://www.ena.lt/degalu-kainos-degalinese/"
PBI_HOST = "https://wabi-west-europe-e-primary-api.analysis.windows.net"  # cluster c=9
DATASET_ID = "300e7751-6e12-405c-890d-fee9774f760a"
REPORT_ID = "60850ad8-c1ee-47ef-8a08-339eaee7bff4"
MODEL_ID = 8451841
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
FUELMAP = {"95 benzinas": "petrol95", "Dyzelinas": "diesel", "SND": "lpg"}
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def _get(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90, context=_CTX) as r:
        return r.read()


def resource_key():
    html = _get(ENA_PAGE).decode("utf-8", "replace")
    m = re.search(r'[?&]r=([A-Za-z0-9%_\-]{40,})', html)
    if not m:
        raise RuntimeError("Power BI ?r= token not found on ena.lt")
    tok = urllib.parse.unquote(m.group(1))
    return json.loads(base64.b64decode(tok + "=" * (-len(tok) % 4)))["k"]


def _query_body(entity):
    col = lambda prop, name: {"Column": {"Expression": {"SourceRef": {"Source": "d"}}, "Property": prop}, "Name": name}
    return {"version": "1.0.0", "queries": [{"Query": {"Commands": [{"SemanticQueryDataShapeCommand": {
        "Query": {"Version": 2, "From": [{"Name": "d", "Entity": entity, "Type": 0}], "Select": [
            col("Įmonė (Degalinių tinklas)", "company"),
            col("Degalinės vieta (Savivaldybė)", "savivaldybe"),
            col("Degalinės vieta (Gyvenvietė, gatvė)", "adresas"),
            col("Degalų tipas", "tipas"),
            {"Aggregation": {"Expression": {"Column": {"Expression": {"SourceRef": {"Source": "d"}}, "Property": "Kaina"}}, "Function": 0}, "Name": "kaina"}]},
        "Binding": {"Primary": {"Groupings": [{"Projections": [0, 1, 2, 3, 4]}]},
                    "DataReduction": {"DataVolume": 6, "Primary": {"Window": {"Count": 30000}}}, "Version": 1}}}]},
        "CacheKey": "", "QueryId": "",
        "ApplicationContext": {"DatasetId": DATASET_ID, "Sources": [{"ReportId": REPORT_ID}]}}],
        "cancelQueries": [], "modelId": MODEL_ID}


def _decode_dsr(ds):
    """Decode a Power BI DataShapeResult: dict-indexed group columns + a repeat
    bitmask that carries forward unchanged columns; value dicts grow incrementally
    (a new value arrives as a literal string and is appended to its dict)."""
    dicts = ds.get("ValueDicts", {})
    dm0 = ds["PH"][0]["DM0"]
    cols = dm0[0]["S"]
    prev = [None] * len(cols)
    out = []
    for row in dm0:
        R = row.get("R", 0)
        NUL = row.get("Ø", 0)
        C = row.get("C", [])
        ci = 0
        cur = []
        for i, col in enumerate(cols):
            if R & (1 << i):
                cur.append(prev[i])
            elif NUL & (1 << i):
                cur.append(None)
            else:
                v = C[ci]; ci += 1
                dn = col.get("DN")
                if dn:
                    d = dicts[dn]
                    if isinstance(v, int):
                        cur.append(d[v])
                    else:
                        d.append(v); cur.append(v)
                else:
                    cur.append(v)
        out.append(cur)
        prev = cur
    return out


def fetch_powerbi():
    rk = resource_key()
    hdr = {"User-Agent": UA, "Content-Type": "application/json;charset=UTF-8",
           "X-PowerBI-ResourceKey": rk, "Origin": "https://app.powerbi.com", "Referer": "https://app.powerbi.com/"}
    resp = _get(PBI_HOST + "/public/reports/querydata?synchronous=true",
                data=json.dumps(_query_body("degalu_kainos")).encode(), headers=hdr)
    ds = json.loads(resp)["results"][0]["result"]["data"]["dsr"]["DS"][0]
    rows = _decode_dsr(ds)
    stations = {}
    for company, muni, addr, tipas, _kaina in rows:
        if not addr:
            continue
        s = stations.setdefault((company, addr), {"company": company, "municipality": muni,
                                                   "address": addr, "fuels": []})
        f = FUELMAP.get(tipas)
        if f and f not in s["fuels"]:
            s["fuels"].append(f)
    result = sorted(stations.values(), key=lambda s: (s["company"], s["address"]))
    if len(result) < 400:
        raise RuntimeError(f"Power BI returned only {len(result)} stations — refusing (looks broken)")
    return result


def norm_addr(a):
    # Order-independent: the Excel writes "Locality, Street, Postcode" while the
    # Power BI writes "Street, Locality" — split on commas, drop the postcode,
    # sort the parts, so the same station matches regardless of component order.
    parts = []
    for p in (a or "").lower().split(","):
        p = re.sub(r'\b\d{5}\b', "", p)
        p = re.sub(r'\s+', " ", p).strip()
        if p:
            parts.append(p)
    return "|".join(sorted(parts))


def committed_pbi():
    try:
        return json.load(open(CACHE, encoding="utf-8")).get("stations", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def main():
    # --merge-only (used by the frequent price workflow) skips the Power BI call
    # and just re-adds the committed registry stations that fetch_prices dropped.
    if "--merge-only" in sys.argv:
        pbi = committed_pbi()
        if pbi is None:
            print("[warn] no committed Power BI cache — nothing to merge")
            return
        print(f"[ok] merge-only: {len(pbi)} committed Power BI stations")
    else:
        try:
            pbi = fetch_powerbi()
            json.dump({"count": len(pbi), "stations": pbi}, open(CACHE, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            print(f"[ok] Power BI: {len(pbi)} stations in LEA's full registry")
        except Exception as e:
            print(f"[warn] Power BI fetch failed: {e}")
            pbi = committed_pbi()
            if pbi is None:
                print("[warn] no committed Power BI cache — skipping extra stations")
                return
            print(f"[warn] using {len(pbi)} previously-committed Power BI stations")

    data = json.load(open(STATIONS, encoding="utf-8"))
    have = {norm_addr(s.get("address")) for s in data.get("stations", [])}
    added = 0
    for s in pbi:
        if norm_addr(s["address"]) in have:
            continue
        data["stations"].append({
            "network": s["company"], "address": s["address"], "municipality": s["municipality"],
            "locality": "", "petrol95": None, "diesel": None, "lpg": None,
            "fuels": sorted(s["fuels"]), "no_price": True, "lat": None, "lon": None,
        })
        have.add(norm_addr(s["address"]))
        added += 1
    json.dump(data, open(STATIONS, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[ok] added {added} price-less stations -> stations.json now {len(data['stations'])}")


if __name__ == "__main__":
    main()
