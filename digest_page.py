#!/usr/bin/env python3
"""
The browser interface: digest.html.

DIGEST.md is a wall of table. It is fine on a laptop and unreadable on a
phone, which is where a job-seeker actually reads it. This module renders
the same data as a single self-contained page you can filter, search and
read one job at a time.

Self-contained is a requirement, not a preference. The repo is private, so
this file gets opened from a download, an email attachment, the Files app
or a home-screen bookmark as often as from a web server. It therefore has
no external CSS, JS, fonts or images, and works with no network at all.

Two entry points:

    run_scan.py                     calls render() with live Posting objects
    python3 digest_page.py          rebuilds digest.html from applications.csv

The second one exists so you can change the layout and see the result
without running a scan. A scan rewrites four committed files and takes ten
minutes; the page is the only thing you are actually changing.

Both paths build the same list of plain dicts and hand it to one renderer,
so a preview cannot drift from what a real scan produces.

The evidence contract survives here exactly as it does in the Markdown:
every card carries the requirement sentence its verdict rests on. The
interface may hide that sentence behind a tap to keep the list scannable,
but never behind a filter, and it is always expanded in reading mode.
"""

from __future__ import annotations

import csv
import html
import json
import os
import sys
from datetime import datetime, timezone

BUCKET_LABEL = {
    "STAFF_NURSE_I": "Level I / new grad",
    "NO_EXPERIENCE": "No experience required",
    "UNCLEAR": "Requirements unclear",
    "GENERAL_EXPERIENCE": "Experience required, not acute",
    "ACUTE_REQUIRED": "Acute care required",
}

# Reverse map, for rebuilding from applications.csv, which stores the label
# rather than the bucket name.
LABEL_BUCKET = {v: k for k, v in BUCKET_LABEL.items()}

# The two buckets that mean "you are eligible for this today". Same set
# notify.py uses to decide an email is worth sending.
APPLY_NOW = ("STAFF_NURSE_I", "NO_EXPERIENCE")

STATUS_APPLIED = {"applied", "pending", "interviewing", "rejected", "offer"}

RANK = {"STAFF_NURSE_I": 0, "NO_EXPERIENCE": 1, "UNCLEAR": 2,
        "GENERAL_EXPERIENCE": 3, "ACUTE_REQUIRED": 4}

DRIVE_ORDER = {"<30": 0, "30-60": 1, "60-90": 2, "90-120": 3}


def record(*, key, title, employer, location, url, bucket, evidence,
           drive, posted, status, is_new):
    """One job, in the shape the page's JavaScript expects."""
    return {
        "key": key or "",
        "title": title or "Registered Nurse",
        "employer": employer or "",
        "location": location or "",
        "url": url or "",
        "bucket": bucket or "UNCLEAR",
        "label": BUCKET_LABEL.get(bucket, bucket or "Requirements unclear"),
        "evidence": " ".join((evidence or "").split()),
        "drive": drive or "",
        "posted": posted or "",
        "status": status or "unapplied",
        "isNew": bool(is_new),
        "applied": (status or "") in STATUS_APPLIED,
    }


def from_postings(shown):
    """Live Posting objects, as run_scan.build() has them."""
    return [record(
        key=p.key, title=p.title, employer=p.employer, location=p.location,
        url=p.url, bucket=p.bucket, evidence=getattr(p, "evidence", ""),
        drive=p.drive_time_bucket, posted=p.posted_date,
        status=getattr(p, "status", "unapplied"),
        is_new=getattr(p, "is_new", False),
    ) for p in shown]


def from_ledger(path="applications.csv"):
    """
    Rebuild from the committed ledger, for previewing a layout change.

    "New" is inferred the way notify.py infers it: a row the scanner has
    only ever seen once carries the same First seen and Last seen stamp.
    Closed rows are dropped — they are history, not openings.
    """
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        if r.get("Status") == "closed":
            continue
        label = r.get("Bucket") or ""
        first, last = r.get("First seen", ""), r.get("Last seen", "")
        out.append(record(
            key=r.get("Key"), title=r.get("Title"), employer=r.get("Employer"),
            location=r.get("Location"), url=r.get("URL"),
            bucket=LABEL_BUCKET.get(label, "UNCLEAR"),
            evidence=r.get("Requirement evidence"),
            drive=r.get("Drive time"), posted=r.get("Posted"),
            status=r.get("Status"), is_new=bool(first) and first == last,
        ))
    out.sort(key=lambda d: (not d["isNew"], RANK.get(d["bucket"], 9),
                            DRIVE_ORDER.get(d["drive"], 9), d["employer"]))
    return out


