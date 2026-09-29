/* AI assistance: parts of this file were written with Claude (Anthropic) and thoroughly reviewed. */
/* The page: it polls /api/state, draws the top bar, the setup checklist, the acting role's view and the
   audit timeline, and sends every action as a JSON POST. Everything that comes from the server is inserted
   as text, never as HTML. Demo mode: the chosen role goes with each action, and the server signs with that
   role's local Hardhat account. */
'use strict';

const POLL_MS = 2000;
// above the server's 10 s RPC timeout, so a stalled node is reported as the node, not as this server
const POLL_TIMEOUT_MS = 15000;
const ROLE_KEY = 'vaccination-card-role';
const ROLES = ['deployer', 'clinic', 'guardian', 'school', 'doctor'];
const ROLE_NOTES = {
  deployer: 'Deploys the three contracts. Never registers.',
  clinic: 'The trusted clinic: the only account that may attest a record.',
  guardian: 'Keeps the child\'s record and decides who may check it.',
  school: 'May check the measles status only, with the guardian\'s consent.',
  doctor: 'May see vaccine and date only, with the guardian\'s consent.',
};
const SCOPE_NOTES = { MEASLES_STATUS: 'status only', VACCINATION_SCHEDULE: 'vaccine and date only' };
const TONES = {
  allowed: 'good', ok: 'good', active: 'good',
  denied: 'bad', rejected: 'bad', revoked: 'bad',
  unavailable: 'warn', failed: 'warn', invalid: 'warn', forbidden: 'warn', expired: 'warn', refused: 'warn',
  mismatch: 'bad', pending: 'info', none: 'muted',
};
const DEPLOY_WARNING = 'Deploy fresh contracts? Every registration, grant and reward on the current contracts '
  + 'is left behind, and the story starts again. Local files and salts are kept.';

const ui = {
  role: readRole(),
  snapshot: null,
  serverMessage: 'connecting to the UI server…',
  record: null, // the guardian's card, only while the guardian's view is open
  recordFor: null, // local.ready when the card was fetched
  recordLoading: false,
  outcomes: {}, // action key -> last result
  pending: {}, // action key -> true while its request runs
  days: {}, // consent cell -> typed day count
  actionSeq: 0,
  shown: {}, // section -> signature of what it shows
  pollTimer: 0,
  deploymentKey: null, // the contract addresses the outcomes on screen belong to
  highlight: { keys: [], txs: [] }, // the outcome cards and audit rows of the last guided step
  refocus: null, // the control that sent the running action, focused again once it is enabled
};

/* ---------- helpers ---------- */

function readRole() {
  // a link such as /#school opens that role; otherwise the last one used
  const linked = window.location.hash.slice(1);
  if (ROLES.includes(linked)) return linked;
  try {
    const role = localStorage.getItem(ROLE_KEY);
    return ROLES.includes(role) ? role : 'guardian';
  } catch (error) {
    return 'guardian';
  }
}

function saveRole(role) {
  try {
    localStorage.setItem(ROLE_KEY, role);
  } catch (error) {
    // private window or blocked storage: the role just is not remembered
  }
}

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function short(hash) {
  return hash && hash.length > 22 ? `${hash.slice(0, 10)}…${hash.slice(-8)}` : (hash || '');
}

function hash(value, shorten) {
  return el('code', { class: 'hash', title: value, text: shorten ? short(value) : value });
}

function badge(text, tone) {
  return el('span', { class: `badge ${tone || TONES[text] || 'muted'}`, text });
}

function button(label, key, onclick, extra) {
  const busy = Boolean(ui.pending[key]);
  return el('button', {
    type: 'button', class: (extra && extra.class) || 'btn', disabled: busy || (extra && extra.disabled), onclick,
    'data-focus': (extra && extra.focus) || `button:${label}`,
  }, busy ? 'sending…' : label);
}

function card(title, ...children) {
  return el('section', { class: 'card' }, el('h2', { text: title }), ...children);
}

