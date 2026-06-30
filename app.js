// Kuro Kainos Lietuvoje - official LEA prices + nearest-to-me + map with price POIs.
// Data shape (scripts/fetch_prices.py + scripts/geocode.py):
// { updated, source, source_url, summary:{...}, stations:[{network,address,municipality,
//   locality,petrol95,diesel,lpg, lat, lon, approx}] }

const FUEL_LABELS = { petrol95: "95 benzinas", diesel: "Dyzelinas", lpg: "Dujos (SND)" };

// Set to your deployed Cloudflare Worker URL to enable "report a price".
// Empty = feature hidden, app works as before. See worker/README.md.
const REPORT_API = "";
const LT_CENTER = [55.17, 23.88];   // Lithuania centre, for the default map view

let DATA = { updated: null, source: "", source_url: "", summary: {}, stations: [] };
let DISCREP = { items: [], byNetwork: {} };   // comparison-engine flags
let REPORTS = {};                             // user-reported prices {stationKey:{fuel:{price,ts}}}
let ORLEN_WS = null;                           // Orlen refinery wholesale reference
let OIL = null;                                // Brent crude weekly trend
let EV = { chargers: [] };                     // EV charging stations (OCPI + OSM)
let EV_STATUS = {};                            // live occupancy {ocpi_id: {a,t,s}} via Worker proxy
let fuelType = "petrol95";    // 'petrol95' | 'diesel' | 'lpg' | 'ev'
let sortDir = "asc";          // 'asc' | 'desc' | 'dist'
let view = "list";            // 'list' | 'map'
let userPos = null;           // {lat, lon} once geolocation granted
let map = null, markersLayer = null, userMarker = null;

async function load() {
    try {
        const res = await fetch("data/stations.json", { cache: "no-store" });
        if (!res.ok) throw new Error("HTTP " + res.status);
        DATA = await res.json();
    } catch (e) {
        DATA = {
            updated: "2026-06-30",
            source: "Lietuvos energetikos agentūra (ena.lt)",
            source_url: "https://www.ena.lt/degalu-kainos-degalinese/",
            summary: {
                petrol95: { min: 1.54,  avg: 1.713, max: 1.849 },
                diesel:   { min: 1.62,  avg: 1.796, max: 1.909 },
                lpg:      { min: 0.639, avg: 0.782, max: 0.959 }
            },
            stations: []
        };
    }
    await loadDiscrepancies();
    await loadReports();
    await loadOrlenWholesale();
    await loadOil();
    await loadEv();
    await loadEvStatus();
    initMunicipalities();
    updateChrome();
    render();
    // Delegate report-button clicks (station keys can contain quotes/pipes).
    const list = document.getElementById("stations-list");
    if (list && !list._reportBound) {
        list._reportBound = true;
        list.addEventListener("click", (e) => {
            const b = e.target.closest(".report-btn");
            if (b) reportPrice(b.dataset.key, fuelType);
        });
    }
}

function stationKey(s) {
    return `${s.network || ""}|${s.address || ""}|${s.municipality || ""}`;
}