def _json(obj):
    """
    Embed JSON in a <script> without letting a posting's own text close it.

    A job description containing "</script>" would otherwise end the block
    and leave the rest of the page as visible garbage.
    """
    return (json.dumps(obj, ensure_ascii=False)
            .replace("</", "<\\/")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def render(jobs, review, hidden, now, quick=False):
    """jobs: list of record() dicts. review: Postings with unresolved geo."""
    review_data = [{"title": p.title, "employer": p.employer,
                    "location": p.location, "url": getattr(p, "url", "")}
                   for p in review[:40]]
    meta = {
        "scanned": now,
        "hidden": hidden,
        "quick": bool(quick),
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return (TEMPLATE
            .replace("__JOBS__", _json(jobs))
            .replace("__REVIEW__", _json(review_data))
            .replace("__META__", _json(meta))
            .replace("__SCANNED__", html.escape(now.replace("T", " ")[:16])))


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>RN openings near Oakland</title>
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#fbfcfc" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#10171c" media="(prefers-color-scheme: dark)">
<!-- Added to the iOS home screen this opens without Safari's chrome, which
     is the difference between a bookmark and something you actually check. -->
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="RN jobs">
<meta name="format-detection" content="telephone=no">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='7' fill='%230b7285'/><path d='M13 8h6v5h5v6h-5v5h-6v-5H8v-6h5z' fill='white'/></svg>">
<style>
:root {
  --bg:#fbfcfc; --card:#ffffff; --ink:#12232e; --dim:#5a6b76; --faint:#8695a0;
  --line:#dfe5e8; --line-soft:#eef2f4;
  --signal:#0b7285; --signal-bg:#e6f4f7;
  --watch:#8a6d1f; --watch-bg:#fbf3df;
  --near:#1c6b4a; --near-bg:#e4f2eb;
  --accent:#0b7285;
  --shadow:0 1px 2px rgba(18,35,46,.05), 0 6px 18px -12px rgba(18,35,46,.35);
  --radius:14px;
  --ui:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  --read:"Charter","Iowan Old Style","Palatino Linotype",Georgia,serif;
  --scale:1;
}
/* Dark comes from the OS unless a host has stamped an explicit choice on
   the root element, in which case the stamp wins in both directions. */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg:#10171c; --card:#18222a; --ink:#e8eef1; --dim:#9fb0ba; --faint:#7f929e;
    --line:#26333d; --line-soft:#1e2932;
    --signal:#5ac8de; --signal-bg:#10333c;
    --watch:#e0bd6a; --watch-bg:#342c14;
    --near:#6fd3a4; --near-bg:#123328;
    --accent:#5ac8de;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 22px -14px rgba(0,0,0,.8);
  }
}
:root[data-theme="dark"] {
    --bg:#10171c; --card:#18222a; --ink:#e8eef1; --dim:#9fb0ba; --faint:#7f929e;
    --line:#26333d; --line-soft:#1e2932;
    --signal:#5ac8de; --signal-bg:#10333c;
    --watch:#e0bd6a; --watch-bg:#342c14;
    --near:#6fd3a4; --near-bg:#123328;
    --accent:#5ac8de;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 22px -14px rgba(0,0,0,.8);
}
* { box-sizing:border-box; }
html {
  -webkit-text-size-adjust:100%;
  font-size:calc(16px * var(--scale));
  scroll-behavior:smooth;
}
body {
  margin:0; background:var(--bg); color:var(--ink);
  font-family:var(--ui); font-size:1rem; line-height:1.5;
  -webkit-font-smoothing:antialiased;
  -webkit-tap-highlight-color:transparent;
  overflow-wrap:anywhere;
  padding-bottom:env(safe-area-inset-bottom);
}
button, input, select { font:inherit; color:inherit; }
/* 16px minimum on form controls, or iOS zooms the whole page on focus. */
input, select { font-size:max(1rem,16px); }
a { color:inherit; }

/* ── header ─────────────────────────────────────────────────────── */
.topbar {
  position:sticky; top:0; z-index:20;
  background:color-mix(in srgb, var(--bg) 88%, transparent);
  -webkit-backdrop-filter:saturate(1.8) blur(14px);
  backdrop-filter:saturate(1.8) blur(14px);
  border-bottom:1px solid var(--line);
  padding:calc(env(safe-area-inset-top) + .7rem) 0 0;
}
.inner { max-width:52rem; margin:0 auto;
         padding-left:max(1rem,env(safe-area-inset-left));
         padding-right:max(1rem,env(safe-area-inset-right)); }
.headrow { display:flex; align-items:flex-start; gap:1rem; }
h1 { font-size:1.02rem; margin:0; font-weight:650; letter-spacing:-.01em; }
h1 span { display:block; font-weight:400; font-size:.8rem; color:var(--dim);
          letter-spacing:0; margin-top:.1rem; }
.stamp { margin:.35rem 0 0; font-size:.72rem; color:var(--faint);
         font-variant-numeric:tabular-nums; }
