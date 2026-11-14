"""HTML and JSON output for summarize_results.py. Stdlib only, no network, no external assets.

The page is one file: CSS and a few lines of JS are inline, charts are inline SVG, and a CSP meta tag blocks
everything else. Strings from result files are redacted with summarize_results.plain() (build_data runs it over the whole payload);
this module then escapes them for HTML. Nothing here reads result files or contacts a target.
"""
from __future__ import annotations

import html
import json
import math
from typing import Any

SEV_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
STATUS_CLASS = {"PASS": "ok", "FAIL": "bad", "INCOMPLETE": "warn", "INCONCLUSIVE": "warn"}
SCHEMA_VERSION = 1
STATUS_ORDER = {"failed": 0, "error": 1, "skipped": 2, "passed": 3}
STATUS_LABEL = {"failed": "FAIL", "error": "ERROR", "skipped": "SKIP", "passed": "PASS"}
MAX_CASES_SHOWN = 3000   # per report; the JSON keeps all of them
HTTP_BUDGET_CHARS = 4_000_000   # request/response text shown on the page; failures get it first
JSON_HTTP_BUDGET_CHARS = 20_000_000   # the same limit for report.json, which keeps more than the page


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _num(v: Any) -> float | None:
    return v if isinstance(v, (int, float)) and math.isfinite(v) else None


def build_data(r, title: str, date: str, command: str, limit: int, mod) -> dict:
    """Plain dict of everything the report shows. `mod` is summarize_results (passed in to avoid a circular import)."""
    by, tot = mod.tally(r)
    status, text = mod.verdict_info(r)
    ordered = sorted(r.findings, key=lambda f: (mod.SEVERITIES.index(f.severity), f.endpoint, f.kind, f.title))
    findings = [{
        "severity": f.severity, "title": f.title, "endpoint": f.endpoint, "type": f.kind, "rating": f.rating,
        "owasp": f.owasp, "detail": list(f.lines), "reproduce": list(f.commands), "note": f.note,
    } for f in ordered[:limit]]
    coverage = [{
        "endpoint": key, "types": sorted(v["types"]), "passed": v["passed"], "failed": v["failed"],
        "skipped": v["skipped"], "errors": v["error"], "k6": v["k6"].rstrip("; "),
    } for key, v in mod.coverage_rows(r)]
    none_run: list[str] = []
    if r.index.eps:
        seen = {c["endpoint"] for c in r.cases}
        none_run = [mod.ep_key(x) for x in r.index.eps if mod.ep_key(x) not in seen]
    cases = [{"type": c["type"], "name": c["name"], "status": c["status"], "endpoint": c["endpoint"],
              "seconds": round(c["time"] or 0, 3), "reason": c.get("reason", ""), "http": c.get("http") or {"total": 0, "items": []}}
             for c in sorted(r.cases, key=lambda c: (c["type"], STATUS_ORDER[c["status"]], c["name"]))]
    keep = allocate_http(cases, JSON_HTTP_BUDGET_CHARS)
    for i, c in enumerate(cases):    # a huge run must not produce a huge JSON file: past the budget, passing tests lose their exchanges
        if c["http"]["items"] and i not in keep:
            c["http"] = {"total": c["http"]["total"], "items": [], "omitted": True}
    data = {
        "schema_version": SCHEMA_VERSION,
        "title": title, "date": date,
        "result": {"status": status, "summary": text, "warning": mod.skip_warning(r)},
        "totals": {**tot, "total": len(r.cases), "test_seconds": round(sum(c["time"] or 0 for c in r.cases), 2)},
        "cases": cases,
        "by_type": {t: {**v, "total": sum(v.values())} for t, v in sorted(by.items())},
        "severity_counts": {s: sum(1 for f in r.findings if f.severity == s) for s in SEV_ORDER},
        "findings": findings, "findings_total": len(r.findings),
        "coverage": coverage, "endpoints_without_results": none_run, "has_intake": bool(r.index.eps),
        "k6": r.k6,
        "run_problems": dict(sorted(r.problems.items())[:50]),
        "skips": dict(sorted(r.skips.items(), key=lambda kv: -kv[1])[:15]),
        "inputs": [{"kind": k, "file": n, "sha256_prefix": d} for k, n, d in r.inputs],
        "command": command,
    }
    return _scrub(data, mod)