function escAttr(s) {
    return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

async function loadReports() {
    if (!REPORT_API) return;
    try {
        const res = await fetch(REPORT_API + "/reports", { cache: "no-store" });
        REPORTS = res.ok ? await res.json() : {};
    } catch (e) { REPORTS = {}; }
}

async function loadOrlenWholesale() {
    try {
        const res = await fetch("data/sources/orlen_wholesale.json", { cache: "no-store" });
        ORLEN_WS = res.ok ? await res.json() : null;
    } catch (e) { ORLEN_WS = null; }
}

async function loadOil() {
    try {
        const res = await fetch("data/oil.json", { cache: "no-store" });
        OIL = res.ok ? await res.json() : null;
    } catch (e) { OIL = null; }
}

async function loadEv() {
    try {
        const res = await fetch("data/sources/ev_chargers.json", { cache: "no-store" });
        EV = res.ok ? await res.json() : { chargers: [] };
    } catch (e) { EV = { chargers: [] }; }
}

// --- EV charging mode (fuelType === "ev") -----------------------------------

// Live occupancy via the Worker proxy (the OCPI source blocks browser CORS).
async function loadEvStatus() {
    if (!REPORT_API) return;
    try {
        const res = await fetch(REPORT_API + "/ev-status", { cache: "no-store" });
        EV_STATUS = res.ok ? await res.json() : {};
    } catch (e) { EV_STATUS = {}; }
}

function evStatus(c) {
    return (c.ocpi_id && EV_STATUS[c.ocpi_id]) || null;
}

function evStatusBadge(c) {
    const st = evStatus(c);
    if (!st) return "";
    const m = {
        available: ["🟢", `Laisva ${st.a}/${st.t}`],
        busy:      ["🔴", `Užimta (0/${st.t})`],
        down:      ["⚫", "Neveikia"],
        unknown:   ["⚪", "Būsena nežinoma"],
    }[st.s] || ["⚪", ""];
    return `<span class="ev-status ev-${st.s}">${m[0]} ${m[1]}</span>`;
}

function getChargers() {
    const q = (document.getElementById("search").value || "").toLowerCase().trim();
    let rows = (EV.chargers || []).filter(c => c.lat != null && c.lon != null);
    if (q) rows = rows.filter(c => ((c.operator || "") + " " + (c.name || "")).toLowerCase().includes(q));
    if (userPos) rows.forEach(c => c._dist = haversine(userPos.lat, userPos.lon, c.lat, c.lon));
    if (userPos) rows.sort((a, b) => (a._dist ?? Infinity) - (b._dist ?? Infinity));
    else rows.sort((a, b) => (b.power_kw || 0) - (a.power_kw || 0));
    return rows;
}

function evInfo(c) {
    return [c.power_kw ? `${c.power_kw} kW` : null,
            c.sockets ? `${c.sockets} jungtys` : null].filter(Boolean).join(" · ");
}

function evNav(c) {
    const q = `${c.lat},${c.lon}`;
    return `<a class="nav-btn nav-gmaps" href="https://www.google.com/maps/dir/?api=1&destination=${q}" target="_blank" rel="noopener">🗺️ Google Maps</a>
            <a class="nav-btn nav-waze" href="https://waze.com/ul?ll=${q}&navigate=yes" target="_blank" rel="noopener">🚗 Waze</a>`;
}

function renderSummaryEv() {
    const box = document.getElementById("summary");
    box.style.display = "block";
    const total = (EV.chargers || []).length;
    const priced = (EV.chargers || []).filter(c => c.price != null).length;
    const live = Object.keys(EV_STATUS).length;
    box.innerHTML = `<div class="summary-title">⚡ Elektromobilių įkrovimo stotelės (${total})</div>
        <div class="wholesale-ref">Šaltiniai: <b>Via Lietuva</b> (oficiali AFIR) + OpenStreetMap ·
        kaina (€/kWh): ${priced} stotelės ·
        ${live ? `užimtumas realiu laiku 🟢🔴: ${live} stotelės` : "realaus laiko užimtumas – kai įjungtas Worker"}</div>`;
}

function renderListEv() {
    const list = document.getElementById("stations-list");
    const rows = getChargers();
    if (!rows.length) { list.innerHTML = `<div class="msg">Nieko nerasta.</div>`; return; }
    list.innerHTML = `<div class="count-line">Rodoma stotelių: ${rows.length}${userPos ? " · arčiausios pirmos" : ""}</div>` +
        rows.map(c => {
            const dist = (userPos && c._dist != null) ? `<span class="dist-badge">📍 ${fmtDist(c._dist)}</span>` : "";
            const info = evInfo(c);
            const badge = evStatusBadge(c);
            return `<div class="station-card">
                ${dist}${badge}
                <div class="station-header">
                    <div class="station-name">⚡ ${c.operator || c.name || "Įkrovimo stotelė"}</div>
                    ${c.price != null ? `<div><span class="station-price">€${c.price.toFixed(2)}</span><span class="price-unit">/kWh</span></div>` : ""}
                </div>
                ${info ? `<div class="station-address">${info}</div>` : ""}
                <div class="nav-row">${evNav(c)}</div>
            </div>`;
        }).join("");
}

function renderMapEv() {
    ensureMap();
    if (!map) return;
    setTimeout(() => map.invalidateSize(), 0);
    markersLayer.clearLayers();
    const rows = getChargers();
    const bounds = [];
    rows.forEach(c => {
        const st = evStatus(c);
        const pinCls = st ? `ev-pin ev-${st.s}` : "ev-pin";
        const icon = L.divIcon({ className: "", html: `<div class="${pinCls}">⚡</div>`, iconSize: null, iconAnchor: [11, 11] });
        const info = evInfo(c);
        const badge = evStatusBadge(c);
        const popup = `<div class="popup-name">⚡ ${c.operator || c.name || "Įkrovimo stotelė"}</div>
            ${badge ? `<div>${badge}</div>` : ""}
            ${c.price != null ? `<div class="popup-price">€${c.price.toFixed(2)}/kWh</div>` : ""}
            ${info ? `<div>${info}</div>` : ""}
            <div class="popup-nav">${evNav(c)}</div>`;
        L.marker([c.lat, c.lon], { icon }).bindPopup(popup, { minWidth: 200 }).addTo(markersLayer);
        bounds.push([c.lat, c.lon]);
    });
    if (!userPos && bounds.length) map.fitBounds(bounds, { padding: [30, 30] });
}

// An active report = reported AFTER the latest official LEA snapshot.
function reportFor(s) {
    const r = REPORTS[stationKey(s)];
    const rep = r && r[fuelType];
    if (!rep) return null;
    const officialTs = DATA.updated ? Date.parse(DATA.updated) : 0;
    return rep.ts > officialTs ? rep : null;
}

async function reportPrice(key, fuel) {
    if (!REPORT_API) return;
    const input = prompt(`Įveskite ${FUEL_LABELS[fuel]} kainą (€/L), pvz. 1.699:`);
    if (input == null) return;
    const price = parseFloat(input.replace(",", "."));
    if (!(price >= 0.3 && price <= 3.5)) { alert("Neteisinga kaina."); return; }
    try {
        const res = await fetch(REPORT_API + "/report", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ station: key, fuel, price }),
        });
        if (!res.ok) throw new Error("HTTP " + res.status);
        (REPORTS[key] = REPORTS[key] || {})[fuel] = { price: Math.round(price * 1000) / 1000, ts: Date.now() };
        render();
    } catch (e) { alert("Nepavyko išsiųsti. Bandykite vėliau."); }
}

