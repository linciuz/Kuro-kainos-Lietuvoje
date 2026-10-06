# Fuelis — kuro kainos Lietuvoje ir degalinių paieška

**Palyginkite benzino 95, dyzelino ir dujų (SND) kainas Lietuvos degalinėse.**
Pagrindinis šaltinis — **Lietuvos energetikos agentūros (LEA) portalas**; patikrintos
atskirų operatorių kainos jį papildo. Prie kainų nurodoma jų data ir šaltinis.

🔗 **Tiesioginė versija:** [Fuelis.lt](https://fuelis.lt/)

[Kuro kainos pagal miestą ir savivaldybę](https://fuelis.lt/kainos/) ·
[Atviri duomenys ir API](https://fuelis.lt/atviri-duomenys.html)

---

## ✨ Funkcijos

- 🏛️ **LEA ir operatorių duomenys** — skelbiamos naujausios gautos kainos iš [LEA portalo](https://degalukainos.ena.lt/) ir patikrintų operatorių šaltinių; degalinės be kainos atskiriamos nuo kainų palyginimo
- ⛽ **Trys kuro tipai** — 95 benzinas, dyzelinas, dujos (SND)
- 📍 **Artimiausios prie jūsų** — pagal GPS vietą surūšiuoja degalines pagal atstumą
- 🗺️ **Žemėlapis su kainomis** — kiekviena degalinė pažymėta kainos ženkleliu (pigiausios žalios, brangiausios raudonos)
- 🚗 **Navigacija** — vienu paspaudimu atidaro **Google Maps** arba **Waze** maršrutą iki degalinės
- 🏙️ **Filtras pagal savivaldybę** ir paieška pagal tinklą / adresą
- 💰 **Rūšiavimas** pagal kainą arba atstumą
- 📊 **Šalies statistika** — pigiausia / vidutinė / brangiausia kiekvienam kurui
- 📱 **PWA** — įsidiekite į telefono ekraną, veikia kaip programėlė ir be interneto (rodo paskutinius duomenis)
- 🔄 **Automatinė patikra** darbo dienomis per GitHub Actions; kainos data priklauso nuo šaltinio paskelbimo

---

## 🛠️ Kaip tai veikia

Grynas HTML / CSS / vanilla JS — be karkasų, be kompiliavimo. Talpinama nemokamai GitHub Pages.

```
index.html / app.js          → sąsaja (Leaflet žemėlapis), skaito data/stations.json
data/stations.json           → degalinių kainos + koordinatės + šalies vidurkiai
data/geocode_cache.json      → adresų → lat/lon talpykla (kad geokodavimas nesikartotų)
scripts/fetch_prices.py      → LEA portalo JSON API → stations.json
scripts/merge_chain_coords.py → oficialios LEA / operatorių koordinatės, patikrintos pataisos
scripts/geocode.py           → trūkstamų koordinačių geokodavimas (OSM Nominatim, talpykla)
.github/workflows/           → dažni kainų ir du pilni darbo dienų atnaujinimai
tools/gen_icons.py           → sugeneruoja PWA ikonas
```

### Duomenų šaltinis
Pagrindinis šaltinis yra [oficialus LEA portalas](https://degalukainos.ena.lt/) ir jo
JSON API `/api/v1/read/prices?per_page=3000`. `fetch_prices.py` per `price_engine`
surenka kainas, išsaugo jų šaltinį bei laiką ir įrašo `data/stations.json` su šalies
statistika. Viešas portalo prieigos raktas perskaitomas iš paties portalo JavaScript.
Naujas portalo `/read/prices/latest` formatas 2026-10-06 sutapo su dabartiniu šaltiniu,
tačiau pakeitė metaduomenų ir laiko formatą; programa kol kas naudoja ankstesnį formatą.

LEA portalas pateikia operatorių registruotas GPS koordinates. Jos ir tiesioginiai
operatorių degalinių katalogai naudojami tikslesnėms vietoms; `geocode.py` yra atsarginis
adresų geokodavimo kelias. Rezultatai saugomi `data/geocode_cache.json`, todėl jau
žinomiems adresams pakartotinės Nominatim užklausos nereikalingos. LEA Power BI papildomas
registras pateikia ir degalines be kainos; jo SUM rezultatų programa nenaudoja gyvoms kainoms.
Saurida skelbiamos atskirų degalinių kainos papildo LEA tik ten, kur sutapatinta degalinė
ir patikrintas duomenų šviežumas bei kainos pobūdis.

[LEA pradiniuose duomenyse](https://www.ena.lt/dk-pr-pr-duomenys/) nuo 2026-09-09
skelbiamas bendras metų Excel archyvas. 2026-10-06 jo parsisiuntimas grąžino HTTP 403
(OneDrive atsarginis kelias — 400); turinys ir schema nepatikrinti, archyvas neintegruotas.
Tikslios patikros ir ribos: [2026-10-06 šaltinių auditas](tools/audit/lea_official_20261006/README.md).

---

## 🚀 Paleidimas lokaliai

```bash
git clone https://github.com/linciuz/Kuro-kainos-Lietuvoje.git
cd Kuro-kainos-Lietuvoje

# Atidaryti per http (kad veiktų fetch ir service worker):
python -m http.server 8000
# → http://localhost:8000
```

### Duomenų atnaujinimas rankiniu būdu
```bash
pip install -r scripts/requirements.txt
python scripts/fetch_prices.py        # perrašo data/stations.json
python scripts/geocode.py             # įrašo lat/lon (kešuojama geocode_cache.json)
```

Arba paleiskite GitHub Action „Update fuel prices“ rankiniu būdu (Actions skiltyje → Run workflow).

---

## 📦 Android programėlė (.apk)

Svetainė yra PWA, todėl APK galima sukurti be Android SDK:
- **[PWABuilder](https://www.pwabuilder.com)** → įveskite tiesioginės versijos URL → Android → atsisiųskite pasirašytą APK.
- Arba `@bubblewrap/cli` su lokaliu manifestu (reikia JDK 17 + Android cmdline-tools).

APK yra plonas apvalkalas, įkeliantis gyvą svetainę — kainos atsinaujina be programėlės perbūdavojimo.

---

## 📄 Licencija / atsakomybė
Pagrindinis duomenų šaltinis: **Lietuvos energetikos agentūra**; papildomų operatorių
kainų šaltiniai nurodomi atskirai. Kainos informacinės; tikslias kainas patvirtina
degalinė. Fuelis yra nepriklausomas projektas, nesusijęs su LEA.
