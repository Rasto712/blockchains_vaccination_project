/* AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed. */
/* The guided demo panel and the demo controls (tamper, jump to expiry). Loaded after app.js, whose helpers
   (el, button, badge, act, render, resultCard, outcomeCard, ...) it uses. The server runs and checks every
   step (ui/demo.py); this file only shows them, switches to each step's role and highlights its result. */
'use strict';

const DEMO_OPEN_KEY = 'vaccination-card-demo-open';
const START_WARNING = 'Start the guided demo? It deploys fresh contracts: every registration, grant and reward '
  + 'done so far is left behind. Local files and salts are kept.';
const EXPIRE_WARNING = 'Jump to the expiry of this consent? Node time only moves forward: this cannot be undone '
  + 'short of restarting the node, and every grant that ends earlier expires too.';

ui.demoOpen = readDemoOpen();
ui.expireChoice = '';
ui.lastStep = null; // the step whose results the panel shows in full

function readDemoOpen() {
  try {
    return localStorage.getItem(DEMO_OPEN_KEY) !== 'closed';
  } catch (error) {
    return true;
  }
}

function toggleDemo() {
  ui.demoOpen = !ui.demoOpen;
  try {
    localStorage.setItem(DEMO_OPEN_KEY, ui.demoOpen ? 'open' : 'closed');
  } catch (error) {
    // not remembered
  }
  render();
}

function renderDemoToggle() {
  return el('button', {
    type: 'button', class: ui.demoOpen ? 'role active' : 'role', 'aria-expanded': String(ui.demoOpen),
    'aria-controls': 'demo', 'data-focus': 'demo-toggle', onclick: toggleDemo,
  }, 'Guided demo');
}

async function runStep(path, confirmText, body) {
  const outcome = await act('demo', path, body || {}, confirmText);
  const entry = outcome && outcome.details && outcome.details.step;
  if (!entry) return;
  // the answer is newer than the snapshot on screen: take its step in at once, so the Next button never
  // names a step that has already run while the next poll is on its way
  const d = ui.snapshot && ui.snapshot.demo;
  if (d) {
    if (entry.step === 0) {
      d.entries = d.entries.map(() => null);
      d.summary = null;
    }
    d.entries[entry.step] = entry;
    if (entry.ok) {
      d.next = entry.step + 1;
      d.started = true;
      d.finished = d.next >= d.steps.length;
      d.stale = false;
    }
  }
  // show each action of the step in its role's own card as well, and mark it and its audit rows
  const keys = [];
  for (const item of entry.results) {
    if (item.key) {
      ui.outcomes[item.key] = { ...item.result, at: outcome.at };
      keys.push(item.key);
    }
  }
  ui.highlight = {
    keys,
    txs: entry.results.filter((item) => item.logged && item.result.tx).map((item) => item.result.tx.toLowerCase()),
  };
  ui.lastStep = entry.step;
  // keyboard focus goes to the button a presenter presses next
  ui.refocus = d && d.finished ? 'demo-start' : 'demo-next';
  if (entry.role !== ui.role) switchRole(entry.role);
  else render();
  ui.refocus = null;
}

function renderDemo() {
  if (!ui.demoOpen) return null;
  const s = ui.snapshot;
  if (!s || !s.demo) return el('section', { class: 'card' }, el('h2', { text: 'Guided demo' }), el('p', { class: 'muted', text: 'loading…' }));
  return [stepper(s), demoControls(s)];
}

function stepper(s) {
  const d = s.demo;
  const current = d.started && !d.finished && !d.stale ? d.next : null;
  const checking = d.finished && !d.summary;
  const failed = current !== null && d.entries[current] && !d.entries[current].ok;
  const run = d.entries.filter(Boolean);
  const shown = d.entries[ui.lastStep] || run[run.length - 1] || null;
  let status = badge('not started', 'muted');
  if (d.stale) status = badge('ended: contracts changed', 'warn');
  else if (checking) status = badge('checking…', 'info');
  else if (d.finished) status = badge('finished', d.summary.ok ? 'good' : 'bad');
  else if (d.started) status = badge(`step ${current} of ${d.total}`, 'info');

  const controls = el('div', { class: 'demo-buttons' },
    button(d.started ? 'Start again: fresh contracts' : 'Start guided demo: fresh contracts', 'demo',
      () => runStep('/api/demo/start', START_WARNING),
      { class: current !== null ? 'btn secondary' : 'btn', disabled: !s.node.reachable, focus: 'demo-start' }),
    current !== null ? button(`${failed ? 'Try again' : 'Next'}: step ${current}, ${d.steps[current].title} ▸`, 'demo',
      () => runStep('/api/demo/next', null, { step: current }), { focus: 'demo-next' }) : null);

  const list = el('ol', { class: 'steps' }, d.steps.map((step) => {
    const entry = d.entries[step.step];
    let state = 'todo';
    if (entry) state = entry.ok ? 'done' : 'failed';
    if (step.step === current && !entry) state = 'current';
    const mark = { done: '✔', failed: '✖', current: '▸', todo: '·' }[state];
    return el('li', { class: `step ${state}${shown && shown.step === step.step ? ' shown' : ''}` },
      el('span', { class: 'mark', 'aria-hidden': 'true', text: mark }),
      el('span', { class: 'sr-only', text: `${state}: ` }),
      el('span', { class: 'step-title', text: `${step.step}. ${step.title}` }),
      el('span', { class: 'chip', text: step.role }));
  }));

  const detail = shown ? stepDetail(shown, d, failed) : el('div', { class: 'step-detail' },
    el('p', {}, 'Plays the story of python -m integration.demo_workflow one click per step, on fresh contracts. '
      + 'Each step acts as its role, switches to that role\'s view and checks its result the way the scripted demo does.'),
    current !== null ? el('p', { class: 'muted', text: `next: ${d.steps[current].expected}` }) : null);

  const demoAnswer = ui.outcomes.demo && !(ui.outcomes.demo.details && ui.outcomes.demo.details.step) ? outcomeCard('demo') : null;
  const stale = d.stale ? el('p', { class: 'notice', text: 'The contracts this guided demo started on are gone or were '
    + 'replaced (a node restart, or a deploy elsewhere). Start again for fresh contracts.' }) : null;
  return el('section', { class: 'card guided', 'aria-labelledby': 'guided-title' },
    el('div', { class: 'demo-head' }, el('h2', { id: 'guided-title', text: 'Guided demo' }), status),
    controls, stale, demoAnswer,
    el('div', { class: 'demo-grid' }, list, detail),
    d.finished && d.summary && !d.stale ? summaryBox(d.summary, d) : null);
}