.readbtn {
  margin-left:auto; flex:none; display:flex; align-items:center; gap:.4rem;
  background:var(--card); border:1px solid var(--line); color:var(--dim);
  border-radius:999px; padding:.45rem .8rem; font-size:.78rem; font-weight:550;
  min-height:36px; cursor:pointer; touch-action:manipulation;
}
.readbtn:hover { border-color:var(--accent); color:var(--accent); }
body.reading .readbtn { background:var(--signal-bg); border-color:var(--accent);
                        color:var(--accent); }

/* ── headline count ─────────────────────────────────────────────── */
.lede { max-width:52rem; margin:0 auto; padding:.9rem 1rem 0; }
.lede-t { font-size:.95rem; margin:0; line-height:1.4; }
.lede-n { font-size:1.9rem; font-weight:680; letter-spacing:-.03em;
          color:var(--signal); line-height:1; vertical-align:-.08em;
          margin-right:.3rem; }
.lede-s { font-size:.8rem; color:var(--dim); margin:.35rem 0 0; max-width:36rem; }

/* ── tabs ───────────────────────────────────────────────────────── */
.tabs { display:flex; gap:.3rem; overflow-x:auto; scrollbar-width:none;
        margin-top:.7rem; padding-bottom:.1rem; }
.tabs::-webkit-scrollbar { display:none; }
.tab {
  flex:none; background:none; border:0; border-bottom:2px solid transparent;
  padding:.55rem .55rem .5rem; font-size:.85rem; font-weight:550;
  color:var(--dim); cursor:pointer; white-space:nowrap; min-height:40px;
  touch-action:manipulation;
}
.tab[aria-selected="true"] { color:var(--ink); border-bottom-color:var(--accent); }
.tab b { font-weight:650; }
.tab .n { font-variant-numeric:tabular-nums; color:var(--faint);
          font-weight:500; margin-left:.25rem; }
.tab[aria-selected="true"] .n { color:var(--accent); }

/* ── controls ───────────────────────────────────────────────────── */
main { max-width:52rem; margin:0 auto;
       padding:0 max(1rem,env(safe-area-inset-left)) 4rem
                 max(1rem,env(safe-area-inset-right)); }
.controls { padding:.9rem 0 .2rem; }
.filters { margin-top:.55rem; }
.filters > summary { list-style:none; display:inline-flex; align-items:center;
  gap:.35rem; cursor:pointer; font-size:.78rem; font-weight:550;
  color:var(--dim); background:var(--card); border:1px solid var(--line);
  border-radius:999px; padding:.4rem .8rem; min-height:34px;
  touch-action:manipulation; }
.filters > summary::-webkit-details-marker { display:none; }
.filters > summary::after { content:"\25be"; font-size:.7rem; }
.filters[open] > summary::after { content:"\25b4"; }
.filters > summary:hover { border-color:var(--accent); color:var(--accent); }
.filters.active > summary { background:var(--signal-bg); color:var(--accent);
                            border-color:var(--accent); }
.search { position:relative; }
.search input {
  width:100%; background:var(--card); border:1px solid var(--line);
  border-radius:12px; padding:.7rem .8rem .7rem 2.15rem; min-height:44px;
  -webkit-appearance:none; appearance:none;
}
.search input:focus { outline:2px solid var(--accent); outline-offset:1px;
                      border-color:transparent; }
.search svg { position:absolute; left:.7rem; top:50%; transform:translateY(-50%);
              width:1rem; height:1rem; color:var(--faint); pointer-events:none; }
.chiprow { display:flex; gap:.4rem; flex-wrap:wrap; align-items:center;
           margin-top:.65rem; }
.chip {
  background:var(--card); border:1px solid var(--line); color:var(--dim);
  border-radius:999px; padding:.4rem .75rem; font-size:.78rem; font-weight:550;
  cursor:pointer; min-height:34px; touch-action:manipulation; white-space:nowrap;
}
.chip[aria-pressed="true"] { background:var(--signal-bg); color:var(--accent);
                             border-color:var(--accent); }
.chiprow label { font-size:.72rem; color:var(--faint); text-transform:uppercase;
                 letter-spacing:.06em; margin-right:.15rem; }
select.chip { -webkit-appearance:none; appearance:none;
              padding-right:1.6rem; max-width:14rem; text-overflow:ellipsis;
              background-image:linear-gradient(45deg,transparent 50%,currentColor 50%),
                               linear-gradient(135deg,currentColor 50%,transparent 50%);
              background-position:calc(100% - .85rem) 55%, calc(100% - .6rem) 55%;
              background-size:5px 5px, 5px 5px; background-repeat:no-repeat; }
.reset { margin-left:auto; background:none; border:0; color:var(--accent);
         font-size:.78rem; cursor:pointer; padding:.4rem .2rem; min-height:34px; }
.showing { font-size:.75rem; color:var(--faint); margin:.75rem 0 .2rem;
           font-variant-numeric:tabular-nums; }

