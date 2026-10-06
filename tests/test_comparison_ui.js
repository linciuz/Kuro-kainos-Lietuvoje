const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const app = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8');
const functions = ['stationKey', 'loadDiscrepancies', 'flagFor'].map(name => {
    const found = app.match(new RegExp('^(?:async )?function ' + name + '\\([^]*?^}', 'm'));
    assert.ok(found, name);
    return found[0];
}).join('\n');
let payload = { lea_date: input.date, items: input.items };
const context = vm.createContext({ DATA: { updated: input.date }, fuelType: 'lpg',
    DISCREP: { items: [], byNetwork: {}, byStation: {} }, fetchTimeout: () => undefined,
    fetch: async () => ({ ok: true, json: async () => payload }) });
vm.runInContext(functions, context);
(async () => {
    await context.loadDiscrepancies();
    for (const station of input.stations.filter(s => s.network === 'UAB Saurida')) {
        const item = input.items.find(it => it.station_key === context.stationKey(station));
        assert.equal(Boolean(context.flagFor(station)), Boolean(item));
    }
    payload = { ...payload, lea_date: '2026-08-11' }; // recorded old comparison date
    await context.loadDiscrepancies();
    assert.equal(context.DISCREP.items.length, 0);
    assert.ok(input.stations.every(s => context.flagFor(s) == null));
})().catch(e => { console.error(e); process.exitCode = 1; });
