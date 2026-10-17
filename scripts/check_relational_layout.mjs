// Focused layout regression: real styles, native form controls and a wide evidence table.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const live = process.argv.includes('--live');
const browser = process.env.METRICGUARD_TEST_BROWSER || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const scratchRoot = path.join(root, '.test-tmp');
await fs.mkdir(scratchRoot, {recursive: true});
const profile = await fs.mkdtemp(path.join(scratchRoot, 'relational-layout-'));
const styles = (await Promise.all(['apps/web/src/style.css', 'apps/web/src/views/relationalEvidence.css']
  .map(name => fs.readFile(path.join(root, name), 'utf8')))).join('\n');
const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>${styles}</style></head>
<body><header class="topbar"><div class="breadcrumbs">Governed Points / <b>Investigations</b></div>
  <div class="topbar-right"><div class="connection-control" style="display:flex;align-items:center;gap:8px">
    <span class="topbar-status">Analyst Beta · analyst</span><button class="button secondary small">Disconnect</button>
    <button class="button secondary small">Replace key</button></div><span class="topbar-status">Processing unavailable</span><span class="avatar">MG</span></div></header>
<main class="content"><div class="v2-page">
<section class="panel v2-step"><div class="v2-step-body v2-grid two">
  <form><fieldset class="form-stack"><label>Registered contract<select><option>Governed Points Contract · revision 1</option><option>Decimal 38 Bounded Arithmetic Contract 1791209019 · revision 1</option></select></label>
    <label>Prepared fact extract<input type="file"></label><label>Snapshot name<input value="Governed Points 5-Row Snapshot"></label>
    <fieldset><legend>Registered categorical fields</legend><label><input type="checkbox"> category</label></fieldset></fieldset></form>
  <div class="v2-side"><label>Validated snapshot<select><option>Governed Points 5-Row Snapshot · 5 fact rows</option></select></label>
    <button class="button secondary">5-Row Governed Points Investigation with saved analyst explanation</button>
    <p>3 matched · 2 unmatched (1 NULL foreign key) · 0 multiplied rows</p></div>
</div></section>
<section class="panel v2-step"><div class="v2-step-body"><div class="join-evidence">
  <div class="table-scroll"><table class="join-records"><thead><tr><th>Fact key</th><th>Supporting records</th></tr></thead>
    <tbody><tr><td>row-1</td><td>Exact comparison evidence remains accessible through horizontal table scrolling.</td></tr></tbody></table></div>
