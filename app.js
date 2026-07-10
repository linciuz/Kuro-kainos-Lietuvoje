// Fuelis - official LEA prices + nearest-to-me + map with price POIs.
// Data shape (scripts/fetch_prices.py + scripts/geocode.py):
// { updated, source, source_url, summary:{...}, stations:[{network,address,municipality,
//   locality,petrol95,diesel,lpg, lat, lon, approx}] }

// Fuel labels are localized via i18n: t("fuel_" + key). See i18n.js.

// Set to your deployed Cloudflare Worker URL to enable "report a price".
// Empty = feature hidden, app works as before. See worker/README.md.
const REPORT_API = "https://kk-reports.fuelis.workers.dev";
// Visitor counter endpoint. Convention: POST = log a visit + return {total,today};
// GET = read only. Point it at EITHER your self-hosted PHP logger on OWEXX
// (e.g. "https://api.fuelis.lt/count.php" — see server/count.php) OR your
// Cloudflare Worker's "/count". Leave empty and it falls back to REPORT_API+"/count".
// SHOW_VISIT_COUNTER=false keeps it counting but hidden. Dormant until one is set.
const COUNT_API = "https://kk-reports.fuelis.workers.dev/count";
const SHOW_VISIT_COUNTER = true;
const LT_CENTER = [55.17, 23.88];   // Lithuania centre, for the default map view

// --- engagement / monetization features (ALL enabled for testing; later split
//     into free vs "Fuelis Pro"). Set DONATE_URL to your Ko-fi/BuyMeACoffee/PayPal.
const DONATE_URL = "https://ko-fi.com/fuelis";
// Contact-form endpoint. Swap the plain email for the FormSubmit masked endpoint
// (https://formsubmit.co/<random-hash>) to keep the address out of the page source.
// FormSubmit masked endpoint (random string mapped to the owner's email
// server-side) — keeps the address out of the public page source.
const CONTACT_ENDPOINT = "https://formsubmit.co/c3b01d524fb6ace4c2549e2810066f7c";

// Google Play bans in-app external donation links, so hide the ☕ Support button
// inside the Android TWA. The wrapper launches with ?src=twa (twa-manifest
// startUrl) and an android-app:// referrer; persist the flag so it sticks across
// navigations. On the plain web the button shows as normal.
const IS_NATIVE = (() => {
    try {
        if (localStorage.getItem("kk_native") === "1") return true;
        const src = new URLSearchParams(location.search).get("src");
        if (src === "twa" || (document.referrer || "").startsWith("android-app://")) {
            localStorage.setItem("kk_native", "1");
            return true;
        }
    } catch (e) {}
    return false;
})();

function lsGet(k, def) { try { const v = localStorage.getItem(k); return v == null ? def : JSON.parse(v); } catch (e) { return def; } }
function lsSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }

let FAVS = lsGet("kk_favs", []);                 // starred station/charger keys
let showFavsOnly = false;                        // favourites-only filter
let ALERTS = lsGet("kk_alerts", { enabled: false, seen: {}, muni: "" });   // price-drop alerts (scope frozen at enable time)
// Migrate the pre-muni stored shape: without this, an old muni-scoped baseline
// would be compared against NATIONAL prices once and fire a false "price
// dropped" alert. Clearing seen re-baselines silently on the next check.
if (!ALERTS || typeof ALERTS !== "object") ALERTS = { enabled: false, seen: {}, muni: "" };
if (typeof ALERTS.muni !== "string") { ALERTS.muni = ""; ALERTS.seen = {}; }
if (!ALERTS.seen || typeof ALERTS.seen !== "object") ALERTS.seen = {};
let HISTORY = null;                              // daily national price-summary snapshots
let FUELLOG = lsGet("kk_fuellog", []);           // [{date, litres, km, price}] fuel-log entries

// --- Loyalty-card effective price (opt-in, default OFF) ----------------------
// User-entered per-network discount in ¢/L, kept in kk_loyalty. Rendered ONLY as
// a secondary "su kortele / with card" badge ALONGSIDE the official price — it
// never replaces the official price and never feeds the national summary,
// cheapest badge, or sort order (all of which stay on the official price).
let LOYALTY = lsGet("kk_loyalty", { enabled: false, cents: {} });
if (!LOYALTY || typeof LOYALTY !== "object" || Array.isArray(LOYALTY)) LOYALTY = { enabled: false, cents: {} };
if (typeof LOYALTY.enabled !== "boolean") LOYALTY.enabled = false;
// Reject arrays too: JSON.stringify drops named props of an array, silently
// losing every discount on reload if a corrupt/legacy shape slipped in.
if (!LOYALTY.cents || typeof LOYALTY.cents !== "object" || Array.isArray(LOYALTY.cents)) LOYALTY.cents = {};

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
    "UAB Viada LT": "3",             // ViadaPLUS tiered −3 ct/l (≤70 L/mo) → −3.5 (>70 L)
    "UAB Neste Lietuva": "3.5",      // Neste card −3.5 ct/l LT; Wed "nuolaidadienis" up to −7
    "UAB Baltic Petroleum": "0.5",   // CASH PRICE+ app ≈ half a cent (e.g. 1.569 → 1.564); card is −3 at regular stations
};

// Optional per-network condition note shown under the config row (i18n key) —
// captures the "depends on the day / your status" caveats so a tiny or absent
// typical value isn't the whole story.
const LOYALTY_NOTES = {
    "UAB Circle K Lietuva": "loyalty_note_circlek",
    "UAB Baltic Petroleum": "loyalty_note_bp",
    "UAB Viada LT": "loyalty_note_viada",
    "UAB Neste Lietuva": "loyalty_note_neste",
    "UAB Emsi": "loyalty_note_emsi",
};

// Sanity cap on the ¢/L a user can enter. Real LT loyalty discounts top out
// around 10 ¢/L (Viada weekends); 30 leaves generous headroom for promos while
// keeping a stray/huge value from painting an absurd near-zero effective price.
const LOYALTY_MAX = 30;

// Discount in ¢/L configured for this station's network (>0), else 0 — also 0
// whenever the whole feature is toggled off.
let VIADA_PROMO = null;   // {valid_date, prices:{petrol95,diesel,lpg}, url} from viada.lt "Super trečiadieniai"
let NESTE_PROMO = null;   // {valid_date, cents} from neste.lt "Nuolaidadienis" (Wednesday −N ct/L, app/card)

async function loadViadaPromos() {
    try {
        const res = await fetch("data/sources/viada_promos.json", { cache: "no-store" });
        const j = res.ok ? await res.json() : null;
        VIADA_PROMO = (j && j.wednesday) || null;
    } catch (e) { VIADA_PROMO = null; }
}

async function loadNestePromo() {
    try {
        const res = await fetch("data/sources/neste_promo.json", { cache: "no-store" });
        const j = res.ok ? await res.json() : null;
        NESTE_PROMO = (j && j.valid_date && j.cents > 0) ? j : null;
    } catch (e) { NESTE_PROMO = null; }
}

// Visitor counter. One endpoint (COUNT_API, or REPORT_API+"/count"): POST logs a
// visit + returns {total,today}, GET reads only. We POST at most once per device
// per day (localStorage dedup) so the log stays lean; other loads just read.
// Dormant until an endpoint is configured.
let VISIT_COUNT = null;
function countEndpoint() {
    if (COUNT_API) return COUNT_API;
    if (REPORT_API) return REPORT_API.replace(/\/+$/, "") + "/count";
    return "";
}
async function loadVisitCount() {
    const api = countEndpoint();
    if (!api) return;
    if (navigator.webdriver) return;   // headless crawlers render JS too — don't count (or bill) them
    let counted = false;
    try { counted = localStorage.getItem("kk_visit_day") === localTodayISO(); } catch (e) {}
    // Mark today as counted BEFORE the POST — otherwise a second load() (e.g. the
    // foreground-refetch) firing before the first POST resolves would double-count.
    if (!counted) { try { localStorage.setItem("kk_visit_day", localTodayISO()); } catch (e) {} }
    try {
        const res = counted ? await fetch(api) : await fetch(api, { method: "POST" });
        if (!res.ok) return;
        const j = await res.json();
        VISIT_COUNT = (j && typeof j.total === "number") ? j.total : null;
        renderVisitCount();
    } catch (e) {}
}
function renderVisitCount() {
    const el = document.getElementById("visit-count");
    if (!el) return;
    if (SHOW_VISIT_COUNTER && VISIT_COUNT != null && VISIT_COUNT > 0) {
        el.textContent = "👀 " + t("visits", { n: VISIT_COUNT.toLocaleString("lt-LT") });
        el.style.display = "";
    } else {
        el.style.display = "none";
    }
}

// Local (device-timezone) date — promo validity must match the LT calendar day,
// and toISOString() would flip to the previous UTC day between 00:00 and 03:00.
function localTodayISO() {
    const d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
}

