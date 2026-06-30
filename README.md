# ⛽ Kuro Kainos Lietuvoje

**Oficialios degalų kainos visose Lietuvos degalinėse — 95 benzinas, dyzelinas ir dujos (SND).**
Duomenys imami iš **Lietuvos energetikos agentūros (LEA)** ir atnaujinami kasdien automatiškai.

🔗 **Tiesioginė versija:** https://linciuz.github.io/Kuro-kainos-Lietuvoje/

---

## ✨ Funkcijos

- 🏛️ **Oficialūs duomenys** — visų ~760 degalinių kainos iš [ena.lt](https://www.ena.lt/degalu-kainos-degalinese/) (nuo 2026 m. degalinės privalo kasdien deklaruoti 10:00 kainas)
- ⛽ **Trys kuro tipai** — 95 benzinas, dyzelinas, dujos (SND)
- 🏙️ **Filtras pagal savivaldybę** ir paieška pagal tinklą / adresą
- 💰 **Rūšiavimas pagal kainą** — pigiausios arba brangiausios pirmos
- 📊 **Šalies statistika** — pigiausia / vidutinė / brangiausia kiekvienam kurui
- 🗺️ **Google Maps nuoroda** vienu paspaudimu
- 📱 **PWA** — įsidiekite į telefono ekraną, veikia kaip programėlė ir be interneto (rodo paskutinius duomenis)
- 🔄 **Automatinis atnaujinimas** kasdien per GitHub Actions

---

## 🛠️ Kaip tai veikia

Grynas HTML / CSS / vanilla JS — be karkasų, be kompiliavimo. Talpinama nemokamai GitHub Pages.

```
index.html / app.js          → vartotojo sąsaja, skaito data/stations.json
data/stations.json           → visų degalinių kainos + šalies vidurkiai
scripts/fetch_prices.py      → parsisiunčia LEA dienos Excel → stations.json
.github/workflows/           → kasdien (I–V) paleidžia fetch_prices.py ir įkelia naujus duomenis
tools/gen_icons.py           → sugeneruoja PWA ikonas
```

### Duomenų šaltinis
LEA dar neturi viešo API, todėl `fetch_prices.py`:
1. Suranda naujausią Excel nuorodą [LEA puslapyje](https://www.ena.lt/degalu-kainos-degalinese/);
2. Parsisiunčia jį anonimiškai iš SharePoint (`?download=1`);
3. Adaptyviai išparsina (atpažįsta lietuviškas stulpelių antraštes);
4. Įrašo `data/stations.json` su kiekvienos degalinės kaina ir šalies vidurkiais.

> LEA duomenyse **nėra GPS koordinačių**, todėl programa filtruoja pagal savivaldybę ir
> nukreipia į Google Maps pagal adresą (atstumo skaičiavimo nėra).

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
Duomenų šaltinis: **Lietuvos energetikos agentūra**. Kainos informacinės; tikslias kainas
patvirtina degalinė. Projektas nesusijęs su LEA.
