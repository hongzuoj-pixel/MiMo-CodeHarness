// Browser QA against generated local files. No site/account or remote app is used.
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL, fileURLToPath } = require('node:url');
const assert = require('node:assert/strict');

async function main() {
  const [playwrightPath, contractDir, outputDir] = process.argv.slice(2);
  const { chromium } = require(path.resolve(playwrightPath));
  fs.mkdirSync(outputDir, { recursive: true });
  const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const results = [];
  try {
    for (const verdict of ['PASS', 'FAIL']) {
      const context = await browser.newContext({ offline: true });
      const page = await context.newPage();
      const errors = [], network = [];
      page.on('pageerror', error => errors.push(String(error)));
      page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
      page.on('request', request => { if (/^https?:/.test(request.url())) network.push(request.url()); });
      const file = path.resolve(contractDir, verdict, 'view', 'index.html');
      for (const width of [320, 768, 1024, 1440]) {
        await page.setViewportSize({ width, height: 960 });
        await page.goto(pathToFileURL(file).href);
        await page.reload(); // reset focus even after a same-document hash navigation
        assert.equal(await page.locator('h1').count(), 1);
        assert.match(await page.locator('.decision').innerText(), verdict === 'PASS' ? /APPROVED/ : /BLOCKED/);
        const layout = await page.evaluate(() => ({
          viewport: document.documentElement.clientWidth,
          content: document.documentElement.scrollWidth,
          headings: [...document.querySelectorAll('h1,h2,h3')].map(h => Number(h.tagName.slice(1))),
          links: [...document.querySelectorAll('a')].map(a => a.getAttribute('href')),
          tableHeaders: [...document.querySelectorAll('th')].every(th => th.getAttribute('scope') === 'col'),
        }));
        assert.ok(layout.content <= layout.viewport + 1, JSON.stringify({ width, layout }));
        assert.ok(layout.tableHeaders);
        for (let i = 1; i < layout.headings.length; i++) assert.ok(layout.headings[i] <= layout.headings[i - 1] + 1);
        for (const href of layout.links) {
          const target = new URL(href, pathToFileURL(file));
          assert.equal(target.protocol, 'file:');
          assert.ok(fs.existsSync(fileURLToPath(target)), href);
          if (target.hash) assert.equal(await page.locator(target.hash).count(), 1);
        }
        await page.keyboard.press('Tab');
        assert.equal(await page.locator(':focus').getAttribute('href'), '#main');
        await page.keyboard.press('Enter');
        assert.equal(await page.locator(':focus').getAttribute('id'), 'main');
        await page.keyboard.press('Tab');
        assert.equal(await page.locator(':focus').getAttribute('href'), '#identity');
        await page.screenshot({ path: path.join(outputDir, `${verdict}-${width}.png`), fullPage: true });
        results.push({ verdict, width, horizontalOverflow: layout.content > layout.viewport, links: layout.links.length });
      }
      assert.deepEqual(errors, []);
      assert.deepEqual(network, []);
      await context.close();
    }
  } finally {
    await browser.close();
  }
  fs.writeFileSync(path.join(outputDir, 'browser-results.json'), JSON.stringify(results, null, 2));
  process.stdout.write(JSON.stringify(results));
}

main().catch(error => { console.error(error); process.exitCode = 1; });
