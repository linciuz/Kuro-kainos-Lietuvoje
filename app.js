// Fuelis - official LEA prices + nearest-to-me + map with price POIs.
// Data shape (scripts/fetch_prices.py + scripts/geocode.py):
// { updated, source, source_url, summary:{...}, stations:[{network,address,municipality,
//   locality,petrol95,diesel,lpg, lat, lon, approx}] }

// Fuel labels are localized via i18n: t("fuel_" + key). See i18n.js.

// Set to your deployed Cloudflare Worker URL to enable "report a price".
// Empty = feature hidden, app works as before. See worker/README.md.
const REPORT_API = "";
const LT_CENTER = [55.17, 23.88];   // Lithuania centre, for the default map view

// --- engagement / monetization features (ALL enabled for testing; later split
//     into free vs "Fuelis Pro"). Set DONATE_URL to your Ko-fi/BuyMeACoffee/PayPal.
const DONATE_URL = "https://ko-fi.com/fuelis";

function lsGet(k, def) { try { const v = localStorage.getItem(k); return v == null ? def : JSON.parse(v); } catch (e) { return def; } }
function lsSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }

let FAVS = lsGet("kk_favs", []);                 // starred station/charger keys
let showFavsOnly = false;                        // favourites-only filter
let ALERTS = lsGet("kk_alerts", { enabled: false, seen: {}, muni: "" });   // price-drop alerts (scope frozen at enable time)
let HISTORY = null;                              // daily national price-summary snapshots
let FUELLOG = lsGet("kk_fuellog", []);           // [{date, litres, km, price}] fuel-log entries

// --- Loyalty-card effective price (opt-in, default OFF) ----------------------
// User-entered per-network discount in ¢/L, kept in kk_loyalty. Rendered ONLY as
// a secondary "su kortele / with card" badge ALONGSIDE the official price — it
// never replaces the official price and never feeds the national summary,
// cheapest badge, or sort order (all of which stay on the official price).
let LOYALTY = lsGet("kk_loyalty", { enabled: false, cents: {} });
if (!LOYALTY || typeof LOYALTY !== "object") LOYALTY = { enabled: false, cents: {} };
if (typeof LOYALTY.enabled !== "boolean") LOYALTY.enabled = false;
if (!LOYALTY.cents || typeof LOYALTY.cents !== "object") LOYALTY.cents = {};

// Major branded networks that run loyalty programs: brand label -> legal company
// name exactly as it appears in stations.json (s.network).
const LOYALTY_NETWORKS = [
    ["Circle K", "UAB Circle K Lietuva"],
    ["Viada", "UAB Viada LT"],
    ["Neste", "UAB Neste Lietuva"],
    ["Baltic Petroleum", "UAB Baltic Petroleum"],
    ["Orlen", "AB Orlen Baltics Retail"],
];

// Typical everyday ¢/L discount per network — used ONLY as a greyed-out input
// placeholder/hint, NEVER as an applied value. Real amounts vary by card tier,
// the network's own app, day of week, and one-off promos (e.g. Viada's bigger
// Wednesday / summer-weekend deals announced ~1 day ahead), so the user always
// types their own. Networks without a confirmed typical get no hint (falls to 0).
const LOYALTY_TYPICAL = {
    "UAB Circle K Lietuva": "3.5",   // typical card/app discount
    "UAB Neste Lietuva": "3.5",      // typical; Neste app can reach ~7
    "UAB Baltic Petroleum": "0.5",   // everyday app discount ≈ half a cent (e.g. 1.569 → 1.564); bigger on Fridays
};

// Optional per-network condition note shown under the config row (i18n key) —
// captures the "depends on the day / your status" caveats so a tiny or absent
// typical value isn't the whole story.
const LOYALTY_NOTES = {
    "UAB Baltic Petroleum": "loyalty_note_bp",
    "UAB Viada LT": "loyalty_note_viada",
    "UAB Neste Lietuva": "loyalty_note_neste",
    "UAB Emsi": "loyalty_note_emsi",
};

