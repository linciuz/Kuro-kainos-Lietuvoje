# -*- coding: utf-8 -*-
"""LEA municipality name -> the place name Lithuanians actually search for.

The registry uses administrative forms ("Vilniaus m. sav.", "Mažeikių r. sav.")
that nobody types into Google — people search "degalų kainos Kaune". Each entry
gives the NOMINATIVE (headings, links) and the LOCATIVE (natural "kainos X-e"
phrasing). Hand-written rather than derived: Lithuanian declension has too many
irregulars, and wrong grammar on a public page is worse than no optimisation.

For the five districts that surround a big city (Vilniaus r. sav. etc.) the
city name belongs to the separate m. sav. page, so those use "X rajonas".
"""

PLACES = {
    # --- city municipalities -------------------------------------------------
    "Vilniaus m. sav.":      ("Vilnius", "Vilniuje"),
    "Kauno m. sav.":         ("Kaunas", "Kaune"),
    "Klaipėdos m. sav.":     ("Klaipėda", "Klaipėdoje"),
    "Šiaulių m. sav.":       ("Šiauliai", "Šiauliuose"),
    "Panevėžio m. sav.":     ("Panevėžys", "Panevėžyje"),
    "Alytaus m. sav.":       ("Alytus", "Alytuje"),
    "Palangos m. sav.":      ("Palanga", "Palangoje"),
    "Birštono sav.":         ("Birštonas", "Birštone"),
    "Druskininkų sav.":      ("Druskininkai", "Druskininkuose"),
    "Elektrėnų sav.":        ("Elektrėnai", "Elektrėnuose"),
    "Kalvarijos sav.":       ("Kalvarija", "Kalvarijoje"),
    "Kazlų Rūdos sav.":      ("Kazlų Rūda", "Kazlų Rūdoje"),
    "Marijampolės sav.":     ("Marijampolė", "Marijampolėje"),
    "Neringos sav.":         ("Neringa", "Neringoje"),
    "Pagėgių sav.":          ("Pagėgiai", "Pagėgiuose"),
    "Rietavo sav.":          ("Rietavas", "Rietave"),
    "Visagino sav.":         ("Visaginas", "Visagine"),
    # --- district municipalities (centre town) -------------------------------
    "Akmenės r. sav.":       ("Akmenė", "Akmenėje"),
    "Alytaus r. sav.":       ("Alytaus rajonas", "Alytaus rajone"),
    "Anykščių r. sav.":      ("Anykščiai", "Anykščiuose"),
    "Biržų r. sav.":         ("Biržai", "Biržuose"),
    "Ignalinos r. sav.":     ("Ignalina", "Ignalinoje"),
    "Jonavos r. sav.":       ("Jonava", "Jonavoje"),
    "Joniškio r. sav.":      ("Joniškis", "Joniškyje"),
    "Jurbarko r. sav.":      ("Jurbarkas", "Jurbarke"),
    "Kaišiadorių r. sav.":   ("Kaišiadorys", "Kaišiadoryse"),
    "Kauno r. sav.":         ("Kauno rajonas", "Kauno rajone"),
    "Kėdainių r. sav.":      ("Kėdainiai", "Kėdainiuose"),
    "Kelmės r. sav.":        ("Kelmė", "Kelmėje"),
    "Klaipėdos r. sav.":     ("Klaipėdos rajonas", "Klaipėdos rajone"),
    "Kretingos r. sav.":     ("Kretinga", "Kretingoje"),
    "Kupiškio r. sav.":      ("Kupiškis", "Kupiškyje"),
    "Lazdijų r. sav.":       ("Lazdijai", "Lazdijuose"),
    "Mažeikių r. sav.":      ("Mažeikiai", "Mažeikiuose"),
    "Molėtų r. sav.":        ("Molėtai", "Molėtuose"),
    "Pakruojo r. sav.":      ("Pakruojis", "Pakruojyje"),
    "Panevėžio r. sav.":     ("Panevėžio rajonas", "Panevėžio rajone"),
    "Pasvalio r. sav.":      ("Pasvalys", "Pasvalyje"),
    "Plungės r. sav.":       ("Plungė", "Plungėje"),
    "Prienų r. sav.":        ("Prienai", "Prienuose"),
    "Radviliškio r. sav.":   ("Radviliškis", "Radviliškyje"),
    "Raseinių r. sav.":      ("Raseiniai", "Raseiniuose"),
    "Rokiškio r. sav.":      ("Rokiškis", "Rokiškyje"),
    "Skuodo r. sav.":        ("Skuodas", "Skuode"),
    "Šakių r. sav.":         ("Šakiai", "Šakiuose"),
    "Šalčininkų r. sav.":    ("Šalčininkai", "Šalčininkuose"),
    "Šiaulių r. sav.":       ("Šiaulių rajonas", "Šiaulių rajone"),
    "Šilalės r. sav.":       ("Šilalė", "Šilalėje"),
    "Šilutės r. sav.":       ("Šilutė", "Šilutėje"),
    "Širvintų r. sav.":      ("Širvintos", "Širvintose"),
    "Švenčionių r. sav.":    ("Švenčionys", "Švenčionyse"),
    "Tauragės r. sav.":      ("Tauragė", "Tauragėje"),
    "Telšių r. sav.":        ("Telšiai", "Telšiuose"),
    "Trakų r. sav.":         ("Trakai", "Trakuose"),
    "Ukmergės r. sav.":      ("Ukmergė", "Ukmergėje"),
    "Utenos r. sav.":        ("Utena", "Utenoje"),
    "Varėnos r. sav.":       ("Varėna", "Varėnoje"),
    "Vilkaviškio r. sav.":   ("Vilkaviškis", "Vilkaviškyje"),
    "Vilniaus r. sav.":      ("Vilniaus rajonas", "Vilniaus rajone"),
    "Zarasų r. sav.":        ("Zarasai", "Zarasuose"),
}


def place(muni):
    """(nominative, locative) for a municipality; falls back to a de-suffixed
    form so an unmapped/renamed municipality still produces a sane page."""
    if muni in PLACES:
        return PLACES[muni]
    import re
    short = re.sub(r"\s*(m\.|r\.)?\s*sav\.$", "", muni or "").strip()
    return (short or muni, short or muni)
