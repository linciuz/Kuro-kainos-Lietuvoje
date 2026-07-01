// Kuro Kainos Lietuvoje - official LEA prices + nearest-to-me + map with price POIs.
// Data shape (scripts/fetch_prices.py + scripts/geocode.py):
// { updated, source, source_url, summary:{...}, stations:[{network,address,municipality,
//   locality,petrol95,diesel,lpg, lat, lon, approx}] }

// Fuel labels are localized via i18n: t("fuel_" + key). See i18n.js.

// Set to your deployed Cloudflare Worker URL to enable "report a price".
// Empty = feature hidden, app works as before. See worker/README.md.
const REPORT_API = "";
const LT_CENTER = [55.17, 23.88];   // Lithuania centre, for the default map view

let DATA = { updated: null, source: "", source_url: "", summary: {}, stations: [] };
let DISCREP = { items: [], byNetwork: {} };   // comparison-engine flags
let REPORTS = {};                             // user-reported prices {stationKey:{fuel:{price,ts}}}
let ORLEN_WS = null;                           // Orlen refinery wholesale reference
let OIL = null;                                // Brent crude weekly trend
let ELEC = null;                               // LT day-ahead electricity market price
let EV = { chargers: [] };                     // EV charging stations (OCPI + OSM)
let EV_STATUS = {};                            // live occupancy {ocpi_id: {a,t,s}} via Worker proxy
let fuelType = "petrol95";    // 'petrol95' | 'diesel' | 'lpg' | 'ev'
let sortDir = "asc";          // 'asc' | 'desc' | 'dist'
let radiusKm = 0;             // 0 = off; otherwise show only stations within this many km of userPos
let view = "list";            // 'list' | 'map'
let userPos = null;           // {lat, lon} once geolocation granted
let map = null, markersLayer = null, userMarker = null;

// --- i18n / language switcher ----------------------------------------------
let locateState = { key: "locate" };   // current locate-button label, kept re-translatable

function applyStaticI18n() {
    document.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
    document.querySelectorAll("[data-i18n-ph]").forEach(el => { el.placeholder = t(el.dataset.i18nPh); });
    document.documentElement.lang = lang;
    renderLocateBtn();
}

function renderLocateBtn() {
    const btn = document.getElementById("locate-btn");
    if (btn) btn.textContent = t(locateState.key, locateState.vars);
}

function buildLangSwitcher() {
    const box = document.getElementById("lang-switcher");
    if (!box) return;
    const cur = LANGS.find(l => l.code === lang) || LANGS[0];
    box.innerHTML =
        `<button type="button" class="lang-current" onclick="toggleLangMenu(event)">${cur.flag} ${cur.abbr} ▾</button>
         <div class="lang-menu" id="lang-menu" hidden>` +
        LANGS.map(l => `<button type="button" class="${l.code === lang ? "active" : ""}" onclick="setLang('${l.code}')">${l.flag} ${l.abbr}</button>`).join("") +
        `</div>`;
}

function toggleLangMenu(e) {
    if (e) e.stopPropagation();
    const m = document.getElementById("lang-menu");
    if (m) m.hidden = !m.hidden;
}

function setLang(code) {
    if (!LANGS.some(l => l.code === code)) return;
    lang = code;
    try { localStorage.setItem("kk_lang", code); } catch (e) {}
    const sel = document.getElementById("muni-select");
    const keep = sel ? sel.value : "";
    buildLangSwitcher();
    applyStaticI18n();
    initMunicipalities();
    if (sel && keep && [...sel.options].some(o => o.value === keep)) sel.value = keep;
    updateChrome();
    render();
}

// Close the language menu on an outside click.
document.addEventListener("click", (e) => {
    const m = document.getElementById("lang-menu");
    if (m && !m.hidden && !e.target.closest("#lang-switcher")) m.hidden = true;
});

