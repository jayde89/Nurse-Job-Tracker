#!/usr/bin/env python3
"""
Build board.html — the standing list, the copy meant to be read in Claude.

  python3 board.py          rebuild board.html from applications.csv

Why this exists, and why it is not the digest.

The digest is a report: it says what the scan found this run. The alert it
used to send was narrower still — only postings that were BOTH new and
applicable — and that is what a mail route forces on you, because three
"here is the same list again" mails a day is how an alert stops being read.
The cost was that a job you did not act on the morning it appeared was never
put in front of you again. It was in DIGEST.md, under a couple of hundred
rows, which is not the same as being shown to you.

So the board is a worklist, not a report. Every open posting stays on it,
run after run, until it is ticked off. Nothing drops off because it stopped
being new.

Ticking happens in the page: each posting has **Applied** and **Not
relevant**, and the marks live in the artifact's own store, keyed by the
ledger Key. That key is what makes the loop work — the scan rewrites this
file three times a day and the board gets republished over itself, and the
marks survive both, because they were never stored in the HTML.

`sync_board.py` folds those marks back into applications.csv, which is
still the record. The board is where you decide; the ledger is what
remembers.
"""

from __future__ import annotations

import csv
import json
import re
import sys

LEDGER_PATH = "applications.csv"
BOARD_PATH = "board.html"

# Tiers are eligibility, which is the only ordering that matters when you
# are a new grad: what you can apply to today, what nobody can tell from
# the posting, and what needs experience you do not have yet. The bucket
# labels are the ones the ledger stores.
TIER_OF = {
    "Level I / new grad": "ready",
    "No experience required": "ready",
    "Requirements unclear": "unclear",
    "Experience required, not acute": "experience",
}

DRIVE_ORDER = {"<30": 0, "30-60": 1, "60-90": 2, "90-120": 3}
# Minutes, for the "within N minutes" filter. A bucket counts as its far end.
DRIVE_MAX = {"<30": 30, "30-60": 60, "60-90": 90, "90-120": 120}


def doc_id(key: str) -> str:
    """
    A stable artifact-store document id for a ledger key.

    Keys look like `PACS Group::JR179656` and the store's id grammar has no
    room for the space. Slugging alone could collide two employers into one
    id and silently merge their marks, so the slug carries a hash of the
    original key. The full key is written into the document body as well —
    sync reads that, never the id, so a change to this function can never
    reattach a mark to the wrong posting.
    """
    import hashlib
    slug = re.sub(r"[^A-Za-z0-9_.~:@+-]", "-", key)[:150].strip("-") or "job"
    return f"{slug}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"


def load_ledger(path=LEDGER_PATH) -> list:
    with open(path, newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("Key")]


def job(row) -> dict:
    """One posting, as the page needs it. Every field is from the ledger."""
    bucket = (row.get("Bucket") or "").strip()
    drive = (row.get("Drive time") or "").strip()
    return {
        "id": doc_id(row["Key"]),
        "key": row["Key"],
        "title": (row.get("Title") or "Untitled posting").strip(),
        "url": (row.get("URL") or "").strip(),
        "employer": (row.get("Employer") or "").strip(),
        "location": (row.get("Location") or "").strip(),
        "drive": drive,
        "driveMax": DRIVE_MAX.get(drive, 999),
        "bucket": bucket,
        "tier": TIER_OF.get(bucket, "unclear"),
        "details": (row.get("Details") or "").strip(),
        "evidence": (row.get("Requirement evidence") or "").strip(),
        "posted": (row.get("Posted") or "").strip(),
        "firstSeen": (row.get("First seen") or "")[:10],
    }


def application(row) -> dict:
    return {
        "key": row["Key"],
        "status": (row.get("Status") or "").strip().lower(),
        "title": (row.get("Title") or "Untitled posting").strip(),
        "url": (row.get("URL") or "").strip(),
        "employer": (row.get("Employer") or "").strip(),
        "location": (row.get("Location") or "").strip(),
        "since": ((row.get("Applied On") or "").strip()
                  or (row.get("Marked active") or "").strip()),
        "lastSeen": (row.get("Last seen") or "")[:10],
    }


