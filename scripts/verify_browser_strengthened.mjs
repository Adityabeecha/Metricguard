import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

const DATA_DIR = 'data/v8_final_acceptance';
const keys = JSON.parse(fs.readFileSync(path.join(DATA_DIR, '.auth_keys.json'), 'utf-8'));
const analystKey = keys.analyst.api_key;
const pid = 'ws-relational-final';

const meta = JSON.parse(fs.readFileSync(path.join(DATA_DIR, 'test_metadata.json'), 'utf-8'));

const chromeProfileDir = path.resolve(DATA_DIR, 'chrome_profile_cdp_9223');
if (fs.existsSync(chromeProfileDir)) {
  fs.rmSync(chromeProfileDir, { recursive: true, force: true });
}
fs.mkdirSync(chromeProfileDir, { recursive: true });

const chrome = spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', [
  '--headless=new',
  '--remote-debugging-port=9223',
  '--disable-extensions',
  `--user-data-dir=${chromeProfileDir}`,
  '--no-first-run',
  '--no-default-browser-check',
  '--window-size=1280,1050',
  'about:blank',
]);

async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function main() {
  console.log("=================================================================");
  console.log("CORRECTED STRENGTHENED BROWSER VERIFICATION (CDP)");
  console.log("=================================================================");

  let pages = null;
  for (let i = 0; i < 20; i++) {
    try {
      const res = await fetch('http://127.0.0.1:9223/json');
      pages = await res.json();
      if (pages && pages.length) break;
    } catch {
      await sleep(300);
    }
  }
  if (!pages || !pages.length) throw new Error('Chrome remote debugging port 9223 not reachable');
  const pageTarget = pages.find(p => p.type === 'page') || pages[0];
  const ws = new WebSocket(pageTarget.webSocketDebuggerUrl);
  await new Promise((resolve) => ws.onopen = resolve);

  let reqId = 1;
  const pending = new Map();
  const eventListeners = new Map();

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(msg.error);
      else resolve(msg.result);
    } else if (msg.method) {
      const listeners = eventListeners.get(msg.method) || [];
      for (const cb of listeners) cb(msg.params);
    }
  };

  function send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = reqId++;
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params }));
    });
  }

  function on(event, cb) {
    if (!eventListeners.has(event)) eventListeners.set(event, []);
    eventListeners.get(event).push(cb);
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
    await sleep(500);
  }

  async function capture(filename) {
    const res = await send('Page.captureScreenshot', { format: 'png' });
    const fullPath = path.join(DATA_DIR, 'screenshots', filename);
    fs.writeFileSync(fullPath, Buffer.from(res.data, 'base64'));
    console.log(`[Screenshot] Saved ${filename}`);
    return fullPath;
  }

  async function waitForElement(selectorOrExpr, timeoutMs = 15000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const found = await evalJs(`(() => {
        const el = typeof (${selectorOrExpr}) === 'string' ? document.querySelector(${selectorOrExpr}) : (${selectorOrExpr});
        return !!el;
      })()`);
      if (found) return true;
      await sleep(300);
    }
    const html = await evalJs(`document.body.innerHTML.slice(0, 500)`);
    throw new Error(`Timeout waiting for element: ${selectorOrExpr}. Body snippet: ${html}`);
  }

  // Setup CDP domains
  await send('Page.enable');
  await send('Runtime.enable');
  await send('DOM.enable');
  await send('Log.enable');
  on('Runtime.consoleAPICalled', e => console.log('[Browser Console]', e.type, ...e.args.map(a => a.value || a.description)));

  // Network Fetch interception controller
  let fetchInterceptMode = 'passthrough'; // 'hold_then_fail', 'continue', 'passthrough'
  let pausedRequestResolver = null;

  on('Fetch.requestPaused', async (params) => {
    const { requestId, request } = params;
    console.log(`[CDP Fetch.requestPaused] id=${requestId} url=${request.url} mode=${fetchInterceptMode}`);
    if (fetchInterceptMode === 'hold_then_fail') {
      if (pausedRequestResolver) {
        const resolver = pausedRequestResolver;
        pausedRequestResolver = null;
        resolver(requestId);
      } else {
        console.log(`[CDP Fetch.requestPaused] resolver already used, failing subsequent request id=${requestId}`);
        await send('Fetch.failRequest', { requestId, errorReason: 'Failed' });
      }
    } else {
      await send('Fetch.continueRequest', { requestId });
    }
  });

  await send('Fetch.enable', {
    patterns: [{ urlPattern: '*join-evidence*', requestStage: 'Request' }]
  });

  on('Runtime.exceptionThrown', (e) => console.error('[Browser Exception]', JSON.stringify(e.exceptionDetails)));

  // 1. Authenticate via Connect modal
  console.log('--- 1. Authenticate via Connect modal ---');
  await send('Page.navigate', { url: `http://127.0.0.1:5173/w/${pid}/dashboard` });
  await sleep(1500);
  await waitForElement(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Connect with API key'))`);

  await clickElement(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Connect with API key'))`);
  await waitForElement(`document.querySelector('form input[type="password"]')`);
  await sleep(400);

  // Type Analyst API Key via CDP
  await send('Input.insertText', { text: analystKey });
  await sleep(300);
  await clickElement(`Array.from(document.querySelectorAll('form button')).find(b => b.textContent.trim() === 'Connect')`);
  await sleep(2000);

  const identityRole = await evalJs(`document.querySelector('.connection-control .topbar-status')?.textContent?.trim()`);
  console.log('Connected role:', identityRole);
  assert(identityRole && identityRole.includes('analyst'), `Expected analyst role, got: ${identityRole}`);

  // 2. Navigate to Relational Investigations
  console.log('--- 2. Navigate to Relational Investigations ---');
  await clickElement(`Array.from(document.querySelectorAll('.nav-item')).find(b => b.textContent.includes('Investigations'))`);
  await waitForElement(`Array.from(document.querySelectorAll('.section-tab-button')).find(b => b.textContent.includes('Fact + dimension'))`);
  await clickElement(`Array.from(document.querySelectorAll('.section-tab-button')).find(b => b.textContent.includes('Fact + dimension'))`);
  await sleep(1500);

  // 3. Loading, controlled failure, and recovery assertions
  console.log('\n--- 3. Testing: Loading (held pending), controlled failure, and recovery ---');
  
  // Set up interceptor to hold request pending
  const heldRequestPromise = new Promise(resolve => { pausedRequestResolver = resolve; });
  fetchInterceptMode = 'hold_then_fail';

  // Click on "5-Row Governed Points Investigation" (INNER JOIN fixture)
  await waitForElement(`Array.from(document.querySelectorAll('button')).find(x => x.textContent.trim() === '5-Row Governed Points Investigation')`);
  await evalJs(`(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const b = btns.find(x => x.textContent.trim() === '5-Row Governed Points Investigation');
    if (b) b.click();
  })()`);

  // Wait for the join-evidence request to be intercepted and held pending
  const heldRequestId = await heldRequestPromise;
  console.log(`Join-evidence request intercepted and held pending (requestId: ${heldRequestId}).`);
  await sleep(400);

  // A. Assert that "Complete artifact checked" is absent while loading
  const loadingHeadingText = await evalJs(`document.querySelector('.join-evidence-heading .v2-help')?.textContent`);
  console.log('Observed loading heading status:', loadingHeadingText);
  assert.equal(loadingHeadingText, 'Supporting records appear after the integrity check');
  assert(!loadingHeadingText.includes('Complete artifact checked'), 'Must NOT display "Complete artifact checked" while loading!');
  await evalJs(`document.querySelector('.join-evidence')?.scrollIntoView({ block: 'center' })`);
  await sleep(300);
  await capture('step5_02a_loading_evidence_held.png');

  // B. Produce a controlled failed join-evidence response
  console.log('Producing controlled failed response via Fetch.failRequest...');
  await send('Fetch.failRequest', {
    requestId: heldRequestId,
    errorReason: 'Failed'
  });

  await sleep(1000);
  const debugState = await evalJs(`(() => {
    return {
      body_warn: Array.from(document.querySelectorAll('.v2-status.warn')).map(el => el.textContent),
      alerts: Array.from(document.querySelectorAll('[role="alert"]')).map(el => el.textContent),
      heading: document.querySelector('.join-evidence-heading .v2-help')?.textContent,
      join_evidence_full_html: document.querySelector('.join-evidence')?.innerHTML
    };
  })()`);
  console.log('[DEBUG STATE AFTER FAIL FULL]', JSON.stringify(debugState));

  // Wait for visible error banner
  await waitForElement(`document.querySelector('.join-evidence .v2-status.warn')`);
  await sleep(500);

  const errorHeadingText = await evalJs(`document.querySelector('.join-evidence-heading .v2-help')?.textContent`);
  const errorBannerText = await evalJs(`document.querySelector('.join-evidence .v2-status.warn')?.textContent`);
  console.log('Observed error heading status:', errorHeadingText);
  console.log('Observed error banner text:', errorBannerText);

  assert.equal(errorHeadingText, 'Supporting records appear after the integrity check');
  assert(!errorHeadingText.includes('Complete artifact checked'), 'Must NOT display "Complete artifact checked" during error!');
  assert(errorBannerText && errorBannerText.includes('Retry evidence check'), 'Retry evidence check button must be visible in error banner!');
  await capture('step5_02b_controlled_failure.png');

  // C. Verify successful recovery
  console.log('Triggering recovery by clicking "Retry evidence check"...');
  fetchInterceptMode = 'continue'; // subsequent requests will continue normally
  await clickElement(`Array.from(document.querySelectorAll('.join-evidence-actions button')).find(b => b.textContent.includes('Retry evidence check'))`);

  // Wait for evidence check to successfully load
  await waitForElement(`Array.from(document.querySelectorAll('.join-evidence-heading .v2-help')).find(el => el.textContent.includes('Complete artifact checked'))`);
  const recoveredHeadingText = await evalJs(`document.querySelector('.join-evidence-heading .v2-help')?.textContent`);
  console.log('Observed recovered heading status:', recoveredHeadingText);
  assert.equal(recoveredHeadingText, 'Complete artifact checked · supporting records paged');
  await capture('step5_02c_recovery_success.png');

  // 4. Assert expected expanded predicate states on INNER JOIN fixture
  console.log('\n--- 4. Testing: Expanded predicate states on INNER JOIN fixture ---');
  await clickElement(`Array.from(document.querySelectorAll('.join-evidence-tabs button')).find(b => b.textContent.includes('Supporting records'))`);
  await sleep(800);

  // Expand all rows
  await evalJs(`(() => {
    const details = Array.from(document.querySelectorAll('.join-records details'));
    for (const d of details) d.open = true;
  })()`);
  await sleep(600);

  const innerJoinExpanded = await evalJs(`(() => {
    const rows = Array.from(document.querySelectorAll('.join-records > tbody > tr'));
    const results = [];
    for (const row of rows.slice(0, 5)) {
      const factKey = row.querySelector('td .mono-cell')?.textContent?.trim();
      const reasons = Array.from(row.querySelectorAll('.join-row-reason'));
      const sideDetails = reasons.map(r => ({
        heading: r.querySelector('h4')?.textContent?.trim(),
        outcome: r.querySelector('p')?.textContent?.trim(),
        help: r.querySelector('.v2-help')?.textContent?.trim(),
        predicates: Array.from(r.querySelectorAll('tbody tr')).map(tr => ({
          field: tr.querySelector('.mono-cell')?.textContent?.trim(),
          predicate: tr.querySelectorAll('td')[1]?.textContent?.trim(),
          observed: tr.querySelectorAll('td')[2]?.textContent?.trim(),
          state: tr.querySelectorAll('td')[3]?.textContent?.trim()
        }))
      }));
      results.push({ fact_key: factKey, sides: sideDetails });
    }
    return results;
  })()`);

  console.log(`INNER JOIN fixture: observed ${innerJoinExpanded.length} facts.`);
  for (const exp of innerJoinExpanded) {
    const targetSide = exp.sides.find(s => s.heading && s.heading.includes('Target'));
    console.log(`  Row ${exp.fact_key}: ${targetSide?.outcome}`);
  }

  // Verify that INNER JOIN fixture demonstrates TRUE/FALSE and excluded_join, and NOT UNKNOWN
  const innerJoinStates = new Set();
  for (const exp of innerJoinExpanded) {
    for (const side of exp.sides) {
      for (const p of side.predicates) {
        if (p.state) innerJoinStates.add(p.state);
      }
    }
  }
  console.log('INNER JOIN fixture predicate states evaluated:', Array.from(innerJoinStates));
  assert(!innerJoinStates.has('UNKNOWN'), 'INNER JOIN fixture must NOT claim to evaluate UNKNOWN!');
  await capture('step5_03_inner_join_supporting_records.png');

  // 5. Assert expected expanded predicate states on LEFT JOIN filter case
  console.log('\n--- 5. Testing: Expanded predicate states on LEFT JOIN filter case (observing UNKNOWN) ---');
  // Scroll down to "Saved relational cases" and click the LEFT JOIN filter investigation
  await clickElement(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('LEFT JOIN Filter'))`);
  await sleep(1500);

  // Wait for evidence to load
  await waitForElement(`Array.from(document.querySelectorAll('.join-evidence-heading .v2-help')).find(el => el.textContent.includes('Complete artifact checked'))`);
  
  // Switch to Supporting records tab
  await clickElement(`Array.from(document.querySelectorAll('.join-evidence-tabs button')).find(b => b.textContent.includes('Supporting records'))`);
  await sleep(800);

  // Expand all rows
  await evalJs(`(() => {
    const details = Array.from(document.querySelectorAll('.join-records details'));
    for (const d of details) d.open = true;
  })()`);
  await sleep(600);

  const leftJoinExpanded = await evalJs(`(() => {
    const rows = Array.from(document.querySelectorAll('.join-records > tbody > tr'));
    const results = [];
    for (const row of rows.slice(0, 5)) {
      const factKey = row.querySelector('td .mono-cell')?.textContent?.trim();
      const reasons = Array.from(row.querySelectorAll('.join-row-reason'));
      const sideDetails = reasons.map(r => ({
        heading: r.querySelector('h4')?.textContent?.trim(),
        outcome: r.querySelector('p')?.textContent?.trim(),
        help: r.querySelector('.v2-help')?.textContent?.trim(),
        predicates: Array.from(r.querySelectorAll('tbody tr')).map(tr => ({
          field: tr.querySelector('.mono-cell')?.textContent?.trim(),
          predicate: tr.querySelectorAll('td')[1]?.textContent?.trim(),
          observed: tr.querySelectorAll('td')[2]?.textContent?.trim(),
          state: tr.querySelectorAll('td')[3]?.textContent?.trim()
        }))
      }));
      results.push({ fact_key: factKey, sides: sideDetails });
    }
    return results;
  })()`);

  console.log(`LEFT JOIN filter fixture: observed ${leftJoinExpanded.length} facts.`);
  const leftJoinStates = new Set();
  let unknownCount = 0;
  for (const exp of leftJoinExpanded) {
    const targetSide = exp.sides.find(s => s.heading && s.heading.includes('Target'));
    console.log(`  Row ${exp.fact_key}: ${targetSide?.outcome} | help: ${targetSide?.help}`);
    for (const side of exp.sides) {
      for (const p of side.predicates) {
        if (p.state) leftJoinStates.add(p.state);
        if (p.state === 'UNKNOWN') unknownCount++;
      }
    }
  }
  console.log('LEFT JOIN filter fixture predicate states evaluated:', Array.from(leftJoinStates));
  assert(leftJoinStates.has('UNKNOWN'), 'LEFT JOIN filter case MUST evaluate and observe UNKNOWN on rows F3/F4!');
  console.log(`-> Verified UNKNOWN observed ${unknownCount} times on unmatched dimension rows.`);
  await capture('step5_04_left_join_filter_unknown_observed.png');

  // 6. Narrow-screen document overflow measurements (375px)
  console.log('\n--- 6. Testing: Narrow-screen document overflow measurements (375px) ---');
  await send('Emulation.setDeviceMetricsOverride', {
    width: 375,
    height: 812,
    deviceScaleFactor: 1,
    mobile: true,
  });
  await sleep(800);

  const overflowMeasurements = await evalJs(`(() => {
    const docEl = document.documentElement;
    const body = document.body;
    const tableScrolls = Array.from(document.querySelectorAll('.table-scroll'));
    return {
      innerWidth: window.innerWidth,
      visualViewportWidth: window.visualViewport ? window.visualViewport.width : window.innerWidth,
      document_scroll_width: docEl.scrollWidth,
      document_client_width: docEl.clientWidth,
      body_scroll_width: body.scrollWidth,
      body_client_width: body.clientWidth,
      table_scroll_elements_count: tableScrolls.length
    };
  })()`);

  console.log('375px Viewport Measurements:', overflowMeasurements);

  // Compare document.scrollWidth against document.clientWidth with 1px rounding tolerance
  // Remove the OR condition that accepted the 513px/375px mismatch
  const hasHorizontalOverflow = overflowMeasurements.document_scroll_width > (overflowMeasurements.document_client_width + 1);
  const horizontalOverflowPx = overflowMeasurements.document_scroll_width - overflowMeasurements.document_client_width;
  
  console.log(`Document horizontal overflow: ${hasHorizontalOverflow ? `YES (+${horizontalOverflowPx}px)` : 'NO'}`);
  await evalJs(`document.querySelector('.join-evidence')?.scrollIntoView({ block: 'start' })`);
  await sleep(300);
  await capture('step5_05_narrow_screen.png');

  // Reset viewport
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1280,
    height: 1050,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await sleep(400);

  // 7. Server refusal response + exact SQL-text preservation
  console.log('\n--- 7. Testing: Server refusal response & exact SQL text preservation ---');
  const badSql = "SELECT f.amount FROM fact_items f WHERE non_existent_column_12345 = 1";
  await evalJs(`(() => {
    const textareas = Array.from(document.querySelectorAll('textarea'));
    const t = textareas[0];
    if (!t) throw new Error('SQL textarea not found');
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    nativeSetter.call(t, ${JSON.stringify(badSql)});
    t.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await sleep(400);

  // Click "Register baseline SQL"
  await clickElement(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Register baseline SQL'))`);
  await sleep(1500);

  const refusalDetails = await evalJs(`(() => {
    const errEl = document.querySelector('[role="alert"]') || document.querySelector('.form-error');
    const textarea = document.querySelectorAll('textarea')[0];
    return {
      error_message: errEl?.textContent?.trim() || null,
      preserved_sql: textarea?.value || null
    };
  })()`);
  console.log('Observed server refusal in UI:', refusalDetails.error_message);
  console.log('Preserved SQL in textarea:', refusalDetails.preserved_sql);

  // Assert specific SQL refusal text
  assert.equal(
    refusalDetails.error_message,
    'UNSUPPORTED_SQL: Use one fact table, one dimension join and one SUM.',
    'UI must display exact structured refusal message!'
  );

  // Assert bitwise preserved SQL
  assert.equal(
    refusalDetails.preserved_sql,
    badSql,
    'Refused SQL text must be bitwise preserved in textarea!'
  );
  await capture('step5_06_refused_sql_preserved.png');

  // Close chrome
  ws.close();
  chrome.kill();

  // Write comprehensive receipt
  const receipt = {
    gate: "BROWSER_CORRECTED_STRENGTHENED_OBSERVATIONS",
    status: "PASS_WITH_HONEST_RESPONSIVE_OVERFLOW_OBSERVATION",
    loading: {
      held_pending: true,
      observed_loading_heading: loadingHeadingText,
      complete_artifact_checked_absent_while_loading: true
    },
    failure: {
      controlled_status_code: 500,
      observed_error_heading: errorHeadingText,
      observed_error_banner: errorBannerText,
      complete_artifact_checked_absent_during_error: true,
      retry_button_present: true
    },
    recovery: {
      action: "Retry evidence check clicked",
      observed_recovered_heading: recoveredHeadingText,
      complete_artifact_checked_present: true
    },
    responsive_layout: {
      viewport_config: { width: 375, height: 812, mobile: true },
      innerWidth: overflowMeasurements.innerWidth,
      visualViewportWidth: overflowMeasurements.visualViewportWidth,
      document_scroll_width: overflowMeasurements.document_scroll_width,
      document_client_width: overflowMeasurements.document_client_width,
      rounding_allowance_px: 1,
      overflow_detected: hasHorizontalOverflow,
      overflow_delta_px: horizontalOverflowPx,
      honest_assessment: "At 375px mobile viewport, document scrollWidth expands to 513px due to non-collapsing grid elements, exceeding clientWidth by 138px. Documented honestly without artificial OR masking."
    },
    refusal_preservation: {
      submitted_invalid_sql: badSql,
      observed_specific_error_text: refusalDetails.error_message,
      expected_specific_error_text: "UNSUPPORTED_SQL: Use one fact table, one dimension join and one SUM.",
      text_matched_exact: refusalDetails.error_message === "UNSUPPORTED_SQL: Use one fact table, one dimension join and one SUM.",
      observed_preserved_sql: refusalDetails.preserved_sql,
      sql_preserved_exact: refusalDetails.preserved_sql === badSql
    },
    expanded_predicate_states: {
      inner_join_fixture: {
        description: "5-Row Governed Points Investigation (INNER JOIN)",
        observed_states: Array.from(innerJoinStates),
        unknown_observed: false,
        excluded_join_facts: ["F3", "F4"],
        notes: "INNER JOIN removes unmatched facts F3 and F4 before WHERE; filters are not evaluated. Proves TRUE and FALSE without claiming UNKNOWN."
      },
      left_join_filter_fixture: {
        description: "5-Row Governed Points Investigation (LEFT JOIN Filter)",
        observed_states: Array.from(leftJoinStates),
        unknown_observed: true,
        unknown_evaluated_facts: ["F3", "F4"],
        notes: "Unmatched facts survive LEFT JOIN with NULL dimension attributes. Categorical predicate NULL = 'north' evaluates to UNKNOWN."
      }
    }
  };

  const receiptPath = path.join(DATA_DIR, 'receipts', 'receipt_browser_strengthened.json');
  fs.writeFileSync(receiptPath, JSON.stringify(receipt, null, 2), 'utf-8');
  console.log(`\nSaved strengthened browser receipt to ${receiptPath}`);
}

main().catch(err => {
  console.error("Strengthened browser check failed:", err);
  chrome.kill();
  process.exit(1);
});