function stepDetail(entry, d, current) {
  const next = d.started && !d.finished && !d.stale && d.next !== entry.step ? d.steps[d.next] : null;
  return el('div', { class: 'step-detail' },
    el('h3', { text: `Step ${entry.step}: ${entry.title}` }),
    rows([
      ['expected', entry.expected],
      ['got', el('span', { class: entry.ok ? 'good-text' : 'bad-text' }, `${entry.ok ? '✔ ' : '✖ '}${entry.got}`)],
    ]),
    entry.results.map((item) => el('div', { class: 'step-action' },
      el('div', { class: 'step-action-label', text: item.label }),
      resultCard({ ...item.result, at: '' }, item.logged, false))),
    !entry.ok && current && !d.stale ? el('p', { class: 'notice', text: 'This step did not give the expected result. '
      + 'Try it again (it picks up where it stopped), or start again for fresh contracts.' }) : null,
    next ? el('p', { class: 'muted', text: `next, step ${next.step}: ${next.expected}` }) : null);
}

function summaryBox(summary, d) {
  if (!('audit_ok' in summary)) {
    // the final check could not run (the node stopped, say): it can run again
    return el('div', { class: 'outcome warn', role: 'status' },
      el('div', { class: 'outcome-head' }, badge('could not check', 'warn')),
      el('p', { class: 'message', text: summary.message }),
      button('Check again', 'demo', () => runStep('/api/demo/next', null, { step: d.next }), { focus: 'demo-check' }));
  }
  return el('div', { class: `outcome ${summary.ok ? 'good' : 'bad'}`, role: 'status' },
    el('div', { class: 'outcome-head' }, badge(summary.ok ? 'demo passed' : 'did not match', summary.ok ? 'good' : 'bad')),
    el('p', { class: 'message', text: summary.message }),
    summary.audit ? rows([
      ['audit of these requests', el('ol', { class: 'summary-audit' }, summary.audit.map((line) => el('li', { text: line })))],
      ['matches the scripted demo', yesNo(summary.audit_ok)],
      ['reward balances', Object.entries(summary.rewards).map(([label, value]) => `${label} ${value}`).join(' · ')],
      ['guardian 2, everyone else 0', yesNo(summary.rewards_ok)],
    ]) : null);
}

function demoControls(s) {
  const active = (s.consents || []).filter((cell) => cell.status === 'active');
  const choices = active.map((cell) => ({ value: `${cell.requester}|${cell.scope}`, label: `${cell.requester} · ${cell.scope} (until ${cell.expires_text})` }));
  if (!choices.some((choice) => choice.value === ui.expireChoice)) ui.expireChoice = choices.length ? choices[0].value : '';
  const select = el('select', {
    'aria-label': 'consent to expire', 'data-focus': 'expire-choice', disabled: !choices.length,
    onchange: (event) => { ui.expireChoice = event.target.value; },
  }, choices.length ? choices.map((choice) => el('option', { value: choice.value, selected: choice.value === ui.expireChoice, text: choice.label }))
    : el('option', { value: '', text: 'no active consent' }));
  const expire = () => {
    const [requester, scope] = ui.expireChoice.split('|');
    return act('expire', '/api/demo/expire', { requester, scope }, EXPIRE_WARNING);
  };
  return el('section', { class: 'card' },
    el('h2', { text: 'Demo controls' }),
    el('div', { class: 'control' },
      el('h3', { text: 'Tamper with a copy' }),
      el('p', {}, 'Copies the local record with one byte of the batch changed, sends the doctor\'s request with that copy, '
        + 'then deletes it. Expect HASH_MISMATCH while the doctor\'s grant is active. The original is never written.'),
      button('Tamper a copy → doctor request', 'tamper', () => act('tamper', '/api/demo/tamper'), { disabled: !s.deployment.deployed }),
      outcomeCard('tamper', { released: true })),
    el('div', { class: 'control' },
      el('h3', { text: 'Jump to the exact expiry' }),
      el('p', {}, 'Moves node time to the chosen consent\'s expiry and mines a block there. Node time only moves forward.'),
      el('div', { class: 'cell-actions' }, select,
        button('Jump to expiry', 'expire', expire, { disabled: !choices.length })),
      outcomeCard('expire')));
}
