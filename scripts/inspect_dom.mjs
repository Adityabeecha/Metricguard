import { spawn } from 'node:child_process';

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
  console.log('Connecting to target:', wsUrl);
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
  
  console.log('Navigating to dashboard...');
  await send('Page.navigate', { url: 'http://127.0.0.1:5173/w/ws-relational-live/dashboard' });
  await new Promise(r => setTimeout(r, 3000));
  
  const text = (await send('Runtime.evaluate', {
    expression: `(() => {
      return {
        url: window.location.href,
        buttons: Array.from(document.querySelectorAll('button')).map(b => b.textContent?.trim()),
        status: document.querySelector('.topbar-status')?.textContent?.trim(),
        text: document.body.innerText.slice(0, 300)
      };
    })()`,
    returnByValue: true
  })).result.value;
  
  console.log('Page inspection:', text);
  ws.close();
  chrome.kill();
}

main().catch(e => {
  console.error(e);
  chrome.kill();
});
