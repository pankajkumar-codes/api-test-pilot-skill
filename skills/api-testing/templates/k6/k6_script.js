// k6 __PROFILE__ test. Generated file, regenerate instead of editing.
// Rerun: __RERUN__
//
// SAFETY: this script refuses to start unless the target passes the gate below (non-production + authorized,
// or a loopback address). It never contains credentials: tokens come from environment variables.
// Do NOT run k6 with --http-debug: it prints request headers, including Authorization.
import http from 'k6/http';
import { check, sleep } from 'k6';
import encoding from 'k6/encoding';
import exec from 'k6/execution';

// All spec-derived data lives in this JSON document, parsed (never evaluated) at start-up.
const CONFIG = JSON.parse(__CONFIG_JSON__);

// ---- gate begin (mirrors scripts/safety_gate.py; tests/test_nonfunctional.py checks parity) ----
const NON_PROD_ENVS = ['local', 'dev', 'development', 'test', 'testing', 'staging', 'stage', 'qa', 'sandbox'];
const PROD_ENVS = ['prod', 'production', 'live'];
const TRUTHY = ['1', 'true', 'yes', 'y', 'on'];
const URL_RE = /^(https?):\/\/(\[[0-9A-Fa-f:]+\]|[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)(?::([0-9]{1,5}))?([\/?#][^\s\\\x00-\x1f]*)?$/;
const LABEL_RE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/;
const OCTET = '(?:0|[1-9][0-9]{0,2})';
const IPV4_RE = new RegExp('^(' + OCTET + ')\\.(' + OCTET + ')\\.(' + OCTET + ')\\.(' + OCTET + ')$');

function envOf(name) {
  const v = __ENV[name];
  return v === undefined || v === null ? '' : String(v);
}

function targetHost(url) {
  const m = URL_RE.exec(url || '');
  if (!m) return null;
  if (m[3] !== undefined && Number(m[3]) > 65535) return null;
  return m[2].toLowerCase().replace(/^\[|\]$/g, '');
}

function isLocalHost(host) {
  const h = (host || '').toLowerCase().replace(/\.+$/, '');
  if (!h) return false;
  if (h === '::1') return true;
  const m = IPV4_RE.exec(h);
  if (m) {
    const o = m.slice(1).map(Number);
    return o.every((x) => x <= 255) && o[0] === 127;
  }
  if (h.indexOf(':') !== -1) return false;
  const labels = h.split('.');
  if (!labels.every((l) => LABEL_RE.test(l))) return false;
  return (labels.length === 1 && labels[0] === 'localhost') || (labels.length >= 2 && labels[labels.length - 1] === 'localhost');
}

function gateReason(baseUrl) {
  const host = targetHost(baseUrl);
  if (host === null) {
    return 'safety gate: the target must be a plain http(s) URL (no credentials, backslashes, spaces or ambiguous host)';
  }
  const tenv = envOf('APITEST_TARGET_ENV').trim().toLowerCase();
  if (PROD_ENVS.indexOf(tenv) !== -1) {
    return 'safety gate: APITEST_TARGET_ENV says production; load, stress, spike, soak, chaos and fuzz runs never target production';
  }
  if (isLocalHost(host)) return null;
  if (NON_PROD_ENVS.indexOf(tenv) === -1) {
    return 'safety gate: confirm the target is non-production: set APITEST_TARGET_ENV to one of ' + NON_PROD_ENVS.slice().sort().join('/');
  }
  if (TRUTHY.indexOf(envOf('APITEST_AUTHORIZED').trim().toLowerCase()) === -1) {
    return 'safety gate: confirm you own or are authorized to test this target: set APITEST_AUTHORIZED=yes';
  }
  return null;
}
// ---- gate end ----

function truthy(name) {
  return TRUTHY.indexOf(envOf(name).trim().toLowerCase()) !== -1;
}

// ---- start-up checks: any failure aborts the run before a single request is sent ----
const BASE = (envOf('APITEST_BASE_URL') || CONFIG.defaultBaseUrl || '').replace(/\/+$/, '');
if (!BASE) {
  throw new Error('Set APITEST_BASE_URL to the target (the spec declares no usable server URL).');
}
const gate = gateReason(BASE);
if (gate) {
  throw new Error(gate);
}

// Caps can be lowered at run time, never raised above what the generator baked in.
function capFromEnv(name, baked) {
  const raw = envOf(name);
  if (raw === '') return baked;
  const n = Number(raw);
  if (!isFinite(n) || n < 1) throw new Error(name + ' must be a positive number');
  return Math.min(baked, n);
}
const MAX_RPS = capFromEnv('APITEST_MAX_RPS', CONFIG.caps.maxRps);
const MAX_VUS = capFromEnv('APITEST_MAX_VUS', CONFIG.caps.maxVus);
if (CONFIG.peakRps > MAX_RPS) {
  throw new Error('peak ' + CONFIG.peakRps + ' req/s exceeds the cap of ' + MAX_RPS + ' (APITEST_MAX_RPS); regenerate with a lower --rate');
}

// ---- values from files (same format as the pytest suite) ----
// APITEST_IDS_FILE      {"path": {"business_id": "..."}}                     ids for path parameters
// APITEST_EXAMPLES_FILE {"operationId.field": value, "*.field": value}      valid values for required query and body fields
// k6 can read files only at start-up, so both are read here, once, and never echoed.
function readJsonFile(name) {
  const path = envOf(name).trim();
  if (path === '') return null;
  let data;
  try {
    data = JSON.parse(open(path));
  } catch (e) {
    throw new Error(name + ' must name a readable JSON file');
  }
  if (data === null || typeof data !== 'object' || Array.isArray(data)) {
    throw new Error(name + ' must hold a JSON object');
  }
  return data;
}
const IDS_FILE = readJsonFile('APITEST_IDS_FILE');
const EXAMPLES_FILE = readJsonFile('APITEST_EXAMPLES_FILE');

function idFromFile(name) {
  const section = IDS_FILE && IDS_FILE.path;
  if (!section || typeof section !== 'object' || !Object.prototype.hasOwnProperty.call(section, name)) return '';
  const v = section[name];
  return v === null || v === undefined ? '' : String(v);
}

// Example values for one operation: its own ("opId.field") and the shared ones ("*.field"); its own win.
function examplesFor(opId) {
  const found = new Map();
  if (!EXAMPLES_FILE) return found;
  for (const key of Object.keys(EXAMPLES_FILE)) {
    const dot = key.indexOf('.');
    if (dot < 1 || dot === key.length - 1) continue;
    const op = key.slice(0, dot);
    const field = key.slice(dot + 1);
    if (op === '*' && !found.has(field)) found.set(field, EXAMPLES_FILE[key]);
    if (op === opId) found.set(field, EXAMPLES_FILE[key]);
  }
  return found;
}

// ---- endpoints: only requests that are safe to send and fully configured ----
function applyScheme(name, headers, query, cookies, meta) {
  const sch = CONFIG.schemes[name];
  if (!sch) return [name];
  const names = sch.env || [];
  const vals = names.map(envOf);
  if (names.length === 0 || vals.some((v) => v === '')) return names.length ? names : [name];
  const kind = (sch.scheme || '').toLowerCase();
  if (sch.type === 'http' && kind === 'basic') {
    headers['Authorization'] = 'Basic ' + encoding.b64encode(vals[0] + ':' + (vals.length > 1 ? vals[1] : ''));
  } else if (sch.type === 'apiKey') {
    const target = sch.in === 'query' ? query : sch.in === 'cookie' ? cookies : headers;
    target[sch.name || 'X-API-Key'] = vals[0];
  } else {
    const word = kind ? kind.charAt(0).toUpperCase() + kind.slice(1) : 'Bearer';
    headers['Authorization'] = word + ' ' + vals[0];
    meta.refresh = word; // this is the header a refreshed token replaces
  }
  return [];
}

function credentialsFor(sec) {
  let missing = [];
  for (const alt of sec) {
    const headers = {};
    const query = {};
    const cookies = {};
    const gaps = [];
    const meta = { refresh: null };
    for (const s of alt) {
      for (const g of applyScheme(s, headers, query, cookies, meta)) gaps.push(g);
    }
    if (gaps.length === 0) return { headers, query, cookies, refresh: meta.refresh };
    if (missing.length === 0) missing = gaps;
  }
  return { missing };
}

function encodeQuery(q) {
  return Object.keys(q)
    .map((k) => encodeURIComponent(k) + '=' + encodeURIComponent(String(q[k])))
    .join('&');
}

// GraphQL ids come from the environment: {"$env": ["APITEST_GQL_<OP>_<ARG>", "APITEST_GQL_<ARG>"]}.
function resolveVars(value, missing) {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    if (Array.isArray(value['$env'])) {
      const hit = value['$env'].map(envOf).find((v) => v !== '');
      if (hit === undefined) missing.push(value['$env'].join(' / '));
      return hit === undefined ? null : hit;
    }
    const out = {};
    for (const k of Object.keys(value)) {
      Object.defineProperty(out, k, { value: resolveVars(value[k], missing), enumerable: true, writable: true, configurable: true });
    }
    return out;
  }
  if (Array.isArray(value)) return value.map((v) => resolveVars(v, missing));
  return value;
}

// Endpoints that are left out, with the reason and the variable(s) that would include them. Printed once from setup().
const SKIPPED = [];
function skip(notes, ep, reason, setVars) {
  notes.push(ep.tag + ': ' + reason);
  SKIPPED.push({ endpoint: ep.tag, reason: reason, set: setVars || [] });
  return null;
}

function build(ep, notes) {
  if (ep.write && (truthy('APITEST_READ_ONLY') || !(CONFIG.includeWrites && truthy('APITEST_ALLOW_WRITES')))) {
    return skip(notes, ep, truthy('APITEST_READ_ONLY') ? 'write method skipped (APITEST_READ_ONLY=yes)' : 'write method skipped (regenerate with --include-writes and set APITEST_ALLOW_WRITES=yes)',
      truthy('APITEST_READ_ONLY') ? [] : ['APITEST_ALLOW_WRITES']);
  }
  let path = ep.path;
  for (const p of ep.pathParams) {
    const val = envOf(p.env) || idFromFile(p.name) || (p.default === null || p.default === undefined ? '' : String(p.default));
    if (val === '') {
      return skip(notes, ep, 'skipped, set ' + p.env + ' to an existing id', [p.env]);
    }
    path = path.split('{' + p.name + '}').join(encodeURIComponent(val));
  }
  const headers = Object.assign({}, ep.headers, { 'X-Load-Test': 'api-test-pilot' });
  const query = Object.assign({}, ep.query);
  const given = ep.graphql || !ep.opId ? new Map() : examplesFor(ep.opId);
  for (const k of Object.keys(query)) {
    if (given.has(k)) query[k] = given.get(k);
  }
  const cookies = {};
  let refresh = null;
  const required = ep.sec.length > 0;
  const wanted = required ? ep.sec : ep.softSec || [];
  if (wanted.length > 0) {
    const cred = credentialsFor(wanted);
    if (cred.missing) {
      if (required) {
        return skip(notes, ep, 'skipped, credentials not configured: set ' + cred.missing.join(', '), cred.missing);
      }
      // optional credentials (schema declares no per-operation auth): send them only when configured
    } else {
      Object.assign(headers, cred.headers);
      Object.assign(query, cred.query);
      Object.assign(cookies, cred.cookies);
      refresh = cred.refresh;
    }
  }
  let body = null;
  let bodyValue = ep.body;
  if (ep.graphql && bodyValue) {
    const missing = [];
    const variables = resolveVars(bodyValue.variables || {}, missing);
    if (missing.length > 0) {
      return skip(notes, ep, 'skipped, set one of ' + missing.join(', ') + ' to an existing id', missing);
    }
    bodyValue = { query: bodyValue.query, variables: variables, operationName: bodyValue.operationName };
  }
  if (given.size > 0 && bodyValue !== null && typeof bodyValue === 'object' && !Array.isArray(bodyValue)) {
    const copy = Object.assign({}, bodyValue);
    for (const k of Object.keys(copy)) {
      if (given.has(k)) Object.defineProperty(copy, k, { value: given.get(k), enumerable: true, writable: true, configurable: true });
    }
    bodyValue = copy;
  }
  if (bodyValue !== null && bodyValue !== undefined) {
    if ((ep.contentType || '').indexOf('x-www-form-urlencoded') !== -1) {
      body = bodyValue;
    } else if (typeof bodyValue === 'string') {
      body = bodyValue;
      if (ep.contentType) headers['Content-Type'] = ep.contentType;
    } else {
      body = JSON.stringify(bodyValue);
      headers['Content-Type'] = ep.contentType || 'application/json';
    }
  }
  const qs = encodeQuery(query);
  return {
    tag: ep.tag,
    method: ep.method,
    url: BASE + path + (qs ? '?' + qs : ''),
    body: body,
    params: {
      headers: headers,
      cookies: cookies,
      tags: { endpoint: ep.tag, name: ep.tag },
      timeout: CONFIG.timeout,
      redirects: 0,
      responseType: ep.graphql ? 'text' : 'none', // bodies are only kept when they must be inspected
    },
    ok: ep.ok,
    graphql: ep.graphql === true,
    refresh: refresh, // "Bearer" etc. when a refreshed token should replace the Authorization header
  };
}

const NOTES = [];
const ACTIVE = [];
for (const ep of CONFIG.endpoints) {
  const built = build(ep, NOTES);
  if (built) ACTIVE.push(built);
}
for (const n of NOTES) {
  console.warn('[api-test-pilot] ' + n); // env var names only, no values
}
if (ACTIVE.length === 0) {
  throw new Error('No endpoint can be tested with the current configuration (see the warnings above).');
}

function statusOk(ep, res) {
  const status = res.status;
  if (ep.ok.length > 0 ? ep.ok.indexOf(status) === -1 : !(status >= 200 && status < 400)) return false;
  if (ep.graphql) {
    // GraphQL reports failures as HTTP 200 + an errors array.
    try {
      const parsed = JSON.parse(res.body);
      return !(parsed && Array.isArray(parsed.errors) && parsed.errors.length > 0);
    } catch (e) {
      return false;
    }
  }
  return true;
}

function stageLabel() {
  const elapsed = Date.now() - exec.scenario.startTime;
  for (const b of CONFIG.stageBounds) {
    if (elapsed < b.end) return b.label;
  }
  return CONFIG.stageBounds[CONFIG.stageBounds.length - 1].label;
}

// ---- options ----
const thresholds = {};
for (const key of Object.keys(CONFIG.thresholds)) {
  thresholds[key] = CONFIG.thresholds[key];
}
// One submetric pair per endpoint puts latency and failures per endpoint in the summary (up to 100 endpoints).
if (ACTIVE.length <= 100) {
  for (const ep of ACTIVE) {
    thresholds['http_req_duration{endpoint:' + ep.tag + '}'] = CONFIG.endpointThresholds;
    thresholds['http_req_failed{endpoint:' + ep.tag + '}'] = ['rate<=1'];
  }
}

export const options = {
  scenarios: {
    [CONFIG.profile]: {
      executor: 'ramping-arrival-rate',
      startRate: CONFIG.startRate,
      timeUnit: '1s',
      preAllocatedVUs: Math.min(CONFIG.preAllocatedVus, MAX_VUS),
      maxVUs: MAX_VUS,
      stages: CONFIG.stages,
      gracefulStop: '30s',
    },
  },
  thresholds: thresholds,
  setupTimeout: '10m', // the baseline probe sends each endpoint once, at most MAX_RPS per second
  userAgent: 'api-test-pilot-k6',
  insecureSkipTLSVerify: false,
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  tags: { testtype: CONFIG.profile },
};

// Before the load starts, every endpoint is sent once. One that already fails with the generated request (a made-up
// value the server rejects, another role's endpoint, a server bug) says nothing about load, so it is left out of the
// run and listed. APITEST_K6_BASELINE=off keeps them all. The probe is sequential and paced under the rate cap.
export function setup() {
  const probe = envOf('APITEST_K6_BASELINE').trim().toLowerCase() !== 'off';
  const failed = [];
  if (probe) {
    for (const ep of ACTIVE) {
      const params = Object.assign({}, ep.params, { tags: Object.assign({}, ep.params.tags, { stage: 'baseline_probe' }) });
      const res = http.request(ep.method, ep.url, ep.body, params);
      if (!statusOk(ep, res)) failed.push({ endpoint: ep.tag, status: res.status });
      sleep(1 / MAX_RPS);
    }
  }
  const dropped = failed.map((f) => f.endpoint);
  const tested = ACTIVE.filter((ep) => dropped.indexOf(ep.tag) === -1).map((ep) => ep.tag);
  // One line for run_k6.py to collect into results/<profile>-coverage.json; names and statuses only, no values.
  console.log('APITEST_COVERAGE ' + JSON.stringify({ tested: tested, skipped: SKIPPED, baselineFailed: failed, baselineProbe: probe }));
  if (tested.length === 0) {
    exec.test.abort('every endpoint fails with its generated request, so there is nothing to load test (see the coverage line above)');
  }
  return { drop: dropped };
}

// ---- token refresh for runs longer than the access token lives ----
// run_k6.py serves fresh tokens on a loopback-only URL (APITEST_TOKEN_URL) from the command in APITEST_TOKEN_CMD. A VU fetches
// a token before its first request, again every APITEST_TOKEN_REFRESH_S seconds (if set) and after a 401. The token is never logged.
const TOKEN_URL = envOf('APITEST_TOKEN_URL').trim();
const REFRESH_MS = Math.max(0, Number(envOf('APITEST_TOKEN_REFRESH_S')) || 0) * 1000;
let TOKEN = null;
let TOKEN_AT = 0;
let TOKEN_TRIED = 0;
let TOKEN_GEN = null;
// The token server counts tokens. Saying which one this VU holds lets it answer a VU that is behind with the current token,
// so a burst of rejections after one renewal causes one sign-in, not one per VU.
function fetchToken() {
  TOKEN_TRIED = Date.now();
  const url = TOKEN_GEN === null ? TOKEN_URL : TOKEN_URL + '?have=' + TOKEN_GEN;
  const res = http.get(url, { responseType: 'text', timeout: '10s', redirects: 0, tags: { name: 'token_refresh' } });
  const text = res.status === 200 && typeof res.body === 'string' ? res.body.trim() : '';
  // a plain length check: k6's regular expressions allow a repeat count of 1000 at most, and tokens can be longer
  if (text.length > 0 && text.length <= 4096 && !/[^\x21-\x7e]/.test(text)) {
    TOKEN = text;
    TOKEN_AT = Date.now();
    const gen = Number((res.headers || {})['X-Token-Gen']);
    TOKEN_GEN = Number.isInteger(gen) && gen > 0 ? gen : null;
  }
}

let LIVE = null;
export default function (data) {
  if (LIVE === null) {
    const drop = (data && data.drop) || [];
    LIVE = ACTIVE.filter((e) => drop.indexOf(e.tag) === -1);
  }
  const ep = LIVE[Math.floor(Math.random() * LIVE.length)];
  const stage = stageLabel();
  const params = Object.assign({}, ep.params, { tags: Object.assign({}, ep.params.tags, { stage: stage, phase: 'load' }) });
  if (TOKEN_URL && ep.refresh) {
    if (TOKEN === null || (REFRESH_MS > 0 && Date.now() - TOKEN_AT > REFRESH_MS)) fetchToken();
    if (TOKEN !== null) params.headers = Object.assign({}, params.headers, { Authorization: ep.refresh + ' ' + TOKEN });
  }
  const res = http.request(ep.method, ep.url, ep.body, params);
  if (res.status === 401 && TOKEN_URL && ep.refresh && Date.now() - TOKEN_TRIED > 5000) fetchToken(); // the next request uses it
  check(res, { 'status is expected': (r) => statusOk(ep, r) }, { endpoint: ep.tag, stage: stage });
}
