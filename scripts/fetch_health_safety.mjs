#!/usr/bin/env node
// Refreshes the Health & Safety figures in the bulletin-data JSON block, reading
// straight from the HSIncident table on SQL-ROTO-02. Deliberately standalone: it
// doesn't import anything from RotoCentral, just the same read-only table and the
// same arithmetic its H&S Overview page uses, re-derived here so this repo has no
// dependency on that codebase. Runs on a self-hosted runner, since SQL-ROTO-02 is
// only reachable from inside the company network, never from GitHub's own runners.
import sql from 'mssql';
import { readFileSync, writeFileSync } from 'node:fs';

const INDEX_PATH = 'index.html';
// \r?\n: the git working tree on Windows checks this file out with CRLF line
// endings, unlike the Python scripts' CRLF-tolerant text-mode file reads.
const BLOCK_RE = /(<script id="bulletin-data" type="application\/json">\r?\n)([\s\S]*?)(\r?\n<\/script>)/;

const required = ['HS_DB_SERVER', 'HS_DB_NAME', 'HS_DB_USER', 'HS_DB_PASSWORD'];
for (const key of required) {
  if (!process.env[key]) {
    console.log(`${key} not set, skipping Health & Safety refresh`);
    process.exit(0);
  }
}

const config = {
  user: process.env.HS_DB_USER,
  password: process.env.HS_DB_PASSWORD,
  // Setting `domain` at all switches mssql to NTLM/integrated auth. An empty/absent
  // value must mean plain SQL auth, not NTLM-with-a-blank-domain (same gotcha as
  // RotoCentral's own lib/db-rotocentral.ts, which this value matches).
  ...(process.env.HS_DB_DOMAIN ? { domain: process.env.HS_DB_DOMAIN } : {}),
  server: process.env.HS_DB_SERVER,
  database: process.env.HS_DB_NAME,
  requestTimeout: 30000,
  connectionTimeout: 15000,
  options: { encrypt: true, trustServerCertificate: true },
};

// ---- Date helpers (UK "YYYY-MM-DD" dates, UTC-anchored day arithmetic) ----
const toDay = (d) => Date.UTC(+d.slice(0, 4), +d.slice(5, 7) - 1, +d.slice(8, 10)) / 86_400_000;
const daysBetween = (a, b) => Math.round(toDay(b) - toDay(a));
const yearBefore = (d) => `${+d.slice(0, 4) - 1}${d.slice(4)}`.replace(/-02-29$/, '-02-28');

function londonToday() {
  // en-CA gives YYYY-MM-DD directly.
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/London' });
}

function periodRangesYTD(today) {
  const y = +today.slice(0, 4);
  return {
    current: { from: `${y}-01-01`, to: today },
    previous: { from: `${y - 1}-01-01`, to: yearBefore(today) },
  };
}

const inRange = (dateStr, r) => dateStr >= r.from && dateStr <= r.to;

// A minor accident, a lost-time incident, or anything RIDDOR reportable breaks the
// run; a near miss alone does not (reporting them is the point).
const breaksRun = (i) => i.type !== 'near_miss' || i.riddor === true;

function daysSinceLast(all, today) {
  const past = all.filter((i) => breaksRun(i) && i.occurred_at <= today);
  if (!past.length) return null;
  const last = past.reduce((a, b) => (b.occurred_at > a.occurred_at ? b : a));
  return { days: daysBetween(last.occurred_at, today), last };
}

function longestRun(all, today) {
  const days = [...new Set(all.filter(breaksRun).map((i) => i.occurred_at).filter((d) => d <= today))].sort();
  let best = 0;
  for (let k = 1; k < days.length; k++) best = Math.max(best, daysBetween(days[k - 1], days[k]));
  if (days.length) best = Math.max(best, daysBetween(days[days.length - 1], today));
  return best;
}

function totals(list) {
  const counts = { near_miss: 0, minor_accident: 0, lost_time: 0, riddor: 0 };
  let daysLost = 0, hoursLost = 0;
  for (const i of list) {
    counts[i.type] = (counts[i.type] ?? 0) + 1;
    if (i.type === 'lost_time' && i.time_lost_value != null) {
      if (i.time_lost_unit === 'days') daysLost += i.time_lost_value; else hoursLost += i.time_lost_value;
    }
  }
  return { counts, daysLost, hoursLost };
}

async function main() {
  const pool = await sql.connect(config);
  const res = await pool.request().query(
    'SELECT type, occurred_at, riddor, time_lost_value, time_lost_unit FROM HSIncident'
  );
  await pool.close();

  // Dates as plain YYYY-MM-DD strings, matching RotoCentral's own convention.
  const rows = res.recordset.map((r) => ({
    type: r.type,
    occurred_at: new Date(r.occurred_at).toISOString().slice(0, 10),
    riddor: r.riddor,
    time_lost_value: r.time_lost_value,
    time_lost_unit: r.time_lost_unit,
  }));

  const today = londonToday();
  const since = daysSinceLast(rows, today);
  const record = since ? Math.max(since.days, longestRun(rows, since.last.occurred_at)) : 0;

  const { current, previous } = periodRangesYTD(today);
  const nowRows = rows.filter((r) => inRange(r.occurred_at, current));
  const beforeRows = rows.filter((r) => inRange(r.occurred_at, previous));
  const now = totals(nowRows);
  const before = totals(beforeRows);
  const riddorNow = nowRows.filter((r) => r.riddor === true).length;
  const riddorBefore = beforeRows.filter((r) => r.riddor === true).length;

  const healthSafety = {
    daysSinceIncident: since?.days ?? 0,
    record,
    nearMisses: { now: now.counts.near_miss, before: before.counts.near_miss },
    minorAccidents: { now: now.counts.minor_accident, before: before.counts.minor_accident },
    lostTimeAccidents: { now: now.counts.lost_time, before: before.counts.lost_time, daysLost: now.daysLost, hoursLost: now.hoursLost },
    riddor: { now: riddorNow, before: riddorBefore },
  };

  const html = readFileSync(INDEX_PATH, 'utf8');
  const m = html.match(BLOCK_RE);
  if (!m) {
    console.error('bulletin-data block not found');
    process.exit(1);
  }
  const bulletin = JSON.parse(m[2]);
  bulletin.healthSafety = healthSafety;
  // Match the file's existing line endings (the git working tree checks this
  // repo out with CRLF) so the diff only touches the healthSafety values.
  const eol = m[1].includes('\r\n') ? '\r\n' : '\n';
  const newJson = JSON.stringify(bulletin, null, 1).replace(/\n/g, eol);
  const newHtml = html.slice(0, m.index) + m[1] + newJson + m[3] + html.slice(m.index + m[0].length);
  writeFileSync(INDEX_PATH, newHtml, 'utf8');
  console.log('Health & Safety refreshed:', JSON.stringify(healthSafety));
}

main().catch((e) => { console.error('ERROR:', e?.message ?? e); process.exit(1); });