function rows(pairs) {
  return el('dl', { class: 'rows' }, pairs.filter(Boolean).map(([term, value]) => [
    el('dt', { text: term }), el('dd', {}, value),
  ]));
}

function yesNo(value, yes, no) {
  if (value === null || value === undefined) return el('span', { class: 'muted', text: '—' });
  return badge(value ? (yes || 'yes') : (no || 'no'), value ? 'good' : 'bad');
}

/* ---------- server ---------- */

async function api(method, path, body) {
  const options = { method, cache: 'no-store', headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (!data || typeof data !== 'object') {
    data = { status: 'failed', message: `failed: the UI server answered HTTP ${response.status}` };
  }
  return data;
}

async function poll() {
  clearTimeout(ui.pollTimer);
  const seq = ui.actionSeq;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), POLL_TIMEOUT_MS);
  try {
    const response = await fetch('/api/state', { cache: 'no-store', signal: controller.signal });
    const data = await response.json();
    // a snapshot that started before the last action finished is older than what the page shows
    if (seq === ui.actionSeq) {
      if (response.ok && data && data.roles) {
        forgetOtherDeployments(data);
        ui.snapshot = data;
        ui.serverMessage = '';
      } else {
        ui.serverMessage = (data && data.message) || `the UI server answered HTTP ${response.status}`;
      }
    }
  } catch (error) {
    ui.serverMessage = error.name === 'AbortError'
      ? `no answer within ${POLL_TIMEOUT_MS / 1000} s: the local node may be stalled`
      : 'the UI server is not answering: is python -m ui.server still running?';
  } finally {
    clearTimeout(timer);
  }
  render();
  schedulePoll();
}

function forgetOtherDeployments(snapshot) {
  // results from earlier contracts would contradict the fresh ones (a deploy here, or deploy_local --reset);
  // the deploy block tells two deployments apart even at the same addresses
  if (!snapshot.deployment.deployed) return;
  const key = JSON.stringify([snapshot.deployment.contracts, snapshot.deployment.deploy_block]);
  if (ui.deploymentKey !== null && ui.deploymentKey !== key) {
    for (const name of Object.keys(ui.outcomes)) {
      if (name !== 'deploy' && name !== 'setup') delete ui.outcomes[name];
    }
  }
  ui.deploymentKey = key;
}

function schedulePoll() {
  clearTimeout(ui.pollTimer);
  if (!document.hidden) ui.pollTimer = setTimeout(poll, POLL_MS);
}

async function act(key, path, body, confirmText) {
  if (ui.pending[key]) return null;
  if (confirmText && !window.confirm(confirmText)) return null;
  // the button is disabled while its request runs, which drops keyboard focus; show() gives it back afterwards
  ui.refocus = document.activeElement && document.activeElement.getAttribute('data-focus');
  ui.pending[key] = true;
  delete ui.outcomes[key];
  // a click of its own ends the highlight of the last guided step
  if (key !== 'demo') ui.highlight = { keys: [], txs: [] };
  render();
  let outcome;
  try {
    outcome = await api('POST', path, body || {});
  } catch (error) {
    outcome = { status: 'failed', message: 'the UI server did not answer', reason: '', tx: '', fields: {}, details: {} };
  }
  ui.pending[key] = false;
  // the time of the answer, so an older result is not mistaken for the current state
  outcome.at = new Date().toLocaleTimeString();
  ui.outcomes[key] = outcome;
  ui.actionSeq += 1;
  render();
  // a control that is gone after the answer does not take focus later
  ui.refocus = null;
  poll();
  return outcome;
}

async function fetchRecord() {
  if (ui.recordLoading || !ui.snapshot) return;
  ui.recordLoading = true;
  ui.recordFor = ui.snapshot.local.ready;
  let record;
  try {
    record = await api('GET', '/api/record?role=guardian');
  } catch (error) {
    record = { status: 'failed', message: 'the UI server did not answer' };
  }
  ui.recordLoading = false;
  // the role may have changed while the request ran; only the guardian's view keeps the card
  ui.record = ui.role === 'guardian' ? record : null;
  render();
}