// Viada "Super trečiadienis": viada.lt publishes the day's absolute promo prices
// (with ViadaPLUS). On the stated date ONLY, the Viada discount becomes
// official-price − promo-price, per station per fuel; any other day (or if the
// promo wouldn't lower the price) falls through to the user's configured cents.
function viadaPromoCents(s, fuel) {
    if (!VIADA_PROMO || !VIADA_PROMO.prices || !s || s.network !== "UAB Viada LT") return null;
    if (localTodayISO() !== VIADA_PROMO.valid_date) return null;
    const promo = VIADA_PROMO.prices[fuel];
    const official = s[fuel];
    if (promo == null || official == null || official <= promo) return null;   // never raise a price
    return Math.round((official - promo) * 10000) / 100;   // € -> ¢/L, 2-decimal precision
}

// Neste "Nuolaidadienis": neste.lt announces the Wednesday −N ct/L (app/card,
// all fuels) with the specific date as text. On the stated date ONLY, that
// figure replaces the user's configured Neste cents (Neste: "nuolaidos
// nesumuojamos" — the day discount is THE discount, not additive).
function nestePromoCents(s) {
    if (!NESTE_PROMO || !s || s.network !== "UAB Neste Lietuva") return null;
    if (localTodayISO() !== NESTE_PROMO.valid_date) return null;
    return NESTE_PROMO.cents;
}

function loyaltyCents(s, fuel) {
    if (!LOYALTY.enabled || !s) return 0;
    if (fuel == null) fuel = fuelType;
    const wed = viadaPromoCents(s, fuel);
    if (wed != null) return wed;
    const nes = nestePromoCents(s);
    if (nes != null) return nes;
    const c = LOYALTY.cents[s.network];
    return (typeof c === "number" && isFinite(c) && c > 0) ? c : 0;
}
// Effective €/L after a ¢/L discount, clamped so it can never go negative.
function loyaltyPrice(price, cents) { return Math.max(0, price - cents / 100); }
// Effective per-litre price at the current fuel for CHEAPEST sorting + the ⭐
// badge: official price minus this station's loyalty discount (0 when the feature
// is off or the network has none), so "cheapest" reflects what the user actually
// pays. The national summary deliberately stays on official prices.
function effPrice(s) {
    const p = s[fuelType];
    return p == null ? null : loyaltyPrice(p, loyaltyCents(s));
}
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
    setInfoBanner();
}

// Side-opening info banner: the "prices are LEA's 10:00 snapshot; real pump
// prices drift during the day (up or down)" caveat. Auto-opens once per session,
// then lives as a small ℹ️ tab on the left edge.
function setInfoBanner() {
    const el = document.getElementById("info-banner-text");
    if (el) el.textContent = t("banner_side");
}
function toggleInfoBanner() {
    const b = document.getElementById("info-banner");
    if (!b) return;
    b.classList.toggle("open");
    // Closing via the ℹ️ tab counts as "seen" too — otherwise the 10-min
    // foreground refetch would auto-slide the banner back open mid-session.
    if (!b.classList.contains("open")) {
        try { sessionStorage.setItem("kk_info_seen", "1"); } catch (e) {}
    }
}
function closeInfoBanner() {
    const b = document.getElementById("info-banner");
    if (b) b.classList.remove("open");
    try { sessionStorage.setItem("kk_info_seen", "1"); } catch (e) {}
}
function maybeAutoOpenInfo() {
    try { if (sessionStorage.getItem("kk_info_seen") === "1") return; } catch (e) {}
    setTimeout(() => { const b = document.getElementById("info-banner"); if (b) b.classList.add("open"); }, 900);
}