def collect(ledger_rows):
    """Split the ledger into what the board draws. Read through run_scan's
    own status helpers so the board and the digest cannot disagree about
    what "already handled" means."""
    import run_scan as R

    jobs, active = [], []
    for row in ledger_rows:
        status = row.get("Status")
        if R.is_open(status):
            jobs.append(job(row))
        elif R.is_active(status):
            active.append(application(row))
    jobs.sort(key=lambda j: (DRIVE_ORDER.get(j["drive"], 9),
                             j["employer"].lower(), j["title"].lower()))
    active.sort(key=lambda a: (R.ACTIVE_ORDER.get(a["status"], 9),
                               a["since"], a["employer"]))
    return jobs, active


def write(ledger, now="", path=BOARD_PATH):
    """Called by the scan. `ledger` is the dict run_scan keeps, or a list."""
    rows = list(ledger.values()) if isinstance(ledger, dict) else list(ledger)
    jobs, active = collect(rows)
    payload = {
        "generated": now,
        "jobs": jobs,
        "active": active,
        "tracked": len(rows),
    }
    with open(path, "w") as f:
        f.write(render(payload))
    return payload


def render(payload) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return TEMPLATE.replace("__DATA__", data)


TEMPLATE = r"""<title>Oakland RN Board</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap">
<style>
  :root {
    color-scheme: light;
    --ground:#f5f7f5; --surface:#ffffff; --raise:#fbfcfb;
    --ink:#1a231f; --dim:#5f6d66; --faint:#8a968f;
    --line:#dee4e0; --hair:#e9edeb;
    --accent:#0e6a57; --accent-ink:#0b5344; --accent-soft:#e2efeb;
    --caution:#8a5a12; --caution-soft:#f6edda;
    --shadow:0 1px 2px rgba(26,35,31,.06);
    --serif:"Source Serif 4",Charter,Georgia,serif;
    --sans:"IBM Plex Sans",system-ui,-apple-system,Segoe UI,sans-serif;
    --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --ground:#0f1412; --surface:#161c19; --raise:#1b2320;
      --ink:#e8edea; --dim:#9aa7a0; --faint:#75827b;
      --line:#2a332e; --hair:#222a26;
      --accent:#5ccfad; --accent-ink:#8fe3c9; --accent-soft:#16302a;
      --caution:#d8a755; --caution-soft:#2d2618;
      --shadow:0 1px 2px rgba(0,0,0,.35);
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --ground:#0f1412; --surface:#161c19; --raise:#1b2320;
    --ink:#e8edea; --dim:#9aa7a0; --faint:#75827b;
    --line:#2a332e; --hair:#222a26;
    --accent:#5ccfad; --accent-ink:#8fe3c9; --accent-soft:#16302a;
    --caution:#d8a755; --caution-soft:#2d2618;
    --shadow:0 1px 2px rgba(0,0,0,.35);
  }

  *{box-sizing:border-box}
  body{margin:0;background:var(--ground);color:var(--ink);
       font-family:var(--sans);font-size:15px;line-height:1.55;
       -webkit-font-smoothing:antialiased}
  .wrap{max-width:53rem;margin:0 auto;padding-inline:16px;
        padding-block:2.25rem 5rem}
  a{color:inherit}
  h1{font-family:var(--serif);font-size:1.75rem;font-weight:600;
     letter-spacing:-.015em;margin:0 0 .3rem;text-wrap:balance}
  .stamp{color:var(--dim);font-size:.82rem;margin:0}
  .stamp b{font-weight:500;color:var(--ink)}

  header{border-bottom:1px solid var(--line);padding-bottom:1.1rem}
  .ledebar{display:flex;flex-wrap:wrap;gap:.4rem .5rem;margin-top:.9rem}
  .count{font-family:var(--mono);font-size:.72rem;letter-spacing:.02em;
         padding:.2rem .5rem;border:1px solid var(--line);border-radius:3px;
         background:var(--surface);color:var(--dim);white-space:nowrap;
         font-variant-numeric:tabular-nums}
  .count.go{color:var(--accent-ink);border-color:var(--accent);
            background:var(--accent-soft)}

  .tools{position:sticky;top:0;z-index:5;background:var(--ground);
         display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;
         padding-block:.7rem;border-bottom:1px solid var(--hair);
         margin-bottom:.25rem}
  .tools input[type=search]{flex:1 1 13rem;min-width:0;font:inherit;
         font-size:.88rem;padding:.42rem .6rem;border:1px solid var(--line);
         border-radius:4px;background:var(--surface);color:var(--ink)}
  .tools select{font:inherit;font-size:.88rem;padding:.42rem .5rem;
         border:1px solid var(--line);border-radius:4px;
         background:var(--surface);color:var(--ink)}
  .shown{font-family:var(--mono);font-size:.72rem;color:var(--faint);
         font-variant-numeric:tabular-nums;margin-left:auto}

  h2{font-family:var(--sans);font-size:.74rem;font-weight:600;
     text-transform:uppercase;letter-spacing:.09em;color:var(--dim);
     margin:2.4rem 0 .2rem}
  h2 .n{font-family:var(--mono);color:var(--faint);font-weight:400}
  section > p.lede{color:var(--dim);font-size:.85rem;margin:.3rem 0 .9rem;
     max-width:44rem}

  .list{list-style:none;margin:0;padding:0}
  .row{display:grid;grid-template-columns:4.6rem 1fr;gap:0 1rem;
       padding:1.05rem 0;border-top:1px solid var(--hair)}
  .row:first-child{border-top:1px solid var(--line)}
  .drive{font-family:var(--mono);font-size:.74rem;color:var(--dim);
         padding-top:.2rem;font-variant-numeric:tabular-nums;
         white-space:nowrap}
  .drive b{display:block;font-weight:500;color:var(--ink);font-size:.86rem}
  .body{min-width:0}
  .row h3{font-family:var(--serif);font-size:1.08rem;font-weight:600;
          margin:0 0 .12rem;line-height:1.3}
  .row h3 a{text-decoration:none;border-bottom:1px solid var(--line);
            border-bottom-color:color-mix(in srgb,var(--ink) 28%,transparent)}
  .row h3 a:hover,.row h3 a:focus-visible{border-bottom-color:var(--accent);
            color:var(--accent-ink)}
  .detail{margin:.1rem 0 .18rem;font-size:.85rem;color:var(--ink)}
  .where{margin:0;font-size:.83rem;color:var(--dim)}
  .quote{font-family:var(--serif);font-size:.88rem;color:var(--dim);
         margin:.5rem 0 0;padding-left:.8rem;
         border-left:2px solid var(--line)}
  .verdict{display:inline-block;font-family:var(--mono);font-size:.68rem;
           letter-spacing:.04em;text-transform:uppercase;padding:.12rem .42rem;
           border-radius:3px;margin-top:.45rem;
           background:var(--raise);color:var(--dim);border:1px solid var(--line)}
  .ready .verdict{background:var(--accent-soft);color:var(--accent-ink);
           border-color:var(--accent)}
  .unclear .verdict{background:var(--caution-soft);color:var(--caution);
           border-color:var(--caution)}

  .marks{display:flex;gap:.45rem;flex-wrap:wrap;margin-top:.6rem}
  button{font:inherit;font-size:.78rem;font-family:var(--sans);
         padding:.3rem .66rem;border-radius:4px;border:1px solid var(--line);
         background:var(--surface);color:var(--dim);cursor:pointer}
  button:hover{border-color:var(--accent);color:var(--accent-ink)}
  button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  button.on{background:var(--accent-soft);border-color:var(--accent);
            color:var(--accent-ink);font-weight:500}
  button.ghost{border-style:dashed}
  button[disabled]{opacity:.5;cursor:default}

  .app{display:grid;grid-template-columns:6.2rem 1fr;gap:0 1rem;
       padding:.8rem 0;border-top:1px solid var(--hair)}
  .app:first-child{border-top:1px solid var(--line)}
  .status{font-family:var(--mono);font-size:.7rem;text-transform:uppercase;
          letter-spacing:.05em;color:var(--accent-ink);padding-top:.22rem}
  .app h3{font-family:var(--serif);font-size:1rem;font-weight:600;margin:0}
  .app h3 a{text-decoration:none;border-bottom:1px solid var(--line)}

  details{margin-top:.4rem}
  summary{cursor:pointer;font-size:.78rem;font-weight:600;
          text-transform:uppercase;letter-spacing:.09em;color:var(--dim);
          padding:.5rem 0}
  summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  summary .n{font-family:var(--mono);font-weight:400;color:var(--faint)}

  .empty{color:var(--faint);font-size:.87rem;font-style:italic;
         padding:.9rem 0;border-top:1px solid var(--line)}
  .saving{font-family:var(--mono);font-size:.72rem;color:var(--faint);
          margin:.6rem 0 0}
  footer{margin-top:3.5rem;padding-top:1.1rem;border-top:1px solid var(--line);
         color:var(--dim);font-size:.82rem}
  footer p{margin:0 0 .6rem;max-width:44rem}
  code{font-family:var(--mono);font-size:.85em;background:var(--raise);
       padding:.08em .3em;border-radius:3px;border:1px solid var(--hair)}

  @media (max-width:33rem){
    .row,.app{grid-template-columns:1fr;gap:.3rem}
    .drive,.status{padding-top:0}
    .drive b{display:inline}
  }
  @media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>

<div class="wrap">
<header>
  <h1>Staff RN openings within two hours of Oakland</h1>
  <p class="stamp" id="stamp"></p>
  <div class="ledebar" id="counts"></div>
</header>

<div class="tools">
  <input id="q" type="search" aria-label="Search postings"
         placeholder="Search title, employer, city…" autocomplete="off">
  <select id="drive" aria-label="Maximum drive time">
    <option value="999">Any drive</option>
    <option value="30">Within 30 min</option>
    <option value="60">Within 60 min</option>
    <option value="90">Within 90 min</option>
  </select>
  <span class="shown" id="shown"></span>
</div>

<main id="main"></main>

<footer>
  <p id="saving" class="saving"></p>
  <p><b>Nothing leaves this list on its own.</b> Every open posting stays
  here, scan after scan, until you mark it. That is the whole difference
  from the old email, which only ever told you about postings that were new
  that morning.</p>
  <p>Each posting shows the requirement sentence its verdict rests on. If a
  quote does not support its label, the rule is wrong — that has happened
  six times in this project. Read the quote before trusting the label.
  Postings that require acute-care experience are not listed at all.</p>
  <p>Your marks are folded back into <code>applications.csv</code>, which
  stays the record. Ask Claude to refresh the board after a scan.</p>
</footer>
</div>

<script type="application/json" id="payload">__DATA__</script>
<script>
(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("payload").textContent);
  var JOBS = DATA.jobs || [], ACTIVE = DATA.active || [];
  var BY_ID = {};
  JOBS.forEach(function (j) { BY_ID[j.id] = j; });

  // marks: id -> {mark: "applied"|"not-relevant", at}
  var marks = {};
  var store = null;                 // the artifact store, once it answers
  var pending = {};                 // ticks made before the store answered
  var LOCAL = "rnboard.marks.v1";

  var TIERS = [
    { key: "ready", title: "Worth applying to now",
      lede: "Level I roles and postings that state no experience requirement. This is the list that matters." },
    { key: "unclear", title: "Requirements unclear",
      lede: "The posting never says what it needs. Shown on purpose — a job you cannot rule out is not a job to hide." },
    { key: "experience", title: "Experience required",
      lede: "Not acute care, but more experience than you have yet. Here so you can see them coming.", fold: true }
  ];

  var el = document.getElementById.bind(document);
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ---- filters -------------------------------------------------------
  var q = "", maxDrive = 999;
  function matches(j) {
    if (j.driveMax > maxDrive) return false;
    if (!q) return true;
    var hay = (j.title + " " + j.employer + " " + j.location + " " +
               j.details + " " + j.bucket).toLowerCase();
    return hay.indexOf(q) !== -1;
  }

  // ---- rendering -----------------------------------------------------
  function driveCell(j) {
    if (!j.drive) return '<div class="drive">&mdash;</div>';
    return '<div class="drive"><b>' + esc(j.drive) + '</b>min drive</div>';
  }

  function jobRow(j) {
    var mark = marks[j.id] && marks[j.id].mark;
    var link = j.url
      ? '<a href="' + esc(j.url) + '" target="_blank" rel="noopener noreferrer">' + esc(j.title) + '</a>'
      : esc(j.title);
    var where = esc(j.employer) + (j.location ? " &middot; " + esc(j.location) : "");
    if (j.posted) where += ' &middot; posted ' + esc(j.posted);
    return '<li class="row ' + esc(j.tier) + '" data-id="' + esc(j.id) + '">' +
      driveCell(j) +
      '<div class="body">' +
        '<h3>' + link + '</h3>' +
        (j.details ? '<p class="detail">' + esc(j.details) + '</p>' : '') +
        '<p class="where">' + where + '</p>' +
        '<span class="verdict">' + esc(j.bucket) + '</span>' +
        (j.evidence ? '<p class="quote">' + esc(j.evidence) + '</p>' : '') +
        '<div class="marks">' +
          btn(j.id, "applied", "Applied", mark === "applied") +
          btn(j.id, "not-relevant", "Not relevant", mark === "not-relevant") +
        '</div>' +
      '</div></li>';
  }

  function btn(id, mark, label, on) {
    return '<button type="button" id="mk-' + esc(id) + '-' + (mark || "clear") + '"' +
      ' data-id="' + esc(id) + '" data-mark="' + mark + '"' +
      (mark ? ' aria-pressed="' + (on ? "true" : "false") + '"' : ' class="ghost"') +
      (on ? ' class="on"' : "") + '>' + esc(label) + "</button>";
  }

  function markedRow(j, label) {
    var link = j.url
      ? '<a href="' + esc(j.url) + '" target="_blank" rel="noopener noreferrer">' + esc(j.title) + '</a>'
      : esc(j.title);
    return '<li class="app" data-id="' + esc(j.id) + '">' +
      '<div class="status">' + esc(label) + '</div>' +
      '<div class="body"><h3>' + link + '</h3>' +
        '<p class="where">' + esc(j.employer) +
          (j.location ? " &middot; " + esc(j.location) : "") + '</p>' +
        '<div class="marks">' + btn(j.id, "", "Put it back", false) + '</div>' +
      '</div></li>';
  }

  function ledgerRow(a) {
    var link = a.url
      ? '<a href="' + esc(a.url) + '" target="_blank" rel="noopener noreferrer">' + esc(a.title) + '</a>'
      : esc(a.title);
    return '<li class="app"><div class="status">' + esc(a.status) + '</div>' +
      '<div class="body"><h3>' + link + '</h3>' +
      '<p class="where">' + esc(a.employer) +
        (a.location ? " &middot; " + esc(a.location) : "") +
        (a.since ? " &middot; since " + esc(a.since) : "") + '</p>' +
      '</div></li>';
  }

  var unfolded = {};   // sections the reader opened, kept across redraws

  function section(title, n, lede, inner, fold) {
    var lead = lede ? '<p class="lede">' + lede + '</p>' : '';
    var count = ' <span class="n">' + n + '</span>';
    if (fold) {
      return '<section><details data-fold="' + esc(title) + '"' +
        (unfolded[title] ? " open" : "") + '><summary>' + esc(title) +
        count + '</summary>' + lead + inner + '</details></section>';
    }
    return '<section><h2>' + esc(title) + count + '</h2>' + lead +
      inner + '</section>';
  }

  function list(items, empty) {
    if (!items.length) return '<p class="empty">' + esc(empty) + '</p>';
    return '<ul class="list">' + items.join("") + '</ul>';
  }

  function draw() {
    var open = [], applied = [], dropped = [];
    JOBS.forEach(function (j) {
      var m = marks[j.id] && marks[j.id].mark;
      if (m === "applied") applied.push(j);
      else if (m === "not-relevant") dropped.push(j);
      else open.push(j);
    });

    var visible = open.filter(matches);
    var html = "";

    if (ACTIVE.length || applied.length) {
      var rows = ACTIVE.map(ledgerRow)
        .concat(applied.map(function (j) { return markedRow(j, "applied"); }));
      html += section("In progress", ACTIVE.length + applied.length,
        "Applications you have marked. They stay here whatever happens to the posting.",
        list(rows, ""));
    }

    TIERS.forEach(function (t) {
      var items = visible.filter(function (j) { return j.tier === t.key; });
      var all = open.filter(function (j) { return j.tier === t.key; });
      var empty = all.length
        ? "Nothing here matches the filter."
        : (t.key === "ready"
            ? "Nothing entry-level open right now. Check the other two lists."
            : "Nothing in this list.");
      html += section(t.title, items.length, t.lede,
                      list(items.map(jobRow), empty), t.fold && !q);
    });

    if (dropped.length) {
      html += section("Not relevant", dropped.length,
        "Ticked off. Kept here so they cannot come back as new postings — and so you can undo one.",
        list(dropped.map(function (j) { return markedRow(j, "dropped"); }), ""),
        true);
    }

    el("main").innerHTML = html;
    el("shown").textContent = visible.length + " of " + open.length + " shown";
  }

  function drawCounts() {
    var open = JOBS.filter(function (j) { return !marks[j.id]; });
    var n = { ready: 0, unclear: 0, experience: 0 };
    open.forEach(function (j) { n[j.tier]++; });
    el("counts").innerHTML =
      '<span class="count go">' + n.ready + ' you can apply to</span>' +
      '<span class="count">' + n.unclear + ' requirements unclear</span>' +
      '<span class="count">' + n.experience + ' need experience</span>' +
      '<span class="count">' + (DATA.tracked || 0) + ' tracked in all</span>';
  }

  function stamp() {
    var when = (DATA.generated || "").replace("T", " ").slice(0, 16);
    el("stamp").innerHTML = when
      ? 'Last scan <b>' + esc(when) + ' UTC</b>. Every open posting stays on this board until you mark it.'
      : 'Every open posting stays on this board until you mark it.';
  }

  // ---- marks ---------------------------------------------------------
  function localSave() {
    try { localStorage.setItem(LOCAL, JSON.stringify(marks)); } catch (e) {}
  }
  function localLoad() {
    try {
      var raw = localStorage.getItem(LOCAL);
      if (raw) marks = JSON.parse(raw) || {};
    } catch (e) { marks = {}; }
  }

  function setMark(id, mark) {
    var jb = BY_ID[id];
    if (!jb) return;
    if (mark) {
      marks[id] = { mark: mark, at: new Date().toISOString() };
    } else {
      delete marks[id];
    }
    localSave();
    drawCounts();
    draw();
    // The store answers a second or so after the page draws, and a tick
    // made in that window would otherwise be overwritten by the first
    // snapshot. Hold it and send it the moment the store is there.
    if (!store) { pending[id] = mark; return; }
    push(id, mark);
  }

  function push(id, mark) {
    var jb = BY_ID[id];
    if (!jb || !store) return Promise.resolve();
    var ref = store.doc("marks/" + id);
    return (mark
      ? ref.set({ key: jb.key, mark: mark, at: new Date().toISOString(),
                  title: jb.title, employer: jb.employer, url: jb.url })
      : ref.delete()
    ).catch(function (e) {
      note("Could not save that mark (" + (e && e.code ? e.code : "error") +
           "). It is held in this browser; try ticking it again.");
    });
  }

  function note(text) { el("saving").textContent = text; }

  document.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest("button[data-id]");
    if (!b) return;
    var id = b.getAttribute("data-id");
    var mark = b.getAttribute("data-mark");
    if (mark && marks[id] && marks[id].mark === mark) mark = "";
    setMark(id, mark);
  });

  document.addEventListener("toggle", function (ev) {
    var d = ev.target;
    if (d && d.tagName === "DETAILS" && d.hasAttribute("data-fold")) {
      unfolded[d.getAttribute("data-fold")] = d.open;
    }
  }, true);

  el("q").addEventListener("input", function (e) {
    q = e.target.value.trim().toLowerCase();
    draw();
  });
  el("drive").addEventListener("change", function (e) {
    maxDrive = parseInt(e.target.value, 10) || 999;
    draw();
  });

  // ---- boot ----------------------------------------------------------
  localLoad();
  stamp();
  drawCounts();
  draw();
  note("Saved in this browser only — connecting…");

  if (window.claude && window.claude.use) {
    window.claude.use("db").then(function (db) {
      if (!db) {
        note("Saved in this browser only. Marks on another device will not appear here.");
        return;
      }
      store = db;
      db.collection("marks").onSnapshot(function (snap) {
        var next = {};
        snap.docs.forEach(function (d) {
          var v = d.data() || {};
          if (v.mark) next[d.id] = { mark: v.mark, at: v.at || "" };
        });
        marks = next;
        localSave();
        drawCounts();
        draw();
        note("Marks saved to this board. Ask Claude to fold them into applications.csv.");
      }, function (e) {
        note("Marks are held in this browser (" +
             (e && e.code ? e.code : "store unavailable") + ").");
      });
    }, function () {
      note("Saved in this browser only.");
    });
  } else {
    note("Saved in this browser only.");
  }
})();
</script>
"""

def main():
    rows = load_ledger()
    # Run by hand, the freshest "Last seen" in the ledger is when the data
    # is from — there is no scan happening to ask.
    last = max((r.get("Last seen") or "" for r in rows), default="")
    payload = write(rows, now=last)
    print(f"board: {len(payload['jobs'])} open postings, "
          f"{len(payload['active'])} in progress -> {BOARD_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
