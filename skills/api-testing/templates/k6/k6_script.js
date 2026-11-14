// k6 __PROFILE__ test. Generated file, regenerate instead of editing.
// Rerun: __RERUN__
//
// SAFETY: this script refuses to start unless the target passes the gate below (non-production + authorized,
// or a loopback address). It never contains credentials: tokens come from environment variables.
// Do NOT run k6 with --http-debug: it prints request headers, including Authorization.
import http from 'k6/http';
import { check } from 'k6';
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

// ---- endpoints: only requests that are safe to send and fully configured ----
function applyScheme(name, headers, query, cookies) {
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
    for (const s of alt) {
      for (const g of applyScheme(s, headers, query, cookies)) gaps.push(g);
    }
    if (gaps.length === 0) return { headers, query, cookies };
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

function build(ep, notes) {
  if (ep.write && (truthy('APITEST_READ_ONLY') || !(CONFIG.includeWrites && truthy('APITEST_ALLOW_WRITES')))) {
    notes.push(ep.tag + (truthy('APITEST_READ_ONLY') ? ': write method skipped (APITEST_READ_ONLY=yes)' : ': write method skipped (regenerate with --include-writes and set APITEST_ALLOW_WRITES=yes)'));
    return null;
  }
  let path = ep.path;
  for (const p of ep.pathParams) {
    const val = envOf(p.env) || (p.default === null || p.default === undefined ? '' : String(p.default));
    if (val === '') {
      notes.push(ep.tag + ': skipped, set ' + p.env + ' to an existing id');
      return null;
    }
    path = path.split('{' + p.name + '}').join(encodeURIComponent(val));
  }
  const headers = Object.assign({}, ep.headers, { 'X-Load-Test': 'api-test-pilot' });
  const query = Object.assign({}, ep.query);
  const cookies = {};
  const required = ep.sec.length > 0;
  const wanted = required ? ep.sec : ep.softSec || [];
  if (wanted.length > 0) {
    const cred = credentialsFor(wanted);
    if (cred.missing) {
      if (required) {
        notes.push(ep.tag + ': skipped, credentials not configured: set ' + cred.missing.join(', '));
        return null;
      }
      // optional credentials (schema declares no per-operation auth): send them only when configured
    } else {
      Object.assign(headers, cred.headers);
      Object.assign(query, cred.query);
      Object.assign(cookies, cred.cookies);
    }
  }
  let body = null;
  let bodyValue = ep.body;
  if (ep.graphql && bodyValue) {
    const missing = [];
    const variables = resolveVars(bodyValue.variables || {}, missing);
    if (missing.length > 0) {
      notes.push(ep.tag + ': skipped, set one of ' + missing.join(', ') + ' to an existing id');
      return null;
    }
    bodyValue = { query: bodyValue.query, variables: variables, operationName: bodyValue.operationName };
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
if (ACTIVE.length <= 20) {
  for (const ep of ACTIVE) {
    thresholds['http_req_duration{endpoint:' + ep.tag + '}'] = CONFIG.endpointThresholds;
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
  userAgent: 'api-test-pilot-k6',
  insecureSkipTLSVerify: false,
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  tags: { testtype: CONFIG.profile },
};

export default function () {
  const ep = ACTIVE[Math.floor(Math.random() * ACTIVE.length)];
  const stage = stageLabel();
  const params = Object.assign({}, ep.params, { tags: Object.assign({}, ep.params.tags, { stage: stage }) });
  const res = http.request(ep.method, ep.url, ep.body, params);
  check(res, { 'status is expected': (r) => statusOk(ep, r) }, { endpoint: ep.tag, stage: stage });
}