// Upper "?" tab → a side banner legend explaining the top action-bar buttons.
function toggleHelp() {
    const b = document.getElementById("help-banner");
    if (!b) return;
    if (!b.classList.contains("open")) renderHelp();
    b.classList.toggle("open");
}
function closeHelp() {
    const b = document.getElementById("help-banner");
    if (b) b.classList.remove("open");
}
function renderHelp() {
    const el = document.getElementById("help-content");
    if (!el) return;
    const items = [
        ["★", t("help_favs")], ["🔔", t("help_alerts")], ["💳", t("help_disc")],
        ["🧮", t("help_tools")], ["✉️", t("help_contact")], ["🔗", t("help_share")],
    ];
    if (!IS_NATIVE) items.push(["☕", t("help_support")]);
    el.innerHTML = `<button class="help-x" onclick="closeHelp()" aria-label="Close">✕</button>
      <div class="help-title">❓ ${esc(t("help_title"))}</div>` +
      items.map(([ic, txt]) => `<div class="help-item"><span class="hi">${ic}</span><span>${esc(txt)}</span></div>`).join("");
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
    await loadViadaPromos();
    await loadNestePromo();
    loadVisitCount();   // fire-and-forget; footer updates when it returns
    await loadOil();
    await loadElectricity();
    await loadEv();
    // Live EV occupancy is a slow upstream (proxied charger feed) — never block
    // boot on it. Only fetch when starting in EV mode; selectFuel() handles the
    // switch-to-EV case. Badges fill in when it returns.
    if (fuelType === "ev") loadEvStatus().then(() => { if (fuelType === "ev") render(); });
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
    applyUrlState();        // restore fuel/muni/view/search from a shared link (once)
    checkPriceAlerts();     // notify if the cheapest in the user's area dropped since last visit
    maybeAutoOpenInfo();    // slide the "10:00 snapshot" caveat banner in once per session
    maybeAutoLocate();      // silent locate when permission is ALREADY granted (never prompts)
    // Delegate report-button clicks (station keys can contain quotes/pipes).
    const list = document.getElementById("stations-list");
    if (list && !list._reportBound) {
        list._reportBound = true;
        list.addEventListener("click", (e) => {
            const fb = e.target.closest(".fav-btn");
            if (fb) { toggleFav(fb.dataset.key); return; }
            const b = e.target.closest(".report-btn");
            if (b) { reportPrice(b.dataset.key, fuelType); return; }
            const sh = e.target.closest(".share-btn");
            if (sh) { shareStation(sh.dataset.key); return; }
            const ea = e.target.closest(".evavail-btn");
            if (ea) { evAvailChooser(ea.closest(".ev-report-row"), ea.dataset.key); return; }
            const es = e.target.closest(".evst-btn");
            if (es) { evReport(es.dataset.key, es.dataset.s); return; }
            const ep = e.target.closest(".evprice-btn");
            if (ep) evReportPrice(ep.dataset.key);
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
        // User-tuned thresholds: a drop must beat 3 ¢/L to be worth a ping; a
        // rise must be a real repricing wave (≥10 ¢/L) — warn early so people
        // can still fill up at stations that haven't lifted prices yet.
        if (now[f] != null && seen[f] != null && now[f] < seen[f] - 0.03) {
            showNotification(t("alert_title"), { body: t("alert_body", { fuel: t("fuel_" + f), price: now[f].toFixed(3), area }), icon: "icon-192.png" });
        } else if (now[f] != null && seen[f] != null && now[f] >= seen[f] + 0.10) {
            showNotification(t("alert_rise_title"), { body: t("alert_rise_body", { fuel: t("fuel_" + f), old: seen[f].toFixed(3), price: now[f].toFixed(3), area }), icon: "icon-192.png" });
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
    const db = document.getElementById("disc-toggle");
    if (db) db.classList.toggle("on", LOYALTY.enabled);
}

// ---- PWA install button ----------------------------------------------------
let deferredInstallPrompt = null;
function isStandalone() {
    return (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) || navigator.standalone === true;
}
function isIOS() {
    // iPadOS 13+ Safari reports a macOS UA, so also detect a touch-capable "Mac".
    const touchMac = navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1;
    return (/iphone|ipad|ipod/i.test(navigator.userAgent) || touchMac) && !window.MSStream;
}
function canOfferInstall() { return !isStandalone() && (!!deferredInstallPrompt || isIOS()); }
window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();               // suppress the mini-infobar; we show our own button instead
    deferredInstallPrompt = e;
    try { buildActionBar(); } catch (x) {}
});
window.addEventListener("appinstalled", () => { deferredInstallPrompt = null; try { buildActionBar(); } catch (x) {} });
async function installApp() {
    if (deferredInstallPrompt) {
        deferredInstallPrompt.prompt();
        try { await deferredInstallPrompt.userChoice; } catch (e) {}
        deferredInstallPrompt = null;
        buildActionBar();
    } else if (isIOS()) {
        openIosInstall();          // iOS has no install prompt — show the Add-to-Home-Screen guide
    } else {
        alert(t("install_hint"));  // rare: a desktop browser that supports install but hasn't fired the prompt
    }
}

// iOS "Add to Home Screen" instructions modal (replaces a jarring browser alert()).
function openIosInstall() {
    renderIosInstall();
    const m = document.getElementById("ios-modal");
    if (m) m.classList.add("open");
    document.body.classList.add("modal-open");
}
function closeIosInstall() {
    const m = document.getElementById("ios-modal");
    if (m) m.classList.remove("open");
    document.body.classList.remove("modal-open");
}
function renderIosInstall() {
    const ttl = document.getElementById("ios-title");
    if (ttl) ttl.textContent = "📲 " + t("ios_install_title");
    const body = document.getElementById("ios-body");
    if (!body) return;
    const share = `<svg class="ios-share" viewBox="0 0 24 24" fill="none" stroke="#0057D8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v11"/><path d="M8 7l4-4 4 4"/><path d="M6 11H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-6a2 2 0 0 0-2-2h-1"/></svg>`;
    body.innerHTML = `<section class="tool-card">
      <div class="ios-intro">${esc(t("ios_install_intro"))}</div>
      <ol class="ios-steps">
        <li class="ios-step"><span class="n">1</span><span>${esc(t("ios_install_s1"))} ${share}</span></li>
        <li class="ios-step"><span class="n">2</span><span>${esc(t("ios_install_s2"))}</span></li>
        <li class="ios-step"><span class="n">3</span><span>${esc(t("ios_install_s3"))}</span></li>
      </ol>
      <div class="ios-note">${esc(t("ios_install_note"))}</div>
    </section>`;
}

// ---- Contact form (emails the developer via FormSubmit, no backend) ---------
let contactCaptcha = 0;
function openContact() {
    renderContact();
    const m = document.getElementById("contact-modal");
    if (m) { m.classList.add("open"); document.body.classList.add("modal-open"); }
}
function closeContact() {
    const m = document.getElementById("contact-modal");
    if (m) { m.classList.remove("open"); document.body.classList.remove("modal-open"); }
}
function cfTopicChange() {
    const sel = document.getElementById("cf-topic"), wrap = document.getElementById("cf-other-wrap");
    if (sel && wrap) wrap.style.display = sel.value === "__other" ? "flex" : "none";
}
function renderContact() {
    const title = document.getElementById("contact-title");
    if (title) title.textContent = "✉️ " + t("contact_title");
    const a = Math.floor(Math.random() * 8) + 1, b = Math.floor(Math.random() * 8) + 1;
    contactCaptcha = a + b;
    const body = document.getElementById("contact-body");
    if (!body) return;
    body.innerHTML = `
      <form id="contact-form" class="tool-card" onsubmit="return submitContact(event)"
            action="${CONTACT_ENDPOINT}" method="POST" enctype="multipart/form-data" target="ff_iframe">
        <div class="tool-note">${esc(t("contact_intro"))}</div>
        <input type="hidden" name="_subject" id="cf-subject" value="Fuelis">
        <input type="hidden" name="_captcha" value="false">
        <input type="hidden" name="_template" value="table">
        <input type="hidden" name="Topic" id="cf-topic-field">
        <input type="text" name="_honey" tabindex="-1" autocomplete="off" aria-hidden="true" style="position:absolute;left:-9999px">
        <label>${esc(t("contact_email"))}<input type="email" name="email" required inputmode="email" autocomplete="email"></label>
        <label>${esc(t("contact_topic"))}
          <select id="cf-topic" onchange="cfTopicChange()">
            <option value="${esc(t("contact_topic_bug"))}">${esc(t("contact_topic_bug"))}</option>
            <option value="${esc(t("contact_topic_improve"))}">${esc(t("contact_topic_improve"))}</option>
            <option value="__other">${esc(t("contact_topic_other"))}</option>
          </select></label>
        <label id="cf-other-wrap" style="display:none">${esc(t("contact_topic_other_ph"))}<input type="text" id="cf-other"></label>
        <label>${esc(t("contact_message"))}<textarea name="message" rows="4" required></textarea></label>
        <label>${esc(t("contact_photo"))}<input type="file" name="attachment" accept="image/*"></label>
        <label>${esc(t("contact_captcha", { a: a, b: b }))}<input type="number" id="cf-captcha" required inputmode="numeric"></label>
        <button type="submit" class="tool-btn">${esc(t("contact_send"))}</button>
        <div id="cf-status" class="tool-out"></div>
      </form>`;
}
function submitContact(e) {
    const status = document.getElementById("cf-status");
    // Offline guard: the hidden cross-origin iframe fires onload even for the
    // browser's error page, which would show a false "sent ✓". Catch the one
    // failure mode we CAN detect client-side.
    if (navigator.onLine === false) {
        if (status) status.innerHTML = `<div class="tool-err">${esc(t("contact_offline"))}</div>`;
        e.preventDefault();
        return false;
    }
    const ans = parseInt((document.getElementById("cf-captcha") || {}).value, 10);
    if (ans !== contactCaptcha) {
        if (status) status.innerHTML = `<div class="tool-err">${esc(t("contact_captcha_wrong"))}</div>`;
        e.preventDefault();
        return false;
    }
    const sel = document.getElementById("cf-topic");
    const topic = (sel && sel.value === "__other")
        ? ((document.getElementById("cf-other") || {}).value || t("contact_topic_other"))
        : (sel ? sel.value : "");
    const subj = document.getElementById("cf-subject");
    const tf = document.getElementById("cf-topic-field");
    if (subj) subj.value = "Fuelis: " + topic;
    if (tf) tf.value = topic;
    if (status) status.innerHTML = `<div class="tool-hint">${esc(t("contact_sending"))}</div>`;
    const iframe = document.querySelector('iframe[name="ff_iframe"]');
    if (iframe) iframe.onload = () => {
        const f = document.getElementById("contact-form");
        if (f) f.reset();
        cfTopicChange();   // reset() restores the select but not the "Other" row visibility
        if (status) status.innerHTML = `<div class="tool-ok">${esc(t("contact_sent"))}</div>`;
    };
    return true;   // allow the multipart POST to submit into the hidden iframe
}

function buildActionBar() {
    const box = document.getElementById("action-bar");
    if (!box) return;
    box.innerHTML =
        `<button type="button" class="act-btn" id="fav-toggle" onclick="toggleFavsOnly()" title="${esc(t("favourites"))}">★</button>
         <button type="button" class="act-btn" id="alert-toggle" onclick="toggleAlerts()" title="${esc(t("alerts_feature_title"))}">🔔</button>
         <button type="button" class="act-btn" id="disc-toggle" onclick="openDiscounts()" title="${esc(t("loyalty_title"))}">💳</button>
         <button type="button" class="act-btn" id="tools-toggle" onclick="openTools()" title="${esc(t("tools_title"))}">🧮</button>
         <button type="button" class="act-btn" id="contact-toggle" onclick="openContact()" title="${esc(t("contact_title"))}">✉️</button>
         <button type="button" class="act-btn" id="share-toggle" onclick="shareView()" title="${esc(t("share_view_title"))}">🔗</button>` +
        (canOfferInstall() ? `<button type="button" class="act-btn install" onclick="installApp()">⬇️ ${esc(t("install_app"))}</button>` : "") +
        (IS_NATIVE ? "" : `<button type="button" class="act-btn donate" onclick="openDonate()">☕ ${esc(t("support"))}</button>`);
    updateFeatureButtons();   // a rebuild wipes the ★/🔔/💳 active highlights — always re-apply
}

// --- shareable links -------------------------------------------------------
const SITE_URL = "https://fuelis.lt/";   // canonical (share the brand, not github.io)

// Build a link that restores the current view (fuel + municipality + list/map +
// search), optionally centred on one station.
function shareState(extra) {
    const p = new URLSearchParams();
    if (fuelType && fuelType !== "petrol95") p.set("fuel", fuelType);
    const muni = (document.getElementById("muni-select") || {}).value || "";
    if (muni) p.set("muni", muni);
    if (view && view !== "list") p.set("view", view);
    const q = ((document.getElementById("search") || {}).value || "").trim();
    if (q) p.set("q", q);
    if (extra) for (const k in extra) p.set(k, extra[k]);
    const qs = p.toString();
    return SITE_URL + (qs ? "?" + qs : "");
}

// Native share sheet where available (mobile), clipboard fallback otherwise.
async function doShare(text, url) {
    if (navigator.share) {
        try { await navigator.share({ title: "Fuelis", text, url }); return; }
        catch (e) { if (e && e.name === "AbortError") return; }
    }
    const full = text + " " + url;
    try { await navigator.clipboard.writeText(full); toast(t("share_copied")); }
    catch (e) { prompt(t("share_copy_manual"), full); }
}

function shareView() {
    const muni = (document.getElementById("muni-select") || {}).value || "";
    const where = muni || t("share_all_lt");
    const fuelLabel = t("fuel_" + fuelType);
    doShare(t("share_view_text", { fuel: fuelLabel, where }), shareState());
}

function shareStation(key) {
    const s = (DATA.stations || []).find(x => stationKey(x) === key);
    if (!s) return;
    const price = s[fuelType] != null ? "€" + s[fuelType].toFixed(3) + "/L" : "";
    doShare(t("share_station_text", {
        net: loyaltyLabel(s.network) || t("station_default"),
        fuel: t("fuel_" + fuelType), price, addr: s.address || "",
    }), shareState({ station: key }));
}

function toast(msg) {
    let el = document.getElementById("kk-toast");
    if (!el) { el = document.createElement("div"); el.id = "kk-toast"; el.className = "kk-toast"; document.body.appendChild(el); }
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("show"), 2200);
}

// Restore state from a shared URL — once, on first load only.
let _urlStateApplied = false;
function applyUrlState() {
    if (_urlStateApplied) return;
    _urlStateApplied = true;
    let p;
    try { p = new URLSearchParams(location.search); } catch (e) { return; }
    const fuel = p.get("fuel");
    if (fuel && ["petrol95", "diesel", "lpg", "ev"].includes(fuel) && document.getElementById("btn-" + fuel)) selectFuel(fuel);
    const muni = p.get("muni");
    if (muni) { const sel = document.getElementById("muni-select"); if (sel && [...sel.options].some(o => o.value === muni)) sel.value = muni; }
    const q = p.get("q");
    if (q) { const s = document.getElementById("search"); if (s) s.value = q; }
    const v = p.get("view");
    if (v === "map" || v === "list") setView(v);
    render();
    // If a specific station was shared, scroll to it and flash a highlight.
    const st = p.get("station");
    if (st && view !== "map") setTimeout(() => {
        const card = document.querySelector(`.share-btn[data-key="${(window.CSS && CSS.escape) ? CSS.escape(st) : st}"]`);
        const host = card && card.closest(".station-card");
        if (host) { host.scrollIntoView({ block: "center" }); host.classList.add("flash"); setTimeout(() => host.classList.remove("flash"), 1600); }
    }, 300);
}

// ---- Fuelis Tools: consumption calculator, "worth the detour?", fuel
//      comparison, and a fill-up log — all client-side, saved in localStorage. --
function toolNum(id) { const v = parseFloat(String((document.getElementById(id) || {}).value || "").replace(",", ".")); return isFinite(v) ? v : null; }
function toolFmt(n, d) { d = d == null ? 2 : d; return (n == null || !isFinite(n)) ? "–" : n.toLocaleString("lt-LT", { minimumFractionDigits: d, maximumFractionDigits: d }); }
function todayISO() { return new Date().toISOString().slice(0, 10); }
// Short date as MM-DD (matches the big-endian LT yyyy-mm-dd order, not dd.mm).
// Accepts ISO "yyyy-mm-dd" or the legacy "dd.mm" the wholesale fetchers emit.
function shortMd(s) {
    s = String(s || "").trim();
    let m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m) return m[2] + "-" + m[3];
    m = s.match(/^(\d{1,2})[.\-\/](\d{1,2})$/);   // legacy dd.mm -> mm-dd
    if (m) return m[2].padStart(2, "0") + "-" + m[1].padStart(2, "0");
    return s;
}