function switchRole(role) {
  if (role === ui.role) return;
  ui.role = role;
  ui.record = null;
  ui.recordFor = null;
  saveRole(role);
  window.history.replaceState(null, '', `#${role}`);
  render();
}

/* ---------- rendering ---------- */

function render() {
  const s = ui.snapshot;
  show('roles', { role: ui.role }, renderRoles);
  show('acting', { role: ui.role, accounts: s && s.accounts }, renderActing);
  show('node-status', { s: s && [s.node, s.deployment], m: ui.serverMessage }, renderNodeStatus);
  // only what each section draws, so an unrelated change (a new audit row, node time) does not rebuild it
  const parts = s && [s.node.reachable, s.node.message, s.node.chain_id, s.deployment, s.local, s.accounts,
    s.registrations, s.record, s.consents, s.rewards];
  const common = { parts, stale: Boolean(ui.serverMessage), outcomes: ui.outcomes, pending: ui.pending, highlight: ui.highlight };
  // demo.js adds the guided demo panel and its toggle
  if (typeof renderDemo === 'function') {
    show('demo-toggle', { open: ui.demoOpen }, renderDemoToggle);
    show('demo', { ...common, open: ui.demoOpen, demo: s && s.demo, choice: ui.expireChoice, step: ui.lastStep }, renderDemo);
  }
  show('setup', common, renderSetup);
  show('view', { ...common, role: ui.role, record: ui.record }, renderView);
  show('audit', { s: s && [s.audit, s.deployment.deployed], stale: Boolean(ui.serverMessage), txs: ui.highlight.txs }, renderAudit);
  if (ui.role === 'guardian' && ui.snapshot && ui.recordFor !== ui.snapshot.local.ready) fetchRecord();
}

function show(id, data, build) {
  // rebuild a section only when what it shows changed, so typing and focus survive the 2-second poll
  const signature = JSON.stringify(data);
  if (ui.shown[id] === signature) return;
  ui.shown[id] = signature;
  const host = document.getElementById(id);
  // keep keyboard focus (and the caret in a days field) on the same control across the rebuild
  const active = host.contains(document.activeElement) ? document.activeElement : null;
  const focusKey = active && active.getAttribute('data-focus');
  const caret = active && active.tagName === 'INPUT' ? [active.selectionStart, active.selectionEnd] : null;
  host.replaceChildren(...[build()].flat(Infinity).filter(Boolean));
  // the focused control, or else the one an action asked for (it may have been disabled, or replaced)
  const find = (key) => key && [...host.querySelectorAll('[data-focus]')].find((node) => node.getAttribute('data-focus') === key && !node.disabled);
  let again = find(focusKey);
  if (!again && ui.refocus) {
    again = find(ui.refocus);
    if (again) ui.refocus = null;
  }
  if (again) {
    again.focus();
    if (caret && again.getAttribute('data-focus') === focusKey) {
      try {
        again.setSelectionRange(caret[0], caret[1]);
      } catch (error) {
        // number fields have no selection in some browsers
      }
    }
  }
}

function renderRoles() {
  return ROLES.map((role) => el('button', {
    type: 'button', class: role === ui.role ? 'role active' : 'role', 'aria-pressed': String(role === ui.role),
    'data-focus': `role:${role}`, onclick: () => switchRole(role),
  }, role));
}

function renderActing() {
  const address = ui.snapshot && ui.snapshot.accounts[ui.role];
  return [el('span', { class: 'muted', text: 'acting as ' }), el('strong', { text: ui.role }),
    address ? [' · ', hash(address)] : null];
}

function renderNodeStatus() {
  if (ui.serverMessage) return [el('span', { class: 'dot bad', 'aria-hidden': 'true' }), ui.serverMessage];
  const { node, deployment } = ui.snapshot;
  if (!node.reachable) return [el('span', { class: 'dot bad', 'aria-hidden': 'true' }), node.message];
  return [
    el('span', { class: 'dot good', 'aria-hidden': 'true' }),
    `node reachable · chain ${node.chain_id} · `,
    deployment.deployed ? 'deployed' : el('strong', { class: 'warn-text', text: 'not deployed' }),
    ` · node time ${node.time_text}`,
  ];
}