def _scrub(value: Any, mod) -> Any:
    """Redact every string in the payload once more. Most are clean already; k6 names, tags and the findings built from them are not."""
    if isinstance(value, str):
        return mod.plain(value, 4000, keep_newlines=True)
    if isinstance(value, dict):
        return {(_scrub(k, mod) if isinstance(k, str) else k): _scrub(v, mod) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, mod) for v in value]
    return value


def to_json(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False, default=list) + "\n"


# --------------------------------------------------------------------------- charts
def donut(passed: int, failed: int, skipped: int, errors: int) -> str:
    total = passed + failed + skipped + errors
    if not total:
        return '<svg viewBox="0 0 42 42" class="donut" role="img" aria-label="no tests"><circle cx="21" cy="21" r="15.9" class="seg-none"/></svg>'
    parts, offset = [], 25.0   # start at 12 o'clock
    for n, cls in ((passed, "seg-pass"), (failed, "seg-fail"), (errors, "seg-err"), (skipped, "seg-skip")):
        if not n:
            continue
        pct = n * 100 / total
        parts.append(f'<circle cx="21" cy="21" r="15.9" class="{cls}" stroke-dasharray="{pct:.2f} {100 - pct:.2f}" stroke-dashoffset="{offset:.2f}"/>')
        offset -= pct
    rate = round(passed * 100 / total)
    label = f"{passed} of {total} tests passed"
    return (f'<svg viewBox="0 0 42 42" class="donut" role="img" aria-label="{e(label)}"><circle cx="21" cy="21" r="15.9" class="seg-none"/>'
            + "".join(parts) + f'<text x="21" y="22.5" class="donut-num">{rate}%</text><text x="21" y="27" class="donut-sub">passed</text></svg>')


def stacked(v: dict) -> str:
    total = v["total"] or 1
    segs = "".join(f'<span class="{cls}" style="width:{v[k] * 100 / total:.2f}%" title="{k}: {v[k]}"></span>'
                   for k, cls in (("passed", "seg-pass"), ("failed", "seg-fail"), ("error", "seg-err"), ("skipped", "seg-skip")) if v[k])
    return f'<div class="stack">{segs}</div>'


def fmt(v: Any, unit: str = "") -> str:
    v = _num(v)
    if v is None:
        return "n/a"
    s = f"{v:.0f}" if abs(v) >= 100 else (f"{v:.2f}".rstrip("0").rstrip(".") if v else "0")
    return s + unit


# --------------------------------------------------------------------------- sections
def sev_pill(s: str) -> str:
    return f'<span class="pill sev-{e(s.lower())}">{e(s)}</span>'


def section_stats(d: dict) -> str:
    t = d["totals"]
    cards = [("Tests", t["total"]), ("Passed", t["passed"]), ("Failed", t["failed"]), ("Skipped", t["skipped"]), ("Errors", t["error"]),
             ("Findings", d["findings_total"])]
    out = "".join(f'<div class="card"><div class="card-n">{e(n)}</div><div class="card-l">{e(l)}</div></div>' for l, n in cards)
    sev = "".join(f'<li>{sev_pill(s)} <b>{n}</b></li>' for s, n in d["severity_counts"].items() if n) or '<li class="muted">No findings</li>'
    return (f'<section class="hero"><div class="donut-wrap">{donut(t["passed"], t["failed"], t["skipped"], t["error"])}</div>'
            f'<div class="cards">{out}</div><ul class="sevlist">{sev}</ul></section>')


def exchange_size(http: dict) -> int:
    return sum(len(x["request_body"]) + len(x["response_body"]) + len(x["url"]) + 600 for x in http["items"])


def allocate_http(cases: list[dict], budget: int) -> set[int]:
    """Indexes of the cases whose request/response text fits in `budget` characters. Failures and errors go first."""
    keep: set[int] = set()
    for idx, c in sorted(enumerate(cases), key=lambda t: STATUS_ORDER[t[1]["status"]]):
        size = exchange_size(c["http"])
        if c["http"]["items"] and size <= budget:
            budget -= size
            keep.add(idx)
    return keep