function openTools() {
    renderTools();
    const m = document.getElementById("tools-modal");
    if (m) { m.classList.add("open"); document.body.classList.add("modal-open"); }
}
function closeTools() {
    const m = document.getElementById("tools-modal");
    if (m) { m.classList.remove("open"); document.body.classList.remove("modal-open"); }
}

// Discounts ("Nuolaidos") — its own tab/modal (shares the tools-modal styles).
function openDiscounts() {
    renderDiscounts();
    const m = document.getElementById("disc-modal");
    if (m) { m.classList.add("open"); document.body.classList.add("modal-open"); }
}
function closeDiscounts() {
    const m = document.getElementById("disc-modal");
    if (m) { m.classList.remove("open"); document.body.classList.remove("modal-open"); }
}
function renderDiscounts() {
    const title = document.getElementById("disc-title");
    if (title) title.textContent = "💳 " + t("loyalty_title");
    const body = document.getElementById("disc-body");
    if (!body) return;
    body.innerHTML = `
      <section class="tool-card">
        <div class="tool-note">${esc(t("loyalty_note"))}</div>
        <label class="loyalty-switch">
          <input type="checkbox" id="loyalty-enabled" ${LOYALTY.enabled ? "checked" : ""} onchange="toggleLoyalty(this.checked)">
          <span>${esc(t("loyalty_enable"))}</span>
        </label>
        <div class="loyalty-config${LOYALTY.enabled ? "" : " off"}">${loyaltyConfigHtml()}</div>
        <div class="tool-note loyalty-disclaimer">${esc(t("loyalty_disclaimer"))}</div>
      </section>`;
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
        <label>${esc(t("cc_litres"))}<input id="cc-litres" type="text" inputmode="decimal" step="0.01" min="0" placeholder="45"></label>
        <label>${esc(t("cc_km"))}<input id="cc-km" type="text" inputmode="decimal" step="0.1" min="0" placeholder="600"></label>
        <label>${esc(t("cc_price"))}<input id="cc-price" type="text" inputmode="decimal" step="0.001" min="0" placeholder="${priceHint}"></label>
        <button type="button" class="tool-btn" onclick="calcConsumption()">${esc(t("tool_calc"))}</button>
        <div id="cc-out" class="tool-out"></div>
      </section>

      <section class="tool-card">
        <h3>${esc(t("wd_title"))}</h3>
        <label>${esc(t("wd_cons"))}<input id="wd-cons" type="text" inputmode="decimal" step="0.1" min="0" placeholder="7.0"></label>
        <label>${esc(t("wd_here"))}<input id="wd-here" type="text" inputmode="decimal" step="0.001" min="0" placeholder="${priceHint}"></label>
        <label>${esc(t("wd_there"))}<input id="wd-there" type="text" inputmode="decimal" step="0.001" min="0"></label>
        <label>${esc(t("wd_dist"))}<input id="wd-dist" type="text" inputmode="decimal" step="0.1" min="0" placeholder="5"></label>
        <label>${esc(t("wd_litres"))}<input id="wd-litres" type="text" inputmode="decimal" step="0.1" min="0" placeholder="45"></label>
        <button type="button" class="tool-btn" onclick="calcDetour()">${esc(t("tool_calc"))}</button>
        <div id="wd-out" class="tool-out"></div>
      </section>

      <section class="tool-card">
        <h3>${esc(t("cmp_title"))}</h3>
        <div class="tool-note">${esc(t("cmp_note"))}</div>
        <label>${esc(t("fuel_petrol95"))} (L/100&nbsp;km)<input id="cmp-petrol95" type="text" inputmode="decimal" step="0.1" min="0" placeholder="7.5"></label>
        <label>${esc(t("fuel_diesel"))} (L/100&nbsp;km)<input id="cmp-diesel" type="text" inputmode="decimal" step="0.1" min="0" placeholder="5.5"></label>
        <label>${esc(t("ws_lpg"))} (L/100&nbsp;km)<input id="cmp-lpg" type="text" inputmode="decimal" step="0.1" min="0" placeholder="9.5"></label>
        <button type="button" class="tool-btn" onclick="calcCompare()">${esc(t("tool_calc"))}</button>
        <div id="cmp-out" class="tool-out"></div>
      </section>

      <section class="tool-card">
        <h3>${esc(t("log_title"))}</h3>
        <div class="log-add-row">
          <input id="lg-date" type="date" value="${todayISO()}">
          <input id="lg-litres" type="text" inputmode="decimal" step="0.01" min="0" placeholder="${esc(t("cc_litres"))}">
          <input id="lg-km" type="text" inputmode="decimal" step="0.1" min="0" placeholder="km">
          <input id="lg-price" type="text" inputmode="decimal" step="0.001" min="0" placeholder="€/L">
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
        ? `<select class="loyalty-add" ${LOYALTY.enabled ? "" : "disabled"} onchange="addLoyaltyNet(this.value); this.value='';">
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
    const ref = loyaltyRefPrice(legal);
    if (ref == null) return "";
    // Promo-aware: on a Wednesday promo day the APPLIED discount can differ from
    // the field value (e.g. Neste −7 instead of the configured 3.5), so preview
    // through loyaltyCents with a probe station at the reference price.
    const rf = (fuelType === "ev") ? "petrol95" : fuelType;
    const probe = { network: legal };
    probe[rf] = ref;
    let c;
    if (LOYALTY.enabled) {
        c = loyaltyCents(probe, rf);
    } else {
        const sc = LOYALTY.cents[legal];
        c = (typeof sc === "number" && isFinite(sc) && sc > 0) ? sc : 0;
    }
    if (!(c > 0)) return "";
    return `€${ref.toFixed(3)} → <b>€${loyaltyPrice(ref, c).toFixed(3)}</b>/L`;
}

function loyaltyRowHtml(label, legal) {
    const c = LOYALTY.cents[legal];
    let val = (typeof c === "number" && isFinite(c) && c > 0) ? loyaltyFmt(c) : "";
    // On Neste's promo day the flat announced discount IS the applied value —
    // show it in the field (locked), so the table matches the prices; the
    // user's everyday value returns tomorrow untouched.
    let promoLock = false;
    if (LOYALTY.enabled && legal === "UAB Neste Lietuva" && NESTE_PROMO
        && localTodayISO() === NESTE_PROMO.valid_date) {
        val = loyaltyFmt(NESTE_PROMO.cents);
        promoLock = true;
    }
    const ph = LOYALTY_TYPICAL[legal] || "0";   // typical value as a hint only
    const noteKey = LOYALTY_NOTES[legal];
    let note = noteKey ? `<div class="loyalty-row-note">↳ ${esc(t(noteKey))}</div>` : "";
    // Viada Wednesday promo: show the officially-announced absolute prices and
    // whether they apply today or on the upcoming date.
    // Wednesday-only promo announcements (all three networks run their specials
    // on trečiadienis; the notes would only confuse on other days).
    const isWed = new Date().getDay() === 3;
    if (isWed && legal === "UAB Viada LT" && VIADA_PROMO && VIADA_PROMO.prices
        && localTodayISO() === VIADA_PROMO.valid_date) {
        const P = VIADA_PROMO.prices;
        const list = [["petrol95", t("fuel_petrol95")], ["diesel", t("fuel_diesel")], ["lpg", t("ws_lpg")]]
            .filter(([k]) => P[k] != null).map(([k, l]) => `${l} €${P[k].toFixed(3)}`).join(" · ");
        note += `<div class="loyalty-row-note wed on">🔥 ${esc(t("loyalty_wed_today"))} ${esc(list)}</div>`;
    }
    if (isWed && legal === "UAB Neste Lietuva" && NESTE_PROMO
        && localTodayISO() === NESTE_PROMO.valid_date) {
        note += `<div class="loyalty-row-note wed on">🔥 ${esc(t("loyalty_neste_today", { cents: loyaltyFmt(NESTE_PROMO.cents) }))}</div>`;
    }
    // Circle K's one-time app discount is also a Wednesday offer, but personal &
    // login-gated — announce it; the user enters their own value.
    if (isWed && legal === "UAB Circle K Lietuva") {
        note += `<div class="loyalty-row-note wed on">🔥 ${esc(t("loyalty_ck_premium"))}</div>`;
    }
    return `<div class="loyalty-item">
      <div class="loyalty-row">
        <span class="loyalty-net">${esc(label)}</span>
        <input type="text" inputmode="decimal" step="any" min="0" max="${LOYALTY_MAX}" placeholder="${escAttr(ph)}"
               value="${escAttr(val)}" data-net="${escAttr(legal)}" oninput="setLoyalty(this)"
               ${promoLock ? 'data-promolock="1"' : ""} ${(LOYALTY.enabled && !promoLock) ? "" : "disabled"}>
        <span class="loyalty-unit">¢/L</span>
      </div>
      <div class="loyalty-preview">${loyaltyPreviewHtml(legal)}</div>${note}
    </div>`;
}

function toggleLoyalty(on) {
    LOYALTY.enabled = !!on;
    // First enable with nothing configured yet: seed the known typical everyday
    // discounts so turning it on has an IMMEDIATE visible effect (otherwise the
    // blank fields mean "on" does nothing). The user can still edit/clear any row.
    let seeded = false;
    // Seed only on GENUINE first-time setup (tracked by LOYALTY.seeded) — a user
    // who deliberately cleared every value must not get them re-seeded on toggle.
    if (LOYALTY.enabled && !LOYALTY.seeded) {
        if (Object.keys(LOYALTY.cents).length === 0) {
            for (const legal in LOYALTY_TYPICAL) {
                const v = parseFloat(LOYALTY_TYPICAL[legal]);
                if (isFinite(v) && v > 0) LOYALTY.cents[legal] = v;
            }
            seeded = true;
        }
        LOYALTY.seeded = true;
    }
    lsSet("kk_loyalty", LOYALTY);
    if (seeded) {
        renderDiscounts();   // repopulate the ¢/L fields with the seeded values
    } else {
        // Mutate the config in place — a full rebuild would reset the scroll/state.
        const cfg = document.querySelector(".loyalty-config");
        if (cfg) {
            cfg.classList.toggle("off", !LOYALTY.enabled);
            cfg.querySelectorAll(".loyalty-row input, .loyalty-add").forEach(el => {
                el.disabled = !LOYALTY.enabled || el.dataset.promolock === "1";   // keep promo-day fields locked
            });
        }
    }
    updateFeatureButtons();   // highlight the 💳 action button when active
    render();        // add/remove badges on the list & map
}

// Live-update as the user types a ¢/L value. A blank/0/invalid value removes the
// discount (so no badge). render() refreshes the badges but never touches the
// discounts modal DOM, so the input the user is typing in is preserved.
function setLoyalty(input) {
    const net = input.dataset.net;
    if (!net) return;
    let v = parseFloat(String(input.value || "").replace(",", "."));
    v = isFinite(v) ? Number(v.toFixed(2)) : NaN;   // round-and-persist so stored == displayed (loyaltyFmt is 2dp)
    if (isFinite(v) && v > 0) LOYALTY.cents[net] = Math.min(v, LOYALTY_MAX);
    else delete LOYALTY.cents[net];
    lsSet("kk_loyalty", LOYALTY);
    const item = input.closest(".loyalty-item");           // live-update this row's preview
    const prev = item && item.querySelector(".loyalty-preview");
    if (prev) prev.innerHTML = loyaltyPreviewHtml(net);
    render();
}

function addLoyaltyNet(net) {
    if (!net || !LOYALTY.enabled || LOYALTY.cents[net] != null) return;
    LOYALTY.cents[net] = 0;   // adds an (empty) row; 0 yields no badge until a value is typed
    lsSet("kk_loyalty", LOYALTY);
    renderDiscounts();
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
    if (!L || !km || L <= 0 || km <= 0) return;   // reject negatives — they corrupt the averages
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

// Line chart of fuel consumption (L/100 km) per fill-up over time — the log's
// headline metric, so the user can see their economy trending up or down.
function logChart(entries) {
    const pts = entries.filter(e => e.km > 0 && e.litres > 0)
        .slice().sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))
        .map(e => ({ date: e.date, cons: e.litres / e.km * 100 }));
    if (pts.length < 2) return "";
    const W = 320, H = 120, padL = 30, padR = 8, padT = 10, padB = 20;
    const vals = pts.map(p => p.cons);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi - lo < 0.5) { const m = (hi + lo) / 2; lo = m - 0.5; hi = m + 0.5; }
    const span = hi - lo;
    const xi = i => padL + (pts.length === 1 ? 0 : (i / (pts.length - 1)) * (W - padL - padR));
    const yv = v => padT + (1 - (v - lo) / span) * (H - padT - padB);
    let grid = "";
    for (let g = 0; g <= 2; g++) {
        const v = lo + span * g / 2, y = yv(v);
        grid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="#e2e7ee" stroke-width="1"/>`;
        grid += `<text x="${padL - 4}" y="${(y + 3).toFixed(1)}" text-anchor="end" font-size="9" fill="#9aa0a8">${v.toFixed(1)}</text>`;
    }
    const line = `<polyline points="${pts.map((p, i) => `${xi(i).toFixed(1)},${yv(p.cons).toFixed(1)}`).join(" ")}" fill="none" stroke="#0057D8" stroke-width="2"/>`;
    const dots = pts.map((p, i) => `<circle cx="${xi(i).toFixed(1)}" cy="${yv(p.cons).toFixed(1)}" r="2.5" fill="#0057D8"/>`).join("");
    const idxs = pts.length <= 2 ? [0, pts.length - 1] : [0, Math.floor((pts.length - 1) / 2), pts.length - 1];
    const xlab = idxs.map(i => `<text x="${xi(i).toFixed(1)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === pts.length - 1 ? "end" : "middle"}" font-size="9" fill="#9aa0a8">${shortMd(pts[i].date)}</text>`).join("");
    return `<div class="log-chart-wrap"><div class="log-chart-title">${esc(t("log_chart_title"))}</div><svg viewBox="0 0 ${W} ${H}">${grid}${line}${dots}${xlab}</svg></div>`;
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
         ${logChart(FUELLOG)}
         ${rows.slice().reverse().map(r => r.html).join("")}
         <div class="log-io">
           <button type="button" class="tool-btn ghost" onclick="exportLog()">⬇️ ${esc(t("tool_export"))}</button>
           <label class="tool-btn ghost csv-import">⬆️ ${esc(t("tool_import"))}<input type="file" accept=".csv,text/csv" onchange="importLog(this)" hidden></label>
         </div>
         <div class="tool-hint">${esc(t("tool_import_hint"))}</div>`;
}
// Restore/merge a fuel log from a previously exported CSV (round-trip back into
// the same viewer). Appends rows, deduped by date+litres+km; the log list itself
// is the "viewer".
function importLog(input) {
    const file = input.files && input.files[0];
    input.value = "";
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
        try {
            const lines = String(reader.result || "").split(/\r?\n/);
            const seen = new Set(FUELLOG.map(e => e.date + "|" + e.litres + "|" + e.km));
            let added = 0;
            for (const line of lines) {
                const c = line.split(",");
                const date = (c[0] || "").trim();
                if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) continue;         // skips header + junk
                const litres = parseFloat(String(c[1]).replace(",", "."));
                const km = parseFloat(String(c[2]).replace(",", "."));
                const priceRaw = (c[3] != null ? String(c[3]).trim() : "");
                const price = priceRaw ? parseFloat(priceRaw.replace(",", ".")) : null;
                if (!(litres > 0) || !(km > 0)) continue;
                const key = date + "|" + litres + "|" + km;
                if (seen.has(key)) continue;
                seen.add(key);
                FUELLOG.push({ date, litres, km, price: (price >= 0 && isFinite(price)) ? price : null });
                added++;
            }
            FUELLOG.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
            lsSet("kk_fuellog", FUELLOG);
            renderLog();
            toast(t("tool_import_done", { n: added }));
        } catch (e) { toast(t("tool_import_fail")); }
    };
    reader.readAsText(file);
}
function addLogManual() {
    const L = toolNum("lg-litres"), km = toolNum("lg-km"), price = toolNum("lg-price");
    const date = (document.getElementById("lg-date") || {}).value || todayISO();
    if (!L || !km || L <= 0 || km <= 0) return;   // reject negatives — they corrupt the averages
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

// --- full price-history chart (tap the trend line to open) -----------------
const CHART_COLORS = { petrol95: "#007AFF", diesel: "#1c9e57", lpg: "#e0850f" };
let chartFuel = "petrol95", chartRange = 90;

function openChart(fuel) {
    chartFuel = fuel || (fuelType === "ev" ? "petrol95" : fuelType);
    renderChartModal();
    const m = document.getElementById("chart-modal");
    if (m) m.classList.add("open");
    document.body.classList.add("modal-open");
}
function closeChart() {
    const m = document.getElementById("chart-modal");
    if (m) m.classList.remove("open");
    document.body.classList.remove("modal-open");
}
function setChartFuel(f) { chartFuel = f; renderChartModal(); }
function setChartRange(r) { chartRange = r; renderChartModal(); }

function renderChartModal() {
    const ttl = document.getElementById("chart-title");
    if (ttl) ttl.textContent = "📈 " + t("chart_title");
    const body = document.getElementById("chart-body");
    if (!body) return;
    const all = (HISTORY && HISTORY.history) || [];
    const series = all.filter(h => h[chartFuel] && h[chartFuel].avg != null).slice(-chartRange);
    const fuelBtns = [["petrol95", "⛽"], ["diesel", "🚛"], ["lpg", "🔥"]].map(([k, ic]) =>
        `<button class="chart-tog ${k === chartFuel ? "sel" : ""}" onclick="setChartFuel('${k}')">${ic} ${esc(t("fuel_" + k))}</button>`).join("");
    const rangeBtns = [[30, "30 d."], [90, "90 d."], [9999, t("chart_all")]].map(([r, lbl]) =>
        `<button class="chart-tog sm ${r === chartRange ? "sel" : ""}" onclick="setChartRange(${r})">${esc(lbl)}</button>`).join("");
    let chart, stat = "";
    if (series.length < 2) {
        chart = `<div class="chart-empty">${esc(t("chart_need_data"))}</div>`;
    } else {
        chart = priceChartSvg(series, chartFuel);
        const first = series[0][chartFuel].avg, last = series[series.length - 1][chartFuel].avg;
        const diff = last - first, pct = first ? diff / first * 100 : 0;
        const cls = diff > 0 ? "up" : diff < 0 ? "down" : "";
        const arrow = diff > 0 ? "▲" : diff < 0 ? "▼" : "■";
        stat = `<div class="chart-stat">${t("chart_latest")}: <b>€${last.toFixed(3)}</b> ·
            <span class="${cls}">${arrow} ${diff >= 0 ? "+" : ""}${diff.toFixed(3)} (${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)</span>
            <span class="chart-over">${esc(t("chart_over", { n: series.length }))}</span></div>`;
    }
    body.innerHTML = `<section class="tool-card">
        <div class="chart-tog-row">${fuelBtns}</div>
        <div class="chart-tog-row">${rangeBtns}</div>
        ${stat}
        <div class="chart-wrap">${chart}</div>
        <div class="chart-legend"><span class="lg-band" style="background:${CHART_COLORS[chartFuel]}"></span>${esc(t("chart_minmax"))}
            &nbsp;<span class="lg-line" style="background:${CHART_COLORS[chartFuel]}"></span>${esc(t("chart_avg"))}</div>
        <div class="tool-note">${esc(t("chart_note"))}</div>
    </section>`;
}

// Dependency-free SVG: shaded min–max band + bold avg line + axes.
function priceChartSvg(series, fuel) {
    const color = CHART_COLORS[fuel] || "#007AFF";
    const n = series.length, W = 320, H = 190, padL = 42, padR = 10, padT = 12, padB = 26;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    let lo = Infinity, hi = -Infinity;
    for (const h of series) { const d = h[fuel]; if (d.min < lo) lo = d.min; if (d.max > hi) hi = d.max; }
    const pad = (hi - lo) * 0.08 || 0.05; lo -= pad; hi += pad;
    const xi = i => padL + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
    const yv = v => padT + (1 - (v - lo) / (hi - lo)) * plotH;
    const top = series.map((h, i) => `${xi(i).toFixed(1)},${yv(h[fuel].max).toFixed(1)}`);
    const bot = series.map((h, i) => `${xi(i).toFixed(1)},${yv(h[fuel].min).toFixed(1)}`).reverse();
    const band = `<polygon points="${top.concat(bot).join(" ")}" fill="${color}" fill-opacity="0.13"/>`;
    const avg = `<polyline points="${series.map((h, i) => `${xi(i).toFixed(1)},${yv(h[fuel].avg).toFixed(1)}`).join(" ")}" fill="none" stroke="${color}" stroke-width="2"/>`;
    let grid = "";
    for (let g = 0; g <= 3; g++) {
        const v = lo + (hi - lo) * g / 3, y = yv(v);
        grid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="#eceff3" stroke-width="1"/>`;
        grid += `<text x="${padL - 5}" y="${(y + 3).toFixed(1)}" text-anchor="end" font-size="9" fill="#9aa0a8">${v.toFixed(2)}</text>`;
    }
    const fmt = d => shortMd(d);   // mm-dd
    const idxs = n <= 2 ? [0, n - 1] : [0, Math.floor((n - 1) / 2), n - 1];
    let xlab = "";
    for (const i of idxs) xlab += `<text x="${xi(i).toFixed(1)}" y="${H - 8}" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}" font-size="9" fill="#9aa0a8">${fmt(series[i].date)}</text>`;
    const dot = `<circle cx="${xi(n - 1).toFixed(1)}" cy="${yv(series[n - 1][fuel].avg).toFixed(1)}" r="3" fill="${color}"/>`;
    return `<svg viewBox="0 0 ${W} ${H}" class="price-chart" preserveAspectRatio="xMidYMid meet">${grid}${band}${avg}${dot}${xlab}</svg>`;
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
    loadEvReports();   // fire-and-forget; re-render when user complaints arrive
}