/* ── job cards ──────────────────────────────────────────────────── */
.list { list-style:none; margin:0; padding:0;
        display:flex; flex-direction:column; gap:.6rem; }
.job { background:var(--card); border:1px solid var(--line);
       border-radius:var(--radius); padding:.8rem 1rem .85rem;
       box-shadow:var(--shadow); scroll-margin-top:9rem; }
.job.apply { border-left:3px solid var(--signal); }
.jhead { display:flex; align-items:center; gap:.45rem; margin-bottom:.4rem;
         flex-wrap:wrap; }
.pill { font-size:.7rem; font-weight:650; letter-spacing:.01em;
        padding:.2rem .5rem; border-radius:999px; white-space:nowrap;
        font-variant-numeric:tabular-nums; }
.pill.drive { background:var(--line-soft); color:var(--dim); }
.pill.drive.d0 { background:var(--near-bg); color:var(--near); }
.pill.new { background:var(--signal-bg); color:var(--signal); }
.pill.status { background:var(--watch-bg); color:var(--watch);
               text-transform:capitalize; }
.job h2 { font-size:1.02rem; line-height:1.32; margin:0 0 .2rem;
          font-weight:600; letter-spacing:-.005em; }
.job h2 a { text-decoration:none; }
.job h2 a:hover, .job h2 a:focus-visible { text-decoration:underline;
                                           text-underline-offset:2px; }
.where { margin:0; font-size:.85rem; color:var(--dim); }
.where b { font-weight:600; color:var(--ink); }
.vrow { display:flex; align-items:center; gap:.75rem; margin-top:.5rem; }
.verdict { display:inline-block; font-size:.76rem;
           font-weight:600; padding:.22rem .55rem; border-radius:6px;
           background:var(--line-soft); color:var(--dim); }
.verdict.good { background:var(--signal-bg); color:var(--signal); }
.verdict.watch { background:var(--watch-bg); color:var(--watch); }
.ev { margin:.35rem 0 0; }
.ev summary { list-style:none; cursor:pointer; font-size:.76rem;
              color:var(--accent); font-weight:550; padding:.3rem 0;
              min-height:30px; display:flex; align-items:center; gap:.3rem;
              touch-action:manipulation; }
.ev summary::-webkit-details-marker { display:none; }
.ev summary::after { content:"›"; transition:transform .15s;
                     display:inline-block; font-size:1rem; line-height:1; }
.ev[open] summary::after { transform:rotate(90deg); }
.ev blockquote { margin:.15rem 0 0; padding:.1rem 0 .1rem .8rem;
                 border-left:2px solid var(--line); color:var(--dim);
                 font-family:var(--read); font-size:.88rem; line-height:1.55; }
.ev .nonequote { font-style:italic; color:var(--faint); }
.open { margin-left:auto; flex:none; display:inline-flex; align-items:center;
        gap:.25rem; font-size:.79rem; font-weight:600; color:var(--accent);
        text-decoration:none; min-height:34px; white-space:nowrap; }
.open:hover { text-decoration:underline; text-underline-offset:2px; }

.empty { text-align:center; color:var(--dim); font-size:.9rem;
         padding:3rem 1rem; border:1px dashed var(--line);
         border-radius:var(--radius); }
.empty b { display:block; color:var(--ink); font-size:1rem; margin-bottom:.3rem; }

footer { max-width:52rem; margin:2.5rem auto 0;
         padding:1.25rem max(1rem,env(safe-area-inset-left))
                 calc(2rem + env(safe-area-inset-bottom));
         border-top:1px solid var(--line); color:var(--dim); font-size:.8rem; }
footer p { margin:0 0 .7rem; }
footer strong { color:var(--ink); }

/* ── reading mode ───────────────────────────────────────────────────
   One column of prose, serif, evidence already open, filters folded
   away. This is the mode for lying on the couch with a phone deciding
   what to apply to, not the one for hunting a specific employer. */
body.reading { --radius:0; }
body.reading .controls, body.reading .lede-s { display:none; }
body.reading main { max-width:36rem; }
body.reading .list { gap:0; }
body.reading .job { background:none; border:0; border-radius:0; box-shadow:none;
                    border-bottom:1px solid var(--line-soft);
                    padding:1.5rem 0 1.4rem; }
body.reading .job.apply { border-left:0; }
body.reading .job h2 { font-family:var(--read); font-size:1.28rem;
                       line-height:1.3; font-weight:600; }
body.reading .where { font-family:var(--read); font-size:1rem;
                      margin-top:.25rem; }
body.reading .ev summary { display:none; }
body.reading .ev blockquote { font-size:1rem; margin-top:.7rem; }
body.reading .open { font-size:.95rem; }
body.reading .showing { display:none; }
body.reading .textsize { display:flex; }
.textsize { display:none; gap:.35rem; align-items:center; margin:1rem 0 .4rem;
            font-size:.72rem; color:var(--faint); text-transform:uppercase;
            letter-spacing:.06em; }