def _kv(h: dict) -> str:
    return "\n".join(f"{k}: {v}" for k, v in h.items())


def exchange_html(http: dict) -> str:
    parts = []
    for i, x in enumerate(http["items"], 1):
        status = x["status"] if x["status"] is not None else "?"
        ms = f" · {x['ms']} ms" if x["ms"] is not None else ""
        req = "\n\n".join(p for p in (_kv(x["request_headers"]), x["request_body"]) if p) or "(no headers or body)"
        res = "\n\n".join(p for p in (_kv(x["response_headers"]), x["response_body"]) if p) or "(no body)"
        parts.append(
            f'<div class="xch"><div class="xl"><b>{e(x["method"])}</b> {e(x["url"])} <span class="muted">→ {e(status)}{e(ms)}</span></div>'
            f'<div class="xcols"><div><h5>Request</h5><pre>{e(req)}</pre></div><div><h5>Response</h5><pre>{e(res)}</pre></div></div></div>')
    more = http["total"] - len(http["items"])
    if more > 0:
        parts.append(f'<p class="muted">{more} more request(s) in this test were not recorded.</p>')
    n = len(http["items"])
    return (f'<details class="xh"><summary>Request and response ({n})</summary>{"".join(parts)}</details>') if parts else ""


def section_types(d: dict) -> str:
    if not d["by_type"]:
        return ""
    by_type: dict[str, list] = {}
    for c in d["cases"]:
        by_type.setdefault(c["type"], []).append(c)
    shown = 0
    with_http = allocate_http(d["cases"], HTTP_BUDGET_CHARS)
    pos = {id(c): i for i, c in enumerate(d["cases"])}
    blocks = []
    for t, v in d["by_type"].items():
        rows = []
        for c in by_type.get(t, []):
            if shown >= MAX_CASES_SHOWN:
                break
            shown += 1
            st = c["status"]
            xh = exchange_html(c["http"]) if pos[id(c)] in with_http else ""
            rows.append(
                f'<tr data-st="{e(st)}"><td><span class="st st-{e(st)}">{STATUS_LABEL[st]}</span></td><td class="tn">{e(c["name"])}</td>'
                f'<td class="te">{e(c["endpoint"])}</td><td class="tr">{e(c["reason"])}</td></tr>'
                + (f'<tr data-st="{e(st)}" class="xr"><td></td><td colspan="3">{xh}</td></tr>' if xh else ""))
        left = len(by_type.get(t, [])) - len(rows)
        more = f'<p class="muted">{left} more case(s) in report.json.</p>' if left > 0 else ""
        filt = "".join(f'<button class="chip sm" type="button" data-f="{k}">{lab} {v[k2]}</button>'
                       for k, lab, k2 in (("failed", "Failed", "failed"), ("error", "Errors", "error"), ("skipped", "Skipped", "skipped"), ("passed", "Passed", "passed")) if v[k2])
        bad = v["failed"] or v["error"]
        errs = f' <b class="f">{v["error"]}</b> error' if v["error"] else ""
        blocks.append(
            f'<details class="type{" has-bad" if bad else ""}"><summary><span class="tname">{e(t)}</span><span class="bar">{stacked(v)}</span>'
            f'<span class="tnums"><b class="p">{v["passed"]}</b> pass <b class="f">{v["failed"]}</b> fail <b>{v["skipped"]}</b> skip'
            f'{errs}</span></summary>'
            f'<div class="tcases"><div class="chips">{filt}</div><div class="scroll"><table class="cases"><thead><tr><th>Status</th><th>Test</th>'
            f'<th>Endpoint</th><th>Failure or skip reason</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>{more}</div></details>')
    return (f'<section><h2>Results by test type</h2><p class="muted">Click a type to see every test that ran, failed or was skipped.</p>'
            f'{"".join(blocks)}<p class="muted">Test time (sum of per-test times): {d["totals"]["test_seconds"]} s.</p></section>')


