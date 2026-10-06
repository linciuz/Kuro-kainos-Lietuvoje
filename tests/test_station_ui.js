// Execute only pure functions from the actual app, without booting the app,
// fetching any URL, using browser storage, or inventing station/price data.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const app = fs.readFileSync(path.join(__dirname, "../app.js"), "utf8");
const names = ["stationKey", "stationMunicipality", "stationMunicipalityHtml", "dedupePricelessStations",
    "getRows", "currentCheapest", "nearestStationMuni", "haversine", "esc", "renderList", "showMoreCards"];
const functions = names.map(name => {
    const match = app.match(new RegExp("^function " + name + "\\([^]*?^}", "m"));
    assert.ok(match, "Missing app function: " + name);
    return match[0];
}).join("\n");
let municipality = "";
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
    radiusKm: 0, sortDir: "asc", t: key => key,
    document: { getElementById: id => {
        if (id === "stations-list") return list;
        if (id === "show-more") return list.innerHTML.includes('id="show-more"') ? more : null;
        return { value: id === "muni-select" ? municipality : "" };
    } },
});
context.effPrice = station => station[context.fuelType];
const chunk = app.match(/^const LIST_CHUNK = (\d+);/m);
assert.ok(chunk);
vm.runInContext(functions + "\nconst escAttr = esc;\n" + chunk[0] + "\nlet _listRest = [];", context);
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
    return { kept: context.DATA.stations.length, visible, byFuel };
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
console.log(JSON.stringify({
    sourceKeys: sourceKeys.size, invisibleBefore: 8, invisibleAfter: 0,
    runtimeBefore: before.kept, runtimeAfter: after.kept,
    visibleByFuelBefore: before.byFuel, visibleByFuelAfter: after.byFuel, cityCounts, allRendered,
}));
