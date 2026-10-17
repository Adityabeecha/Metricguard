import { spawn } from 'node:child_process';

const chrome = spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', [
  '--headless=new',
  '--remote-debugging-port=9222',
  '--user-data-dir=C:\\Users\\badit\\OneDrive\\Desktop\\metricguard\\data\\v8_live_retest\\chrome_profile',
  '--no-first-run',
  '--no-default-browser-check',
  'about:blank',
]);

async function check() {
  await new Promise(r => setTimeout(r, 1500));
  const res = await fetch('http://127.0.0.1:9222/json/version');
  const json = await res.json();
  console.log('Connected to Chrome via CDP:', json.Browser);
  chrome.kill();
}

check().catch(e => {
  console.error(e);
  chrome.kill();
});