async function loadDiscrepancies() {
    try {
        const res = await fetch("data/discrepancies.json", { cache: "no-store" });
        if (!res.ok) throw new Error("HTTP " + res.status);
        const d = await res.json();
        const byNetwork = {};
        for (const it of (d.items || [])) {
            for (const net of (it.networks || [])) {
                (byNetwork[net] = byNetwork[net] || {})[it.fuel] = it;
            }
        }
        DISCREP = { items: d.items || [], byNetwork };
    } catch (e) {
        DISCREP = { items: [], byNetwork: {} };
    }
}

// Discrepancy flag for a station at the current fuel, or null.
function flagFor(s) {
    const m = DISCREP.byNetwork[s.network];
    return (m && m[fuelType]) || null;
}

function initMunicipalities() {
    const sel = document.getElementById("muni-select");
    const munis = [...new Set((DATA.stations || [])
        .map(s => (s.municipality || "").trim()).filter(Boolean))]
        .sort((a, b) => a.localeCompare(b, "lt"));
    sel.innerHTML = '<option value="">Visos savivaldybės</option>' +
        munis.map(m => `<option value="${m}">${m}</option>`).join("");
}

function updateChrome() {
    document.getElementById("source-line").innerHTML =
        `Šaltinis: <a href="${DATA.source_url}" target="_blank" rel="noopener">${DATA.source}</a>`;
    const upd = document.getElementById("updated-line");
    if (!DATA.updated) { upd.textContent = ""; upd.className = ""; return; }
    // LEA publishes Mon–Fri; >4 days old means a missed/failed update — warn.
    const days = Math.floor((Date.now() - Date.parse(DATA.updated)) / 86400000);
    if (days > 4) {
        upd.className = "stale";
        upd.textContent = `⚠️ Duomenys gali būti pasenę — paskutinis atnaujinimas ${DATA.updated} (prieš ${days} d.)`;
    } else {
        upd.className = "";
        upd.textContent = `Duomenys atnaujinti: ${DATA.updated}`;
    }
}

