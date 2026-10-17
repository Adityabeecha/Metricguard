import { spawn } from 'node:child_process';
import fs from 'node:fs';

const keys = JSON.parse(fs.readFileSync('data/v8_live_retest/.auth_keys.json', 'utf-8'));
const analystKey = keys.analyst.api_key;
const pid = 'ws-relational-live';
const mid = 'metric-e8cb92dbef384193ae56cad1f887fdff';

const chrome = spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', [
  '--headless=new',
  '--remote-debugging-port=9222',
  '--disable-extensions',
  '--user-data-dir=C:\\Users\\badit\\OneDrive\\Desktop\\metricguard\\data\\v8_live_retest\\chrome_profile_cdp',
  '--no-first-run',
  '--no-default-browser-check',
  '--window-size=1280,950',
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
  
  console.log('Navigating to dashboard...');
  await send('Page.navigate', { url: `http://127.0.0.1:5173/w/${pid}/dashboard` });
  await sleep(2000);
  
  // Click Connect
  await send('Runtime.evaluate', {
    expression: `Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Connect with API key')).click()`
  });
  await sleep(500);
  await send('Input.insertText', { text: analystKey });
  await sleep(300);
  await send('Runtime.evaluate', {
    expression: `Array.from(document.querySelectorAll('form button')).find(b => b.textContent === 'Connect').click()`
  });
  await sleep(2000);
  
  // Click Metrics navigation item
  console.log('Clicking Metrics nav item...');
  await send('Runtime.evaluate', {
    expression: `Array.from(document.querySelectorAll('nav button')).find(b => b.textContent.includes('Metrics')).click()`
  });
  await sleep(2000);
  
  // Find card for mid and click View detail
  console.log('Clicking View detail on metric card...');
  const clickedDetail = await send('Runtime.evaluate', {
    expression: `(() => {
      const cards = Array.from(document.querySelectorAll('.metric-card, .card, div'));
      const card = cards.find(c => c.textContent.includes('${mid}') && c.querySelector('button'));
      if (card) {
        const btn = Array.from(card.querySelectorAll('button')).find(b => b.textContent.includes('View detail'));
        if (btn) { btn.click(); return true; }
      }
      // Alternative: find any button with View detail that belongs to this metric
      const allBtns = Array.from(document.querySelectorAll('button')).filter(b => b.textContent.includes('View detail'));
      for (const b of allBtns) {
        let parent = b.parentElement;
        while (parent) {
          if (parent.textContent.includes('${mid}')) {
            b.click();
            return true;
          }
          parent = parent.parentElement;
        }
      }
      return false;
    })()`,
    returnByValue: true
  });
  console.log('Clicked detail:', clickedDetail.result.value);
  await sleep(2000);
  
  // Now click Contracts tab
  console.log('Clicking Contracts tab...');
  const clickedTab = await send('Runtime.evaluate', {
    expression: `(() => {
      const tabBtn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Contracts');
      if (tabBtn) { tabBtn.click(); return true; }
      return false;
    })()`,
    returnByValue: true
  });
  console.log('Clicked Contracts tab:', clickedTab.result.value);
  await sleep(2000);
  
  const buttonsInPanel = (await send('Runtime.evaluate', {
    expression: `Array.from(document.querySelectorAll('button')).map(b => b.textContent.trim())`,
    returnByValue: true
  })).result.value;
  console.log('Buttons after opening Contracts panel:', buttonsInPanel);
  
  ws.close();
  chrome.kill();
}

run().catch(e => { console.error(e); chrome.kill(); });
