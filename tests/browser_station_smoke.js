// Local preview with real committed data. Abort external requests, including
// the production Worker, visitor counter, maps and analytics.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const zlib = require('node:zlib');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
    '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml' };
const server = http.createServer((req, res) => {
    const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    const file = path.resolve(root, '.' + (pathname === '/' ? '/index.html' : pathname));
    if (!file.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
    fs.readFile(file, (error, data) => {
        if (error) { res.writeHead(404).end(); return; }
        res.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream');
        res.end(data);
    });
});
(async () => {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    const browser = await chromium.launch({ headless: true,
        ...(process.platform === 'win32' ? { channel: 'chrome' } : {}) });
    try {
        const context = await browser.newContext({ serviceWorkers: 'block' });
        let blocked = 0;
        await context.route('**/*', route => {
            if (route.request().url().startsWith(origin + '/')) return route.continue();
            blocked++; return route.abort();
        });
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(origin + '/?muni=' + encodeURIComponent('Panevėžio m. sav.'), { waitUntil: 'domcontentloaded' });
        await page.locator('#stations-list .station-card').first().waitFor();
        await page.locator('#muni-select').selectOption('Panevėžio m. sav.');
        await page.locator('#search').fill('Ramygalos g. 186A');
        const target = page.locator('#stations-list .station-card')
            .filter({ hasText: 'Emsi' }).filter({ hasText: 'Ramygalos g. 186A' });
        await target.waitFor();
        assert.equal(await target.count(), 1);
        const card = await target.innerText();
        assert.ok(card.includes('1.899'), card);
        assert.ok(card.includes('Panevėžio m. sav.'), card);
        assert.equal(await target.locator('[title="LEA: Panevėžio r. sav."]').count(), 1);
        const key = await target.locator('.share-btn').getAttribute('data-key');
        assert.ok(key.endsWith('|Panevėžio r. sav.'), key);
        await page.locator('#view-map').click();
        await page.locator('.leaflet-marker-icon').first().waitFor();
        await page.locator('#view-list').click();
        await page.locator('#search').fill('');
        await page.locator('#muni-select').selectOption('');
        await page.waitForFunction(() => document.querySelector('#show-more'));
        const expected = await page.evaluate(() => getRows().length);
        while (await page.locator('#show-more').count()) await page.locator('#show-more').click();
        assert.equal(await page.locator('#stations-list .station-card').count(), expected);
        assert.ok(expected > 600, expected);
        const lea = JSON.parse(zlib.gunzipSync(fs.readFileSync(path.join(root, 'tests/fixtures/lea-prices-20261006.json.gz'))));
        const sourceKeys = [...new Set(lea.data.map(r => [r.company_name.trim(), r.address.trim(), r.municipality.trim()].join('|')))];
        const priceless = await page.evaluate(keys => DATA.stations.filter(s => s.no_price && keys.includes(stationKey(s))), sourceKeys);
        assert.equal(priceless.length, 8);
        for (const station of priceless) {
            await page.locator('#btn-' + station.fuels[0]).click();
            await page.locator('#search').fill(station.address);
            await page.waitForFunction(address => [...document.querySelectorAll('#stations-list .station-card')]
                .some(card => card.innerText.includes(address)), station.address);
            const match = page.locator('#stations-list .station-card').filter({ hasText: station.address }).first();
            assert.equal(await match.locator('.no-price-badge').count(), 1, station.address);
        }
        await page.setViewportSize({ width: 390, height: 844 });
        await page.locator('#btn-petrol95').click();
        await page.locator('#search').fill('Ramygalos g. 186A');
        await page.locator('#muni-select').selectOption('Panevėžio m. sav.');
        await target.waitFor();
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        fs.mkdirSync(path.join(root, '.claude'), { recursive: true });
        await page.screenshot({ path: path.join(root, '.claude/browser-emsi.png'), fullPage: true });
        assert.deepEqual(errors, []);
        console.log(JSON.stringify({ nationalCards: expected, pricelessCards: priceless.length,
            emsiCity: true, rawIdentityPreserved: true, mobileLayout: true, externalRequestsAborted: blocked }));
        const cold = await context.newPage();
        await cold.route('**/data/stations.json', route => route.abort());
        await cold.goto(origin + '/', { waitUntil: 'domcontentloaded' });
        await cold.waitForFunction(() => DATA && DATA.stations.length === 0 && OFFLINE_DATA);
        assert.equal(await cold.locator('#stations-list .station-card').count(), 0);
        assert.equal(await cold.evaluate(() => Object.keys(DATA.summary).length), 0);
        console.log('Cold offline boot: no invented summary or stations.');
        await context.close();
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => server.close());