// Top banner: only the DRASTIC "prices may rise" alert (the persistent weekly
// average + direction lives in the bottom footer widget below).
function renderOilBanner() {
    const el = document.getElementById("oil-banner");
    if (!el) return;
    if (!OIL || fuelType === "ev" || !(OIL.level === "rise" || OIL.level === "strong")) {
        el.style.display = "none";
        return;
    }
    const wk = OIL.week_change_pct, sign = wk > 0 ? "+" : "";
    el.className = "oil-banner oil-" + OIL.level;
    el.style.display = "block";
    el.innerHTML = OIL.level === "strong"
        ? `🛢️ Brent nafta per savaitę <b>${sign}${wk}%</b> → degalų kainos netrukus greičiausiai <b>kils</b>.`
        : `🛢️ Brent nafta per savaitę <b>${sign}${wk}%</b> → degalų kainos artimiausiu metu gali <b>kilti</b>.`;
}

// Bottom footer: always-visible weekly-average Brent price + a direction
// indicator (fuel prices may go up/down on drastic crude moves).
function renderOilFooter() {
    const el = document.getElementById("oil-footer");
    if (!el) return;
    if (!OIL) { el.style.display = "none"; return; }
    const avg = (OIL.week_avg != null ? OIL.week_avg : OIL.price);
    const chg = (OIL.avg_change_pct != null ? OIL.avg_change_pct : OIL.week_change_pct);
    const sign = chg > 0 ? "+" : "";
    const ind = {
        strong: ["↑", "degalų kainos gali kilti", "up"],
        rise:   ["↑", "degalų kainos gali kilti", "up"],
        watch:  ["↗", "galimas kainų kilimas", "up"],
        fall:   ["↓", "kainos gali mažėti", "down"],
        stable: ["→", "rinka stabili", "flat"],
    }[OIL.level] || ["→", "rinka stabili", "flat"];
    el.className = "oil-footer oil-ind-" + ind[2];
    el.style.display = "flex";
    el.innerHTML = `🛢️ Brent nafta · savaitės vid. <b>$${avg.toFixed(2)}</b> ·
        per savaitę ${sign}${chg}% <span class="oil-ind">${ind[0]} ${ind[1]}</span>`;
}

function selectFuel(f) {
    fuelType = f;
    document.querySelectorAll(".fuel-btn").forEach(b => b.classList.remove("active"));
    document.getElementById("btn-" + f).classList.add("active");
    render();
    if (f === "ev" && REPORT_API) loadEvStatus().then(render);   // refresh live occupancy
}

function setSort(dir) {
    if (dir === "dist" && !userPos) return;
    sortDir = dir;
    ["asc", "desc", "dist"].forEach(d =>
        document.getElementById("sort-" + d).classList.toggle("active", d === dir));
    render();
}

function setView(v) {
    view = v;
    document.getElementById("view-list").classList.toggle("active", v === "list");
    document.getElementById("view-map").classList.toggle("active", v === "map");
    document.getElementById("list-view").style.display = v === "list" ? "block" : "none";
    document.getElementById("map-view").style.display = v === "map" ? "block" : "none";
    if (v === "map") ensureMap();
    render();
}