function outcomeCard(key, options) {
  if (ui.pending[key]) {
    return el('div', { class: 'outcome info', role: 'status' }, badge('pending', 'info'), ' sending… waiting for the receipt');
  }
  const result = ui.outcomes[key];
  if (!result) return null;
  return resultCard(result, Boolean(options && options.released), ui.highlight.keys.includes(key));
}

function resultCard(result, access, highlighted) {
  // one action's answer: status, reason, message and tx hash, and for a school or doctor request what it released
  const tone = TONES[result.status] || 'warn';
  // the denial text names the transaction; it is shown on its own line below
  const message = (result.message || '').replace(/ \(logged on-chain in 0x[0-9a-fA-F]+\)$/, '');
  const details = result.details || {};
  return el('div', { class: `outcome ${tone}${highlighted ? ' highlight' : ''}`, role: 'status' },
    el('div', { class: 'outcome-head' }, badge(result.status, tone),
      result.reason && result.status !== 'allowed' ? el('span', { class: 'reason', text: result.reason }) : null,
      result.at ? el('span', { class: 'outcome-time', text: `at ${result.at}` }) : null),
    el('p', { class: 'message', text: message }),
    result.tx ? el('p', { class: 'tx' }, access ? 'logged on-chain in tx ' : 'tx ', hash(result.tx)) : null,
    access ? released(result) : null,
    'copy_deleted' in details ? rows([
      ['tampered copy deleted', yesNo(details.copy_deleted)],
      ['original still matches on-chain', yesNo(details.original_matches)],
    ]) : null,
    details.note ? el('p', { class: 'muted', text: details.note }) : null,
    details.contracts ? rows(Object.entries(details.contracts).map(([name, address]) => [name, hash(address)])) : null);
}

function released(result) {
  const fields = result.fields || {};
  if (result.status !== 'allowed' || !Object.keys(fields).length) {
    return el('p', { class: 'released' }, el('strong', { text: 'released: ' }), 'nothing');
  }
  const list = fields.vaccinations
    ? el('table', { class: 'fields' }, el('thead', {}, el('tr', {}, el('th', { text: 'vaccine' }), el('th', { text: 'date' }))),
      el('tbody', {}, fields.vaccinations.map((event) => el('tr', {}, el('td', { text: event.vaccine }), el('td', { text: event.date })))))
    : el('code', { class: 'json', text: JSON.stringify(fields) });
  return el('div', { class: 'released' }, el('strong', { text: 'released: ' }), list);
}

/* ---------- setup ---------- */

