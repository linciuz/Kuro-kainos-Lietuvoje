#!/usr/bin/env python3
"""Apply reviewed geographic overrides without refreshing any station prices.

Uses exact raw station identities from data/coord_overrides.json. Verifies that
all other document and row fields, their order, and raw identity stay unchanged.
No network, geocoding, deletion, price/date stamping or publishing occurs here.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import merge_chain_coords

GEOGRAPHIC_FIELDS = {'lat', 'lon', 'approx', 'coord_source',
                     'display_municipality', 'display_municipality_source',
                     'display_address', 'display_address_source'}

def station_key(station):
    return '|'.join(station.get(field) or '' for field in ('network', 'address', 'municipality'))

def protected_projection(document):
    result = copy.deepcopy(document)
    for station in result['stations']:
        for field in GEOGRAPHIC_FIELDS:
            station.pop(field, None)
    return result

def repair_geography(document):
    overrides = json.loads((ROOT / 'data/coord_overrides.json').read_text(encoding='utf-8'))['overrides']
    before = {station_key(s): s for s in document['stations']}
    if len(before) != len(document['stations']):
        raise RuntimeError('Duplicate raw station identities; refusing ambiguous edits')
    seen = set()
    for override in overrides:
        key = override.get('station_key')
        if not isinstance(key, str) or not key:
            raise RuntimeError('Geography repair requires an exact reviewed station identity')
        if key in seen:
            raise RuntimeError('Duplicate override identity: ' + key)
        seen.add(key)
        if key not in before:
            raise RuntimeError('Reviewed identity absent from current dataset: ' + key)
        if 'lat' in override or 'lon' in override:
            lat, lon = override.get('lat'), override.get('lon')
            if not all(isinstance(v, (float, int)) and math.isfinite(v) for v in (lat, lon)):
                raise RuntimeError('Non-finite or incomplete reviewed coordinate: ' + str(key))
            if not (53.8 < lat < 56.5 and 20.8 < lon < 26.9):
                raise RuntimeError('Reviewed point outside Lithuania screening bounds: ' + str(key))
    repaired = copy.deepcopy(document)
    merge_chain_coords.apply_overrides(repaired['stations'])
    if protected_projection(repaired) != protected_projection(document):
        raise RuntimeError('A price, raw identity, date, timestamp, summary or other protected field changed')
    rows = []
    for original, result in zip(document['stations'], repaired['stations']):
        if original != result:
            rows.append({'station_key': station_key(original),
                         'before': {k: original.get(k) for k in sorted(GEOGRAPHIC_FIELDS)},
                         'after': {k: result.get(k) for k in sorted(GEOGRAPHIC_FIELDS)}})
    return repaired, {'station_rows': len(before), 'geography_changed_rows': len(rows),
                      'coordinate_changed_rows': sum((r['before']['lat'], r['before']['lon']) !=
                           (r['after']['lat'], r['after']['lon']) for r in rows),
                      'display_municipality_changed_rows': sum(r['before']['display_municipality'] !=
                           r['after']['display_municipality'] for r in rows),
                      'display_address_changed_rows': sum(r['before']['display_address'] !=
                           r['after']['display_address'] for r in rows),
                      'prices_raw_identities_order_dates_timestamps_summary_unchanged': True,
                      'changes': rows}

def main():
    os.chdir(ROOT)
    destination = ROOT / 'data/stations.json'
    original_bytes = destination.read_bytes()
    original = json.loads(original_bytes)
    repaired, report = repair_geography(original)
    report['before_sha256'] = hashlib.sha256(original_bytes).hexdigest()
    if repaired != original:
        candidate = (json.dumps(repaired, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        if destination.read_bytes() != original_bytes:
            raise RuntimeError('stations.json changed during review; refusing to overwrite another edit')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='wb', dir=destination.parent,
                 prefix='_station_locations_', suffix='.json', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(candidate)
            os.replace(temporary, destination)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        report['after_sha256'] = hashlib.sha256(candidate).hexdigest()
    else:
        report['after_sha256'] = report['before_sha256']
    report_path = ROOT / 'tools/audit/map_locations_20261006/applied.json'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'changes'}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