// --- geolocation -----------------------------------------------------------

function locate() {
    const btn = document.getElementById("locate-btn");
    if (!navigator.geolocation) { btn.textContent = "📍 Vietos nustatymas nepalaikomas"; return; }
    btn.disabled = true;
    btn.textContent = "📍 Nustatoma vieta…";
    navigator.geolocation.getCurrentPosition(
        (pos) => {
            userPos = { lat: pos.coords.latitude, lon: pos.coords.longitude };
            btn.disabled = false;
            btn.classList.add("on");
            btn.textContent = "📍 Vieta nustatyta · artimiausios pirmos";
            document.getElementById("sort-dist").disabled = false;
            setSort("dist");
            if (map) {
                if (userMarker) userMarker.remove();
                userMarker = L.circleMarker([userPos.lat, userPos.lon], {
                    radius: 8, color: "#fff", weight: 2, fillColor: "#1a73e8", fillOpacity: 1
                }).addTo(map).bindPopup("Jūs esate čia");
                map.setView([userPos.lat, userPos.lon], 12);
            }
        },
        (err) => {
            btn.disabled = false;
            btn.textContent = err.code === 1
                ? "📍 Vietos prieiga atmesta – įjunkite leidimą"
                : "📍 Nepavyko nustatyti vietos";
        },
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
    );
}

function haversine(aLat, aLon, bLat, bLon) {
    const R = 6371, toRad = d => d * Math.PI / 180;
    const dLat = toRad(bLat - aLat), dLon = toRad(bLon - aLon);
    const h = Math.sin(dLat / 2) ** 2 +
        Math.cos(toRad(aLat)) * Math.cos(toRad(bLat)) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));   // km
}

function fmtDist(km) {
    return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(km < 10 ? 1 : 0)} km`;
}

// --- shared row selection --------------------------------------------------

function getRows() {
    const muni = document.getElementById("muni-select").value;
    const q = (document.getElementById("search").value || "").toLowerCase().trim();

    let rows = (DATA.stations || []).filter(s => s[fuelType] != null);
    if (muni) rows = rows.filter(s => (s.municipality || "") === muni);
    if (q) rows = rows.filter(s =>
        ((s.network || "") + " " + (s.address || "") + " " + (s.locality || "")).toLowerCase().includes(q));

    if (userPos) rows.forEach(s => {
        s._dist = (s.lat != null && s.lon != null)
            ? haversine(userPos.lat, userPos.lon, s.lat, s.lon) : null;
    });

    if (sortDir === "dist" && userPos) {
        rows.sort((a, b) => (a._dist ?? Infinity) - (b._dist ?? Infinity));
    } else {
        rows.sort((a, b) => sortDir === "asc" ? a[fuelType] - b[fuelType] : b[fuelType] - a[fuelType]);
    }
    return rows;
}

// --- rendering -------------------------------------------------------------

function render() {
    renderOilBanner();
    renderOilFooter();
    if (fuelType === "ev") {
        // EV mode: no fuel-price banner; chargers in list/map.
        document.getElementById("change-banner").style.display = "none";
        renderSummaryEv();
        if (view === "map") renderMapEv();
        else renderListEv();
        return;
    }
    renderBanner();
    renderSummary();
    if (view === "map") renderMap();
    else renderList();
}

function renderBanner() {
    const el = document.getElementById("change-banner");
    if (!el) return;
    const flagged = (DISCREP.items || []).filter(it => it.fuel === fuelType);
    if (!flagged.length) { el.style.display = "none"; return; }
    const chains = [...new Set(flagged.map(it => it.source))].join(", ");
    el.style.display = "block";
    el.innerHTML = `⚠️ ${FUEL_LABELS[fuelType]}: kai kurių tinklų (${chains}) kainos galėjo
        pasikeisti nuo 10:00 oficialaus pranešimo.`;
}

function renderSummary() {
    const s = (DATA.summary || {})[fuelType];
    const box = document.getElementById("summary");
    if (!s) { box.style.display = "none"; return; }
    box.style.display = "block";
    const WS_LABELS = { petrol95: "95", diesel: "Dyzelinas", diesel_agri: "Agro", lpg: "Dujos" };
    let wsLine = "";
    if (ORLEN_WS && ORLEN_WS.prices) {
        const parts = ["petrol95", "diesel", "diesel_agri", "lpg"]
            .filter(k => ORLEN_WS.prices[k] != null)
            .map(k => `${WS_LABELS[k]} <b>€${ORLEN_WS.prices[k].toFixed(3)}</b>`);
        if (parts.length) wsLine = `<div class="wholesale-ref">🏭 Orlen didmeninė kaina
            (${ORLEN_WS.stated_date || ""}): ${parts.join(" · ")} <br><i>be antkainio — ne degalinės kaina</i></div>`;
    }
    box.innerHTML = `
        <div class="summary-title">${FUEL_LABELS[fuelType]} — šalies kainos (oficialios)</div>
        <div class="summary-stats">
            <div><div class="stat-label">Pigiausia</div><div class="stat-value lowest">€${s.min.toFixed(3)}</div></div>
            <div><div class="stat-label">Vidutinė</div><div class="stat-value">€${s.avg.toFixed(3)}</div></div>
            <div><div class="stat-label">Brangiausia</div><div class="stat-value highest">€${s.max.toFixed(3)}</div></div>
        </div>${wsLine}`;
}

function navButtons(s) {
    const hasGeo = s.lat != null && s.lon != null;
    const gmaps = hasGeo
        ? `https://www.google.com/maps/dir/?api=1&destination=${s.lat},${s.lon}`
        : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent((s.network||"") + " " + (s.address||""))}`;
    const waze = hasGeo
        ? `https://waze.com/ul?ll=${s.lat},${s.lon}&navigate=yes`
        : `https://waze.com/ul?q=${encodeURIComponent((s.address||"") + " " + (s.municipality||""))}&navigate=yes`;
    return `<a class="nav-btn nav-gmaps" href="${gmaps}" target="_blank" rel="noopener">🗺️ Google Maps</a>
            <a class="nav-btn nav-waze" href="${waze}" target="_blank" rel="noopener">🚗 Waze</a>`;
}