// Drop price-less registry stations that duplicate a priced station of the SAME
// operator at the same spot. The daily Excel and the Power BI registry format
// addresses differently (comma placement/order), so a few duplicates slip past
// the address-based dedup; same operator + exact coords within ~70 m = the same
// physical station. (Runs each load, so it self-heals across daily refreshes.)
function dedupePricelessStations() {
    const priced = (DATA.stations || []).filter(s => !s.no_price && s.lat != null && s.lon != null);
    DATA.stations = (DATA.stations || []).filter(s => {
        if (!s.no_price || s.approx || s.lat == null) return true;
        return !priced.some(p => (p.network || "") === (s.network || "")
            && haversine(s.lat, s.lon, p.lat, p.lon) < 0.07);
    });
}

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
    dedupePricelessStations();
    await loadDiscrepancies();
    await loadReports();
    await loadOrlenWholesale();
    await loadOil();
    await loadElectricity();
    await loadEv();
    await loadEvStatus();
    // Preserve the chosen municipality across a foreground refetch (initMunicipalities
    // rebuilds the <select> and would otherwise reset it to "All municipalities").
    const _muniSel = document.getElementById("muni-select");
    const _keepMuni = _muniSel ? _muniSel.value : "";
    initMunicipalities();
    if (_muniSel && _keepMuni && [..._muniSel.options].some(o => o.value === _keepMuni)) _muniSel.value = _keepMuni;
    buildLangSwitcher();
    applyStaticI18n();
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

// Escape data-derived text before it goes into innerHTML / Leaflet popups —
// station & charger names/addresses/operators come from world-editable sources
// (OpenStreetMap tags, the LEA registry), so they are untrusted.
function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/'/g, "&#39;");
}
const escAttr = esc;   // back-compat alias

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

async function loadElectricity() {
    try {
        const res = await fetch("data/electricity.json", { cache: "no-store" });
        ELEC = res.ok ? await res.json() : null;
    } catch (e) { ELEC = null; }
}

async function loadEv() {
    try {
        const res = await fetch("data/sources/ev_chargers.json", { cache: "no-store" });
        EV = res.ok ? await res.json() : { chargers: [] };
    } catch (e) { EV = { chargers: [] }; }
    tagChargerMunicipalities();
}

// Tag each charger with the municipality of its nearest fuel station, so the
// municipality filter (manual or auto-from-location) narrows the EV list too.
function tagChargerMunicipalities() {
    const stations = (DATA.stations || []).filter(s => s.lat != null && s.lon != null && s.municipality);
    if (!stations.length) return;
    for (const c of (EV.chargers || [])) {
        if (c.lat == null || c.lon == null) { c._muni = null; continue; }
        const cosLat = Math.cos(c.lat * Math.PI / 180);
        let best = null, bestD = Infinity;
        for (const s of stations) {
            const dlat = s.lat - c.lat, dlon = (s.lon - c.lon) * cosLat;
            const d = dlat * dlat + dlon * dlon;   // squared planar dist (no trig) — only need nearest
            if (d < bestD) { bestD = d; best = s.municipality; }
        }
        c._muni = best;
    }
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
        available: ["🟢", t("ev_status_free", { a: st.a, t: st.t })],
        busy:      ["🔴", t("ev_status_busy", { t: st.t })],
        down:      ["⚫", t("ev_status_down")],
        unknown:   ["⚪", t("ev_status_unknown")],
    }[st.s] || ["⚪", ""];
    return `<span class="ev-status ev-${st.s}">${m[0]} ${m[1]}</span>`;
}

function getChargers() {
    const muni = document.getElementById("muni-select").value;
    const q = (document.getElementById("search").value || "").toLowerCase().trim();
    let rows = (EV.chargers || []).filter(c => c.lat != null && c.lon != null);
    if (muni) rows = rows.filter(c => c._muni === muni);
    if (q) rows = rows.filter(c => ((c.operator || "") + " " + (c.name || "")).toLowerCase().includes(q));
    if (userPos) rows.forEach(c => c._dist = haversine(userPos.lat, userPos.lon, c.lat, c.lon));
    if (userPos && radiusKm) rows = rows.filter(c => c._dist != null && c._dist <= radiusKm);
    // Honour the cheapest/expensive/nearest buttons. Chargers without a €/kWh
    // price always sort to the bottom (ranked by power) so priced ones lead.
    const byPower = (a, b) => (b.power_kw || 0) - (a.power_kw || 0);
    const byPrice = dir => (a, b) => {
        if (a.price == null && b.price == null) return byPower(a, b);
        if (a.price == null) return 1;
        if (b.price == null) return -1;
        return dir === "desc" ? b.price - a.price : a.price - b.price;
    };
    if (sortDir === "dist" && userPos) rows.sort((a, b) => (a._dist ?? Infinity) - (b._dist ?? Infinity));
    else if (sortDir === "asc" || sortDir === "desc") rows.sort(byPrice(sortDir));
    else rows.sort(byPower);
    return rows;
}

