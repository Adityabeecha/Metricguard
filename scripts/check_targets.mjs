import { spawn } from 'node:child_process';

const chrome = spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', [
  '--headless=new',
  '--remote-debugging-port=9222',
  '--user-data-dir=C:\\Users\\badit\\OneDrive\\Desktop\\metricguard\\data\\v8_live_retest\\chrome_profile_cdp',
  '--no-first-run',
  '--no-default-browser-check',
  '--window-size=1280,900',
  'about:blank',
]);

async function main() {
  await new Promise(r => setTimeout(r, 1500));
  const pages = await (await fetch('http://127.0.0.1:9222/json')).json();
  console.log('Available targets:');
  for (const p of pages) {
    console.log(`- type: ${p.type}, url: ${p.url}, id: ${p.id}`);
  }
  
  const pageTarget = pages.find(p => p.type === 'page');
  console.log('Selected page target:', pageTarget);
  chrome.kill();
}

main().catch(e => {
  console.error(e);
  chrome.kill();
});