function renderList() {
    const list = document.getElementById("stations-list");
    if (!DATA.stations || DATA.stations.length === 0) {
        list.innerHTML = `<div class="msg">Šalies vidurkiai rodomi viršuje.<br>
            Visų degalinių sąrašas atsiras po automatinio duomenų atnaujinimo.</div>`;
        return;
    }
    const rows = getRows();
    if (rows.length === 0) { list.innerHTML = `<div class="msg">Nieko nerasta pagal pasirinktus filtrus.</div>`; return; }

    const best = Math.min(...rows.map(r => r[fuelType]));
    list.innerHTML =
        `<div class="count-line">Rodoma degalinių: ${rows.length}${userPos ? " · rūšiuojama pagal atstumą" : ""}</div>` +
        rows.map(s => {
            const isBest = s[fuelType] === best;
            const dist = (userPos && s._dist != null)
                ? `<span class="dist-badge">📍 ${s.approx ? "~" : ""}${fmtDist(s._dist)}</span>` : "";
            const approxTag = s.approx ? ' <span class="approx-tag">apytikslė vieta</span>' : "";
            const fl = flagFor(s);
            const flagLine = fl ? `<div class="change-flag">⚠️ Kaina galėjo pasikeisti nuo 10:00 —
                ${fl.source} tinkle ${fl.direction === "down" ? "pigiau" : "brangiau"}: €${fl.live.toFixed(3)}/L</div>` : "";
            const rep = reportFor(s);
            const repLine = rep ? `<div class="report-line">🗣️ Pranešta kaina: €${rep.price.toFixed(3)}/L —
                gali skirtis nuo oficialios, kol bus atnaujinta</div>` : "";
            const repBtn = REPORT_API ? `<button class="report-btn" data-key="${escAttr(stationKey(s))}">🗣️ Pranešti kainą</button>` : "";
            return `
            <div class="station-card">
                ${isBest ? '<div class="best-price-badge">⭐ PIGIAUSIA</div>' : ''}${dist}
                <div class="station-header">
                    <div class="station-name">${s.network || "Degalinė"}</div>
                    <div><span class="station-price">€${s[fuelType].toFixed(3)}</span><span class="price-unit">/L</span></div>
                </div>
                <div class="station-address">${s.address || ""}${s.locality ? ", " + s.locality : ""}</div>
                <div class="station-muni">📍 ${s.municipality || ""}${approxTag}</div>
                ${flagLine}${repLine}
                <div class="nav-row">${navButtons(s)}</div>
                ${repBtn ? `<div class="report-row">${repBtn}</div>` : ""}
            </div>`;
        }).join("");
}