function evInfo(c) {
    return [c.power_kw ? `${c.power_kw} kW` : null,
            c.sockets ? t("ev_sockets", { n: c.sockets }) : null].filter(Boolean).join(" · ");
}

function evNav(c) {
    const ll = `${c.lat},${c.lon}`;
    // Navigate by address when we have one; otherwise fall back to the exact
    // coordinates so chargers with no street address still get directions.
    const addr = c.address ? encodeURIComponent(`${c.address}${c.city ? ", " + c.city : ""}`) : "";
    const gmaps = `https://www.google.com/maps/dir/?api=1&destination=${addr || ll}`;
    const waze = addr ? `https://waze.com/ul?q=${addr}&navigate=yes` : `https://waze.com/ul?ll=${ll}&navigate=yes`;
    return `<a class="nav-btn nav-gmaps" href="${gmaps}" target="_blank" rel="noopener">🗺️ Google Maps</a>
            <a class="nav-btn nav-waze" href="${waze}" target="_blank" rel="noopener">🚗 Waze</a>`;
}

function renderSummaryEv() {
    const box = document.getElementById("summary");
    box.style.display = "block";
    const priced = (EV.chargers || []).filter(c => c.price != null).length;
    const elLine = (ELEC && ELEC.current_ct_kwh != null)
        ? `<div class="summary-title">⚡ ${t("el_market")}: <b>${ELEC.current_ct_kwh.toFixed(1)} ct/kWh</b> · ${t("oil_weekavg")} <b>${ELEC.week_avg_ct_kwh.toFixed(1)} ct/kWh</b></div>`
        : "";
    box.innerHTML = elLine + `<div class="wholesale-ref">${t("ev_sources")} · ${t("ev_price_count", { n: priced })}</div>`;
}

function renderListEv() {
    const list = document.getElementById("stations-list");
    const rows = getChargers();
    if (!rows.length) { list.innerHTML = `<div class="msg">${t("nothing_found")}</div>`; return; }
    const LIST_MAX = 600;                       // keep the DOM snappy on phones
    const filtered = rows.length, totalCh = (EV.chargers || []).length;
    const shown = rows.slice(0, LIST_MAX);
    const nLabel = filtered < totalCh ? `${filtered} / ${totalCh}` : `${totalCh}`;  // your area / overall
    list.innerHTML = `<div class="count-line">${t("showing_chargers", { n: nLabel })}</div>` +
        shown.map(c => {
            const dist = (userPos && c._dist != null) ? `<span class="dist-badge">📍 ${fmtDist(c._dist)}</span>` : "";
            const info = evInfo(c);
            const badge = evStatusBadge(c);
            const addr = c.address ? esc(`${c.address}${c.city ? ", " + c.city : ""}`) : "";
            return `<div class="station-card">
                ${dist}${badge}
                <div class="station-header">
                    <div class="station-name">⚡ ${esc(c.operator || c.name || t("ev_charger"))}</div>
                    ${c.price != null ? `<div><span class="station-price">€${c.price.toFixed(2)}</span><span class="price-unit">/kWh</span></div>` : ""}
                </div>
                ${addr ? `<div class="station-address">${addr}</div>` : ""}
                ${info ? `<div class="station-muni">${info}</div>` : ""}
                <div class="nav-row">${evNav(c)}</div>
            </div>`;
        }).join("");
}