def section_k6(d: dict) -> str:
    if not d["k6"]:
        return '<section><h2>Load and performance (k6)</h2><p class="muted">No k6 summary supplied.</p></section>'
    blocks = []
    for k in d["k6"]:
        o = k["overall"]
        stats = "".join(f'<div class="card"><div class="card-n">{e(v)}</div><div class="card-l">{e(l)}</div></div>' for l, v in (
            ("Requests", fmt(o["requests"])), ("Req/s", fmt(o["rps"])), ("p95", fmt(o["p95"], " ms")), ("p99", fmt(o["p99"], " ms")),
            ("Max", fmt(o["max"], " ms")), ("Failed rate", fmt(o["failed_rate"]))))
        th = ""
        if k["thresholds"]:
            crossed = '<span class="pill sev-high">CROSSED</span>'
            fine = '<span class="pill ok">ok</span>'
            rows = "".join(
                f'<tr><td>{e(t["metric"])}</td><td><code>{e(t["expr"])}</code></td><td class="n">{e(fmt(t["observed"]))}</td>'
                f'<td>{crossed if t["crossed"] else fine}</td></tr>'
                for t in k["thresholds"][:100])
            th = f'<div class="scroll"><table><thead><tr><th>Metric</th><th>Threshold</th><th>Observed</th><th>Result</th></tr></thead><tbody>{rows}</tbody></table></div>'
        st = ""
        if k["stages"]:
            top = max([_num(v.get("p95")) or 0 for v in k["stages"].values()] + [1])
            rows = "".join(
                f'<tr><td>{e(name)}</td><td class="bar"><div class="stack"><span class="seg-pass" style="width:{(_num(v.get("p95")) or 0) * 100 / top:.1f}%"></span></div></td>'
                f'<td class="n">{e(fmt(v.get("p95"), " ms"))}</td><td class="n">{e(fmt(v.get("failed")))}</td></tr>'
                for name, v in sorted(k["stages"].items()))
            st = f'<div class="scroll"><table><thead><tr><th>Stage</th><th>p95 latency</th><th></th><th>Failed rate</th></tr></thead><tbody>{rows}</tbody></table></div>'
        blocks.append(f'<h3>Profile {e(k["profile"])}</h3><div class="cards">{stats}</div>{th}{st}')
    return ('<section><h2>Load and performance (k6)</h2>' + "".join(blocks)
            + '<p class="muted">A short or default-rate run shows shape, not capacity.</p></section>')


def section_coverage(d: dict) -> str:
    if not d["coverage"]:
        return ""
    rows = "".join(
        f'<tr><td>{e(c["endpoint"])}</td><td>{e(", ".join(c["types"]) or "none run")}</td><td class="n">{c["passed"]}</td>'
        f'<td class="n{" hot" if c["failed"] else ""}">{c["failed"]}</td><td class="n">{c["skipped"] + c["errors"]}</td><td>{e(c["k6"])}</td></tr>'
        for c in d["coverage"])
    note = "" if d["has_intake"] else '<p class="muted">No intake file was given, so only endpoints named in a failing request appear. Pass <code>--intake</code> for full coverage.</p>'
    return (f'<section><h2>Coverage per endpoint</h2><input id="cov-q" class="search" type="search" placeholder="Filter endpoints" aria-label="Filter endpoints">'
            f'<div class="scroll"><table id="cov"><thead><tr><th>Endpoint</th><th>Types run</th><th>Pass</th><th>Fail</th><th>Skip/err</th><th>k6</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>{note}</section>')


def section_findings(d: dict) -> str:
    if not d["findings"]:
        return '<section><h2>Findings</h2><p class="muted">None.</p></section>'
    chips = '<button class="chip on" data-sev="all">All</button>' + "".join(
        f'<button class="chip" data-sev="{s.lower()}">{s} {n}</button>' for s, n in d["severity_counts"].items() if n)
    items = []
    for f in d["findings"]:
        meta = [f"Type: {f['type']}", f"Rating: {f['rating']}"] + ([f"OWASP: {f['owasp']}"] if f["owasp"] else [])
        items.append(
            f'<details class="finding sev-{e(f["severity"].lower())}" data-sev="{e(f["severity"].lower())}"><summary>{sev_pill(f["severity"])} '
            f'<span class="ft">{e(f["title"])}</span><span class="fe">{e(f["endpoint"])}</span></summary>'
            f'<div class="fbody"><p class="muted">{e(" · ".join(meta))}</p>'
            f'<h4>What happened vs expected</h4><pre>{e(chr(10).join(f["detail"]) or "(no detail in the result file)")}</pre>'
            f'<h4>Reproduce</h4><div class="cmd"><pre>{e(chr(10).join(f["reproduce"]))}</pre><button class="copy" type="button">Copy</button></div>'
            + (f'<p class="note">{e(f["note"])}</p>' if f["note"] else "") + '</div></details>')
    more = d["findings_total"] - len(d["findings"])
    tail = f'<p class="muted">{more} more finding(s) not shown (least severe last); raise <code>--max-findings</code> to list them.</p>' if more > 0 else ""
    return f'<section><h2>Findings, most severe first</h2><div class="chips">{chips}</div>{"".join(items)}{tail}</section>'