// --- map -------------------------------------------------------------------

function ensureMap() {
    if (map || typeof L === "undefined") return;
    map = L.map("map", { zoomControl: true }).setView(LT_CENTER, 7);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19, attribution: "© OpenStreetMap"
    }).addTo(map);
    markersLayer = L.layerGroup().addTo(map);
    if (userPos) {
        userMarker = L.circleMarker([userPos.lat, userPos.lon], {
            radius: 8, color: "#fff", weight: 2, fillColor: "#1a73e8", fillOpacity: 1
        }).addTo(map).bindPopup("Jūs esate čia");
        map.setView([userPos.lat, userPos.lon], 12);
    }
}

function renderMap() {
    ensureMap();
    if (!map) return;
    setTimeout(() => map.invalidateSize(), 0);
    markersLayer.clearLayers();

    let rows = getRows().filter(s => s.lat != null && s.lon != null);
    const MAX = 300;                       // keep the map snappy on phones
    const capped = rows.length > MAX;
    rows = rows.slice(0, MAX);
    if (rows.length === 0) return;

    const prices = rows.map(r => r[fuelType]);
    const lo = Math.min(...prices), hi = Math.max(...prices);
    const bounds = [];

    rows.forEach(s => {
        const p = s[fuelType];
        let cls = "price-pin";
        if (p <= lo + (hi - lo) * 0.25) cls += " cheap";
        else if (p >= lo + (hi - lo) * 0.75) cls += " dear";
        if (s.approx) cls += " approx";
        const icon = L.divIcon({
            className: "", html: `<div class="${cls}">€${p.toFixed(2)}</div>`,
            iconSize: null, iconAnchor: [22, 12]
        });
        const dist = (userPos && s._dist != null) ? `<br>📍 ${s.approx ? "~" : ""}${fmtDist(s._dist)}` : "";
        const approxNote = s.approx ? `<br><span style="color:#999">apytikslė vieta (savivaldybės centras)</span>` : "";
        const popup = `<div class="popup-name">${s.network || "Degalinė"}</div>
            <div>${s.address || ""}</div>
            <div class="popup-price">${FUEL_LABELS[fuelType]}: €${p.toFixed(3)}/L</div>${dist}${approxNote}
            <div class="popup-nav">${navButtons(s)}</div>`;
        L.marker([s.lat, s.lon], { icon }).bindPopup(popup, { minWidth: 220 }).addTo(markersLayer);
        bounds.push([s.lat, s.lon]);
    });

    if (!userPos && bounds.length) map.fitBounds(bounds, { padding: [30, 30] });
    if (capped) console.log(`Map capped at ${MAX} nearest/cheapest stations.`);
}

window.addEventListener("load", load);

// Re-fetch data when a long-open / backgrounded PWA is brought back to the
// foreground (throttled to once per 10 min) so prices don't go silently stale.
let _lastLoad = Date.now();
document.addEventListener("visibilitychange", () => {
    if (!document.hidden && Date.now() - _lastLoad > 10 * 60 * 1000) {
        _lastLoad = Date.now();
        load();
    }
});