// Discount in ¢/L configured for this station's network (>0), else 0 — also 0
// whenever the whole feature is toggled off.
function loyaltyCents(s) {
    if (!LOYALTY.enabled || !s) return 0;
    const c = LOYALTY.cents[s.network];
    return (typeof c === "number" && isFinite(c) && c > 0) ? c : 0;
}
// Effective €/L after a ¢/L discount, clamped so it can never go negative.
function loyaltyPrice(price, cents) { return Math.max(0, price - cents / 100); }
// Format a ¢/L value compactly: up to 2 decimals, trailing zeros trimmed
// (so 3 -> "3", 3.5 -> "3.5", 0.05 -> "0.05").
function loyaltyFmt(c) { return Number(Number(c).toFixed(2)).toString(); }
// A representative €/L price for a network (its cheapest station at the current
// fuel; falls back to the overall cheapest). Used only for the live config
// preview so the user can sanity-check the MAGNITUDE of the discount they type.
function loyaltyRefPrice(legal) {
    const rf = (fuelType === "ev") ? "petrol95" : fuelType;
    const own = (DATA.stations || []).filter(s => s.network === legal && s[rf] != null).map(s => s[rf]);
    if (own.length) return Math.min(...own);
    const all = (DATA.stations || []).filter(s => s[rf] != null).map(s => s[rf]);
    return all.length ? Math.min(...all) : null;
}
// Tidy a legal company name for display ("UAB Viada LT" -> "Viada LT").
function loyaltyLabel(net) { return String(net || "").replace(/^(UAB|AB|VšĮ|VŠĮ|MB|IĮ|Iį)\s+/i, ""); }

let DATA = { updated: null, source: "", source_url: "", summary: {}, stations: [] };
let DISCREP = { items: [], byNetwork: {} };   // comparison-engine flags
let REPORTS = {};                             // user-reported prices {stationKey:{fuel:{price,ts}}}
let ORLEN_WS = null;                           // Orlen refinery wholesale reference
let CK_BIZ = null;                             // Circle K business fixed price (today-stamped, incl. VAT)
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
    buildActionBar();
    updateFeatureButtons();
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
    await loadCircleKBusiness();
    await loadOil();
    await loadElectricity();
    await loadEv();
    await loadEvStatus();
    await loadHistory();
    // Preserve the chosen municipality across a foreground refetch (initMunicipalities
    // rebuilds the <select> and would otherwise reset it to "All municipalities").
    const _muniSel = document.getElementById("muni-select");
    const _keepMuni = _muniSel ? _muniSel.value : "";
    initMunicipalities();
    if (_muniSel && _keepMuni && [..._muniSel.options].some(o => o.value === _keepMuni)) _muniSel.value = _keepMuni;
    buildLangSwitcher();
    applyStaticI18n();
    updateChrome();
    buildActionBar();
    render();
    updateFeatureButtons();
    checkPriceAlerts();     // notify if the cheapest in the user's area dropped since last visit
    // Delegate report-button clicks (station keys can contain quotes/pipes).
    const list = document.getElementById("stations-list");
    if (list && !list._reportBound) {
        list._reportBound = true;
        list.addEventListener("click", (e) => {
            const fb = e.target.closest(".fav-btn");
            if (fb) { toggleFav(fb.dataset.key); return; }
            const b = e.target.closest(".report-btn");
            if (b) reportPrice(b.dataset.key, fuelType);
        });
    }
}

function stationKey(s) {
    return `${s.network || ""}|${s.address || ""}|${s.municipality || ""}`;
}

// --- favourites (starred stations & chargers, kept in localStorage) ----------
function chargerKey(c) { return "ev:" + (c.ocpi_id || `${c.operator}|${c.lat}|${c.lon}`); }
function favKey(x) { return fuelType === "ev" ? chargerKey(x) : "st:" + stationKey(x); }
function isFav(key) { return FAVS.includes(key); }
function toggleFav(key) {
    const i = FAVS.indexOf(key);
    if (i >= 0) FAVS.splice(i, 1); else FAVS.push(key);
    lsSet("kk_favs", FAVS);
    render();
    updateFeatureButtons();
}
function toggleFavsOnly() { showFavsOnly = !showFavsOnly; updateFeatureButtons(); render(); scrollListTop(); }

// --- price-drop alerts (local: fire when the app is opened / refetched) ------
function currentMuni() { return (document.getElementById("muni-select") || {}).value || ""; }