function renderSetup() {
  const s = ui.snapshot;
  if (!s) return null;
  const { node, deployment, local, registrations, record } = s;
  const items = [
    {
      done: node.reachable, title: 'node reachable',
      detail: node.reachable ? `chain ${node.chain_id}` : node.message,
    },
    {
      done: deployment.deployed, title: 'contracts deployed',
      detail: deployment.deployed ? 'the three contracts are verified on this node' : (deployment.message || 'needs the node'),
      action: [deployment.deployed ? null : button('Deploy fresh contracts', 'deploy', () => act('deploy', '/api/deploy'),
        { disabled: !node.reachable }), outcomeCard('deploy')],
    },
    {
      done: local.ready, title: 'local files and salts',
      detail: local.ready ? 'identities, record and salts are in place' : local.message,
      action: [local.ready ? null : button('Create local files', 'setup', () => act('setup', '/api/setup')), outcomeCard('setup')],
    },
    {
      done: s.registering.every((label) => registrations[label] && registrations[label].registered),
      title: 'guardian, school and doctor registered',
      detail: deployment.deployed ? s.registering.map((label) => `${label}: ${registrations[label] && registrations[label].registered ? 'yes' : 'no'}`).join(' · ') : '',
      action: s.registering.map((label) => [
        deployment.deployed && !(registrations[label] && registrations[label].registered)
          ? button(`Register as ${label}`, `register:${label}`, () => act(`register:${label}`, '/api/register', { role: label }))
          : null,
        outcomeCard(`register:${label}`)]),
    },
    {
      done: Boolean(record && record.attested), title: 'record attested by the clinic',
      detail: attestedText(s),
      action: [deployment.deployed && !(record && record.attested) ? button('Attest as clinic', 'attest:clinic',
        () => act('attest:clinic', '/api/attest', { role: 'clinic' })) : null, outcomeCard('attest:clinic')],
    },
  ];
  for (const item of items) {
    if (item.action && !item.action.flat(Infinity).some(Boolean)) item.action = null;
  }
  const allDone = items.every((item) => item.done);
  return el('details', { class: 'card setup', open: !allDone },
    el('summary', {}, el('h2', { text: allDone ? 'Setup: every step done' : 'Setup' })),
    el('ul', { class: 'checklist' }, items.map((item) => el('li', { class: item.done ? 'done' : 'todo' },
      el('span', { class: 'mark', 'aria-hidden': 'true', text: item.done ? '✔' : '✖' }),
      el('span', { class: 'sr-only', text: item.done ? 'done: ' : 'not done: ' }),
      el('div', { class: 'item' },
        el('div', {}, el('strong', { text: item.title }), item.detail ? el('span', { class: 'detail', text: ` ${item.detail}` }) : null),
        item.action ? el('div', { class: 'item-action' }, item.action) : null)))));
}

function attestedText(s) {
  const { record, deployment, local } = s;
  if (!deployment.deployed) return '';
  if (!record || !record.attested) return 'not attested yet';
  if (record.matches === null) return `cannot compare with the local record: ${local.message}`;
  return record.matches ? 'matches the local record' : 'does not match the local record';
}

/* ---------- role views ---------- */

function renderView() {
  const s = ui.snapshot;
  if (!s) return el('p', { class: 'muted', text: 'loading…' });
  const views = { deployer: deployerView, clinic: clinicView, guardian: guardianView, school: requesterView, doctor: requesterView };
  return [
    el('div', { class: 'view-head' }, el('h1', { text: ui.role }), el('p', { class: 'muted', text: ROLE_NOTES[ui.role] })),
    ui.serverMessage ? el('p', { class: 'notice', text: `Not current: ${ui.serverMessage}. This is the last state the page received.` }) : null,
    ...views[ui.role](s),
  ];
}

function needsDeployment(s) {
  if (s.deployment.deployed) return null;
  return el('p', { class: 'notice', text: s.node.reachable ? s.deployment.message : s.node.message });
}

function registrationCard(s, label) {
  const registration = s.registrations[label];
  const local = s.local.identity_hashes[label];
  return card('Registration',
    rows([
      ['registered on-chain', registration ? yesNo(registration.registered) : el('span', { class: 'muted', text: '—' })],
      registration && registration.registered ? ['on-chain identity hash', hash(registration.identity_hash)] : null,
      ['local identity hash', local ? hash(local) : el('span', { class: 'muted', text: s.local.message || '—' })],
      registration && registration.registered ? ['matches the local hash', yesNo(registration.matches)] : null,
    ]),
    el('p', { class: 'muted', text: 'Only the salted hash goes on-chain; the identity and its salt stay in local files.' }),
    button(`Register as ${label}`, `register:${label}`, () => act(`register:${label}`, '/api/register', { role: label })),
    outcomeCard(`register:${label}`));
}

function attestCard(s) {
  const record = s.record;
  return card('Attest the guardian\'s record',
    el('p', {}, `Sends registerVaccination from this account (${ui.role}). Only the trusted clinic may attest; `
      + 'any other account is rejected with NotTrustedClinic.'),
    rows([
      ['local commitment', s.local.commitment ? hash(s.local.commitment) : el('span', { class: 'muted', text: s.local.message || '—' })],
      ['attested on-chain', record ? yesNo(record.attested) : el('span', { class: 'muted', text: '—' })],
    ]),
    // one outcome per role, so one role's result never shows up in another role's card
    button(`Attest as ${ui.role}`, `attest:${ui.role}`, () => act(`attest:${ui.role}`, '/api/attest', { role: ui.role })),
    outcomeCard(`attest:${ui.role}`));
}