.textsize button { width:36px; height:36px; border-radius:8px;
                   border:1px solid var(--line); background:var(--card);
                   color:var(--dim); cursor:pointer; font-weight:600;
                   touch-action:manipulation; }
.textsize button:hover { border-color:var(--accent); color:var(--accent); }

.rev { list-style:none; margin:0; padding:0; }
.rev li { padding:.85rem 0; border-bottom:1px solid var(--line-soft);
          font-size:.9rem; }
.rev span { display:block; color:var(--dim); font-size:.82rem; margin-top:.15rem; }

:focus-visible { outline:2px solid var(--accent); outline-offset:2px;
                 border-radius:4px; }
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior:auto; }
  * { transition:none !important; }
}
@media print {
  .topbar, .controls, .textsize, .readbtn { display:none; }
  .job { break-inside:avoid; box-shadow:none; }
}
</style>
</head>
<body>

<header class="topbar">
  <div class="inner">
    <div class="headrow">
      <div>
        <h1>RN openings <span>Staff nurse roles within two hours of Oakland</span></h1>
        <p class="stamp">Scanned __SCANNED__ UTC</p>
      </div>
      <button class="readbtn" id="readbtn" type="button" aria-pressed="false">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M8 3.5S6.5 2 4 2H1.5v11H4c2.5 0 4 1.5 4 1.5m0-11S9.5 2 12 2h2.5v11H12c-2.5 0-4 1.5-4 1.5m0-11v11"/></svg>
        <span id="readlabel">Reading mode</span>
      </button>
    </div>
    <nav class="tabs" id="tabs" role="tablist" aria-label="Job lists"></nav>
  </div>
</header>

<div class="lede inner" id="lede"></div>

<main>
  <section class="controls" id="controls">
    <div class="search">
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5 14 14"/></svg>
      <input id="q" type="search" inputmode="search" autocomplete="off"
             placeholder="Search role, employer or city"
             aria-label="Search role, employer or city">
    </div>
    <details class="filters" id="filters">
      <summary id="filtersummary">Filter &amp; sort</summary>
      <div class="chiprow" id="drivechips" role="group" aria-label="Drive time">
        <label>Drive</label>
      </div>
      <div class="chiprow">
        <label for="employer">Employer</label>
        <select class="chip" id="employer" aria-label="Filter by employer"></select>
        <select class="chip" id="sort" aria-label="Sort order">
          <option value="near">Nearest first</option>
          <option value="new">Newest first</option>
          <option value="employer">Employer</option>
          <option value="title">Role A&ndash;Z</option>
        </select>
        <button class="reset" id="reset" type="button">Clear</button>
      </div>
    </details>
  </section>

  <div class="textsize">
    Text size
    <button id="smaller" type="button" aria-label="Smaller text">A&minus;</button>
    <button id="bigger" type="button" aria-label="Larger text">A+</button>
  </div>

  <p class="showing" id="showing"></p>
  <ol class="list" id="list"></ol>
</main>

<footer class="inner">
  <p>Every card carries the requirement sentence its verdict rests on.
  <strong>If the quote does not support the label, the rule is wrong</strong>
  &mdash; that has happened six times in this project. Read the quote before
  you trust the label.</p>
  <p>Only acute-care-required roles are hidden. Everything else reaches you,
  including the ones marked unclear.</p>
  <p>To move a job into <strong>Applied</strong>, change its <strong>Status</strong>
  column in <code>applications.csv</code> from <code>unapplied</code> to
  <code>applied</code>. The scanner never overwrites that column, and a posting
  that disappears is marked closed rather than deleted.</p>
</footer>