def section_limits(d: dict) -> str:
    items = []
    if d["run_problems"]:
        items.append("<h3>Run problems</h3><ul>" + "".join(f"<li>{e(k)}" + (f" (x{v})" if v > 1 else "") + "</li>" for k, v in d["run_problems"].items()) + "</ul>")
    lim = []
    if d["skips"]:
        lim += [f"{n} x {e(reason)}" for reason, n in d["skips"].items()]
    if d["endpoints_without_results"]:
        n = len(d["endpoints_without_results"])
        lim.append(f"{n} intake endpoint(s) with no test result" + ("" if n > 10 else ": " + e(", ".join(d["endpoints_without_results"]))))
    if not d["k6"]:
        lim.append("No load, stress, spike or soak results were supplied.")
    lim.append("A pass means the checks that ran found nothing. It does not mean the API is secure.")
    items.append("<h3>Not tested and limitations</h3><ul>" + "".join(f"<li>{x}</li>" for x in lim) + "</ul>")
    prov = "".join(f'<li>{e(i["kind"])}: {e(i["file"])} <span class="muted">(sha256 {e(i["sha256_prefix"])}…)</span></li>' for i in d["inputs"])
    items.append(f'<h3>How this report was produced</h3><ul>{prov}</ul><p>Regenerate with:</p><div class="cmd"><pre>{e(d["command"])}</pre>'
                 '<button class="copy" type="button">Copy</button></div><p class="muted">Secrets are never written: values are scrubbed from every message, URL and command.</p>')
    return "<section><h2>Notes</h2>" + "".join(items) + "</section>"