// Deliver a notification via the service worker — REQUIRED on Android Chrome and
// in the Play/TWA WebView, where the page-context `new Notification()` throws
// "Illegal constructor". Fall back to the constructor on desktop browsers.
async function showNotification(title, opts) {
    try {
        if ("serviceWorker" in navigator) {
            const reg = await navigator.serviceWorker.ready;
            if (reg && reg.showNotification) { await reg.showNotification(title, opts); return true; }
        }
    } catch (e) {}
    try { new Notification(title, opts); return true; } catch (e) {}
    return false;
}

async function toggleAlerts() {
    if (!ALERTS.enabled) {
        if (!("Notification" in window)) { alert(t("alerts_unsupported")); return; }
        if (Notification.permission !== "granted"
            && await Notification.requestPermission() !== "granted") { alert(t("alerts_denied")); return; }
        ALERTS.enabled = true;
        ALERTS.muni = currentMuni();                 // freeze the scope so the baseline and every
        ALERTS.seen = currentCheapest(ALERTS.muni);  // future check compare like-for-like
        alert(t("alerts_on_msg"));
    } else {
        ALERTS.enabled = false;
    }
    lsSet("kk_alerts", ALERTS);
    updateFeatureButtons();
}

function currentCheapest(muni) {
    if (muni == null) muni = currentMuni();
    const out = {};
    for (const f of ["petrol95", "diesel", "lpg"]) {
        const p = (DATA.stations || []).filter(s => s[f] != null && (!muni || s.municipality === muni)).map(s => s[f]);
        if (p.length) out[f] = Math.min(...p);
    }
    return out;
}

async function checkPriceAlerts() {
    if (!ALERTS.enabled || !("Notification" in window) || Notification.permission !== "granted") return;
    const scope = ALERTS.muni || "";                 // frozen scope — NOT the live municipality select
    const now = currentCheapest(scope);
    if (!Object.keys(now).length) return;            // failed/offline load left an empty dataset —
                                                     // don't touch the baseline or we'd swallow real drops
    const area = scope || "Lietuva";
    const seen = ALERTS.seen || {};
    for (const f of ["petrol95", "diesel", "lpg"]) {
        if (now[f] != null && seen[f] != null && now[f] < seen[f] - 0.0005) {
            showNotification(t("alert_title"), { body: t("alert_body", { fuel: t("fuel_" + f), price: now[f].toFixed(3), area }), icon: "icon-192.png" });
        }
        if (now[f] != null) seen[f] = now[f];        // raise/lower only fuels present in this load
    }
    ALERTS.seen = seen;
    lsSet("kk_alerts", ALERTS);
}

function openDonate() { window.open(DONATE_URL, "_blank", "noopener"); }

function updateFeatureButtons() {
    const fb = document.getElementById("fav-toggle");
    if (fb) fb.classList.toggle("on", showFavsOnly);
    const ab = document.getElementById("alert-toggle");
    if (ab) ab.classList.toggle("on", ALERTS.enabled);
}

function buildActionBar() {
    const box = document.getElementById("action-bar");
    if (!box) return;
    box.innerHTML =
        `<button type="button" class="act-btn" id="fav-toggle" onclick="toggleFavsOnly()" title="${esc(t("favourites"))}">★</button>
         <button type="button" class="act-btn" id="alert-toggle" onclick="toggleAlerts()" title="${esc(t("alert_title"))}">🔔</button>
         <button type="button" class="act-btn" id="tools-toggle" onclick="openTools()" title="${esc(t("tools_title"))}">🧮</button>
         <button type="button" class="act-btn donate" onclick="openDonate()">☕ ${esc(t("support"))}</button>`;
}

// ---- Fuelis Tools: consumption calculator, "worth the detour?", fuel
//      comparison, and a fill-up log — all client-side, saved in localStorage. --
function toolNum(id) { const v = parseFloat((document.getElementById(id) || {}).value); return isFinite(v) ? v : null; }
function toolFmt(n, d) { d = d == null ? 2 : d; return (n == null || !isFinite(n)) ? "–" : n.toLocaleString("lt-LT", { minimumFractionDigits: d, maximumFractionDigits: d }); }
function todayISO() { return new Date().toISOString().slice(0, 10); }

function openTools() {
    renderTools();
    const m = document.getElementById("tools-modal");
    if (m) { m.classList.add("open"); document.body.classList.add("modal-open"); }
}
function closeTools() {
    const m = document.getElementById("tools-modal");
    if (m) { m.classList.remove("open"); document.body.classList.remove("modal-open"); }
}

