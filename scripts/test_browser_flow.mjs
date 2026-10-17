import { spawn } from 'node:child_process';
import fs from 'node:fs';

const keys = JSON.parse(fs.readFileSync('data/v8_live_retest/.auth_keys.json', 'utf-8'));
const analystKey = keys.analyst.api_key;
const ownerKey = keys.owner.api_key;

const chrome = spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', [
  '--headless=new',
  '--remote-debugging-port=9222',
  '--disable-extensions',
  '--user-data-dir=C:\\Users\\badit\\OneDrive\\Desktop\\metricguard\\data\\v8_live_retest\\chrome_profile_cdp',
  '--no-first-run',
  '--no-default-browser-check',
  '--window-size=1280,900',
  'about:blank',
]);

async function main() {
  await new Promise(r => setTimeout(r, 1500));
  const pages = await (await fetch('http://127.0.0.1:9222/json')).json();
  const pageTarget = pages.find(p => p.type === 'page') || pages[0];
  const wsUrl = pageTarget.webSocketDebuggerUrl;
  const ws = new WebSocket(wsUrl);
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
  await send('DOM.enable');
  
  console.log('Navigating to dashboard...');
  await send('Page.navigate', { url: 'http://127.0.0.1:5173/w/ws-relational-live/dashboard' });
  await new Promise(r => setTimeout(r, 2000));
  
  // 1. Initial Unauthenticated Screenshot
  const res1 = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync('data/v8_live_retest/screenshots/browser_01_unauthenticated.png', Buffer.from(res1.data, 'base64'));
  console.log('Captured browser_01_unauthenticated.png');
  
  // 2. Click "Connect with API key" button
  const getButtonPos = await send('Runtime.evaluate', {
    expression: `(() => {
      const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Connect with API key'));
      if (!btn) return null;
      const rect = btn.getBoundingClientRect();
      return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
    })()`,
    returnByValue: true
  });
  console.log('Connect button pos:', getButtonPos.result.value);
  
  const { x, y } = getButtonPos.result.value;
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
  await new Promise(r => setTimeout(r, 500));
  
  // 3. Capture modal open
  const resModal = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync('data/v8_live_retest/screenshots/browser_02_connect_modal_open.png', Buffer.from(resModal.data, 'base64'));
  console.log('Captured browser_02_connect_modal_open.png');
  
  // 4. Type API key into input
  console.log('Inserting analyst API key...');
  await send('Input.insertText', { text: analystKey });
  await new Promise(r => setTimeout(r, 300));
  
  // 5. Click Connect button in the form
  const getSubmitPos = await send('Runtime.evaluate', {
    expression: `(() => {
      const btn = Array.from(document.querySelectorAll('form button')).find(b => b.textContent === 'Connect');
      if (!btn) return null;
      const rect = btn.getBoundingClientRect();
      return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
    })()`,
    returnByValue: true
  });
  console.log('Submit button pos:', getSubmitPos.result.value);
  
  const sPos = getSubmitPos.result.value;
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: sPos.x, y: sPos.y, button: 'left', clickCount: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: sPos.x, y: sPos.y, button: 'left', clickCount: 1 });
  await new Promise(r => setTimeout(r, 2000));
  
  // 6. Check topbar status text
  const topbar = await send('Runtime.evaluate', {
    expression: `document.querySelector('.topbar-status')?.textContent?.trim()`,
    returnByValue: true
  });
  console.log('Topbar status after analyst connect:', topbar.result.value);
  
  const resAnalyst = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync('data/v8_live_retest/screenshots/browser_03_analyst_connected.png', Buffer.from(resAnalyst.data, 'base64'));
  console.log('Captured browser_03_analyst_connected.png');
  
  ws.close();
  chrome.kill();
}

main().catch(e => {
  console.error(e);
  chrome.kill();
});