# --------------------------------------------------------------------------- page
CSS = """
:root{--bg:#f6f7fb;--panel:#fff;--ink:#14171f;--muted:#5d6678;--line:#e3e6ee;--accent:#4f46e5;--pass:#1f9d6b;--fail:#d6334a;--err:#e07b12;--skip:#98a2b3;
--critical:#9b1c31;--high:#d6334a;--medium:#e07b12;--low:#2f7fd1;--info:#6b7280;--code:#f0f2f8;--shadow:0 1px 2px rgba(20,23,31,.06),0 6px 18px rgba(20,23,31,.05)}
@media (prefers-color-scheme:dark){:root{--bg:#0f1218;--panel:#171b24;--ink:#e8ebf3;--muted:#9aa4b8;--line:#262c39;--accent:#8b85ff;--pass:#34c38f;--fail:#ff5c75;--err:#f5a04a;--skip:#667085;
--critical:#ff6b81;--high:#ff5c75;--medium:#f5a04a;--low:#5aa7ff;--info:#8b93a5;--code:#0f131b;--shadow:none}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:0 16px 48px}
header.top{background:linear-gradient(135deg,#312e81,#4f46e5 55%,#0ea5e9);color:#fff;padding:32px 0 56px}
header.top .wrap{padding-bottom:0}h1{margin:0 0 4px;font-size:28px;letter-spacing:-.02em}.sub{opacity:.85;font-size:14px}
.verdict{margin-top:18px;display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap}
.badge{font-weight:700;letter-spacing:.06em;padding:5px 14px;border-radius:999px;font-size:13px;color:#fff}
.badge.ok{background:var(--pass)}.badge.bad{background:var(--fail)}.badge.warn{background:var(--err)}
.vtext{flex:1;min-width:240px;opacity:.95}.warnbox{margin-top:10px;background:rgba(255,255,255,.14);padding:8px 12px;border-radius:8px;font-size:14px}
main{margin-top:-32px}section{background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);padding:20px 22px;margin:18px 0}
h2{margin:0 0 12px;font-size:18px}h3{margin:18px 0 8px;font-size:15px}h4{margin:14px 0 6px;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.muted{color:var(--muted);font-size:13px}code,pre{font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:10px 12px;overflow:auto;margin:0;white-space:pre-wrap;word-break:break-word}
.hero{display:grid;grid-template-columns:160px 1fr;gap:18px 28px;align-items:center}
.donut{width:150px;height:150px;transform:rotate(0)}.donut circle{fill:none;stroke-width:5}.seg-none{stroke:var(--line)}
.donut .seg-pass,.stack .seg-pass{stroke:var(--pass);background:var(--pass)}.donut .seg-fail,.stack .seg-fail{stroke:var(--fail);background:var(--fail)}
.donut .seg-err,.stack .seg-err{stroke:var(--err);background:var(--err)}.donut .seg-skip,.stack .seg-skip{stroke:var(--skip);background:var(--skip)}
.donut-num{font-size:8px;font-weight:700;fill:var(--ink);text-anchor:middle}.donut-sub{font-size:3px;fill:var(--muted);text-anchor:middle}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px}.card{background:var(--code);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.card-n{font-size:22px;font-weight:700;letter-spacing:-.02em}.card-l{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.sevlist{grid-column:1/-1;list-style:none;display:flex;flex-wrap:wrap;gap:10px;margin:0;padding:0}
.pill{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.05em;padding:2px 9px;border-radius:999px;color:#fff;background:var(--info)}
.pill.ok{background:var(--pass)}.sev-critical.pill{background:var(--critical)}.sev-high.pill{background:var(--high)}.sev-medium.pill{background:var(--medium)}.sev-low.pill{background:var(--low)}
.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:middle}
th{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}td.n{text-align:right;font-variant-numeric:tabular-nums}td.hot{color:var(--fail);font-weight:700}
td.bar{min-width:160px;width:40%}.stack{display:flex;height:10px;border-radius:6px;overflow:hidden;background:var(--line)}.stack span{display:block;height:100%}
.search{width:100%;max-width:340px;margin-bottom:10px;padding:7px 10px;border-radius:8px;border:1px solid var(--line);background:var(--code);color:var(--ink)}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}.chip{border:1px solid var(--line);background:var(--code);color:var(--ink);border-radius:999px;padding:4px 12px;cursor:pointer;font:inherit;font-size:13px}
.chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
.finding{border:1px solid var(--line);border-left:4px solid var(--info);border-radius:10px;margin:8px 0;background:var(--panel)}
.finding.sev-critical{border-left-color:var(--critical)}.finding.sev-high{border-left-color:var(--high)}.finding.sev-medium{border-left-color:var(--medium)}.finding.sev-low{border-left-color:var(--low)}
.finding summary{cursor:pointer;padding:10px 14px;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}.ft{font-weight:600}.fe{color:var(--muted);font-size:13px;margin-left:auto}
.fbody{padding:0 14px 14px}.cmd{position:relative}.copy{position:absolute;top:6px;right:6px;font-size:12px;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:6px;padding:2px 8px;cursor:pointer}
.note{font-size:13px;color:var(--muted);border-left:3px solid var(--err);padding-left:10px;margin-top:10px}
details.type{border:1px solid var(--line);border-radius:10px;margin:8px 0;background:var(--panel)}details.type.has-bad{border-left:4px solid var(--fail)}
details.type>summary{cursor:pointer;display:grid;grid-template-columns:150px 1fr auto;gap:12px;align-items:center;padding:10px 14px;list-style:none}
details.type>summary::-webkit-details-marker{display:none}details.type>summary .tname::before{content:">";display:inline-block;width:16px;color:var(--muted);transition:transform .15s}
details.type[open]>summary .tname::before{transform:rotate(90deg)}.tname{font-weight:600}.tnums{font-size:13px;color:var(--muted);white-space:nowrap}.tnums b.p{color:var(--pass)}.tnums b.f{color:var(--fail)}
.tcases{padding:0 14px 12px}.chip.sm{font-size:12px;padding:2px 10px}.cases td{font-size:13px;vertical-align:top}.tn{word-break:break-word}.te{color:var(--muted)}.tr{color:var(--muted);word-break:break-word}
.st{display:inline-block;min-width:48px;text-align:center;font-size:11px;font-weight:700;padding:2px 8px;border-radius:6px;color:#fff;background:var(--skip)}
.st-passed{background:var(--pass)}.st-failed{background:var(--fail)}.st-error{background:var(--err)}
@media (max-width:640px){details.type>summary{grid-template-columns:1fr}.tnums{white-space:normal}}
.xr td{border-bottom:1px solid var(--line);padding-top:0}.xh{margin-top:0}.xh summary{cursor:pointer;color:var(--accent);font-size:12px}.xch{margin:8px 0;padding:8px;border:1px solid var(--line);border-radius:8px}
.xl{font-size:12px;word-break:break-all}.xcols{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:6px}.xcols pre{max-height:260px;font-size:12px}h5{margin:0 0 4px;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
@media (max-width:760px){.xcols{grid-template-columns:1fr}}
footer{color:var(--muted);font-size:12px;text-align:center;padding:12px}
@media (max-width:640px){.hero{grid-template-columns:1fr;justify-items:center}.fe{margin-left:0;flex-basis:100%}h1{font-size:22px}}
@media print{header.top{background:#312e81!important;-webkit-print-color-adjust:exact;print-color-adjust:exact}.chips,.search,.copy{display:none}details.finding{break-inside:avoid}}
"""

