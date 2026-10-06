// Execute only pure functions from the actual app, without booting the app,
// fetching any URL, using browser storage, or inventing station/price data.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const app = fs.readFileSync(path.join(__dirname, "../app.js"), "utf8");
const names = ["stationKey", "stationMunicipality", "stationMunicipalityHtml", "dedupePricelessStations",
    "stationAddress", "stationAddressHtml", "navButtons", "shareStation", "stationCardHtml", "renderMap",
    "sharedStationTarget", "stationSearchText", "applyUrlState", "favKey", "isFav", "toggleFav",
    "getRows", "currentCheapest", "nearestStationMuni", "haversine", "esc", "renderList", "showMoreCards"];
const functions = names.map(name => {
    const match = app.match(new RegExp("^function " + name + "\\([^\\n]*}\\r?$", "m"))
        || app.match(new RegExp("^function " + name + "\\([^]*?^}", "m"));
    assert.ok(match, "Missing app function: " + name);
    return match[0];
}).join("\n");
let municipality = "";
let searchText = "";
const list = { innerHTML: "" };
const more = {
    textContent: "",
    insertAdjacentHTML(position, html) {
        assert.equal(position, "beforebegin");
        const button = list.innerHTML.indexOf("<button");
        assert.ok(button >= 0);
        list.innerHTML = list.innerHTML.slice(0, button) + html + list.innerHTML.slice(button);
    },
    remove() { list.innerHTML = list.innerHTML.replace(/<button\b[^]*?<\/button>/, ""); },
};
const context = vm.createContext({
    DATA: input.before, fuelType: "petrol95", showFavsOnly: false, userPos: null,
    STATION_ALIASES: new WeakMap(), URLSearchParams, FAVS: [],
    radiusKm: 0, sortDir: "asc", t: key => key,
    document: { getElementById: id => {
        if (id === "stations-list") return list;
        if (id === "show-more") return list.innerHTML.includes('id="show-more"') ? more : null;
        return { value: id === "muni-select" ? municipality : id === "search" ? searchText : "" };
    } },
});
context.effPrice = station => station[context.fuelType];
const chunk = app.match(/^const LIST_CHUNK = (\d+);/m);
assert.ok(chunk);
vm.runInContext(functions + "\nconst escAttr = esc;\n" + chunk[0] + "\nlet _listRest = [];\nlet _urlStateApplied = false;", context);
const actualCardHtml = context.stationCardHtml;
const key = station => context.stationKey(station);
const sourceKeys = new Set(input.source_keys);
const fuels = ["petrol95", "diesel", "lpg"];
function inspect(dataset) {
    context.DATA = structuredClone(dataset);
    context.dedupePricelessStations();
    municipality = "";
    const visible = new Set();
    const byFuel = {};
    for (const fuel of fuels) {
        context.fuelType = fuel;
        const rows = context.getRows();
        byFuel[fuel] = rows.length;
        rows.forEach(station => visible.add(key(station)));
    }
    return { kept: context.DATA.stations.length, visible, byFuel,
        aliases: new Map(context.STATION_ALIASES.get(context.DATA)) };
}
const before = inspect(input.before);
const after = inspect(input.after);
assert.equal([...sourceKeys].filter(k => !before.visible.has(k)).length, 8);
assert.equal([...sourceKeys].filter(k => !after.visible.has(k)).length, 0);
const targetBefore = input.before.stations.find(station => key(station) === input.target_key);
const targetAfter = context.DATA.stations.find(station => key(station) === input.target_key);
assert.ok(targetBefore && targetAfter);
assert.equal(key(targetBefore), key(targetAfter), "Favourite/report station identity changed");
assert.equal(targetAfter.municipality, "Panevėžio r. sav.");
assert.equal(context.stationMunicipality(targetAfter), "Panevėžio m. sav.");
const unattributed = { ...targetAfter };
delete unattributed.display_municipality_source;
assert.equal(context.stationMunicipality(unattributed), targetAfter.municipality);
municipality = "Panevėžio m. sav.";
const cityCounts = {};
for (const fuel of fuels) {
    context.fuelType = fuel;
    const rows = context.getRows();
    assert.ok(rows.some(station => key(station) === input.target_key), "EMSI missing from city: " + fuel);
    cityCounts[fuel] = rows.length;
}
assert.equal(context.nearestStationMuni({ lat: targetAfter.lat, lon: targetAfter.lon }), "Panevėžio m. sav.");
assert.equal(context.currentCheapest("Panevėžio m. sav.").diesel, Math.min(...context.DATA.stations
    .filter(station => context.stationMunicipality(station) === "Panevėžio m. sav." && station.diesel != null)
    .map(station => station.diesel)));
