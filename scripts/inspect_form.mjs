import { spawn } from 'node:child_process';
import fs from 'node:fs';

const keys = JSON.parse(fs.readFileSync('data/v8_live_retest/.auth_keys.json', 'utf-8'));
const ownerKey = keys.owner.api_key;
const pid = 'ws-relational-live';
const mid = 'metric-e8cb92dbef384193ae56cad1f887fdff';
const origCheckId = 'mcc-71d977f7b857463d8aee0ef551a53fda';

const chrome = spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', [
  '--headless=new',
  '--remote-debugging-port=9222',
  '--disable-extensions',
  '--user-data-dir=C:\\Users\\badit\\OneDrive\\Desktop\\metricguard\\data\\v8_live_retest\\chrome_profile_cdp',
  '--no-first-run',
  '--no-default-browser-check',
  '--window-size=1280,1050',
  'about:blank',
]);

async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function run() {
  await sleep(1500);
  const pages = await (await fetch('http://127.0.0.1:9222/json')).json();
  const pageTarget = pages.find(p => p.type === 'page') || pages[0];
  const ws = new WebSocket(pageTarget.webSocketDebuggerUrl);
  await new Promise((resolve) => ws.onopen = resolve);
  
  let reqId = 1;
  const pending = new Map();
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(msg.error);
      else resolve(msg.result);
    }
  };
  
  function send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = reqId++;
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params }));
    });
  }
  
  await send('Page.enable');
  await send('Runtime.enable');
  
  await send('Page.navigate', { url: `http://127.0.0.1:5173/w/${pid}/dashboard` });
  await sleep(2000);
  
  // Connect as Owner
  await send('Runtime.evaluate', {
    expression: `Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Connect with API key')).click()`
  });
  await sleep(500);
  await send('Input.insertText', { text: ownerKey });
  await sleep(300);
  await send('Runtime.evaluate', {
    expression: `Array.from(document.querySelectorAll('form button')).find(b => b.textContent === 'Connect').click()`
  });
  await sleep(2000);
  
  // Navigate to metric
  await send('Runtime.evaluate', {
    expression: `Array.from(document.querySelectorAll('nav button')).find(b => b.textContent.includes('Metrics')).click()`
  });
  await sleep(2000);
  await send('Runtime.evaluate', {
    expression: `(() => {
      const rows = Array.from(document.querySelectorAll('.metric-card-row'));
      const targetRow = rows.find(r => r.textContent.includes('${mid}'));
      if (targetRow) targetRow.querySelector('button').click();
    })()`
  });
  await sleep(2000);
  await send('Runtime.evaluate', {
    expression: `Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Contracts').click()`
  });
  await sleep(2000);
  
  // Find row containing 10:21:28
  const res = await send('Runtime.evaluate', {
    expression: `(() => {
      const rows = Array.from(document.querySelectorAll('.contract-card .contract-history-row'));
      const target = rows.find(r => r.textContent.includes('10:21:28'));
      if (!target) return { found: false };
      const btn = target.querySelector('button');
      btn.scrollIntoView({ block: 'center' });
      btn.click();
      return { found: true, text: target.innerText };
    })()`,
    returnByValue: true
  });
  console.log('Target row click:', res.result.value);
  await sleep(3000);
  
  const reportInfo = await send('Runtime.evaluate', {
    expression: `(() => {
      const rep = document.querySelector('.contract-result');
      return {
        title: rep?.querySelector('h3')?.innerText,
        p: rep?.querySelector('p')?.innerText,
        hashP: Array.from(rep ? rep.querySelectorAll('p') : []).find(p => p.textContent.includes('Check hash:'))?.innerText
      };
    })()`,
    returnByValue: true
  });
  console.log('Reopened report info:\n', JSON.stringify(reportInfo.result.value, null, 2));
  
  ws.close();
  chrome.kill();
}

run().catch(e => { console.error(e); chrome.kill(); });