function renderTools() {
    const title = document.getElementById("tools-title");
    if (title) title.textContent = "🧮 " + t("tools_title");
    const body = document.getElementById("tools-body");
    if (!body) return;
    const p95 = currentCheapest("").petrol95, pdie = currentCheapest("").diesel;
    const priceHint = (p95 || pdie || 1.7).toFixed(3);
    body.innerHTML = `
      <section class="tool-card">
        <h3>${esc(t("cc_title"))}</h3>
        <label>${esc(t("cc_litres"))}<input id="cc-litres" type="number" inputmode="decimal" step="0.01" min="0" placeholder="45"></label>
        <label>${esc(t("cc_km"))}<input id="cc-km" type="number" inputmode="decimal" step="0.1" min="0" placeholder="600"></label>
        <label>${esc(t("cc_price"))}<input id="cc-price" type="number" inputmode="decimal" step="0.001" min="0" placeholder="${priceHint}"></label>
        <button type="button" class="tool-btn" onclick="calcConsumption()">${esc(t("tool_calc"))}</button>
        <div id="cc-out" class="tool-out"></div>
      </section>

      <section class="tool-card">
        <h3>${esc(t("wd_title"))}</h3>
        <label>${esc(t("wd_cons"))}<input id="wd-cons" type="number" inputmode="decimal" step="0.1" min="0" placeholder="7.0"></label>
        <label>${esc(t("wd_here"))}<input id="wd-here" type="number" inputmode="decimal" step="0.001" min="0" placeholder="${priceHint}"></label>
        <label>${esc(t("wd_there"))}<input id="wd-there" type="number" inputmode="decimal" step="0.001" min="0"></label>
        <label>${esc(t("wd_dist"))}<input id="wd-dist" type="number" inputmode="decimal" step="0.1" min="0" placeholder="5"></label>
        <label>${esc(t("wd_litres"))}<input id="wd-litres" type="number" inputmode="decimal" step="0.1" min="0" placeholder="45"></label>
        <button type="button" class="tool-btn" onclick="calcDetour()">${esc(t("tool_calc"))}</button>
        <div id="wd-out" class="tool-out"></div>
      </section>

      <section class="tool-card">
        <h3>${esc(t("cmp_title"))}</h3>
        <div class="tool-note">${esc(t("cmp_note"))}</div>
        <label>${esc(t("fuel_petrol95"))} (L/100&nbsp;km)<input id="cmp-petrol95" type="number" inputmode="decimal" step="0.1" min="0" placeholder="7.5"></label>
        <label>${esc(t("fuel_diesel"))} (L/100&nbsp;km)<input id="cmp-diesel" type="number" inputmode="decimal" step="0.1" min="0" placeholder="5.5"></label>
        <label>${esc(t("ws_lpg"))} (L/100&nbsp;km)<input id="cmp-lpg" type="number" inputmode="decimal" step="0.1" min="0" placeholder="9.5"></label>
        <button type="button" class="tool-btn" onclick="calcCompare()">${esc(t("tool_calc"))}</button>
        <div id="cmp-out" class="tool-out"></div>
      </section>

      <section class="tool-card">
        <h3>💳 ${esc(t("loyalty_title"))}</h3>
        <div class="tool-note">${esc(t("loyalty_note"))}</div>
        <label class="loyalty-switch">
          <input type="checkbox" id="loyalty-enabled" ${LOYALTY.enabled ? "checked" : ""} onchange="toggleLoyalty(this.checked)">
          <span>${esc(t("loyalty_enable"))}</span>
        </label>
        <div class="loyalty-config${LOYALTY.enabled ? "" : " off"}">${loyaltyConfigHtml()}</div>
        <div class="tool-note loyalty-disclaimer">${esc(t("loyalty_disclaimer"))}</div>
      </section>

      <section class="tool-card">
        <h3>${esc(t("log_title"))}</h3>
        <div class="log-add-row">
          <input id="lg-date" type="date" value="${todayISO()}">
          <input id="lg-litres" type="number" inputmode="decimal" step="0.01" min="0" placeholder="${esc(t("cc_litres"))}">
          <input id="lg-km" type="number" inputmode="decimal" step="0.1" min="0" placeholder="km">
          <input id="lg-price" type="number" inputmode="decimal" step="0.001" min="0" placeholder="€/L">
          <button type="button" class="tool-btn small" onclick="addLogManual()">${esc(t("log_add"))}</button>
        </div>
        <div id="log-body" class="log-body"></div>
      </section>`;
    renderLog();
}

