import { spawn } from 'node:child_process';
import fs from 'node:fs';

const chrome = spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', [
  '--headless=new',
  '--remote-debugging-port=9222',
  '--user-data-dir=C:\\Users\\badit\\OneDrive\\Desktop\\metricguard\\data\\v8_live_retest\\chrome_profile',
  '--no-first-run',
  '--no-default-browser-check',
  '--window-size=1280,900',
  'about:blank',
]);

async function run() {
  await new Promise(r => setTimeout(r, 1500));
  const pages = await (await fetch('http://127.0.0.1:9222/json')).json();
  const wsUrl = pages[0].webSocketDebuggerUrl;
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
  
  await send('Page.navigate', { url: 'http://127.0.0.1:5173/w/ws-relational-live/dashboard' });
  await new Promise(r => setTimeout(r, 2000));
  
  const title = (await send('Runtime.evaluate', { expression: 'document.title', returnByValue: true })).result.value;
  console.log('Page Title:', title);
  
  const res = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync('data/v8_live_retest/screenshots/test_screenshot.png', Buffer.from(res.data, 'base64'));
  console.log('Wrote data/v8_live_retest/screenshots/test_screenshot.png successfully!');
  
  ws.close();
  chrome.kill();
}

run().catch(e => {
  console.error(e);
  chrome.kill();
});