</div></div></section></div></main></body></html>`;

const chrome = spawn(browser, ['--headless=new', '--remote-debugging-port=0', '--no-first-run',
  '--no-default-browser-check', '--disable-extensions', `--user-data-dir=${profile}`, 'about:blank'],
  {stdio: 'ignore', windowsHide: true});
let socket;
let sequence = 0;
const pending = new Map();
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(read, label) {
  for (let attempt = 0; attempt < 150; attempt++) {
    const value = await read();
    if (value) return value;
    await pause(100);
  }
  throw new Error(`Timed out: ${label}`);
}
function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timeout = setTimeout(() => {pending.delete(id); reject(new Error(`Timed out: ${method}`));}, 10000);
    pending.set(id, {resolve, reject, timeout});
    socket.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const response = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
  if (response.exceptionDetails) throw new Error(response.exceptionDetails.text);
  return response.result.value;
}

try {
  const port = await until(async () => {
    try {return (await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0];}
    catch (error) {if (error.code !== 'ENOENT') throw error;}
  }, 'browser startup');
  const tabs = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
  socket = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl);
  await once(socket, 'open');
  socket.addEventListener('message', event => {
    const response = JSON.parse(event.data);
    const request = pending.get(response.id);
    if (!request) return;
    clearTimeout(request.timeout); pending.delete(response.id);
    if (response.error) request.reject(new Error(response.error.message)); else request.resolve(response.result);
  });
  await send('Page.enable');
  if (live) {
    const keys = JSON.parse(await fs.readFile(path.join(root, 'data/v8_final_acceptance/.auth_keys.json'), 'utf8'));
    await send('Page.navigate', {url: 'http://127.0.0.1:5173/w/ws-relational-final/investigations/21d7e74d-32be-49c4-9699-06944b0299e1?mode=relational'});
    await until(() => evaluate("!!Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Connect with API key'))"), 'connection control');
    await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Connect with API key')).click()");
    await until(() => evaluate("!!document.querySelector('form input[type=password]')"), 'key input');
    await evaluate("document.querySelector('form input[type=password]').focus()");
    await send('Input.insertText', {text: keys.analyst.api_key});
    await evaluate("Array.from(document.querySelectorAll('form button')).find(b => b.textContent.trim() === 'Connect').click()");
    await until(() => evaluate("!!document.querySelector('.join-definitions')"), 'saved investigation');
    await evaluate("Array.from(document.querySelectorAll('.join-evidence-tabs button')).find(b => b.textContent.includes('Supporting records')).click()");
    await until(() => evaluate("!!document.querySelector('.join-records')"), 'supporting records');
    await evaluate("document.querySelectorAll('.join-records details').forEach(detail => {detail.open = true})");
  } else {
    await send('Page.navigate', {url: `data:text/html;charset=utf-8,${encodeURIComponent(html)}`});
    await until(() => evaluate("document.readyState === 'complete' && !!document.querySelector('.join-records')"), 'layout fixture');
  }
  for (const width of [375, 1280]) {
    await send('Emulation.setDeviceMetricsOverride', {width, height: 900, deviceScaleFactor: 1, mobile: width === 375});
    await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
    const actual = await evaluate(`(() => {
      const doc = document.documentElement;
      const scroller = document.querySelector('.join-records').closest('.table-scroll');
      const form = document.querySelector('.form-stack');
      return {clientWidth: doc.clientWidth, scrollWidth: doc.scrollWidth,
        gridColumns: getComputedStyle(document.querySelector('.v2-grid')).gridTemplateColumns.split(' ').length,
        formWidth: form.clientWidth, formScrollWidth: form.scrollWidth,
        tableWidth: scroller.clientWidth, tableScrollWidth: scroller.scrollWidth,
        tableOverflowX: getComputedStyle(scroller).overflowX,
        overflowingElements: Array.from(document.querySelectorAll('.v2-page *, .topbar *'))
          .filter(el => !el.closest('.table-scroll') && el.getBoundingClientRect().right > doc.clientWidth + 1)
          .slice(0, 12).map(el => ({tag: el.tagName, class: el.className, width: Math.round(el.getBoundingClientRect().width)}))};
    })()`);
    console.log(JSON.stringify({width, ...actual}));
    assert(actual.scrollWidth <= actual.clientWidth + 1, `Page overflows at ${width}px: ${actual.scrollWidth}px`);
    assert(actual.formScrollWidth <= actual.formWidth + 1, 'Form controls overflow their grid cell');
    assert.equal(actual.gridColumns, width === 375 ? 1 : 2);
    if (width === 375) {
      assert(actual.tableScrollWidth > actual.tableWidth, 'Wide evidence must remain scrollable');
      assert.equal(actual.tableOverflowX, 'auto');
    }
  }
  console.log('PASS: 375px and desktop layout; evidence scrolls inside its container.');
} finally {
  if (socket) socket.close();
  for (const request of pending.values()) clearTimeout(request.timeout);
  const stopped = once(chrome, 'exit').catch(() => {});
  chrome.kill();
  await Promise.race([stopped, pause(2000)]);
  // Only remove the temporary profile created by this check, under the workspace.
  if (path.dirname(profile) === scratchRoot) {
    await fs.rm(profile, {recursive: true, force: true, maxRetries: 3, retryDelay: 200}).catch(() => {});
  }
}