const areaHtml = context.stationMunicipalityHtml(targetAfter);
assert.ok(areaHtml.includes("LEA: Panevėžio r. sav."));
assert.ok(areaHtml.includes(targetAfter.display_municipality_source));
// Real national datasets exceed the old 600-card ceiling. A minimal card
// renderer records actual station identities while the app's chunking and
// show-more functions execute unchanged against the small DOM stub above.
context.stationCardHtml = station => `<article data-test-station="${encodeURIComponent(key(station))}"></article>`;
const renderedKeys = () => [...list.innerHTML.matchAll(/data-test-station="([^"]+)"/g)]
    .map(match => decodeURIComponent(match[1]));
municipality = "";
const allRendered = {};
for (const fuel of fuels) {
    context.fuelType = fuel;
    const expected = [...context.getRows()].map(key);
    assert.ok(expected.length > 600, "Fixture must exercise the former list ceiling: " + fuel);
    context.renderList();
    assert.equal(renderedKeys().length, Number(chunk[1]));
    let clicks = 0;
    while (context.document.getElementById("show-more")) {
        const previous = renderedKeys().length;
        context.showMoreCards();
        const added = renderedKeys().length - previous;
        assert.ok(added > 0 && added <= 2 * Number(chunk[1]));
        assert.ok(++clicks < 20, "Show-more did not drain the remaining cards");
    }
    assert.deepEqual(renderedKeys(), expected, "National list dropped matching stations: " + fuel);
    allRendered[fuel] = expected.length;
}
// The configured correction is from BP's captured official station38, while
// this actual LEA station189 retains its original house18 identity and prices.
const overrides = JSON.parse(fs.readFileSync(path.join(__dirname, "../data/coord_overrides.json"), "utf8"));
const addressOverride = input.address_override || overrides.overrides.find(o => o.display_address && o.display_address_source);
assert.ok(addressOverride, "An actual attributed address override is required");
const rawAddressStation = input.before.stations.find(s => key(s) === addressOverride.station_key);
assert.ok(rawAddressStation, "Address override must refer to a recorded real station");
const addressStation = { ...rawAddressStation,
    display_address: addressOverride.display_address,
    display_address_source: addressOverride.display_address_source };
