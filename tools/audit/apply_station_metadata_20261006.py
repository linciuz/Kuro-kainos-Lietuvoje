#!/usr/bin/env python3
"""One-time, local station metadata repair from the recorded LEA observation.

Run from this worktree after updating its Git data. No network, price fetching,
geocoding, publishing, date stamping, or summary recalculation occurs here.
Only supported-fuel/priceless metadata and the attributed display-area override
may change. All 765 recorded active source station keys must already exist.
"""

import copy
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_prices
import merge_chain_coords
import price_engine

METADATA_FIELDS = {"fuels", "no_price", "display_municipality", "display_municipality_source"}


def station_key(station):
    return fetch_prices.station_key(station.get("network") or "", station.get("address") or "",
                                    station.get("municipality") or "")


def protected_projection(document):
    """Protect every payload/row field except the explicitly allowed metadata."""
    protected = copy.deepcopy(document)
    for station in protected["stations"]:
        for field in METADATA_FIELDS:
            station.pop(field, None)
    return protected


def repair_station_metadata(document, source):
    groups, ids = {}, set()
    for row in source["data"]:
        if not row.get("is_active", True):
            continue
        if not row.get("company_name"):
            raise RuntimeError("Active LEA row lacks company_name; refusing an incomplete repair")
        key = fetch_prices.station_key(row["company_name"].strip(), row["address"].strip(),
                                        row["municipality"].strip())
        groups.setdefault(key, []).append(row)
        ids.add(row["gas_station_id"])
    if len(groups) != 765 or len(ids) != 765:
        raise RuntimeError(f"Recorded source identity changed: {len(groups)} keys/{len(ids)} IDs, expected 765/765")

    result = copy.deepcopy(document)
    current = {station_key(s): s for s in result["stations"]}
    if len(current) != len(result["stations"]):
        raise RuntimeError("Current stations.json contains duplicate raw station keys")
    missing = sorted(set(groups) - set(current))
    if missing:
        raise RuntimeError("Source keys absent from current stations.json: " + json.dumps(missing, ensure_ascii=False))

    missing_before, priceless = [], []
    for key, rows in groups.items():
        station = current[key]
        fuels = {price_engine.PORTAL_FUELS[r["fuel_type"]] for r in rows
                 if r.get("fuel_type") in price_engine.PORTAL_FUELS}
        if not fuels:
            raise RuntimeError(f"Recorded active station has no supported fuels: {key}")
        has_price = any(station.get(f) is not None for f in price_engine.FUELS)
        if not has_price and (not station.get("no_price") or not station.get("fuels")):
            missing_before.append(key)
        station["fuels"] = [f for f in price_engine.FUELS if f in fuels]
        if has_price:
            station.pop("no_price", None)
        else:
            station["no_price"] = True
            priceless.append({"station_key": key, "fuels": station["fuels"]})
    if len(priceless) != 8:
        raise RuntimeError(f"Current data has {len(priceless)} source stations without prices, expected eight; re-audit before repairing")

    merge_chain_coords.apply_overrides(result["stations"])
    if protected_projection(result) != protected_projection(document):
        raise RuntimeError("Repair changed a price, coordinate, raw identity, date, timestamp, summary or other protected field")
    after = {station_key(s): s for s in result["stations"]}
    if set(after) != set(current):
        raise RuntimeError("Raw station identities changed")
    changed_rows = sum(before != new for before, new in zip(document["stations"], result["stations"]))
    report = {
        "price_date": document["updated"],
        "source_active_ids": len(ids),
        "source_active_keys": len(groups),
        "source_keys_present": len(set(groups) & set(current)),
        "total_station_rows": len(result["stations"]),
        "metadata_changed_rows": changed_rows,
        "missing_priceless_metadata_before": len(missing_before),
        "missing_priceless_metadata_after": 0,
        "source_stations_without_prices": priceless,
        "display_area_overrides": [
            {"station_key": station_key(s), "raw_municipality": s["municipality"],
             "display_municipality": s["display_municipality"],
             "source": s["display_municipality_source"]}
            for s in result["stations"] if s.get("display_municipality") and s.get("display_municipality_source")
        ],
        "prices_coordinates_raw_identities_dates_timestamps_summary_unchanged": True,
    }
    return result, report


def main():
    os.chdir(ROOT)  # apply_overrides uses the worktree's data/coord_overrides.json
    fixture = ROOT / "tests/fixtures/lea-prices-20261006.json.gz"
    fixture_metadata = json.loads(fixture.with_name("lea-prices-20261006.metadata.json").read_text(encoding="utf-8"))
    source_bytes = gzip.decompress(fixture.read_bytes())
    if hashlib.sha256(source_bytes).hexdigest() != fixture_metadata["payload_sha256"]:
        raise RuntimeError("Recorded LEA source SHA-256 mismatch")
    source = json.loads(source_bytes)
    if len(source["data"]) != fixture_metadata["raw_rows"]:
        raise RuntimeError("Recorded LEA row count mismatch")

    destination = ROOT / "data/stations.json"
    original_bytes = destination.read_bytes()
    original = json.loads(original_bytes)
    repaired, report = repair_station_metadata(original, source)
    report["source_observed_utc"] = fixture_metadata["captured_utc"]
    report["source_payload_sha256"] = fixture_metadata["payload_sha256"]
    report["checked_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    report["before_file_sha256"] = hashlib.sha256(original_bytes).hexdigest()
    if repaired == original:
        report["file_written"] = False
        report["after_file_sha256"] = report["before_file_sha256"]
    else:
        candidate = (json.dumps(repaired, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if destination.read_bytes() != original_bytes:
            raise RuntimeError("stations.json changed during verification; refusing to overwrite another edit")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=destination.parent,
                                             prefix="_station_metadata_", suffix=".json", delete=False) as handle:
                temporary = pathlib.Path(handle.name)
                handle.write(candidate)
            os.replace(temporary, destination)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        report["file_written"] = True
        report["after_file_sha256"] = hashlib.sha256(candidate).hexdigest()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