// User "neveikia/veikia" reports about chargers (48h TTL server-side).
let EV_REPORTS = {};
async function loadEvReports() {
    if (!REPORT_API) return;
    try {
        const res = await fetch(REPORT_API + "/ev-reports", { cache: "no-store" });
        EV_REPORTS = res.ok ? await res.json() : {};
        if (fuelType === "ev") render();
    } catch (e) { EV_REPORTS = {}; }
}

// "prieš 5 min. / prieš 3 val. / prieš 2 d." from an age in minutes.
function agoMin(min) {
    if (min < 60) return t("ago_min", { n: Math.max(1, Math.round(min)) });
    if (min < 1440) return t("ago_h", { n: Math.round(min / 60) });
    return t("ago_d", { n: Math.round(min / 1440) });
}
function agoTs(ts) { return agoMin((Date.now() - ts) / 60000); }

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
    // Operator data can go stale (13% of sites are >24h old) — say so instead of
    // presenting a week-old status as live truth.
    const stale = (st.m != null && st.m > 1440)
        ? ` <span class="ev-stale" title="${escAttr(t("ev_stale_hint"))}">⚠️ ${esc(t("ev_updated_ago", { ago: agoMin(st.m) }))}</span>` : "";
    return `<span class="ev-status ev-${st.s}">${m[0]} ${m[1]}</span>${stale}`;
}