assert.equal(key(addressStation), key(rawAddressStation));
for (const fuel of fuels) assert.equal(addressStation[fuel], rawAddressStation[fuel]);
assert.equal(context.stationAddress(addressStation), addressOverride.display_address);
const withoutAddressSource = { ...addressStation };
delete withoutAddressSource.display_address_source;
assert.equal(context.stationAddress(withoutAddressSource), rawAddressStation.address);
const withoutDisplayAddress = { ...addressStation };
delete withoutDisplayAddress.display_address;
assert.equal(context.stationAddress(withoutDisplayAddress), rawAddressStation.address);
assert.equal(context.stationAddressHtml(withoutAddressSource), context.esc(rawAddressStation.address));
assert.equal(context.stationAddressHtml(withoutDisplayAddress), context.esc(rawAddressStation.address));
const addressHtml = context.stationAddressHtml(addressStation);
assert.ok(addressHtml.includes("LEA: " + rawAddressStation.address));
assert.ok(addressHtml.includes(addressOverride.display_address_source));
assert.ok(addressHtml.includes(addressOverride.display_address));
context.DATA = { stations: [addressStation] };
context.fuelType = "petrol95";
municipality = "";
for (const query of [rawAddressStation.address, addressOverride.display_address, "Aukštikalnių", "Mūšos g. 18"]) {
    searchText = query.toLowerCase();
    assert.equal(context.getRows().length, 1, "Raw/corrected address search lost station: " + query);
}
searchText = "";
// Withhold GPS from the actual record to exercise address-based routing.
const nav = decodeURIComponent(context.navButtons({ ...addressStation, lat: null, lon: null }));
assert.ok(nav.includes(addressOverride.display_address));
assert.ok(!nav.includes(rawAddressStation.address));
let shared;
context.t = (name, values) => name === "share_station_text" ? values : name;
context.loyaltyLabel = network => network;
context.shareState = state => state;
context.doShare = (message, state) => { shared = { message, state }; };
context.shareStation(key(addressStation));
assert.equal(shared.message.addr, addressOverride.display_address);
assert.equal(shared.state.station, key(rawAddressStation));
// Execute the actual card and map-popup renderers against a small DOM/Leaflet
// stub. Numeric fuel values still come exclusively from the recorded station.
Object.assign(context, {
    REPORT_API: "", ARROW_DOWN: "", ARROW_UP: "", flagFor: () => null,
    reportFor: () => null, loyaltyCents: () => 0, fuelChips: () => "",
});
const card = actualCardHtml(addressStation, null, null);
assert.ok(card.includes(addressHtml));
let popup;
Object.assign(context, {
    map: { invalidateSize() {}, fitBounds() {} }, ensureMap() {}, addUserMarker() {},
    setTimeout(callback) { callback(); }, markersLayer: { clearLayers() {} },
    L: { divIcon: options => options, marker: () => ({
        bindPopup(html) { popup = html; return this; }, addTo() { return this; },
    }) },
});
context.renderMap();
assert.ok(popup.includes(addressHtml));
// Saved URLs use the exact original LEA key/municipality. Exercise actual
// corrected records and aliases already coalesced by the real dedupe rule.
const sharedCases = input.after.stations.filter(s => s.network === "UAB Saurida" &&
    ((s.address.includes("Martinavos g. 1") && s.municipality === "Kauno m. sav.") ||
     (s.address.includes("Jūrininkų pr. 29") && s.municipality === "Klaipėdos r. sav.") ||
     (s.address.includes("Lekavičiaus g. 71") && s.municipality === "Kauno r. sav.")));
assert.equal(sharedCases.length, 4, "Actual corrected station/alias cases must remain in fixture");
const sharedResults = [];
for (const original of sharedCases) {
    context.DATA = structuredClone(input.after);
    context.dedupePricelessStations();
    const target = context.sharedStationTarget(key(original));
    assert.ok(target, "Exact source key or coalesced alias failed to resolve");
    const select = { value: "", options: [...new Set(context.DATA.stations.map(s => context.stationMunicipality(s)))].map(value => ({ value })) };
    const search = { value: "" };
    let highlighted = false;
    context.stationCardHtml = actualCardHtml;
    context.document.getElementById = id => id === "muni-select" ? select : id === "search" ? search : id === "stations-list" ? list : {};
    context.document.querySelector = selector => {
        const encodedKey = context.esc(key(target.station));
        if (!selector.includes(key(target.station)) || !list.innerHTML.includes(`data-key="${encodedKey}"`)) return null;
        return { closest: () => ({ scrollIntoView() { highlighted = true; }, classList: { add() {}, remove() {} } }) };
    };
    const params = new URLSearchParams({ fuel: "diesel", view: "list", muni: original.municipality,
        q: original.address, station: key(original) });
    context.location = { search: "?" + params.toString() };
    context.window = {};
    context.selectFuel = fuel => { context.fuelType = fuel; };
    context.setView = view => { context.view = view; };
    context.view = "list";
    context.render = () => context.renderList();
    vm.runInContext("_urlStateApplied = false;", context);
    context.applyUrlState();
    assert.equal(select.value, context.stationMunicipality(target.station));
    assert.equal(search.value, original.address, "Saved search was changed");
    assert.equal(context.fuelType, "diesel");
    assert.equal(context.view, "list");
    assert.equal(context.location.search, "?" + params.toString(), "Original shared key/URL was changed");
    assert.ok(context.getRows().some(s => key(s) === key(target.station)), "Selected real station is filtered out");
    assert.ok(highlighted, "Resolved real station card was not highlighted");
    // A failed foreground reload reuses DATA, so a second dedupe must retain
    // already recorded alias identities rather than losing old saved links.
    context.dedupePricelessStations();
    assert.equal(key(context.sharedStationTarget(key(original)).station), key(target.station));
    sharedResults.push({ originalKey: key(original), selectedKey: key(target.station), municipality: select.value });
}
// Coordinate corrections now put nine formerly visible registry aliases at
// their verified priced premises. Keep each previously stored raw favourite
// reachable, with its own recorded fuel support and unchanged source prices.
const newlyCoalesced = [...after.aliases.keys()].filter(k => !before.aliases.has(k));
assert.equal(newlyCoalesced.length, 9, "All nine actual newly coalesced aliases must be covered");
const favouriteCases = input.after.stations.filter(s => newlyCoalesced.includes(key(s)));
assert.equal(favouriteCases.filter(s => s.network === "UAB Saurida").length, 8);
assert.equal(favouriteCases.filter(s => s.network === "UAB Alauša").length, 1);
municipality = "";
searchText = "";
context.document.getElementById = id => id === "stations-list" ? list
    : { value: id === "muni-select" ? municipality : id === "search" ? searchText : "" };