<script id="jobs-data" type="application/json">__JOBS__</script>
<script id="review-data" type="application/json">__REVIEW__</script>
<script id="meta-data" type="application/json">__META__</script>
<script>
(function () {
  "use strict";
  var read = function (id) {
    try { return JSON.parse(document.getElementById(id).textContent); }
    catch (e) { return null; }
  };
  var JOBS = read("jobs-data") || [];
  var REVIEW = read("review-data") || [];
  var META = read("meta-data") || {};

  var APPLY_NOW = { STAFF_NURSE_I: 1, NO_EXPERIENCE: 1 };
  var DRIVE = ["<30", "30-60", "60-90", "90-120"];
  var DRIVE_LABEL = { "<30": "Under 30 min", "30-60": "30&ndash;60 min",
                      "60-90": "60&ndash;90 min", "90-120": "90&ndash;120 min" };

  // localStorage throws outright in some privacy modes rather than
  // returning null, so every touch of it is wrapped.
  var store = {
    get: function (k, d) {
      try { var v = localStorage.getItem("rn." + k); return v === null ? d : v; }
      catch (e) { return d; }
    },
    set: function (k, v) { try { localStorage.setItem("rn." + k, v); } catch (e) {} }
  };

  var TABS = [
    { id: "apply",  label: "Apply now",
      test: function (j) { return APPLY_NOW[j.bucket] && !j.applied; } },
    { id: "new",    label: "New",
      test: function (j) { return j.isNew && !j.applied; } },
    { id: "all",    label: "Everything",
      test: function (j) { return !j.applied; } },
    { id: "applied", label: "Applied",
      test: function (j) { return j.applied; } },
    { id: "review", label: "Check location", test: null }
  ];

  var state = {
    tab: store.get("tab", "apply"),
    q: "",
    drive: store.get("drive", ""),
    employer: store.get("employer", ""),
    sort: store.get("sort", "near")
  };
  if (!TABS.some(function (t) { return t.id === state.tab; })) state.tab = "apply";

  var esc = function (s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  };
  var plural = function (n, one, many) { return n === 1 ? one : (many || one + "s"); };

  function tabJobs(id) {
    var t = TABS.filter(function (x) { return x.id === id; })[0];
    return t && t.test ? JOBS.filter(t.test) : [];
  }

  function filtered() {
    var out = tabJobs(state.tab);
    if (state.drive) {
      out = out.filter(function (j) { return j.drive === state.drive; });
    }
    if (state.employer) {
      out = out.filter(function (j) { return j.employer === state.employer; });
    }
    var q = state.q.trim().toLowerCase();
    if (q) {
      var terms = q.split(/\s+/);
      out = out.filter(function (j) {
        var hay = (j.title + " " + j.employer + " " + j.location + " " +
                   j.label + " " + j.evidence).toLowerCase();
        return terms.every(function (t) { return hay.indexOf(t) !== -1; });
      });
    }
    var di = function (j) { var i = DRIVE.indexOf(j.drive); return i < 0 ? 9 : i; };
    var by = {
      near: function (a, b) {
        return di(a) - di(b) || a.employer.localeCompare(b.employer) ||
               a.title.localeCompare(b.title);
      },
      new: function (a, b) {
        return (b.isNew ? 1 : 0) - (a.isNew ? 1 : 0) || di(a) - di(b) ||
               a.title.localeCompare(b.title);
      },
      employer: function (a, b) {
        return a.employer.localeCompare(b.employer) || di(a) - di(b) ||
               a.title.localeCompare(b.title);
      },
      title: function (a, b) { return a.title.localeCompare(b.title); }
    };
    return out.slice().sort(by[state.sort] || by.near);
  }

  function reading() { return document.body.classList.contains("reading"); }

  function card(j) {
    var good = !!APPLY_NOW[j.bucket];
    var vcls = good ? " good" : (j.bucket === "UNCLEAR" ? " watch" : "");
    var d = j.drive
      ? '<span class="pill drive' + (j.drive === "<30" ? " d0" : "") + '">' +
        esc(j.drive) + ' min</span>'
      : '<span class="pill drive">drive time unknown</span>';
    var flags = d +
      (j.isNew ? '<span class="pill new">new</span>' : "") +
      (j.applied && j.status !== "unapplied"
        ? '<span class="pill status">' + esc(j.status) + "</span>" : "");

    // No requirement sentence is itself information: it means the posting
    // states no requirement, which is why the verdict is what it is. Say so
    // rather than showing an empty quote.
    var quote = j.evidence
      ? "<blockquote>&ldquo;" + esc(j.evidence) + "&rdquo;</blockquote>"
      : '<blockquote class="nonequote">This posting states no requirement at ' +
        'all. The verdict rests on that absence, not on a sentence.</blockquote>';

    var title = j.url
      ? '<a href="' + esc(j.url) + '" target="_blank" rel="noopener noreferrer">' +
        esc(j.title) + "</a>"
      : esc(j.title);

    return '<li class="job' + (good ? " apply" : "") + '">' +
      '<div class="jhead">' + flags + "</div>" +
      "<h2>" + title + "</h2>" +
      '<p class="where"><b>' + esc(j.employer) + "</b>" +
        (j.location ? " &middot; " + esc(j.location) : "") + "</p>" +
      '<div class="vrow"><span class="verdict' + vcls + '">' + esc(j.label) +
        "</span>" +
        (j.url ? '<a class="open" href="' + esc(j.url) + '" target="_blank" ' +
          'rel="noopener noreferrer">Open &#8599;</a>' : "") + "</div>" +
      '<details class="ev"' + (reading() ? " open" : "") +
        "><summary>Why it is labelled that</summary>" + quote + "</details>" +
      "</li>";
  }

  function reviewList() {
    if (!REVIEW.length) {
      return '<p class="empty"><b>Every location resolved.</b>' +
             "Nothing needs a manual check this run.</p>";
    }
    return '<ul class="rev">' + REVIEW.map(function (r) {
      var t = r.url
        ? '<a href="' + esc(r.url) + '" target="_blank" rel="noopener noreferrer">' +
          esc(r.title) + "</a>"
        : esc(r.title);
      return "<li>" + t + "<span>" + esc(r.employer) +
             (r.location ? " &middot; " + esc(r.location) : "") + "</span></li>";
    }).join("") + "</ul>";
  }

  var EMPTY = {
    apply: ["Nothing entry-level open right now.",
            "The scan runs three times a day. Check the other tabs to see what is out there."],
    new: ["Nothing new this run.", "Same list as last time."],
    all: ["No openings match.", "Try clearing the filters."],
    applied: ["Nothing sent yet.",
              "Set Status to applied in applications.csv and it will appear here."]
  };

  function render() {
    var listEl = document.getElementById("list");
    var showEl = document.getElementById("showing");

    if (state.tab === "review") {
      listEl.innerHTML = reviewList();
      showEl.textContent = REVIEW.length
        ? REVIEW.length + " " + plural(REVIEW.length, "posting") +
          " whose location the geocoder could not place"
        : "";
      document.getElementById("controls").style.display = "none";
    } else {
      document.getElementById("controls").style.display = "";
      var rows = filtered();
      var total = tabJobs(state.tab).length;
      if (rows.length) {
        listEl.innerHTML = rows.map(card).join("");
        showEl.textContent = rows.length === total
          ? rows.length + " " + plural(rows.length, "opening")
          : "Showing " + rows.length + " of " + total;
      } else {
        var e = EMPTY[state.tab] || EMPTY.all;
        listEl.innerHTML = '<p class="empty"><b>' + esc(e[0]) + "</b>" +
                           esc(e[1]) + "</p>";
        showEl.textContent = "";
      }
    }

    Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (b) {
      b.setAttribute("aria-selected", b.dataset.tab === state.tab ? "true" : "false");
    });
    Array.prototype.forEach.call(document.querySelectorAll("#drivechips .chip"),
      function (b) {
        b.setAttribute("aria-pressed", b.dataset.drive === state.drive ? "true" : "false");
      });

    // A collapsed filter that is still filtering is how a list silently
    // lies about being empty. Say so on the closed summary.
    var on = [];
    if (state.drive) on.push(DRIVE_LABEL[state.drive].replace("&ndash;", "\u2013"));
    if (state.employer) on.push(state.employer);
    if (state.sort !== "near") {
      on.push(document.getElementById("sort").selectedOptions[0].text);
    }
    var fil = document.getElementById("filters");
    fil.classList.toggle("active", on.length > 0);
    document.getElementById("filtersummary").textContent =
      on.length ? on.join(" \u00b7 ") : "Filter & sort";
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function buildChrome() {
    document.getElementById("tabs").innerHTML = TABS.map(function (t) {
      var n = t.id === "review" ? REVIEW.length : tabJobs(t.id).length;
      return '<button class="tab" role="tab" type="button" data-tab="' + t.id +
        '" aria-selected="false"><b>' + t.label +
        '</b><span class="n">' + n + "</span></button>";
    }).join("");

    var chips = document.getElementById("drivechips");
    chips.insertAdjacentHTML("beforeend",
      '<button class="chip" type="button" data-drive="" aria-pressed="true">Any</button>' +
      DRIVE.map(function (d) {
        return '<button class="chip" type="button" data-drive="' + d +
               '" aria-pressed="false">' + DRIVE_LABEL[d] + "</button>";
      }).join(""));

    var names = {};
    JOBS.forEach(function (j) { if (j.employer) names[j.employer] = 1; });
    var sel = document.getElementById("employer");
    sel.innerHTML = '<option value="">All employers</option>' +
      Object.keys(names).sort().map(function (n) {
        return '<option value="' + esc(n) + '">' + esc(n) + "</option>";
      }).join("");

    var apply = JOBS.filter(function (j) { return APPLY_NOW[j.bucket]; }).length;
    var watch = JOBS.length - apply;
    var newN = JOBS.filter(function (j) { return j.isNew; }).length;
    var hid = META.hidden || 0;
    document.getElementById("lede").innerHTML =
      '<p class="lede-t"><span class="lede-n">' + apply + "</span>" +
        plural(apply, "opening") + " you are eligible for today &mdash; " +
        "Level&nbsp;I or no experience required." +
        (newN ? " <b>" + newN + " new since the last scan.</b>" : "") + "</p>" +
      '<p class="lede-s">' +
        (watch ? watch + " other " + plural(watch, "posting") +
          " need experience you do not have yet; they are here to watch, not " +
          "to apply to. " : "") +
        (hid ? hid + " acute-care-required " + plural(hid, "posting") +
          " hidden." : "") +
        (META.quick ? " Quick mode: requirements were not analysed." : "") +
        "</p>";
  }

  function wire() {
    document.getElementById("tabs").addEventListener("click", function (e) {
      var b = e.target.closest(".tab");
      if (!b) return;
      state.tab = b.dataset.tab; store.set("tab", state.tab); render();
    });
    document.getElementById("drivechips").addEventListener("click", function (e) {
      var b = e.target.closest(".chip");
      if (!b) return;
      state.drive = b.dataset.drive; store.set("drive", state.drive); render();
    });
    var q = document.getElementById("q");
    q.addEventListener("input", function () { state.q = q.value; render(); });
    var emp = document.getElementById("employer");
    emp.value = state.employer;
    emp.addEventListener("change", function () {
      state.employer = emp.value; store.set("employer", state.employer); render();
    });
    var sort = document.getElementById("sort");
    sort.value = state.sort;
    sort.addEventListener("change", function () {
      state.sort = sort.value; store.set("sort", state.sort); render();
    });
    document.getElementById("reset").addEventListener("click", function () {
      state.q = ""; state.drive = ""; state.employer = ""; state.sort = "near";
      q.value = ""; emp.value = ""; sort.value = "near";
      store.set("drive", ""); store.set("employer", ""); store.set("sort", "near");
      render();
    });

    var btn = document.getElementById("readbtn");
    function setReading(on) {
      document.body.classList.toggle("reading", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      document.getElementById("readlabel").textContent = on ? "Reading on" : "Reading mode";
      store.set("reading", on ? "1" : "0");
    }
    btn.addEventListener("click", function () {
      var on = !document.body.classList.contains("reading");
      setReading(on);
      // Cards already in the DOM keep whatever they were rendered with,
      // so the quotes have to be opened (or left alone) by hand here.
      Array.prototype.forEach.call(document.querySelectorAll(".ev"),
        function (d) { if (on) d.open = true; });
    });
    setReading(store.get("reading", "0") === "1");

    var scale = parseFloat(store.get("scale", "1")) || 1;
    function setScale(v) {
      scale = Math.min(1.5, Math.max(0.85, Math.round(v * 100) / 100));
      document.documentElement.style.setProperty("--scale", scale);
      store.set("scale", scale);
    }
    setScale(scale);
    document.getElementById("bigger").addEventListener("click", function () {
      setScale(scale + 0.1);
    });
    document.getElementById("smaller").addEventListener("click", function () {
      setScale(scale - 0.1);
    });

    // "/" focuses search on a desktop keyboard; harmless on a phone.
    document.addEventListener("keydown", function (e) {
      if (e.key === "/" && document.activeElement !== q) { e.preventDefault(); q.focus(); }
      if (e.key === "Escape" && document.activeElement === q) { q.blur(); }
    });
  }

  buildChrome();
  wire();
  render();
})();
</script>
</body>
</html>
"""


def _check():
    """
    Cheap proof the template still renders, for the workflow to run before
    a scan starts.

    A syntax error in here would otherwise surface ten minutes into a run,
    after every source had been fetched, and take the digest down with it.
    This costs milliseconds and catches an unclosed placeholder, a broken
    f-string, or JSON that will not parse in the browser.
    """
    import re

    page = render(
        [record(key="X::1", title="RN <script>alert(1)</script>",
                employer="Test & Co", location="Oakland",
                url="https://example.invalid/j?a=1&b=2", bucket="NO_EXPERIENCE",
                evidence='He said "no experience" </script> needed',
                drive="<30", posted="", status="unapplied", is_new=True)],
        [], 7, "2026-01-01T00:00:00", quick=False)

    assert "__JOBS__" not in page and "__META__" not in page, "placeholder left"
    assert "<script>alert(1)</script>" not in page, "posting title not escaped"
    for tag in ("jobs-data", "review-data", "meta-data"):
        blob = re.search(r'id="%s"[^>]*>(.*?)</script>' % tag, page, re.S).group(1)
        json.loads(blob.replace("<\\/", "</"))
    print("digest_page: template renders, data parses, markup escaped")
    return 0


def _main():
    """Rebuild digest.html from the committed ledger, without a scan."""
    if "--check" in sys.argv:
        return _check()
    jobs = from_ledger()
    try:
        mtime = os.path.getmtime("applications.csv")
        now = datetime.fromtimestamp(mtime, timezone.utc).isoformat(
            timespec="seconds")
    except OSError:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    html_out = render(jobs, [], 0, now)
    with open("digest.html", "w") as f:
        f.write(html_out)
    apply_now = sum(1 for j in jobs if j["bucket"] in APPLY_NOW)
    print(f"wrote digest.html from applications.csv — {len(jobs)} openings, "
          f"{apply_now} eligible today")
    print("note: rebuilt from the ledger, so the hidden-count and the "
          "location-review list are empty. A real scan fills those in.")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