// Build the per-network discount rows: the 5 major branded networks are always
// shown; any other network the user has already configured gets a row too; and
// an "add another network" picker lists every remaining network in the data.
function loyaltyConfigHtml() {
    const majorLegals = new Set(LOYALTY_NETWORKS.map(m => m[1]));
    const rows = LOYALTY_NETWORKS.map(([brand, legal]) => loyaltyRowHtml(brand, legal));
    const extras = Object.keys(LOYALTY.cents).filter(n => !majorLegals.has(n))
        .sort((a, b) => a.localeCompare(b, "lt"));
    for (const legal of extras) rows.push(loyaltyRowHtml(loyaltyLabel(legal), legal));

    const shown = new Set([...majorLegals, ...extras]);
    const others = [...new Set((DATA.stations || []).map(s => s.network).filter(Boolean))]
        .filter(n => !shown.has(n))
        .sort((a, b) => a.localeCompare(b, "lt"));
    const addSel = others.length
        ? `<select class="loyalty-add" onchange="addLoyaltyNet(this.value); this.value='';">
             <option value="">${esc(t("loyalty_add"))}</option>
             ${others.map(n => `<option value="${escAttr(n)}">${esc(loyaltyLabel(n))}</option>`).join("")}
           </select>`
        : "";
    return `<div class="loyalty-rows">${rows.join("")}</div>${addSel}`;
}

// Live "€ref → €disc" preview for a config row so the user can sanity-check the
// MAGNITUDE of the value they typed (e.g. 0.5 ¢/L visibly changes the price;
// 0.05 does not). Numbers only — no user data — so it is safe as innerHTML.
function loyaltyPreviewHtml(legal) {
    const c = LOYALTY.cents[legal];
    if (!(typeof c === "number" && isFinite(c) && c > 0)) return "";
    const ref = loyaltyRefPrice(legal);
    if (ref == null) return "";
    return `€${ref.toFixed(3)} → <b>€${loyaltyPrice(ref, c).toFixed(3)}</b>/L`;
}

function loyaltyRowHtml(label, legal) {
    const c = LOYALTY.cents[legal];
    const val = (typeof c === "number" && isFinite(c) && c > 0) ? loyaltyFmt(c) : "";
    const ph = LOYALTY_TYPICAL[legal] || "0";   // typical value as a hint only
    const noteKey = LOYALTY_NOTES[legal];
    const note = noteKey ? `<div class="loyalty-row-note">↳ ${esc(t(noteKey))}</div>` : "";
    return `<div class="loyalty-item">
      <div class="loyalty-row">
        <span class="loyalty-net">${esc(label)}</span>
        <input type="number" inputmode="decimal" step="any" min="0" max="50" placeholder="${escAttr(ph)}"
               value="${escAttr(val)}" data-net="${escAttr(legal)}" oninput="setLoyalty(this)"
               ${LOYALTY.enabled ? "" : "disabled"}>
        <span class="loyalty-unit">¢/L</span>
      </div>
      <div class="loyalty-preview">${loyaltyPreviewHtml(legal)}</div>${note}
    </div>`;
}

function toggleLoyalty(on) {
    LOYALTY.enabled = !!on;
    lsSet("kk_loyalty", LOYALTY);
    renderTools();   // enable/disable the ¢/L inputs
    render();        // add/remove badges on the list & map
}

// Live-update as the user types a ¢/L value. A blank/0/invalid value removes the
// discount (so no badge). render() refreshes the badges but never touches the
// Tools modal DOM, so the input the user is typing in is preserved.
function setLoyalty(input) {
    const net = input.dataset.net;
    if (!net) return;
    const v = parseFloat(String(input.value || "").replace(",", "."));
    if (isFinite(v) && v > 0) LOYALTY.cents[net] = Math.min(v, 50);
    else delete LOYALTY.cents[net];
    lsSet("kk_loyalty", LOYALTY);
    const item = input.closest(".loyalty-item");           // live-update this row's preview
    const prev = item && item.querySelector(".loyalty-preview");
    if (prev) prev.innerHTML = loyaltyPreviewHtml(net);
    render();
}