function renderMapEv() {
    ensureMap();
    if (!map) return;
    setTimeout(() => map.invalidateSize(), 0);
    markersLayer.clearLayers();
    const rows = getChargers().slice(0, 500);   // cap markers (sorted, so most relevant first)
    const bounds = [];
    rows.forEach(c => {
        const st = evStatus(c);
        const pinCls = st ? `ev-pin ev-${st.s}` : "ev-pin";
        const icon = L.divIcon({ className: "", html: `<div class="${pinCls}">⚡</div>`, iconSize: null, iconAnchor: [11, 11] });
        const info = evInfo(c);
        const badge = evStatusBadge(c);
        const addr = c.address ? esc(`${c.address}${c.city ? ", " + c.city : ""}`) : "";
        const popup = `<div class="popup-name">⚡ ${esc(c.operator || c.name || t("ev_charger"))}</div>
            ${addr ? `<div class="popup-addr">${addr}</div>` : ""}
            ${badge ? `<div>${badge}</div>` : ""}
            ${c.price != null ? `<div class="popup-price">€${c.price.toFixed(2)}/kWh</div>` : ""}
            ${info ? `<div>${info}</div>` : ""}
            <div class="popup-nav">${evNav(c)}</div>`;
        L.marker([c.lat, c.lon], { icon }).bindPopup(popup, { minWidth: 200 }).addTo(markersLayer);
        bounds.push([c.lat, c.lon]);
    });
    if (!userPos && bounds.length) map.fitBounds(bounds, { padding: [30, 30] });
    addUserMarker();   // keep the "you are here" pin on top of the charger pins
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
    const input = prompt(t("report_prompt", { fuel: t("fuel_" + fuel) }));
    if (input == null) return;
    const price = parseFloat(input.replace(",", "."));
    if (!(price >= 0.3 && price <= 3.5)) { alert(t("report_invalid")); return; }
    try {
        const res = await fetch(REPORT_API + "/report", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ station: key, fuel, price }),
        });
        if (!res.ok) throw new Error("HTTP " + res.status);
        (REPORTS[key] = REPORTS[key] || {})[fuel] = { price: Math.round(price * 1000) / 1000, ts: Date.now() };
        render();
    } catch (e) { alert(t("report_failed")); }
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

const BIG_CITIES = ["Vilniaus m. sav.", "Kauno m. sav.", "Klaipėdos m. sav.",
                    "Šiaulių m. sav.", "Panevėžio m. sav."];

function initMunicipalities() {
    const sel = document.getElementById("muni-select");
    const all = [...new Set((DATA.stations || [])
        .map(s => (s.municipality || "").trim()).filter(Boolean))];
    const big = BIG_CITIES.filter(m => all.includes(m));
    const rest = all.filter(m => !BIG_CITIES.includes(m)).sort((a, b) => a.localeCompare(b, "lt"));
    const opt = m => `<option value="${esc(m)}">${esc(m)}</option>`;
    sel.innerHTML = `<option value="">${t("all_munis")}</option>` +
        (big.length ? `<optgroup label="${t("big_cities")}">${big.map(opt).join("")}</optgroup>` : "") +
        `<optgroup label="${t("other_munis")}">${rest.map(opt).join("")}</optgroup>`;
}

function updateChrome() {
    document.getElementById("source-line").innerHTML =
        `${t("source")} <a href="${DATA.source_url}" target="_blank" rel="noopener">${DATA.source}</a>`;
    const upd = document.getElementById("updated-line");
    if (!DATA.updated) { upd.textContent = ""; upd.className = ""; return; }
    // LEA publishes Mon–Fri; >4 days old means a missed/failed update — warn.
    const days = Math.floor((Date.now() - Date.parse(DATA.updated)) / 86400000);
    if (days > 4) {
        upd.className = "stale";
        upd.textContent = t("data_stale", { date: DATA.updated, days });
    } else {
        upd.className = "";
        upd.textContent = t("data_updated", { date: DATA.updated });
    }
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
        strong_up:   ["↑", "oil_up", "up"],
        rise:        ["↑", "oil_up", "up"],
        stable:      ["→", "oil_flat", "flat"],
        fall:        ["↓", "oil_down", "down"],
        strong_down: ["↓", "oil_down", "down"],
    }[OIL.level] || ["→", "oil_flat", "flat"];
    el.className = "oil-footer oil-ind-" + ind[2];
    el.style.display = "flex";
    el.innerHTML = `🛢️ ${t("oil_brent")} · ${t("oil_weekavg")} <b>$${avg.toFixed(2)}</b> ·
        ${t("oil_perweek")} ${sign}${chg}% <span class="oil-ind">${ind[0]} ${t(ind[1])}</span>`;
}

// After changing fuel/sort, jump the list back to the top so re-rendering from a
// scrolled-down position doesn't strand you at the end of the new list. (The list
// scrolls inside its own 60vh container, so this resets that, not the page.)
function scrollListTop() {
    const l = document.getElementById("stations-list");
    if (l) l.scrollTop = 0;
}

// Debounce the search box so a full ~780-card re-render doesn't run on every keystroke.
let _searchTimer = null;
function onSearchInput() {
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(render, 150);
}

function selectFuel(f) {
    fuelType = f;
    document.querySelectorAll(".fuel-btn").forEach(b => b.classList.remove("active"));
    document.getElementById("btn-" + f).classList.add("active");
    render();
    scrollListTop();
    if (f === "ev" && REPORT_API) loadEvStatus().then(render);   // refresh live occupancy
}