context.showFavsOnly = true;
context.lsSet = () => {};
context.updateFeatureButtons = () => {};
const favouriteResults = [];
for (const original of favouriteCases) {
    const savedKey = "st:" + key(original);
    context.FAVS = [savedKey];
    context.DATA = structuredClone(input.after);
    // Fuel stations stay starred even when the user's saved view is EV.
    context.fuelType = "ev";
    context.dedupePricelessStations();
    for (const fuel of original.fuels) {
        context.fuelType = fuel;
        const rows = context.getRows();
        assert.equal(rows.length, 1, "Saved alias missing or duplicated: " + savedKey + "/" + fuel);
        assert.equal(key(rows[0]), key(original), "Stored raw favourite identity was substituted");
        for (const priceFuel of fuels) assert.equal(rows[0][priceFuel], original[priceFuel], "Alias borrowed another row's price");
        assert.deepEqual([...rows[0].fuels], original.fuels);
        context.renderList();
        assert.ok(list.innerHTML.includes(`data-key="${context.esc(key(original))}"`));
    }
    context.dedupePricelessStations();
    assert.ok(context.getRows().some(s => key(s) === key(original)), "Repeated dedupe lost a stored alias");
    assert.deepEqual(context.FAVS, [savedKey], "Saved favourite key was rewritten");
    // If both source identities were explicitly starred, each remains
    // independently removable using the existing favourite controls.
    const canonicalKey = after.aliases.get(key(original)).stationKey;
    context.FAVS.push("st:" + canonicalKey);
    context.fuelType = "lpg";
    const bothStarred = context.getRows();
    assert.equal(bothStarred.length, 2);
    assert.deepEqual(new Set(bothStarred.map(key)), new Set([key(original), canonicalKey]));
    const canonical = input.after.stations.find(s => key(s) === canonicalKey);
    for (const fuel of fuels) assert.equal(bothStarred.find(s => key(s) === canonicalKey)[fuel], canonical[fuel]);
    context.toggleFav(savedKey);
    assert.equal(context.getRows().length, 1);
    assert.equal(key(context.getRows()[0]), canonicalKey);
    context.toggleFav("st:" + canonicalKey);
    assert.equal(context.getRows().length, 0, "Unfavourite remained stuck on another raw alias");
    context.showFavsOnly = false;
    assert.ok(!context.getRows().some(s => key(s) === key(original)), "Unstarred alias remains as a duplicate card");
    context.showFavsOnly = true;
    favouriteResults.push(key(original));
}
context.DATA = structuredClone(input.after);
context.FAVS = favouriteCases.map(s => "st:" + key(s));
context.fuelType = "lpg";
context.dedupePricelessStations();
assert.deepEqual(new Set(context.getRows().map(key)), new Set(newlyCoalesced), "Combined saved favourites lost an actual alias");
context.dedupePricelessStations();
assert.equal(context.getRows().length, 9);
console.log(JSON.stringify({
    sourceKeys: sourceKeys.size, invisibleBefore: 8, invisibleAfter: 0,
    runtimeBefore: before.kept, runtimeAfter: after.kept,
    visibleByFuelBefore: before.byFuel, visibleByFuelAfter: after.byFuel, cityCounts, allRendered,
    addressCorrection: { stationKey: key(addressStation), raw: rawAddressStation.address,
        displayed: context.stationAddress(addressStation), navigation: true, rawAndDisplaySearch: true,
        sharePreservesKey: true, cardAndPopupAttribution: true },
    sharedMunicipalityCorrections: sharedResults,
    preservedFavouriteAliases: favouriteResults,
}));