function addLoyaltyNet(net) {
    if (!net || LOYALTY.cents[net] != null) return;
    LOYALTY.cents[net] = 0;   // adds an (empty) row; 0 yields no badge until a value is typed
    lsSet("kk_loyalty", LOYALTY);
    renderTools();
    try {
        const sel = '.loyalty-row input[data-net="' + ((window.CSS && CSS.escape) ? CSS.escape(net) : net) + '"]';
        const el = document.querySelector(sel);
        if (el) el.focus();
    } catch (e) {}
}

function calcConsumption() {
    const L = toolNum("cc-litres"), km = toolNum("cc-km"), price = toolNum("cc-price");
    const out = document.getElementById("cc-out");
    if (!L || !km || L <= 0 || km <= 0) { out.innerHTML = `<div class="tool-hint">${esc(t("tool_need_lkm"))}</div>`; return; }
    const cons = L / km * 100;
    let html = `<div class="res-row"><span>${esc(t("tool_consumption"))}</span><b>${toolFmt(cons, 1)} L/100&nbsp;km</b></div>`;
    if (price && price > 0) {
        html += `<div class="res-row"><span>${esc(t("tool_total_cost"))}</span><b>€${toolFmt(L * price)}</b></div>
                 <div class="res-row"><span>${esc(t("tool_per100"))}</span><b>€${toolFmt(cons * price)}</b></div>
                 <div class="res-row"><span>${esc(t("tool_perkm"))}</span><b>€${toolFmt(L * price / km, 3)}</b></div>`;
    }
    html += `<button type="button" class="tool-btn save" onclick="saveLogFromCalc()">${esc(t("tool_save_log"))}</button>`;
    out.innerHTML = html;
}
function saveLogFromCalc() {
    const L = toolNum("cc-litres"), km = toolNum("cc-km"), price = toolNum("cc-price");
    if (!L || !km) return;
    FUELLOG.push({ date: todayISO(), litres: L, km: km, price: price || null });
    lsSet("kk_fuellog", FUELLOG);
    renderLog();
    document.getElementById("cc-out").insertAdjacentHTML("beforeend", `<div class="tool-ok">${esc(t("tool_saved"))}</div>`);
}

function calcDetour() {
    const cons = toolNum("wd-cons"), here = toolNum("wd-here"), there = toolNum("wd-there"),
          dist = toolNum("wd-dist"), litres = toolNum("wd-litres");
    const out = document.getElementById("wd-out");
    if (cons == null || here == null || there == null || dist == null || litres == null) {
        out.innerHTML = `<div class="tool-hint">${esc(t("tool_fill_fields"))}</div>`; return;
    }
    const gross = litres * (here - there);                 // saved on the fill
    const detour = (2 * dist) * (cons / 100) * there;      // round-trip fuel burned to get there
    const net = gross - detour;
    const good = net > 0;
    out.innerHTML =
        `<div class="res-row"><span>${esc(t("tool_gross_saving"))}</span><b>€${toolFmt(gross)}</b></div>
         <div class="res-row"><span>${esc(t("tool_detour_cost"))}</span><b>€${toolFmt(detour)}</b></div>
         <div class="res-row big ${good ? "good" : "bad"}"><span>${esc(t("tool_net_saving"))}</span><b>€${toolFmt(net)}</b></div>
         <div class="verdict-line ${good ? "good" : "bad"}">${esc(good ? t("tool_worth_yes") : t("tool_worth_no"))}</div>`;
}

function calcCompare() {
    const cheap = currentCheapest("");
    const rows = [["petrol95", t("fuel_petrol95")], ["diesel", t("fuel_diesel")], ["lpg", t("ws_lpg")]]
        .map(([k, label]) => { const c = toolNum("cmp-" + k), p = cheap[k]; return (c && p) ? { label, per100: c * p, price: p } : null; })
        .filter(Boolean).sort((a, b) => a.per100 - b.per100);
    const out = document.getElementById("cmp-out");
    if (!rows.length) { out.innerHTML = `<div class="tool-hint">${esc(t("cmp_hint"))}</div>`; return; }
    out.innerHTML = rows.map((r, i) =>
        `<div class="res-row ${i === 0 ? "good" : ""}"><span>${esc(r.label)}${i === 0 ? " ★" : ""} <small>(€${toolFmt(r.price, 3)}/L)</small></span><b>€${toolFmt(r.per100)}/100&nbsp;km</b></div>`
    ).join("");
}

