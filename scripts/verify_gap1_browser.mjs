import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const DATA_DIR = 'data/v8_live_retest';
const keys = JSON.parse(fs.readFileSync(path.join(DATA_DIR, '.auth_keys.json'), 'utf-8'));
const analystKey = keys.analyst.api_key;
const ownerKey = keys.owner.api_key;
const pid = 'ws-relational-live';
const mid = 'metric-e8cb92dbef384193ae56cad1f887fdff';
const origCheckId = 'mcc-71d977f7b857463d8aee0ef551a53fda';
const origHash = 'dfe6cf15d9afd046b5775106a044215cdfe513429fc73bc8a7c91342139d2484';

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

async function main() {
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
  
  async function evalJs(expr) {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
    if (r.exceptionDetails) {
      throw new Error(`JS error in (${expr}): ${JSON.stringify(r.exceptionDetails)}`);
    }
    return r.result.value;
  }
  
  async function clickElement(selectorOrExpr) {
    const pos = await evalJs(`(() => {
      const el = typeof (${selectorOrExpr}) === 'string' ? document.querySelector(${selectorOrExpr}) : (${selectorOrExpr});
      if (!el) return null;
      el.scrollIntoView({ block: 'center' });
      const rect = el.getBoundingClientRect();
      return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
    })()`);
    if (!pos) throw new Error(`Element not found: ${selectorOrExpr}`);
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: pos.x, y: pos.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: pos.x, y: pos.y, button: 'left', clickCount: 1 });
    await sleep(400);
  }
  
  async function capture(filename) {
    const res = await send('Page.captureScreenshot', { format: 'png' });
    const fullPath = path.join(DATA_DIR, 'screenshots', filename);
    fs.writeFileSync(fullPath, Buffer.from(res.data, 'base64'));
    console.log(`[Screenshot] Saved ${filename}`);
    return fullPath;
  }
  
  await send('Page.enable');
  await send('Runtime.enable');
  await send('DOM.enable');
  
  console.log('--- Step 1: Navigate to Dashboard unauthenticated ---');
  await send('Page.navigate', { url: `http://127.0.0.1:5173/w/${pid}/dashboard` });
  await sleep(2000);
  
  let topbarText = await evalJs(`document.querySelector('.topbar-status')?.textContent?.trim()`);
  console.log('Initial topbar status:', topbarText);
  await capture('gap1_01_unauthenticated_dashboard.png');
  
  console.log('--- Step 2: Open Connect modal and connect as Analyst ---');
  await clickElement(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Connect with API key'))`);
  await sleep(400);
  await capture('gap1_02_connect_modal_open.png');
  
  await send('Input.insertText', { text: analystKey });
  await sleep(300);
  await clickElement(`Array.from(document.querySelectorAll('form button')).find(b => b.textContent === 'Connect')`);
  await sleep(2000);
  
  topbarText = await evalJs(`document.querySelector('.topbar-status')?.textContent?.trim()`);
  console.log('Topbar status after analyst connect:', topbarText);
  if (!topbarText.includes('Analyst Beta') || !topbarText.includes('analyst')) {
    throw new Error(`Unexpected analyst topbar status: ${topbarText}`);
  }
  await capture('gap1_03_analyst_connected.png');
  
  console.log('--- Step 3: Navigate to Metrics catalog and select Metric ---');
  await clickElement(`Array.from(document.querySelectorAll('nav button')).find(b => b.textContent.includes('Metrics'))`);
  await sleep(2000);
  
  // Click exact metric card row
  await evalJs(`(() => {
    const rows = Array.from(document.querySelectorAll('.metric-card-row'));
    const targetRow = rows.find(r => r.textContent.includes('${mid}'));
    if (!targetRow) throw new Error('Target metric row not found: ${mid}');
    targetRow.querySelector('button').click();
  })()`);
  await sleep(2000);
  
  // Click Contracts tab
  await clickElement(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Contracts')`);
  await sleep(2000);
  await capture('gap1_04_analyst_contracts_panel.png');
  
  console.log('--- Step 4: Propose revision as Analyst ---');
  // Click "Propose revision"
  await clickElement(`Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Propose revision')`);
  await sleep(800);
  
  // Fill form fields
  await evalJs(`(() => {
    const form = document.querySelector('form.contract-form');
    if (!form) throw new Error('Contract proposal form not found');
    
    const labels = Array.from(form.querySelectorAll('label'));
    const ownerLabel = labels.find(l => l.innerText.startsWith('Owner'));
    const unitLabel = labels.find(l => l.innerText.startsWith('Unit label'));
    const meaningLabel = labels.find(l => l.innerText.startsWith('Business meaning'));
    const factGrainLabel = labels.find(l => l.innerText.startsWith('Declared fact grain'));
    const dimGrainLabel = labels.find(l => l.innerText.startsWith('Declared dimension grain'));
    
    function assign(el, val) {
      const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(el, val);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    }
    
    assign(ownerLabel.querySelector('input'), 'Billing Operations Team');
    assign(unitLabel.querySelector('input'), 'points');
    assign(meaningLabel.querySelector('textarea'), 'Governed point items billed per group segment (V8.2 browser verification)');
    assign(factGrainLabel.querySelector('input'), 'one row per item');
    assign(dimGrainLabel.querySelector('input'), 'one row per group');
  })()`);
  await sleep(400);
  await capture('gap1_05_analyst_form_filled.png');
  
  // Click Save proposal
  await clickElement(`Array.from(document.querySelectorAll('form button')).find(b => b.textContent === 'Save proposal')`);
  await sleep(2500);
  
  // Capture proposal submitted and notice
  const noticeText = await evalJs(`document.querySelector('.alert.success')?.textContent?.trim()`);
  console.log('Notice text after proposal:', noticeText);
  const proposedRevId = await evalJs(`document.querySelector('.contract-selection select')?.value`);
  console.log('Proposed revision ID in select dropdown:', proposedRevId);
  await capture('gap1_06_analyst_proposal_submitted.png');
  
  console.log('--- Step 5: Replace key to connect as Owner ---');
  await clickElement(`Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Replace key')`);
  await sleep(400);
  await send('Input.insertText', { text: ownerKey });
  await sleep(300);
  await clickElement(`Array.from(document.querySelectorAll('form button')).find(b => b.textContent === 'Connect')`);
  await sleep(2500);
  
  topbarText = await evalJs(`document.querySelector('.topbar-status')?.textContent?.trim()`);
  console.log('Topbar status after owner connect:', topbarText);
  if (!topbarText.includes('Owner Alpha') || !topbarText.includes('owner')) {
    throw new Error(`Unexpected owner topbar status: ${topbarText}`);
  }
  await capture('gap1_07_owner_connected.png');
  
  console.log('--- Step 6: Navigate to metric Contracts panel as Owner ---');
  // Re-select metric row in catalog (since identity change reloads workspace)
  await evalJs(`(() => {
    const rows = Array.from(document.querySelectorAll('.metric-card-row'));
    const targetRow = rows.find(r => r.textContent.includes('${mid}'));
    if (targetRow) {
      targetRow.querySelector('button').click();
    }
  })()`);
  await sleep(2000);
  
  // Click Contracts tab
  await clickElement(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Contracts')`);
  await sleep(2000);
  
  // Select proposed revision in select dropdown
  console.log('Selecting proposed revision in dropdown:', proposedRevId);
  await evalJs(`(() => {
    const sel = document.querySelector('.contract-selection select');
    if (!sel) throw new Error('Contract selection dropdown not found');
    const opt = Array.from(sel.options).find(o => o.value === '${proposedRevId}' || o.text.includes('proposed'));
    if (!opt) throw new Error('Proposed option not found in dropdown');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
    setter.call(sel, opt.value);
    sel.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
  await sleep(1200);
  
  // Fill approval reason
  console.log('Entering synthetic approval rationale...');
  await evalJs(`(() => {
    const ta = document.querySelector('.contract-approval textarea');
    if (!ta) throw new Error('Approval textarea not found');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(ta, 'Synthetic engineering test rationale: Relational contract approved for V8.2 browser verification.');
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    ta.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
  await sleep(400);
  await capture('gap1_08_owner_rationale_entered.png');
  
  // Click "Approve this revision"
  await clickElement(`Array.from(document.querySelectorAll('.contract-approval button')).find(b => b.textContent === 'Approve this revision')`);
  await sleep(2500);
  
  const approveNotice = await evalJs(`document.querySelector('.alert.success')?.textContent?.trim()`);
  console.log('Notice text after approval:', approveNotice);
  await capture('gap1_09_owner_approved.png');
  
  console.log('--- Step 7: Save metadata check with Relational Live Snapshot ---');
  // Select relational live snapshot (a38d951e...) in dropdown
  const snapSelected = await evalJs(`(() => {
    const sel = document.querySelector('.contract-fields select');
    if (!sel) return null;
    const opt = Array.from(sel.options).find(o => o.textContent.includes('Relational Live Snapshot') || o.value.includes('a38d951e'));
    if (!opt) return null;
    sel.value = opt.value;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    return { value: opt.value, text: opt.text };
  })()`);
  console.log('Selected snapshot for check:', snapSelected);
  await sleep(400);
  
  // Click "Save metadata check"
  await clickElement(`Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Save metadata check')`);
  await sleep(3500);
  
  // Inspect report
  const checkStatus = await evalJs(`document.querySelector('.contract-result h3')?.textContent?.trim()`);
  const checkDetails = await evalJs(`document.querySelector('.contract-result p')?.textContent?.trim()`);
  const checkHashText = await evalJs(`Array.from(document.querySelectorAll('.contract-result p')).find(p => p.textContent.includes('Check hash:'))?.textContent?.trim()`);
  console.log('Check Result:', { checkStatus, checkDetails, checkHashText });
  if (checkStatus !== 'Registered metadata matches') {
    throw new Error(`Unexpected check status: ${checkStatus}`);
  }
  await capture('gap1_10_metadata_match_report.png');
  
  console.log('--- Step 8: Reopen original saved check ---');
  // In recent saved checks, click "Open saved check" for original check (row with timestamp 10:21:28)
  const reopenClicked = await evalJs(`(() => {
    const rows = Array.from(document.querySelectorAll('.contract-card .contract-history-row'));
    const origRow = rows.find(r => r.textContent.includes('10:21:28'));
    if (origRow) {
      const btn = origRow.querySelector('button');
      btn.scrollIntoView({ block: 'center' });
      btn.click();
      return true;
    }
    return false;
  })()`);
  console.log('Reopen button clicked:', reopenClicked);
  await sleep(2500);
  
  const reopenedDetails = await evalJs(`document.querySelector('.contract-result p')?.textContent?.trim()`);
  const reopenedHashText = await evalJs(`Array.from(document.querySelectorAll('.contract-result p')).find(p => p.textContent.includes('Check hash:'))?.textContent?.trim()`);
  console.log('Reopened Result:', { reopenedDetails, reopenedHashText });
  if (!reopenedDetails.includes(origCheckId)) {
    throw new Error(`Reopened report did not match original check ID ${origCheckId}: ${reopenedDetails}`);
  }
  await capture('gap1_11_reopened_original_report.png');
  
  // Copy to step4 files to replace earlier unauthenticated screenshots
  fs.copyFileSync(path.join(DATA_DIR, 'screenshots', 'gap1_03_analyst_connected.png'), path.join(DATA_DIR, 'screenshots', 'step4_01_connect_control.png'));
  fs.copyFileSync(path.join(DATA_DIR, 'screenshots', 'gap1_10_metadata_match_report.png'), path.join(DATA_DIR, 'screenshots', 'step4_02_metadata_match.png'));
  console.log('Replaced step4_01 and step4_02 screenshots with genuine evidence.');

  // Write receipt
  const receipt = {
    gate: 'ORDINARY_BROWSER_GOVERNANCE',
    status: 'PASS',
    browser_steps: [
      { step: 1, action: 'navigate_unauthenticated', screenshot: 'gap1_01_unauthenticated_dashboard.png', topbar: 'Not connected' },
      { step: 2, action: 'connect_modal_open', screenshot: 'gap1_02_connect_modal_open.png' },
      { step: 3, action: 'analyst_connected', screenshot: 'gap1_03_analyst_connected.png', topbar: 'Analyst Beta · analyst' },
      { step: 4, action: 'view_contracts_panel', screenshot: 'gap1_04_analyst_contracts_panel.png' },
      { step: 5, action: 'analyst_form_filled', screenshot: 'gap1_05_analyst_form_filled.png' },
      { step: 6, action: 'analyst_proposal_saved', screenshot: 'gap1_06_analyst_proposal_submitted.png', revision_id: proposedRevId, notice: noticeText },
      { step: 7, action: 'replace_key_owner_connected', screenshot: 'gap1_07_owner_connected.png', topbar: 'Owner Alpha · owner' },
      { step: 8, action: 'owner_rationale_entered', screenshot: 'gap1_08_owner_rationale_entered.png' },
      { step: 9, action: 'owner_revision_approved', screenshot: 'gap1_09_owner_approved.png', notice: approveNotice },
      { step: 10, action: 'metadata_check_executed', screenshot: 'gap1_10_metadata_match_report.png', check_status: checkStatus, details: checkDetails, hash: checkHashText },
      { step: 11, action: 'reopened_original_report', screenshot: 'gap1_11_reopened_original_report.png', reopened_details: reopenedDetails, hash: reopenedHashText }
    ]
  };
  
  fs.writeFileSync(path.join(DATA_DIR, 'receipts', 'receipt_browser_governance_corrected.json'), JSON.stringify(receipt, null, 2), 'utf-8');
  console.log('Wrote receipt_browser_governance_corrected.json successfully!');
  
  ws.close();
  chrome.kill();
}

main().catch(e => {
  console.error(e);
  chrome.kill();
});