// Latest user complaint for a charger ("broken" newer than any "ok" counter-report).
// Latest user availability report, shown only while it's still meaningful:
// "neveikia" persists (server TTL caps it), "užimta" is transient (3 h),
// "veikia" confirms for a day.
function evUserAvail(c) {
    const r = EV_REPORTS[chargerKey(c)];
    if (!r || !r.s || !r.ts) return null;
    const ageMin = (Date.now() - r.ts) / 60000;
    const win = { broken: 4320, busy: 180, ok: 1440 }[r.s];   // broken 72h, busy 3h, ok 24h
    return (win != null && ageMin <= win) ? r : null;
}
function evUserNote(c) {
    const r = EV_REPORTS[chargerKey(c)] || {};
    const av = evUserAvail(c);
    let note = "";
    if (av) {
        const m = {
            broken: ["⚠️", "ev_user_broken"],
            busy:   ["🔴", "ev_user_busy"],
            ok:     ["🟢", "ev_user_ok"],
        }[av.s];
        note = `<div class="ev-user-note ${av.s}">${m[0]} ${esc(t(m[1], { ago: agoTs(av.ts) }))}</div>`;
    }
    // User-reported €/kWh price (chargers' prices are often missing or stale).
    if (r.p != null && r.pts) {
        note += `<div class="report-line">${t("ev_price_line", { price: r.p.toFixed(2), ago: agoTs(r.pts) })}</div>`;
    }
    return note;
}
// Announce buttons in their own row BELOW navigation, matching the fuel cards'
// report button. "Prieinamumas" expands in place into the three-state chooser.
function evReportRow(c) {
    if (!REPORT_API) return "";
    const key = escAttr(chargerKey(c));
    return `<div class="report-row ev-report-row">
      <button class="evavail-btn" data-key="${key}">🚦 ${esc(t("ev_avail_btn"))}</button>
      <button class="evprice-btn" data-key="${key}">🗣️ ${esc(t("report_btn_short"))}</button>
    </div>`;
}
function evAvailChooser(rowEl, key) {
    const k = escAttr(key);
    rowEl.innerHTML = `
      <button class="evst-btn ok" data-key="${k}" data-s="ok">🟢 ${esc(t("ev_st_ok"))}</button>
      <button class="evst-btn busy" data-key="${k}" data-s="busy">🔴 ${esc(t("ev_st_busy"))}</button>
      <button class="evst-btn broken" data-key="${k}" data-s="broken">⚫ ${esc(t("ev_st_broken"))}</button>`;
}