JS = """
(function(){
  var chips=document.querySelectorAll('.chip[data-sev]'),fs=document.querySelectorAll('details.finding');
  chips.forEach(function(c){c.addEventListener('click',function(){
    chips.forEach(function(x){x.classList.remove('on')});c.classList.add('on');
    var s=c.getAttribute('data-sev');fs.forEach(function(f){f.style.display=(s==='all'||f.getAttribute('data-sev')===s)?'':'none'});
  })});
  document.querySelectorAll('.copy').forEach(function(b){b.addEventListener('click',function(){
    var t=b.parentNode.querySelector('pre').textContent;
    if(navigator.clipboard){navigator.clipboard.writeText(t).then(function(){b.textContent='Copied';setTimeout(function(){b.textContent='Copy'},1200)},function(){})}
  })});
  document.querySelectorAll('details.type').forEach(function(t){
    var cs=t.querySelectorAll('.chip.sm'),rows=t.querySelectorAll('tbody tr'),on=null;
    cs.forEach(function(c){c.addEventListener('click',function(){
      var f=c.getAttribute('data-f');on=(on===f)?null:f;
      cs.forEach(function(x){x.classList.toggle('on',on!==null&&x.getAttribute('data-f')===on)});
      rows.forEach(function(r){r.style.display=(on===null||r.getAttribute('data-st')===on)?'':'none'});
    })});
    if(t.classList.contains('has-bad')){t.open=true}
  });
  var q=document.getElementById('cov-q');
  if(q){q.addEventListener('input',function(){var v=q.value.toLowerCase();
    document.querySelectorAll('#cov tbody tr').forEach(function(r){r.style.display=r.textContent.toLowerCase().indexOf(v)<0?'none':''})})}
})();
"""


def render_html(d: dict) -> str:
    cls = STATUS_CLASS.get(d["result"]["status"], "warn")
    warn = f'<div class="warnbox">{e(d["result"]["warning"])}</div>' if d["result"]["warning"] else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">
<title>Test report: {e(d["title"])}</title>
<style>{CSS}</style></head><body>
<header class="top"><div class="wrap"><h1>{e(d["title"])}</h1><div class="sub">API test report · {e(d["date"])}</div>
<div class="verdict"><span class="badge {cls}">{e(d["result"]["status"])}</span><span class="vtext">{e(d["result"]["summary"])}{warn}</span></div></div></header>
<main class="wrap">
{section_stats(d)}
{section_types(d)}
{section_findings(d)}
{section_k6(d)}
{section_coverage(d)}
{section_limits(d)}
</main>
<footer>Generated offline by the api-testing skill. Self-contained: no external scripts, fonts or images.</footer>
<script>{JS}</script></body></html>
"""
