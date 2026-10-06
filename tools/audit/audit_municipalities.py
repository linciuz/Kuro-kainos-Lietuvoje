"""Offline coordinate/municipality audit using official RC EPSG:3346 polygons.

This establishes where a supplied pin falls, not whether that pin identifies the
actual station. Never use its mismatches alone to change source station identity.
Requires optional Shapely2 and pyproj3 audit dependencies. Download the current
official RC municipality JSON separately and pass its path explicitly. This
audit reads station and boundary inputs; it never fetches or repairs product data.
"""
import argparse
import collections
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import unicodedata

HERE = Path(__file__).resolve().parent
import pyproj
import shapely
from shapely.geometry import Point, shape
from shapely.strtree import STRtree
from shapely.validation import explain_validity


def normalize_label(value):
    # Keep city/district words and accented letters; normalize typography only.
    return "".join(c for c in unicodedata.normalize("NFC", value or "").casefold()
                   if not c.isspace() and c != ".")


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stations", type=Path, required=True)
    parser.add_argument("--boundaries", type=Path, required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--edge-threshold-m", type=float, default=100.0)
    args = parser.parse_args()
    repository = HERE.parents[1]
    allowed_roots = [repository / ".claude", HERE / "reports"]
    if not any(args.output_prefix.resolve().is_relative_to(root.resolve()) for root in allowed_roots):
        parser.error("Output prefix must stay inside .claude or tools/audit/reports")
    if not math.isfinite(args.edge_threshold_m) or args.edge_threshold_m < 0:
        parser.error("Edge threshold must be finite and non-negative")
    output_paths = [Path(str(args.output_prefix) + suffix).resolve()
                    for suffix in (".json", "-mismatches.json", "-municipality-codes.json")]
    if any(p in (args.stations.resolve(), args.boundaries.resolve()) for p in output_paths):
        parser.error("Audit outputs must not overwrite station or boundary inputs")
    station_bytes = args.stations.read_bytes()
    boundary_bytes = args.boundaries.read_bytes()
    station_doc, boundary_doc = json.loads(station_bytes), json.loads(boundary_bytes)
    if boundary_doc.get("crs", {}).get("properties", {}).get("name") != "EPSG:3346":
        raise ValueError("Expected official boundary geometry in EPSG:3346")
    features = boundary_doc["features"]
    geometries = [shape(f["geometry"]) for f in features]
    labels = [f["properties"]["SAV_PAV"] for f in features]
    codes = [str(f["properties"]["SAV_KODAS"]) for f in features]
    label_indexes = {normalize_label(label): i for i, label in enumerate(labels)}
    if len(set(codes)) != 60 or len(geometries) != 60:
        raise ValueError("Expected 60 unique municipal codes and geometries")
    invalid = [{"code": codes[i], "municipality": labels[i],
                "reason": explain_validity(g)} for i, g in enumerate(geometries)
               if not g.is_valid or g.is_empty]
    if invalid:
        raise ValueError("Official geometry invalid; stop before classifying stations: " + json.dumps(invalid, ensure_ascii=False))
    tree = STRtree(geometries)
    boundaries = [g.boundary for g in geometries]
    boundary_tree = STRtree(boundaries)
    transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3346", always_xy=True)
    records = []
    raw_counts, display_counts = collections.Counter(), collections.Counter()
    stations = station_doc["stations"]
    station_keys = collections.Counter()
    for index, station in enumerate(stations):
        raw = station.get("municipality") or ""
        display = (station.get("display_municipality")
                   if station.get("display_municipality") and station.get("display_municipality_source") else raw)
        key = "|".join(station.get(k) or "" for k in ("network", "address", "municipality"))
        station_keys[key] += 1
        raw_counts[normalize_label(raw)] += 1
        display_counts[normalize_label(display)] += 1
        record = {"input_index": index, "station_key": key,
                  "network": station.get("network"), "address": station.get("address"),
                  "locality": station.get("locality"), "raw_municipality": raw,
                  "display_municipality": station.get("display_municipality"),
                  "display_municipality_source": station.get("display_municipality_source"),
                  "effective_municipality": display,
                  "lat": station.get("lat"), "lon": station.get("lon"),
                  "coord_source": station.get("coord_source"), "approx": station.get("approx"),
                  "actual_station_identity_verified_by_this_audit": False}
        lat, lon = record["lat"], record["lon"]
        valid_coord = (isinstance(lat, (int, float)) and not isinstance(lat, bool)
                       and isinstance(lon, (int, float)) and not isinstance(lon, bool)
                       and math.isfinite(lat) and math.isfinite(lon)
                       and -90 <= lat <= 90 and -180 <= lon <= 180)
        if not valid_coord:
            record["classification"] = "invalid_or_missing_coordinates"
            records.append(record)
            continue
        x, y = transformer.transform(lon, lat)
        if not math.isfinite(x) or not math.isfinite(y):
            record["classification"] = "projection_failed"
            records.append(record)
            continue
        point = Point(x, y)
        hits = [int(i) for i in tree.query(point) if geometries[int(i)].covers(point)]
        nearest_index = int(boundary_tree.nearest(point))
        distance = point.distance(boundaries[nearest_index])
        record.update({"lks94_x": round(x, 3), "lks94_y": round(y, 3),
                       "containing_municipalities": [{"code": codes[i], "name": labels[i]} for i in hits],
                       "nearest_boundary_distance_m": round(distance, 3),
                       "nearest_boundary_municipality": labels[nearest_index],
                       "near_boundary": distance <= args.edge_threshold_m})
        for label_kind, label in (("raw", raw), ("effective", display)):
            municipality_index = label_indexes.get(normalize_label(label))
            record[f"distance_to_{label_kind}_municipality_polygon_m"] = (
                round(point.distance(geometries[municipality_index]), 3)
                if municipality_index is not None else None)
        if not hits:
            record["classification"] = "outside_all_municipalities"
        elif len(hits) != 1:
            record["classification"] = "overlapping_or_boundary_ambiguous"
        else:
            match_raw = normalize_label(raw) == normalize_label(labels[hits[0]])
            match_effective = normalize_label(display) == normalize_label(labels[hits[0]])
            record.update({"raw_matches_polygon": match_raw,
                           "effective_matches_polygon": match_effective})
            if match_effective:
                record["classification"] = ("raw_mismatch_display_matches" if not match_raw else
                                            "label_matches_coordinate_polygon")
            else:
                record["classification"] = "label_mismatch_coordinate_polygon"
            if record["near_boundary"]:
                record["classification"] += "_near_boundary"
        records.append(record)
    raw_labels = {s.get("municipality") or "" for s in stations}
    effective_labels = {r["effective_municipality"] for r in records}
    mapping = [{"municipality_code": codes[i], "official_label": labels[i],
                "fuelis_raw_labels": sorted(s for s in raw_labels if normalize_label(s) == normalize_label(labels[i])),
                "fuelis_effective_labels": sorted(s for s in effective_labels if normalize_label(s) == normalize_label(labels[i])),
                "fuelis_raw_station_count": raw_counts[normalize_label(labels[i])],
                "fuelis_effective_station_count": display_counts[normalize_label(labels[i])],
                "boundary_formav_dat": features[i]["properties"].get("formav_dat")}
               for i in sorted(range(len(features)), key=lambda i: int(codes[i]))]
    mismatches = [r for r in records if r.get("raw_matches_polygon") is False or
                  r.get("effective_matches_polygon") is False or not r.get("containing_municipalities") or
                  len(r.get("containing_municipalities", [])) != 1]
    try:
        head = subprocess.check_output(["git", "-C", str(args.stations.parent), "rev-parse", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        head = None
    summary = {"station_count": len(stations), "valid_coordinate_count": sum("lks94_x" in r for r in records),
               "unique_station_key_count": len(station_keys),
               "duplicate_station_key_count": sum(n-1 for n in station_keys.values() if n > 1),
               "unique_containing_municipality_count": sum(len(r.get("containing_municipalities", [])) == 1 for r in records),
               "raw_label_mismatches": sum(r.get("raw_matches_polygon") is False for r in records),
               "effective_label_mismatches": sum(r.get("effective_matches_polygon") is False for r in records),
               "outside_all_municipalities": sum(r["classification"] == "outside_all_municipalities" for r in records),
               "multiple_containing_municipalities": sum(len(r.get("containing_municipalities", [])) > 1 for r in records),
               "near_boundary_count": sum(bool(r.get("near_boundary")) for r in records),
               "edge_distance_counts_m": {str(m): sum(r.get("nearest_boundary_distance_m", math.inf) <= m for r in records) for m in (1, 5, 25, 50, 100, 250)},
               "classification_counts": dict(collections.Counter(r["classification"] for r in records)),
               "coordinate_source_counts": dict(collections.Counter(str(r["coord_source"]) for r in records)),
               "approximate_coordinate_count": sum(bool(r["approx"]) for r in records),
               "raw_labels_without_official_name_match": sorted(s for s in raw_labels if normalize_label(s) not in {normalize_label(v) for v in labels})}
    report = {"audited_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "station_input_path": str(args.stations.resolve()), "station_git_head": head,
              "station_input_sha256": hashlib.sha256(station_bytes).hexdigest(),
              "station_data_updated": station_doc.get("updated"),
              "boundary_input_path": str(args.boundaries.resolve()),
              "boundary_sha256": hashlib.sha256(boundary_bytes).hexdigest(),
              "boundary_source_url": "https://www.registrucentras.lt/aduomenys/?byla=adr_gra_savivaldybes.json",
              "boundary_catalogue_url": "https://data.gov.lt/datasets/1345/",
              "boundary_license": "CC BY 4.0; VĮ Registrų centras",
              "boundary_crs": "EPSG:3346", "station_coordinate_crs_assumption": "EPSG:4326 (lon,lat)",
              "boundary_feature_count": len(features), "invalid_boundary_geometries": invalid,
              "boundary_formav_dat_counts": dict(collections.Counter(f["properties"].get("formav_dat") for f in features)),
              "method": "Transform supplied pins WGS84->LKS94 with PROJ; GEOS polygon.covers, with holes and multipolygons; nearest boundary distance in LKS94 metres.",
              "coordinate_transform_description": transformer.description,
              "coordinate_transform_reported_accuracy_m": transformer.accuracy,
              "edge_threshold_m": args.edge_threshold_m,
              "edge_threshold_note": "Analyst review band, not a legal boundary precision or GPS-accuracy claim.",
              "limitations": ["The audit proves pin containment only; station identity and correctness of supplied coordinates require separate evidence.",
                             "Approximate/geocoded pins must not determine municipality corrections without trustworthy station coordinates.",
                             "Source and display municipality fields and original station keys were preserved.",
                             "Boundary snapshot formation date is source metadata, not a claim that every boundary changed then."],
              "software": {"python": sys.version.split()[0], "shapely": shapely.__version__, "pyproj": pyproj.__version__, "proj": pyproj.proj_version_str},
              "summary": summary, "municipality_mapping": mapping, "stations": records}
    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    write_json(Path(str(args.output_prefix) + ".json"), report)
    write_json(Path(str(args.output_prefix) + "-mismatches.json"), {"summary": summary, "mismatches": mismatches,
                                                                 "near_boundary_stations": [r for r in records if r.get("near_boundary")]})
    write_json(Path(str(args.output_prefix) + "-municipality-codes.json"), mapping)
    print(json.dumps({"report_prefix": str(args.output_prefix), "summary": summary,
                      "boundary_formav_dat_counts": report["boundary_formav_dat_counts"], "software": report["software"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
