"""Identity joins exercised with recorded LEA/operator address evidence."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('merge_chain_coords', ROOT / 'scripts/merge_chain_coords.py')
merge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(merge)
FIXTURE = json.loads((ROOT / 'tests/fixtures/chain_coordinate_cases_20261006.json').read_text(encoding='utf-8'))


def entry(network, address):
    return next(e for e in FIXTURE['directory'] if e['network'] == network and e['address'] == address)


class ChainCoordinateTests(unittest.TestCase):
    def test_shared_house_number_does_not_join_different_streets(self):
        directory = [entry('Emsi', 'Jeruzalės g. 2, Vilnius, Vilniaus apskritis, Lietuva')]
        stations = [FIXTURE['stations']['lea_emsi_jeruzales'], FIXTURE['stations']['lea_emsi_spygliu']]
        matches, stats = merge.match_chain_addresses(stations, directory)
        self.assertEqual([index for index, _ in matches], [0])
        self.assertEqual(stats['unmatched'], 1)

    def test_village_and_street_identity_prevent_83km_viada_misjoin(self):
        station = FIXTURE['stations']['lea_viada_silagalis']
        directory = [e for e in FIXTURE['directory'] if e['network'] == 'Viada']
        matches, stats = merge.match_chain_addresses([station], directory)
        self.assertEqual(matches, [])
        self.assertEqual(stats['unmatched'], 1)
        # The verified Aplinkkelio/Panevėžio-aplinkl alias belongs to an exact
        # attributed override; it must not make the Rokiškis candidate viable.
        wrong = entry('Viada', 'Rokiškis, Panevėžio g. 5')
        correct = entry('Viada', 'Panevėžio r., Šilagalis, Aplinkkelio g. 5')
        self.assertGreater(merge.haversine(wrong['lat'], wrong['lon'], correct['lat'], correct['lon']), 83)

    def test_house_suffix_is_complete_identity_not_a_shared_numeric_bonus(self):
        station = FIXTURE['stations']['lea_viada_vievis']
        for address in ('Vievis, Kauno g. 55A', 'Vievis, Kauno g. 26'):
            with self.subTest(address=address):
                self.assertFalse(merge.address_identity_agrees(station, entry('Viada', address)))
        self.assertEqual(merge.address_identity(station['address'])['houses'], ('26a',))

    def test_neste_separate_city_is_used_for_same_street_and_house(self):
        neste = entry('Neste', 'Kauno g. 26')
        self.assertTrue(merge.address_identity_agrees(FIXTURE['stations']['lea_neste_kauno'], neste))
        # Both address strings are actual operator records, with the same
        # street and house. Vievis must not be assigned to Neste's Vilnius pin.
        vievis = entry('Viada', 'Vievis, Kauno g. 26')
        self.assertFalse(merge.address_identity_agrees(vievis, neste))

    def test_multiple_actual_operator_points_for_one_address_are_ambiguous(self):
        station = FIXTURE['stations']['lea_emsi_palemono']
        directory = [e for e in FIXTURE['directory'] if e['network'] == 'Emsi' and 'Palemono' in e['address']]
        self.assertEqual(len(directory), 2)
        matches, stats = merge.match_chain_addresses([station], directory)
        self.assertEqual(matches, [])
        self.assertEqual(stats['ambiguous'], 1)

    def test_distinct_actual_addresses_cannot_share_one_directory_point(self):
        # Published pre-repair records really contain this bad shared point.
        # Using them as directory evidence tests collision refusal, without
        # inventing a new coordinate or claiming these are operator records.
        stations = [FIXTURE['stations']['lea_emsi_jeruzales'], FIXTURE['stations']['lea_emsi_spygliu']]
        self.assertEqual((stations[0]['lat'], stations[0]['lon']),
                         (stations[1]['lat'], stations[1]['lon']))
        matches, stats = merge.match_chain_addresses(stations, stations)
        self.assertEqual(matches, [])
        self.assertEqual(stats['collision'], 2)

    def test_same_actual_identity_aliases_can_share_the_verified_point(self):
        station = FIXTURE['stations']['lea_viada_rokiskis']
        operator = entry('Viada', 'Rokiškis, Panevėžio g. 5')
        matches, stats = merge.match_chain_addresses([station, operator], [operator])
        self.assertEqual([index for index, _ in matches], [0, 1])
        self.assertEqual(stats['collision'], 0)

    def test_identity_matching_does_not_mutate_source_rows(self):
        stations = list(FIXTURE['stations'].values())
        directory = FIXTURE['directory']
        before = copy.deepcopy((stations, directory))
        merge.match_chain_addresses(stations, directory)
        self.assertEqual((stations, directory), before)

    def test_actual_village_house_and_road_abbreviation_formats_keep_verified_pins(self):
        cases = [('lea_bp_kalnuju', 'Baltic Petroleum', 'Raseinių r. sav., Kalnujų sen., Kalnujų k. 1'),
                 ('lea_viada_vejuku', 'Viada', 'Raseinių r., Viduklės sen., Vejukų k. 5'),
                 ('lea_bp_velzio', 'Baltic Petroleum', 'Panevežys, Velžio kelias 74')]
        for name, network, address in cases:
            with self.subTest(station=name):
                matches, stats = merge.match_chain_addresses([FIXTURE['stations'][name]], [entry(network, address)])
                self.assertEqual(len(matches), 1)
                self.assertEqual(stats['unmatched'], 0)


if __name__ == '__main__':
    unittest.main()
