"""Accessible static report: no JavaScript, external fonts, assets or requests."""
from __future__ import annotations

from html import escape
from urllib.parse import quote

from mimo_harness.evidence import portable_name


def _text(value):
    return escape(str(value), quote=True)


def _link(name, label=None):
    portable_name(name)
    return f'<a href="{quote(name, safe="/")}">{_text(label or name)}</a>'


def _table(caption, headings, rows):
    head = "".join(f'<th scope="col">{_text(h)}</th>' for h in headings)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return (f'<div class="table-scroll" role="region" aria-label="{_text(caption)}" tabindex="0">'
            f'<table><caption>{_text(caption)}</caption><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')


def render_report(report: dict) -> str:
    run, gate = report["run"], report["gate"]
    approved = gate["allowed"]
    label = "APPROVED" if approved else "BLOCKED"
    candidates = [[_text(c["candidate_id"]), _text(c["verdict"]),
                   _text(", ".join(c["reason_codes"])), _text(", ".join(c.get("fixed_tests", [])) or "None"),
                   _text(", ".join(c.get("regressed_tests", [])) or "None")]
                  for c in report["candidates"]]
    candidate_section = (_table("Patch verification results", ["Candidate", "Verdict", "Reasons", "Fixed tests", "Regressions"], candidates)
                         if candidates else '<p class="muted">No patch verification result is available. This is not an approval.</p>')
    stages = []
    for number, stage in enumerate(report["stages"], 1):
        cases = [[_text(c["nodeid"]), _text(c["status"]), _text(bool(c.get("assertion_failure")))] for c in stage["cases"]]
        evidence_links = "".join(f"<li>{_link(name)}</li>" for name in stage["links"])
        stages.append(f'<section aria-labelledby="stage-{number}"><h3 id="stage-{number}">{_text(stage["name"])}</h3>'
                      f'<p>{_text(stage["executed_checks"])} external check(s) executed.</p>'
                      + (_table("Recorded behavior cases", ["Test ID", "Status", "Assertion failure"], cases)
                         if cases else '<p class="muted">No behavior cases recorded.</p>')
                      + (f'<details><summary>Check logs and machine-readable results</summary><ul>{evidence_links}</ul></details>' if evidence_links else "")
                      + "</section>")
    files = "".join(f"<li>{_link(item['href'], item['name'])}</li>" for item in report.get("files", []))
    exclusions = "".join(f"<li>{_text(value)}</li>" for value in report.get("excluded", []))
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{label} · MiMoHarness evidence</title>
<style>
:root{{--paper:#fff;--canvas:#f5f7fa;--ink:#172b4d;--muted:#46566b;--line:#c5ced9;--link:#0747a6;--good:#145a32;--bad:#9c2323;--space:.25rem}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--canvas);color:var(--ink);font:1rem/1.6 system-ui,"Segoe UI",sans-serif}}
main{{max-width:80rem;margin:auto;padding:2rem}}header{{border-bottom:.25rem solid var(--ink);padding-bottom:1.5rem;margin-bottom:2rem}}
h1{{font-size:2.25rem;line-height:1.2;margin:.5rem 0 1rem}}h2{{font-size:1.5rem;margin:2rem 0 1rem}}h3{{font-size:1.125rem;margin:1.5rem 0 .5rem}}
p{{margin:.75rem 0}}.eyebrow{{font-size:.875rem;letter-spacing:.1em;text-transform:uppercase;font-weight:700}}
.decision{{font-size:1.25rem;font-weight:700;color:var(--{'good' if approved else 'bad'})}}.muted{{color:var(--muted)}}
code{{font: .875em/1.6 ui-monospace,Consolas,monospace;overflow-wrap:anywhere}}a{{color:var(--link);text-decoration:underline;text-underline-offset:.15em}}
a:focus-visible,summary:focus-visible,.table-scroll:focus-visible{{outline:.2rem solid var(--link);outline-offset:.2rem}}
.skip{{position:absolute;top:-5rem;left:1rem;padding:.75rem;background:var(--paper)}}.skip:focus{{top:.5rem}}
nav{{display:flex;gap:1rem;flex-wrap:wrap;margin-top:1rem}}dl{{display:grid;grid-template-columns:minmax(7rem,auto) 1fr;gap:.5rem 1rem}}
dt{{font-weight:600}}dd{{margin:0;overflow-wrap:anywhere}}.table-scroll{{max-width:100%;overflow-x:auto;border:1px solid var(--line);background:var(--paper)}}
table{{width:100%;border-collapse:collapse;text-align:left;font-size:.875rem}}caption{{padding:.75rem 1rem;text-align:left;font-weight:600}}
th,td{{padding:.75rem 1rem;border-top:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}}th{{background:var(--canvas)}}
ul{{padding-left:1.5rem}}li{{margin:.5rem 0;overflow-wrap:anywhere}}details{{border-bottom:1px solid var(--line);padding:.75rem 0}}summary{{cursor:pointer;font-weight:600}}
footer{{margin-top:2rem;border-top:1px solid var(--line);padding-top:1rem;color:var(--muted);font-size:.875rem}}
@media(max-width:40rem){{main{{padding:1rem}}h1{{font-size:1.75rem}}dl{{grid-template-columns:1fr;gap:.25rem}}dd{{margin-bottom:.75rem}}th,td{{padding:.5rem}}}}
@media print{{body{{background:white}}main{{max-width:none;padding:0}}nav,.skip{{display:none}}.table-scroll{{overflow:visible}}}}
</style></head><body><a class="skip" href="#main">Skip to evidence</a>
<main id="main" tabindex="-1"><header><p class="eyebrow">MiMoHarness / Verification record</p><h1>Evidence for a code change</h1>
<p class="decision">{label} — {_text(gate['verdict'])}</p>
<p>{_text(', '.join(gate['reason_codes']))} · Gate exit code {_text(gate['exit_code'])}</p>
<nav aria-label="Report sections"><a href="#identity">Run identity</a><a href="#results">Patch results</a><a href="#checks">Behavior checks</a><a href="#files">Evidence files</a></nav></header>
<section id="identity"><h2>Run identity</h2><dl><dt>Run ID</dt><dd><code>{_text(run.get('run_id'))}</code></dd>
<dt>Input fingerprint</dt><dd><code>{_text(run.get('fingerprint'))}</code></dd><dt>Run state</dt><dd>{_text(run.get('status'))}</dd>
<dt>Harness version</dt><dd>{_text(run.get('harness_version'))}</dd></dl></section>
<section id="results"><h2>Patch verification</h2>{candidate_section}</section>
<section id="checks"><h2>Behavior checks</h2>{''.join(stages) or '<p class="muted">No completed behavior evidence is available.</p>'}</section>
<section id="files"><h2>Evidence files</h2><p>Links are relative to this extracted bundle. Original JSON and JUnit files retain provenance metadata.</p><ul>{files}</ul>
<details><summary>Excluded from this evidence bundle</summary><ul>{exclusions}</ul></details></section>
<footer>This report is an offline view, not a digital signature. Use <code>mimo gate --bundle evidence.zip</code> to check inventory hashes and recompute the verdict. A passing result covers the configured acceptance tests, not every possible program behavior.</footer>
</main></body></html>'''