function deployerView(s) {
  const { deployment, node } = s;
  return [
    card('Deployment',
      rows([
        ['status', deployment.deployed ? badge('deployed', 'good') : el('span', { text: deployment.message || node.message })],
        node.reachable ? ['chain', String(node.chain_id)] : null,
        ...Object.entries(deployment.contracts).map(([name, address]) => [name, hash(address)]),
      ]),
      button('Deploy fresh contracts', 'deploy', () => act('deploy', '/api/deploy', {}, deployment.deployed ? DEPLOY_WARNING : null),
        { disabled: !node.reachable }),
      outcomeCard('deploy')),
    attestCard(s),
  ];
}

function clinicView(s) {
  return [needsDeployment(s), attestCard(s)];
}

function guardianView(s) {
  const record = s.record;
  return [
    needsDeployment(s),
    consentCard(s),
    rewardsCard(s),
    registrationCard(s, 'guardian'),
    card('Record commitment',
      rows([
        ['local commitment', s.local.commitment ? hash(s.local.commitment) : el('span', { class: 'muted', text: s.local.message })],
        ['on-chain commitment', record && record.attested ? hash(record.onchain)
          : el('span', { class: 'muted', text: record ? 'not attested yet' : 'unknown: no deployment to read' })],
        ['they match', record && record.attested && record.matches === null
          ? el('span', { class: 'muted', text: `cannot compare: ${s.local.message}` })
          : (record ? yesNo(record.matches) : el('span', { class: 'muted', text: '—' }))],
      ]),
      el('p', { class: 'muted', text: 'SHA-256 of a private salt and the exact file bytes. The salt never leaves this computer.' })),
    localRecordCard(),
    attestCard(s),
  ];
}

function localRecordCard() {
  const record = ui.record;
  let body;
  if (!record) body = el('p', { class: 'muted', text: 'loading…' });
  else if (record.status !== 'ok') body = el('p', { class: 'notice', text: record.message });
  else {
    const data = record.details.card;
    const event = data.vaccinations[0];
    body = rows([
      ['child ID', data.child_id], ['vaccine', event.vaccine], ['covers', event.covers.join(', ')],
      ['date', event.date], ['clinic', event.clinic], ['batch', event.batch],
    ]);
  }
  return card('My local record',
    el('p', { class: 'muted', text: 'Only the guardian\'s own view shows the card. The school and the doctor only ever get what disclosure releases.' }),
    body);
}

function consentCard(s) {
  const busy = Boolean(ui.pending.consent);
  const header = el('tr', {}, el('th', { text: 'requester' }), s.scopes.map((scope) => el('th', {},
    el('span', { text: scope }), el('span', { class: 'th-note', text: SCOPE_NOTES[scope] || '' }))));
  const body = s.requesters.map((requester) => el('tr', {}, el('th', { text: requester }), s.scopes.map((scope) => {
    const cell = s.consents.find((item) => item.requester === requester && item.scope === scope);
    return el('td', {}, consentCell(requester, scope, cell, busy));
  })));
  return card('Consent',
    el('p', { class: 'muted', text: 'Each grant is one requester and one scope for 1-365 days. The first grant of each pair mints one reward unit to you.' }),
    s.deployment.deployed ? el('table', { class: 'matrix' }, el('thead', {}, header), el('tbody', {}, body)) : null,
    outcomeCard('consent'));
}