function renderLog() {
    const box = document.getElementById("log-body");
    if (!box) return;
    if (!FUELLOG.length) { box.innerHTML = `<div class="tool-hint">${esc(t("tool_log_empty"))}</div>`; return; }
    let tL = 0, tKm = 0, tCost = 0;
    const rows = FUELLOG.map((e, idx) => {
        const cons = e.km ? e.litres / e.km * 100 : null;
        const cost = e.price ? e.litres * e.price : null;
        tL += e.litres || 0; tKm += e.km || 0; tCost += cost || 0;
        return { idx, html:
            `<div class="log-row">
               <div class="log-main"><b>${esc(e.date)}</b> · ${toolFmt(e.litres, 1)} L · ${toolFmt(e.km, 0)} km${e.price ? ` · €${toolFmt(e.price, 3)}/L` : ""}</div>
               <div class="log-sub">${cons != null ? `${toolFmt(cons, 1)} L/100 km` : ""}${cost != null ? ` · €${toolFmt(cost)}` : ""}
                 <button type="button" class="log-del" onclick="delLog(${idx})" title="${esc(t("tool_delete"))}">✕</button></div>
             </div>` };
    });
    const avg = tKm ? tL / tKm * 100 : null;
    box.innerHTML =
        `<div class="log-summary">
           <div><span>${esc(t("tool_avg_cons"))}</span><b>${toolFmt(avg, 1)} L/100 km</b></div>
           <div><span>${esc(t("tool_total_spent"))}</span><b>€${toolFmt(tCost)}</b></div>
           <div><span>${esc(t("tool_total_km"))}</span><b>${toolFmt(tKm, 0)} km</b></div>
         </div>
         ${rows.slice().reverse().map(r => r.html).join("")}
         <button type="button" class="tool-btn ghost" onclick="exportLog()">${esc(t("tool_export"))}</button>`;
}
function addLogManual() {
    const L = toolNum("lg-litres"), km = toolNum("lg-km"), price = toolNum("lg-price");
    const date = (document.getElementById("lg-date") || {}).value || todayISO();
    if (!L || !km) return;
    FUELLOG.push({ date, litres: L, km: km, price: price || null });
    lsSet("kk_fuellog", FUELLOG);
    ["lg-litres", "lg-km", "lg-price"].forEach(id => { const el = document.getElementById(id); if (el) el.value = ""; });
    renderLog();
}
function delLog(i) { FUELLOG.splice(i, 1); lsSet("kk_fuellog", FUELLOG); renderLog(); }
function exportLog() {
    const head = "date,litres,km,price_eur_per_l,consumption_l_per_100km,cost_eur\n";
    const lines = FUELLOG.map(e => {
        const cons = e.km ? (e.litres / e.km * 100).toFixed(2) : "";
        const cost = e.price ? (e.litres * e.price).toFixed(2) : "";
        return [e.date, e.litres, e.km, e.price != null ? e.price : "", cons, cost].join(",");
    }).join("\n");
    const blob = new Blob([head + lines], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "fuelis-log.csv"; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

async function loadHistory() {
    try {
        const res = await fetch("data/price_history.json", { cache: "no-store" });
        HISTORY = res.ok ? await res.json() : null;
    } catch (e) { HISTORY = null; }
}

// Tiny inline sparkline for the price-history trend.
function sparkline(vals, color) {
    vals = (vals || []).filter(v => v != null);
    if (vals.length < 2) return "";
    const w = 60, h = 16, lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
    const pts = vals.map((v, i) => `${((i / (vals.length - 1)) * w).toFixed(1)},${(h - ((v - lo) / span) * h).toFixed(1)}`).join(" ");
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="vertical-align:middle"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.6"/></svg>`;
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

async function loadCircleKBusiness() {
    try {
        const res = await fetch("data/sources/circlek_business.json", { cache: "no-store" });
        CK_BIZ = res.ok ? await res.json() : null;
    } catch (e) { CK_BIZ = null; }
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
    if (showFavsOnly) rows = rows.filter(c => isFav(favKey(c)));
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
    if (!rows.length) { list.innerHTML = `<div class="msg">${showFavsOnly ? t("no_favourites") : t("nothing_found")}</div>`; return; }
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
                <button class="fav-btn" data-key="${esc(favKey(c))}">${isFav(favKey(c)) ? "★" : "☆"}</button>
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
    if (showFavsOnly) rows = rows.filter(s => isFav(favKey(s)));

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
    // Circle K business fixed price (VAT-incl) — the one genuine SAME-DAY (today)
    // reference, shown right below Orlen. Order: 95, diesel, LPG, 98, AdBlue.
    let ckbLine = "";
    if (CK_BIZ && CK_BIZ.prices) {
        const CKB_LABELS = { petrol95: t("fuel_petrol95"), diesel: t("fuel_diesel"), lpg: t("ws_lpg"), petrol98: t("fuel_98"), adblue: t("fuel_adblue") };
        const parts = ["petrol95", "diesel", "lpg", "petrol98", "adblue"]
            .filter(k => CK_BIZ.prices[k] != null)
            .map(k => `${CKB_LABELS[k]} <b>€${CK_BIZ.prices[k].toFixed(3)}</b>`);
        if (parts.length) ckbLine = `<div class="wholesale-ref">${t("ws_circlek_biz", { date: CK_BIZ.stated_date || "" })} ${parts.join(" · ")}</div>`;
    }
    // Price-history trend (grows as the daily pipeline accumulates snapshots).
    let trendLine = "";
    const H = (HISTORY && HISTORY.history) || [];
    if (H.length >= 2) {
        const win = H.slice(-14);
        const sp = f => sparkline(win.map(h => h[f] && h[f].avg), "#007AFF");
        trendLine = `<div class="wholesale-ref">${t("trend_label")}: ⛽ ${sp("petrol95")} 🚛 ${sp("diesel")} 🔥 ${sp("lpg")}</div>`;
    }
    box.innerHTML = `
        <div class="summary-title">${t("national_title")}</div>
        <table class="nat-table">
            <thead><tr><th></th><th>${t("stat_cheapest")}</th><th>${t("stat_avg")}</th><th>${t("stat_dearest")}</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>${wsLine}${ckbLine}${trendLine}`;
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
    if (rows.length === 0) { list.innerHTML = `<div class="msg">${showFavsOnly ? t("no_favourites") : t("no_filter")}</div>`; return; }

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
            const lc = s[fuelType] != null ? loyaltyCents(s) : 0;
            // Suppress the badge when the discount is too small to change the
            // 3-decimal price — showing an identical "with card" price reads as a bug.
            const lDisc = lc > 0 ? loyaltyPrice(s[fuelType], lc).toFixed(3) : null;
            const loyaltyLine = (lDisc && lDisc !== s[fuelType].toFixed(3))
                ? `<div class="loyalty-line" title="−${loyaltyFmt(lc)} ¢/L">💳 ${esc(t("loyalty_with_card"))} <span class="loyalty-price">€${lDisc}</span><span class="price-unit">/L</span></div>`
                : "";
            return `
            <div class="station-card">
                <button class="fav-btn" data-key="${esc(favKey(s))}">${isFav(favKey(s)) ? "★" : "☆"}</button>
                ${isBest ? `<div class="best-price-badge">${t("badge_cheapest")}</div>` : ''}${dist}
                <div class="station-header">
                    <div class="station-name">${esc(s.network || t("station_default"))}</div>
                    <div>${s[fuelType] != null
                        ? `<span class="station-price">€${s[fuelType].toFixed(3)}</span><span class="price-unit">/L</span>${loyaltyLine}`
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
        const plc = p != null ? loyaltyCents(s) : 0;
        const pDisc = plc > 0 ? loyaltyPrice(p, plc).toFixed(3) : null;
        const loyaltyPop = (pDisc && pDisc !== p.toFixed(3))
            ? `<div class="popup-loyalty">💳 ${esc(t("loyalty_with_card"))}: €${pDisc}/L</div>`
            : "";
        const popup = `<div class="popup-name">${esc(s.network || t("station_default"))}</div>
            <div>${esc(s.address || "")}</div>
            ${priceLine}${loyaltyPop}${dist}${approxNote}
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
