// Kuro Kainos Lietuvoje - official LEA prices + nearest-to-me + map with price POIs.
// Data shape (scripts/fetch_prices.py + scripts/geocode.py):
// { updated, source, source_url, summary:{...}, stations:[{network,address,municipality,
//   locality,petrol95,diesel,lpg, lat, lon, approx}] }

const FUEL_LABELS = { petrol95: "95 benzinas", diesel: "Dyzelinas", lpg: "Dujos (SND)" };
const LT_CENTER = [55.17, 23.88];   // Lithuania centre, for the default map view

let DATA = { updated: null, source: "", source_url: "", summary: {}, stations: [] };
let DISCREP = { items: [], byNetwork: {} };   // comparison-engine flags
let fuelType = "petrol95";
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
    initMunicipalities();
    updateChrome();
    render();
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
    document.getElementById("updated-line").textContent =
        DATA.updated ? `Duomenys atnaujinti: ${DATA.updated}` : "";
}

function selectFuel(f) {
    fuelType = f;
    document.querySelectorAll(".fuel-btn").forEach(b => b.classList.remove("active"));
    document.getElementById("btn-" + f).classList.add("active");
    render();
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
    box.innerHTML = `
        <div class="summary-title">${FUEL_LABELS[fuelType]} — šalies kainos (oficialios)</div>
        <div class="summary-stats">
            <div><div class="stat-label">Pigiausia</div><div class="stat-value lowest">€${s.min.toFixed(3)}</div></div>
            <div><div class="stat-label">Vidutinė</div><div class="stat-value">€${s.avg.toFixed(3)}</div></div>
            <div><div class="stat-label">Brangiausia</div><div class="stat-value highest">€${s.max.toFixed(3)}</div></div>
        </div>`;
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
            return `
            <div class="station-card">
                ${isBest ? '<div class="best-price-badge">⭐ PIGIAUSIA</div>' : ''}${dist}
                <div class="station-header">
                    <div class="station-name">${s.network || "Degalinė"}</div>
                    <div><span class="station-price">€${s[fuelType].toFixed(3)}</span><span class="price-unit">/L</span></div>
                </div>
                <div class="station-address">${s.address || ""}${s.locality ? ", " + s.locality : ""}</div>
                <div class="station-muni">📍 ${s.municipality || ""}${approxTag}</div>
                ${flagLine}
                <div class="nav-row">${navButtons(s)}</div>
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