function consentCell(requester, scope, cell, busy) {
  const key = `${requester}:${scope}`;
  const status = cell ? cell.status : 'none';
  let when = '';
  if (status === 'active') when = `until ${cell.expires_text}`;
  if (status === 'expired') when = `since ${cell.expires_text}`;
  if (status === 'revoked') when = `was until ${cell.expires_text}`;
  const input = el('input', {
    type: 'number', min: '1', max: '365', step: '1', value: ui.days[key] || '30', 'aria-label': `days for ${requester} ${scope}`,
    'data-focus': `days:${key}`,
    oninput: (event) => { ui.days[key] = event.target.value; },
  });
  return [
    el('div', { class: 'cell-state' }, badge(status), when ? el('span', { class: 'cell-when', text: when }) : null),
    el('div', { class: 'cell-actions' },
      input, el('span', { class: 'muted', text: 'days' }),
      el('button', {
        type: 'button', class: 'btn', disabled: busy, 'data-focus': `grant:${key}`,
        onclick: () => act('consent', '/api/grant', { role: 'guardian', requester, scope, days: Number(input.value) }),
      }, 'Grant'),
      el('button', {
        type: 'button', class: 'btn secondary', disabled: busy, 'data-focus': `revoke:${key}`,
        onclick: () => act('consent', '/api/revoke', { role: 'guardian', requester, scope }),
      }, 'Revoke')),
  ];
}

function rewardsCard(s) {
  const own = s.rewards.guardian;
  return card('Rewards',
    el('p', { class: 'big' }, own === undefined ? '—' : `${own} unit${own === 1 ? '' : 's'}`),
    el('p', { class: 'muted', text: 'One non-transferable unit per first grant of each requester and scope. A balance never authorizes access.' }),
    Object.keys(s.rewards).length ? rows(Object.entries(s.rewards).filter(([label]) => label !== 'guardian')
      .map(([label, balance]) => [label, String(balance)])) : null);
}

function requesterView(s) {
  const school = ui.role === 'school';
  const key = ui.role;
  const path = school ? '/api/school-check' : '/api/doctor-view';
  return [
    needsDeployment(s),
    card(school ? 'Check measles status' : 'View vaccination schedule',
      el('p', {}, school
        ? 'Signed as the school with scope MEASLES_STATUS. Allowed releases only the status: no vaccine, date, clinic, batch or child ID.'
        : 'Signed as the doctor with scope VACCINATION_SCHEDULE. Allowed releases only vaccine and date: no child ID, coverage, clinic or batch.'),
      el('p', { class: 'muted', text: 'Every request is logged on-chain, allowed or denied. A denial releases nothing.' }),
      button(school ? 'Check measles status' : 'View vaccination schedule', key, () => act(key, path)),
      outcomeCard(key, { released: true })),
    registrationCard(s, ui.role),
    attestCard(s),
  ];
}

/* ---------- audit ---------- */

function renderAudit() {
  const s = ui.snapshot;
  const head = el('div', { class: 'audit-head' }, el('h2', { text: 'Audit timeline' }),
    s && s.deployment.deployed ? badge(`${s.audit.length}`, 'muted') : null);
  const note = el('p', { class: 'muted', text: 'Every AccessAttempt on-chain, newest first: allowed and denied alike, never deleted.' });
  if (!s || !s.deployment.deployed) return [head, note, el('p', { class: 'muted', text: 'no deployment to read' })];
  if (!s.audit.length) return [head, note, el('p', { class: 'muted', text: 'no access attempts yet' })];
  const marked = ui.highlight.txs;
  return [head, note, el('ol', { class: 'audit-list' }, s.audit.map((event) => el('li', {
    class: `${event.allowed ? 'allowed' : 'denied'}${marked.includes(event.tx.toLowerCase()) ? ' highlight' : ''}`,
  },
    el('div', { class: 'audit-top' }, el('span', { class: 'audit-time', text: event.time_text }),
      badge(event.allowed ? 'allowed' : 'denied')),
    el('div', { class: 'audit-main' }, el('strong', { text: event.requester }), ` ${event.scope} `,
      event.allowed ? null : el('span', { class: 'reason', text: event.reason })),
    el('div', { class: 'audit-tx' }, 'tx ', hash(event.tx, true)))))];
}

/* ---------- start ---------- */

document.addEventListener('visibilitychange', () => {
  if (document.hidden) clearTimeout(ui.pollTimer);
  else poll();
});
// deferred scripts (this one, then demo.js) have all run before DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
  render();
  poll();
});
