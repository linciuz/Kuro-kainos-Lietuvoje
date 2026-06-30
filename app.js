// Kuro Kainos Lietuvoje - reads official LEA data from data/stations.json
// Data shape (produced by scripts/fetch_prices.py):
// { updated, source, source_url, summary:{petrol95:{min,avg,max},...}, stations:[{network,address,municipality,locality,petrol95,diesel,lpg}] }

const FUEL_LABELS = { petrol95: "95 benzinas", diesel: "Dyzelinas", lpg: "Dujos (SND)" };

let DATA = { updated: null, source: "", source_url: "", summary: {}, stations: [] };
let fuelType = "petrol95";
let sortDir = "asc";

async function load() {
    try {
        const res = await fetch("data/stations.json", { cache: "no-store" });
        if (!res.ok) throw new Error("HTTP " + res.status);
        DATA = await res.json();
    } catch (e) {
        // Fallback: official national figures (2026-06-16) so the app still shows real data.
        DATA = {
            updated: "2026-06-16",
            source: "Lietuvos energetikos agentūra (ena.lt)",
            source_url: "https://www.ena.lt/degalu-kainos-degalinese/",
            summary: {
                petrol95: { min: 1.629, avg: 1.832, max: 1.94 },
                diesel:   { min: 1.699, avg: 1.890, max: 1.999 },
                lpg:      { min: 0.70,  avg: 0.831, max: 0.969 }
            },
            stations: []
        };
    }
    initMunicipalities();
    updateChrome();
    render();
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
    const src = document.getElementById("source-line");
    src.innerHTML = `Šaltinis: <a href="${DATA.source_url}" target="_blank" rel="noopener">${DATA.source}</a>`;
    const upd = document.getElementById("updated-line");
    upd.textContent = DATA.updated ? `Duomenys atnaujinti: ${DATA.updated}` : "";
}

function selectFuel(f) {
    fuelType = f;
    document.querySelectorAll(".fuel-btn").forEach(b => b.classList.remove("active"));
    document.getElementById("btn-" + f).classList.add("active");
    render();
}

function setSort(dir) {
    sortDir = dir;
    document.getElementById("sort-asc").classList.toggle("active", dir === "asc");
    document.getElementById("sort-desc").classList.toggle("active", dir === "desc");
    render();
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

function render() {
    renderSummary();
    const list = document.getElementById("stations-list");
    const muni = document.getElementById("muni-select").value;
    const q = (document.getElementById("search").value || "").toLowerCase().trim();

    let rows = (DATA.stations || []).filter(s => s[fuelType] != null);
    if (muni) rows = rows.filter(s => (s.municipality || "") === muni);
    if (q) rows = rows.filter(s =>
        ((s.network || "") + " " + (s.address || "") + " " + (s.locality || "")).toLowerCase().includes(q));

    rows.sort((a, b) => sortDir === "asc" ? a[fuelType] - b[fuelType] : b[fuelType] - a[fuelType]);

    if (!DATA.stations || DATA.stations.length === 0) {
        list.innerHTML = `<div class="msg">Šalies vidurkiai rodomi viršuje.<br>
            Visų degalinių sąrašas atsiras, kai suveiks automatinis duomenų atnaujinimas
            (GitHub Action „Update fuel prices“).</div>`;
        return;
    }
    if (rows.length === 0) {
        list.innerHTML = `<div class="msg">Nieko nerasta pagal pasirinktus filtrus.</div>`;
        return;
    }

    const best = sortDir === "asc" ? rows[0][fuelType] : Math.min(...rows.map(r => r[fuelType]));
    list.innerHTML =
        `<div class="count-line">Rodoma degalinių: ${rows.length}</div>` +
        rows.map(s => {
            const isBest = s[fuelType] === best;
            const q = encodeURIComponent(`${s.network} ${s.address} ${s.municipality}`);
            return `
            <div class="station-card">
                ${isBest ? '<div class="best-price-badge">⭐ PIGIAUSIA</div>' : ''}
                <div class="station-header">
                    <div class="station-name">${s.network || "Degalinė"}</div>
                    <div><span class="station-price">€${s[fuelType].toFixed(3)}</span><span class="price-unit">/L</span></div>
                </div>
                <div class="station-address">${s.address || ""}${s.locality ? ", " + s.locality : ""}</div>
                <div class="station-footer">
                    <span class="station-muni">📍 ${s.municipality || ""}</span>
                    <button class="station-directions" onclick="window.open('https://www.google.com/maps/search/?api=1&query=${q}','_blank')">🗺️ Žemėlapyje</button>
                </div>
            </div>`;
        }).join("");
}

window.addEventListener("load", load);