async function evReportPrice(key) {
    if (!REPORT_API) return;
    const input = prompt(t("ev_price_prompt"));
    if (input == null) return;
    const price = parseFloat(String(input).replace(",", "."));
    if (!(price >= 0.05 && price <= 2)) { alert(t("ev_price_invalid")); return; }
    try {
        const res = await fetch(REPORT_API + "/ev-report", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ charger: key, price }),
        });
        if (!res.ok) throw new Error("HTTP " + res.status);
        const cur = EV_REPORTS[key] || {};
        cur.p = Math.round(price * 1000) / 1000; cur.pts = Date.now();
        EV_REPORTS[key] = cur;
        render();
    } catch (e) { alert(t("report_failed")); }
}
async function evReport(key, status) {
    if (!REPORT_API) return;
    // No confirm: picking one of the three explicit states IS the deliberate choice.
    try {
        const res = await fetch(REPORT_API + "/ev-report", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ charger: key, status }),
        });
        if (!res.ok) throw new Error("HTTP " + res.status);
        const cur = EV_REPORTS[key] || {};
        cur.s = status; cur.ts = Date.now();
        EV_REPORTS[key] = cur;
        render();
    } catch (e) { alert(t("report_failed")); }
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
    box.innerHTML = elLine + `<div class="wholesale-ref">${t("ev_sources")} · ${t("ev_price_count", { n: priced })}</div>`
        + (REPORT_API ? `<div class="wholesale-ref ev-disclaimer">${esc(t("ev_disclaimer"))}</div>` : "");
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
                    <div class="ev-price-col">${c.price != null ? `<span class="station-price">€${c.price.toFixed(2)}</span><span class="price-unit">/kWh</span>` : ""}${evUserNote(c)}</div>
                </div>
                ${addr ? `<div class="station-address">${addr}</div>` : ""}
                ${info ? `<div class="station-muni">${info}</div>` : ""}
                <div class="nav-row">${evNav(c)}</div>
                ${evReportRow(c)}
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
            ${evUserNote(c)}
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

// Report a pump price via a small modal: the price field is PREFILLED with the
// station's currently-shown price (a realistic starting point), plus a "with a
// discount?" checkbox so a loyalty price isn't mistaken for the official one.
let _reportCtx = null;
function reportPrice(key, fuel) {
    if (!REPORT_API) return;
    const s = (DATA.stations || []).find(x => stationKey(x) === key);
    _reportCtx = { key, fuel };
    const cur = (s && s[fuel] != null) ? s[fuel].toFixed(3) : "";
    const ttl = document.getElementById("report-title");
    if (ttl) ttl.textContent = "🗣️ " + t("report_btn");
    const body = document.getElementById("report-body");
    if (body) body.innerHTML = `
      <section class="tool-card">
        <div class="tool-note">${esc(s ? (s.network || t("station_default")) : "")} · ${esc(t("fuel_" + fuel))}</div>
        <label class="tool-label">${esc(t("report_price_label"))}</label>
        <input id="rp-price" type="text" inputmode="decimal" step="0.001" value="${escAttr(cur)}" placeholder="${escAttr(cur || "1.699")}">
        <label class="loyalty-switch report-disc">
          <input type="checkbox" id="rp-discount"><span>${esc(t("report_discount_q"))}</span>
        </label>
        <div id="rp-status"></div>
        <button type="button" class="tool-btn" onclick="submitReport()">${esc(t("report_send"))}</button>
      </section>`;
    const m = document.getElementById("report-modal");
    if (m) m.classList.add("open");
    document.body.classList.add("modal-open");
    setTimeout(() => { const i = document.getElementById("rp-price"); if (i) { i.focus(); i.select(); } }, 60);
}
function closeReport() {
    const m = document.getElementById("report-modal");
    if (m) m.classList.remove("open");
    document.body.classList.remove("modal-open");
}
async function submitReport() {
    if (!REPORT_API || !_reportCtx) return;
    const status = document.getElementById("rp-status");
    const price = parseFloat(String((document.getElementById("rp-price") || {}).value || "").replace(",", "."));
    const discount = !!(document.getElementById("rp-discount") || {}).checked;
    if (!(price >= 0.3 && price <= 3.5)) {
        if (status) status.innerHTML = `<div class="tool-err">${esc(t("report_invalid"))}</div>`;
        return;
    }
    const { key, fuel } = _reportCtx;
    try {
        const res = await fetch(REPORT_API + "/report", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ station: key, fuel, price, discount }),
        });
        if (!res.ok) throw new Error("HTTP " + res.status);
        (REPORTS[key] = REPORTS[key] || {})[fuel] = { price: Math.round(price * 1000) / 1000, ts: Date.now(), d: discount };
        closeReport();
        render();
    } catch (e) { if (status) status.innerHTML = `<div class="tool-err">${esc(t("report_failed"))}</div>`; }
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