function setSort(dir) {
    if (dir === "dist" && !userPos) return;
    sortDir = dir;
    ["asc", "desc", "dist"].forEach(d =>
        document.getElementById("sort-" + d).classList.toggle("active", d === dir));
    render();
    scrollListTop();
}

// "Cheapest within X km" — filter to a radius around the user's location. Picking
// a radius clears the municipality scope so it's a clean distance filter.
function setRadius(km) {
    if (km && !userPos) return;
    radiusKm = km;
    document.querySelectorAll(".radius-btn").forEach(b => b.classList.toggle("active", +b.dataset.km === km));
    if (km) document.getElementById("muni-select").value = "";
    render();
    scrollListTop();
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

// Municipality of the station nearest the user — used to auto-scope the list.
function nearestStationMuni(pos) {
    let best = null, bestD = Infinity;
    for (const s of (DATA.stations || [])) {
        if (s.lat == null || s.lon == null || !s.municipality) continue;
        const d = haversine(pos.lat, pos.lon, s.lat, s.lon);
        if (d < bestD) { bestD = d; best = s.municipality; }
    }
    return best;
}

function locate() {
    const btn = document.getElementById("locate-btn");
    if (!navigator.geolocation) { locateState = { key: "loc_unsupported" }; renderLocateBtn(); return; }
    btn.disabled = true;
    locateState = { key: "loc_detecting" }; renderLocateBtn();
    navigator.geolocation.getCurrentPosition(
        (pos) => {
            userPos = { lat: pos.coords.latitude, lon: pos.coords.longitude };
            btn.disabled = false;
            btn.classList.add("on");
            document.getElementById("sort-dist").disabled = false;
            document.getElementById("radius-row").style.display = "flex";   // enable "within X km"
            // Auto-scope to the user's area so cheapest/priciest are LOCAL, not national.
            const muni = nearestStationMuni(userPos);
            const sel = document.getElementById("muni-select");
            if (muni && [...sel.options].some(o => o.value === muni)) sel.value = muni;
            locateState = muni ? { key: "loc_set_muni", vars: { muni } } : { key: "loc_set" };
            renderLocateBtn();
            setSort("dist");
            if (map) {
                addUserMarker();
                map.setView([userPos.lat, userPos.lon], 13);
            }
        },
        (err) => {
            btn.disabled = false;
            locateState = err.code === 1 ? { key: "loc_denied" } : { key: "loc_failed" };
            renderLocateBtn();
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

// Fuel-availability chips (⛽ 95 / 🚛 diesel / 🔥 LPG). Fuels this station sells
// are highlighted; ones it doesn't are greyed out — so it's clear a station may
// not carry every fuel type.
const FUEL_ICONS = [["petrol95", "⛽"], ["diesel", "🚛"], ["lpg", "🔥"]];
function fuelChips(s) {
    // A fuel is "available" if the station has a price for it OR (for price-less
    // stations from the full registry) it's listed in s.fuels.
    const has = k => s[k] != null || (s.fuels || []).includes(k);
    const chips = FUEL_ICONS.map(([k, ic]) =>
        `<span class="fuel-chip ${has(k) ? "" : "off"}" title="${escAttr(t("fuel_" + k))}">${ic}</span>`).join("");
    return `<div class="fuel-chips"><span class="lbl">${t("fuels_label")}</span>${chips}</div>`;
}

// --- shared row selection --------------------------------------------------

function getRows() {
    const muni = document.getElementById("muni-select").value;
    const q = (document.getElementById("search").value || "").toLowerCase().trim();

    // Priced stations for this fuel + price-less registry stations that sell it.
    let rows = (DATA.stations || []).filter(s =>
        s[fuelType] != null || (s.no_price && (s.fuels || []).includes(fuelType)));
    if (muni) rows = rows.filter(s => (s.municipality || "") === muni);
    if (q) rows = rows.filter(s =>
        ((s.network || "") + " " + (s.address || "") + " " + (s.locality || "")).toLowerCase().includes(q));

    if (userPos) rows.forEach(s => {
        s._dist = (s.lat != null && s.lon != null)
            ? haversine(userPos.lat, userPos.lon, s.lat, s.lon) : null;
    });
    if (userPos && radiusKm) rows = rows.filter(s => s._dist != null && s._dist <= radiusKm);

    if (sortDir === "dist" && userPos) {
        rows.sort((a, b) => (a._dist ?? Infinity) - (b._dist ?? Infinity));
    } else {
        rows.sort((a, b) => {
            const ap = a[fuelType], bp = b[fuelType];   // price-less (null) sort to the bottom
            if (ap == null || bp == null) return (ap == null) - (bp == null);
            return sortDir === "asc" ? ap - bp : bp - ap;
        });
    }
    return rows;
}

// --- rendering -------------------------------------------------------------

function render() {
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
    const flagged = DISCREP.items || [];   // any fuel, any network
    if (!flagged.length) { el.style.display = "none"; return; }
    el.style.display = "block";
    el.innerHTML = t("banner_change_all");
}

function renderSummary() {
    const box = document.getElementById("summary");
    const sum = DATA.summary || {};
    // All three fuels at once: cheapest / average / most expensive.
    const FUELS = [["petrol95", t("fuel_petrol95")], ["diesel", t("fuel_diesel")], ["lpg", t("ws_lpg")]];
    const rows = FUELS.filter(([k]) => sum[k]).map(([k, label]) => {
        const s = sum[k];
        return `<tr><td>${label}</td>
            <td class="lo">€${s.min.toFixed(3)}</td>
            <td class="avg">€${s.avg.toFixed(3)}</td>
            <td class="hi">€${s.max.toFixed(3)}</td></tr>`;
    }).join("");
    if (!rows) { box.style.display = "none"; return; }
    box.style.display = "block";
    // Orlen wholesale reference (all products, with clear names).
    const WS_LABELS = { petrol95: t("fuel_petrol95"), diesel: t("fuel_diesel"), diesel_agri: t("ws_agri"), lpg: t("ws_lpg") };
    let wsLine = "";
    if (ORLEN_WS && ORLEN_WS.prices) {
        const parts = ["petrol95", "diesel", "diesel_agri", "lpg"]
            .filter(k => ORLEN_WS.prices[k] != null)
            .map(k => `${WS_LABELS[k]} <b>€${ORLEN_WS.prices[k].toFixed(3)}</b>`);
        if (parts.length) wsLine = `<div class="wholesale-ref">${t("ws_orlen", { date: ORLEN_WS.stated_date || "" })} ${parts.join(" · ")}</div>`;
    }
    box.innerHTML = `
        <div class="summary-title">${t("national_title")}</div>
        <table class="nat-table">
            <thead><tr><th></th><th>${t("stat_cheapest")}</th><th>${t("stat_avg")}</th><th>${t("stat_dearest")}</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>${wsLine}`;
}

function navButtons(s) {
    // Use precise coordinates ONLY when they're verified-exact. For approximate
    // (town-centroid) coords, navigate by ADDRESS instead so Google/Waze find the
    // real station rather than driving to our rough point.
    const exact = s.lat != null && s.lon != null && !s.approx;
    const q = encodeURIComponent(`${s.network || ""} ${s.address || ""} ${s.municipality || ""}`.trim());
    const gmaps = exact
        ? `https://www.google.com/maps/dir/?api=1&destination=${s.lat},${s.lon}`
        : `https://www.google.com/maps/search/?api=1&query=${q}`;
    const waze = exact
        ? `https://waze.com/ul?ll=${s.lat},${s.lon}&navigate=yes`
        : `https://waze.com/ul?q=${q}&navigate=yes`;
    return `<a class="nav-btn nav-gmaps" href="${gmaps}" target="_blank" rel="noopener">🗺️ Google Maps</a>
            <a class="nav-btn nav-waze" href="${waze}" target="_blank" rel="noopener">🚗 Waze</a>`;
}

function renderList() {
    const list = document.getElementById("stations-list");
    if (!DATA.stations || DATA.stations.length === 0) {
        list.innerHTML = `<div class="msg">${t("empty_list")}</div>`;
        return;
    }
    const rows = getRows();
    if (rows.length === 0) { list.innerHTML = `<div class="msg">${t("no_filter")}</div>`; return; }

    const priced = rows.filter(r => r[fuelType] != null);
    const best = priced.length ? Math.min(...priced.map(r => r[fuelType])) : null;
    const total = (DATA.stations || []).filter(s =>
        s[fuelType] != null || (s.no_price && (s.fuels || []).includes(fuelType))).length;
    const nLabel = rows.length < total ? `${rows.length} / ${total}` : `${total}`;  // your area / overall
    const shown = rows.slice(0, 600);           // keep the DOM snappy on phones (like the EV list)
    list.innerHTML =
        `<div class="count-line">${t("showing_stations", { n: nLabel })}</div>` +
        shown.map(s => {
            const isBest = s[fuelType] != null && s[fuelType] === best;
            const dist = (userPos && s._dist != null)
                ? `<span class="dist-badge">📍 ${s.approx ? "~" : ""}${fmtDist(s._dist)}</span>` : "";
            const approxTag = s.approx ? ` <span class="approx-tag">${t("approx_warn")}</span>` : "";
            const fl = flagFor(s);
            const flagLine = fl ? `<div class="change-flag">${t("flag_change", { price: fl.live.toFixed(3) })}</div>` : "";
            const rep = reportFor(s);
            const repLine = rep ? `<div class="report-line">${t("report_line", { price: rep.price.toFixed(3) })}</div>` : "";
            const repBtn = REPORT_API ? `<button class="report-btn" data-key="${escAttr(stationKey(s))}">${t("report_btn")}</button>` : "";
            return `
            <div class="station-card">
                ${isBest ? `<div class="best-price-badge">${t("badge_cheapest")}</div>` : ''}${dist}
                <div class="station-header">
                    <div class="station-name">${esc(s.network || t("station_default"))}</div>
                    <div>${s[fuelType] != null
                        ? `<span class="station-price">€${s[fuelType].toFixed(3)}</span><span class="price-unit">/L</span>`
                        : `<span class="no-price-badge">${t("no_price")}</span>`}</div>
                </div>
                <div class="station-address">${esc(s.address || "")}${s.locality ? ", " + esc(s.locality) : ""}</div>
                <div class="station-muni">📍 ${esc(s.municipality || "")}${approxTag}</div>
                ${fuelChips(s)}
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
        addUserMarker();
        map.setView([userPos.lat, userPos.lon], 13);
    }
}

// A distinctive pulsing "you are here" marker. Uses a divIcon marker (marker
// pane) with a high zIndexOffset so it always sits ABOVE the charger/price pins
// — a plain circleMarker sits on a lower pane and gets buried under them.
function addUserMarker() {
    if (!map || !userPos) return;
    if (userMarker) userMarker.remove();
    const icon = L.divIcon({ className: "", html: '<div class="user-dot"></div>', iconSize: [18, 18], iconAnchor: [9, 9] });
    userMarker = L.marker([userPos.lat, userPos.lon], { icon, zIndexOffset: 1000, keyboard: false })
        .addTo(map).bindPopup(t("you_are_here"));
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

    const prices = rows.map(r => r[fuelType]).filter(p => p != null);
    const lo = Math.min(...prices), hi = Math.max(...prices);
    const bounds = [];

    rows.forEach(s => {
        const p = s[fuelType];
        let cls = "price-pin", label;
        if (p == null) {                       // price-less registry station
            cls += " noprice"; label = "?";
        } else {
            if (p <= lo + (hi - lo) * 0.25) cls += " cheap";
            else if (p >= lo + (hi - lo) * 0.75) cls += " dear";
            label = `€${p.toFixed(2)}`;
        }
        if (s.approx) cls += " approx";
        const icon = L.divIcon({
            className: "", html: `<div class="${cls}">${label}</div>`,
            iconSize: null, iconAnchor: [22, 12]
        });
        const dist = (userPos && s._dist != null) ? `<br>📍 ${s.approx ? "~" : ""}${fmtDist(s._dist)}` : "";
        const approxNote = s.approx ? `<br><span style="color:#b3792f">⚠️ ${t("approx_warn")}</span>` : "";
        const priceLine = p != null
            ? `<div class="popup-price">${t("fuel_" + fuelType)}: €${p.toFixed(3)}/L</div>`
            : `<div class="no-price-badge">${t("no_price")}</div>`;
        const popup = `<div class="popup-name">${esc(s.network || t("station_default"))}</div>
            <div>${esc(s.address || "")}</div>
            ${priceLine}${dist}${approxNote}
            ${fuelChips(s)}
            <div class="popup-nav">${navButtons(s)}</div>`;
        L.marker([s.lat, s.lon], { icon }).bindPopup(popup, { minWidth: 220 }).addTo(markersLayer);
        bounds.push([s.lat, s.lon]);
    });

    if (!userPos && bounds.length) map.fitBounds(bounds, { padding: [30, 30] });
    addUserMarker();   // keep the "you are here" pin on top of the price pins
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