// Auto-locate ONLY when the browser reports the permission is already granted —
// then getCurrentPosition is silent (no iOS/Android prompt). We never ambush
// with a permission dialog on load; the first-ever ask stays behind the button
// tap. Once granted persistently (e.g. the installed home-screen app), every
// open locates automatically with zero prompts and zero taps.
let _autoLocated = false;
async function maybeAutoLocate() {
    if (_autoLocated || userPos || fuelType === "ev") return;
    // A shared link that pins a municipality wins over silent auto-locate.
    try { if (new URLSearchParams(location.search).get("muni")) { _autoLocated = true; return; } } catch (e) {}
    _autoLocated = true;
    if (!navigator.geolocation || !navigator.permissions || !navigator.permissions.query) return;
    try {
        const st = await navigator.permissions.query({ name: "geolocation" });
        if (st.state === "granted") locate();
    } catch (e) {}
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
    // Mirror the top fuel selector: the chip of the currently SELECTED fuel gets
    // the same active background, so the card echoes what you're looking at.
    const chips = FUEL_ICONS.map(([k, ic]) =>
        `<span class="fuel-chip ${has(k) ? (k === fuelType ? "sel" : "") : "off"}" title="${escAttr(t("fuel_" + k))}">${ic}</span>`).join("");
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
            const ap = effPrice(a), bp = effPrice(b);   // discounted when loyalty on; price-less (null) sort to the bottom
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
        if (parts.length) wsLine = `<div class="wholesale-ref">${t("ws_orlen", { date: esc(shortMd(ORLEN_WS.stated_date)) })} ${parts.join(" · ")}</div>`;
    }
    // Circle K business fixed price (VAT-incl) — the one genuine SAME-DAY (today)
    // reference, shown right below Orlen. Order: 95, diesel, LPG, 98, AdBlue.
    let ckbLine = "";
    if (CK_BIZ && CK_BIZ.prices) {
        const CKB_LABELS = { petrol95: t("fuel_petrol95"), diesel: t("fuel_diesel"), lpg: t("ws_lpg"), petrol98: t("fuel_98"), adblue: t("fuel_adblue") };
        const parts = ["petrol95", "diesel", "lpg", "petrol98", "adblue"]
            .filter(k => CK_BIZ.prices[k] != null)
            .map(k => `${CKB_LABELS[k]} <b>€${CK_BIZ.prices[k].toFixed(3)}</b>`);
        if (parts.length) ckbLine = `<div class="wholesale-ref">${t("ws_circlek_biz", { date: esc(shortMd(CK_BIZ.stated_date)) })} ${parts.join(" · ")}</div>`;
    }
    // Price-history trend (grows as the daily pipeline accumulates snapshots).
    let trendLine = "";
    const H = (HISTORY && HISTORY.history) || [];
    if (H.length >= 2) {
        const win = H.slice(-14);
        const sp = f => sparkline(win.map(h => h[f] && h[f].avg), "#007AFF");
        trendLine = `<button type="button" class="trend-open" onclick="openChart()" title="${esc(t("chart_title"))}">${t("trend_label")}: ⛽ ${sp("petrol95")} 🚛 ${sp("diesel")} 🔥 ${sp("lpg")} <span class="trend-cta">📈</span></button>`;
    }
    box.innerHTML = `
        <div class="summary-title">${t("national_title")}</div>
        <table class="nat-table">
            <thead><tr><th></th><th>${t("stat_cheapest")}</th><th class="avg-col">${t("stat_avg")}</th><th>${t("stat_dearest")}</th></tr></thead>
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

// Chevron marks for the cheapest (green, down) / dearest (red, up) list badges —
// the app-logo arrows. stroke=currentColor so each inherits its badge colour.
const ARROW_DOWN = '<svg class="badge-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 9l7 7 7-7"/></svg>';
const ARROW_UP = '<svg class="badge-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 15l7-7 7 7"/></svg>';

function renderList() {
    const list = document.getElementById("stations-list");
    if (!DATA.stations || DATA.stations.length === 0) {
        list.innerHTML = `<div class="msg">${t("empty_list")}</div>`;
        return;
    }
    const rows = getRows();
    if (rows.length === 0) { list.innerHTML = `<div class="msg">${showFavsOnly ? t("no_favourites") : t("no_filter")}</div>`; return; }

    const priced = rows.filter(r => r[fuelType] != null);
    const best = priced.length ? Math.min(...priced.map(r => effPrice(r))) : null;   // discounted when loyalty on
    const worst = priced.length ? Math.max(...priced.map(r => effPrice(r))) : null;  // most expensive
    const total = (DATA.stations || []).filter(s =>
        s[fuelType] != null || (s.no_price && (s.fuels || []).includes(fuelType))).length;
    const nLabel = rows.length < total ? `${rows.length} / ${total}` : `${total}`;  // your area / overall
    const shown = rows.slice(0, 600);           // keep the DOM snappy on phones (like the EV list)
    list.innerHTML =
        `<div class="count-line">${t("showing_stations", { n: nLabel })}</div>` +
        shown.map(s => {
            const isBest = s[fuelType] != null && effPrice(s) === best;
            const isWorst = s[fuelType] != null && worst != null && worst > best && effPrice(s) === worst;
            const dist = (userPos && s._dist != null)
                ? `<span class="dist-badge">📍 ${s.approx ? "~" : ""}${fmtDist(s._dist)}</span>` : "";
            const approxTag = s.approx ? ` <span class="approx-tag">${t("approx_warn")}</span>` : "";
            const fl = flagFor(s);
            const flagLine = fl ? `<div class="change-flag">${t("flag_change", { price: fl.live.toFixed(3) })}</div>` : "";
            const rep = reportFor(s);
            const repLine = rep ? `<div class="report-line">${t("report_line", { price: rep.price.toFixed(3), ago: agoTs(rep.ts) })}${rep.d ? ` <b class="rep-disc">${esc(t("report_with_discount"))}</b>` : ""}</div>` : "";
            const repBtn = REPORT_API ? `<button class="report-btn" data-key="${escAttr(stationKey(s))}">${t("report_btn")}</button>` : "";
            const shareBtn = `<button class="share-btn" data-key="${escAttr(stationKey(s))}">🔗 ${esc(t("share_btn"))}</button>`;
            const lc = s[fuelType] != null ? loyaltyCents(s) : 0;
            // Suppress the badge when the discount is too small to change the
            // 3-decimal price (identical "with card" price reads as a bug) or big
            // enough to zero out the price (an implausible near-zero effective price).
            const lVal = lc > 0 ? loyaltyPrice(s[fuelType], lc) : 0;
            const lDisc = lc > 0 ? lVal.toFixed(3) : null;
            const loyaltyLine = (lDisc && lVal > 0 && lDisc !== s[fuelType].toFixed(3))
                ? `<div class="loyalty-line" title="−${loyaltyFmt(lc)} ¢/L">💳 ${esc(t("loyalty_with_card"))} <span class="loyalty-price">€${lDisc}</span><span class="price-unit">/L</span></div>`
                : "";
            return `
            <div class="station-card">
                <button class="fav-btn" data-key="${esc(favKey(s))}">${isFav(favKey(s)) ? "★" : "☆"}</button>
                ${isBest ? `<div class="best-price-badge">${ARROW_DOWN} ${t("stat_cheapest")}</div>`
                  : isWorst ? `<div class="worst-price-badge">${ARROW_UP} ${t("stat_dearest")}</div>` : ''}${dist}
                <div class="station-header">
                    <div class="station-name">${esc(s.network || t("station_default"))}</div>
                    <div>${s[fuelType] != null
                        ? `<span class="station-price">€${s[fuelType].toFixed(3)}</span><span class="price-unit">/L</span>${loyaltyLine}`
                        : `<span class="no-price-badge">${t("no_price")}</span>`}</div>
                </div>
                ${repLine}
                <div class="station-address">${esc(s.address || "")}${s.locality ? ", " + esc(s.locality) : ""}</div>
                <div class="station-muni">📍 ${esc(s.municipality || "")}${approxTag}</div>
                ${fuelChips(s)}
                ${flagLine}
                <div class="nav-row">${navButtons(s)}</div>
                <div class="report-row">${shareBtn}${repBtn}</div>
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

    const prices = rows.map(r => effPrice(r)).filter(p => p != null);   // discounted when loyalty on
    const lo = Math.min(...prices), hi = Math.max(...prices);
    const bounds = [];

    rows.forEach(s => {
        const p = s[fuelType];
        const ep = effPrice(s);                // discounted when loyalty on, else official
        let cls = "price-pin", label;
        if (ep == null) {                      // price-less registry station
            cls += " noprice"; label = "?";
        } else {
            if (ep <= lo + (hi - lo) * 0.25) cls += " cheap";
            else if (ep >= lo + (hi - lo) * 0.75) cls += " dear";
            if (ep !== p) cls += " disc";      // this pin's price reflects a loyalty discount
            label = `€${ep.toFixed(2)}`;
        }
        if (s.approx) cls += " approx";
        const favStar = isFav(favKey(s)) ? `<span class="pin-fav">★</span>` : "";
        const icon = L.divIcon({
            className: "", html: `<div class="${cls}">${label}${favStar}</div>`,
            iconSize: null, iconAnchor: [22, 12]
        });
        const dist = (userPos && s._dist != null) ? `<br>📍 ${s.approx ? "~" : ""}${fmtDist(s._dist)}` : "";
        const approxNote = s.approx ? `<br><span style="color:#b3792f">⚠️ ${t("approx_warn")}</span>` : "";
        const priceLine = p != null
            ? `<div class="popup-price">${t("fuel_" + fuelType)}: €${p.toFixed(3)}/L</div>`
            : `<div class="no-price-badge">${t("no_price")}</div>`;
        const plc = p != null ? loyaltyCents(s) : 0;
        const pVal = plc > 0 ? loyaltyPrice(p, plc) : 0;
        const pDisc = plc > 0 ? pVal.toFixed(3) : null;
        const loyaltyPop = (pDisc && pVal > 0 && pDisc !== p.toFixed(3))
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
