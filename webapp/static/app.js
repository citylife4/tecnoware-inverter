/* Dashboard front-end. No build step, no dependencies -- this runs on a
   Raspberry Pi serving a handful of clients on the LAN.

   The page reads the poller's cached snapshot rather than forcing a serial
   read per request: at 2400 baud a QPIGS exchange takes about half a
   second, so per-request reads would queue up behind each other as soon as
   a second tab opened. "Refresh" is the explicit escape hatch. */

const $ = (sel) => document.querySelector(sel);
const fmt = (v, digits = 1) =>
  (v === null || v === undefined || v === "") ? "—" : Number(v).toFixed(digits);

let pollTimer = null;
let latestStatus = null;

/* ---------- API helper ---------- */

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (res.status === 401) { window.location = "/login"; throw new Error("unauthorized"); }
  let body;
  try { body = await res.json(); } catch (e) { body = { ok: false, error: `HTTP ${res.status}` }; }
  return body;
}

function showError(msg) {
  const el = $("#global-error");
  if (!msg) { el.hidden = true; return; }
  el.textContent = msg;
  el.hidden = false;
}

/* ---------- stat tiles ---------- */

/* Each entry renders one tile. `sub` is the secondary line -- kept short so
   the tile stays a glanceable number rather than a paragraph. */
function tileSpecs(s) {
  const netA = s.battery_net_current;
  let battSub = "idle";
  if (typeof netA === "number" && netA > 0) battSub = `charging ${netA} A`;
  else if (typeof netA === "number" && netA < 0) battSub = `discharging ${-netA} A`;
  return [
    { k: "Battery", v: fmt(s.battery_voltage, 2), u: "V", sub: battSub, cls: "accent-batt" },
    { k: "State of charge", v: fmt(s.battery_capacity, 0), u: "%", sub: "reported by inverter" },
    { k: "PV power", v: fmt(s.pv_charging_power, 0), u: "W",
      sub: `${fmt(s.pv_input_voltage, 1)} V array`, cls: "accent-pv" },
    { k: "Output load", v: fmt(s.ac_output_active_power, 0), u: "W",
      sub: `${fmt(s.output_load_percent, 0)}% of rating`, cls: "accent-load" },
    { k: "Grid in", v: fmt(s.grid_voltage, 1), u: "V", sub: `${fmt(s.grid_frequency, 1)} Hz` },
    { k: "AC out", v: fmt(s.ac_output_voltage, 1), u: "V", sub: `${fmt(s.ac_output_frequency, 1)} Hz` },
    { k: "Heatsink", v: fmt(s.heatsink_temperature, 0), u: "°C", sub: "internal temperature" },
    { k: "Bus", v: fmt(s.bus_voltage, 0), u: "V", sub: "DC link" },
  ];
}

function renderTiles(s) {
  $("#tiles").innerHTML = tileSpecs(s).map((t) => `
    <div class="tile ${t.cls || ""}">
      <div class="k">${t.k}</div>
      <div class="v">${t.v}<small>${t.u}</small></div>
      <div class="sub">${t.sub}</div>
    </div>`).join("");
}

/* ---------- charts ----------
   Three single-series small multiples, each on its own y-scale. Volts and
   watts are different measures, so they get different charts rather than a
   second y-axis on one chart. */

const CHARTS = [
  { el: "#chart-batt", label: "#lbl-batt", key: "battery_voltage",
    color: "--series-batt", unit: "V", digits: 2 },
  { el: "#chart-pv", label: "#lbl-pv", key: "pv_charging_power",
    color: "--series-pv", unit: "W", digits: 0 },
  { el: "#chart-load", label: "#lbl-load", key: "ac_output_active_power",
    color: "--series-load", unit: "W", digits: 0 },
];

const W = 300, H = 120, PAD_L = 34, PAD_R = 8, PAD_T = 10, PAD_B = 16;

function niceBounds(min, max) {
  if (min === max) {
    // A dead-flat series (e.g. PV at 0 all night) still needs a band to draw in.
    const pad = Math.abs(min) * 0.05 || 1;
    return [min - pad, max + pad];
  }
  const pad = (max - min) * 0.12;
  return [min - pad, max + pad];
}

function drawChart(spec, rows) {
  const host = document.querySelector(spec.el);
  const labelEl = document.querySelector(spec.label);
  const pts = rows
    .map((r) => ({ ts: r.ts, v: r[spec.key] }))
    .filter((p) => typeof p.v === "number");

  if (pts.length) {
    labelEl.textContent = fmt(pts[pts.length - 1].v, spec.digits);
  }
  if (pts.length < 2) {
    host.innerHTML = `<div class="chart-empty">collecting data…</div>`;
    return;
  }

  const values = pts.map((p) => p.v);
  const [lo, hi] = niceBounds(Math.min(...values), Math.max(...values));
  const x = (i) => PAD_L + (i / (pts.length - 1)) * (W - PAD_L - PAD_R);
  const y = (v) => PAD_T + (1 - (v - lo) / (hi - lo)) * (H - PAD_T - PAD_B);

  const line = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
  const area = `${line}L${x(pts.length - 1).toFixed(1)},${H - PAD_B}L${x(0).toFixed(1)},${H - PAD_B}Z`;
  const color = `var(${spec.color})`;
  const uid = spec.key;

  // Three recessive gridlines with value labels; no chart junk beyond that.
  const ticks = [lo, (lo + hi) / 2, hi].map((v) => `
    <line class="gridline" x1="${PAD_L}" x2="${W - PAD_R}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}"/>
    <text class="axis-label" x="${PAD_L - 4}" y="${(y(v) + 3).toFixed(1)}" text-anchor="end">${fmt(v, spec.digits)}</text>`).join("");

  host.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img"
         aria-label="${spec.key} over time">
      <defs><linearGradient id="g-${uid}" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0%" stop-color="${color}" stop-opacity=".22"/>
        <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
      </linearGradient></defs>
      ${ticks}
      <path d="${area}" fill="url(#g-${uid})"/>
      <path d="${line}" fill="none" stroke="${color}" stroke-width="2"
            stroke-linejoin="round" stroke-linecap="round"
            vector-effect="non-scaling-stroke"/>
      <circle cx="${x(pts.length - 1).toFixed(1)}" cy="${y(pts[pts.length - 1].v).toFixed(1)}"
              r="3.5" fill="${color}" stroke="var(--surface-1)" stroke-width="2"/>
      <line class="cursor" x1="0" x2="0" y1="${PAD_T}" y2="${H - PAD_B}"
            stroke="${color}" stroke-width="1" opacity="0"/>
    </svg>
    <div class="tip" hidden></div>`;

  attachHover(host, spec, pts, x, y);
}

/* Crosshair + tooltip. An SVG chart on a page is interactive by default;
   a bare sparkline with no readout would hide the actual numbers. */
function attachHover(host, spec, pts, x, y) {
  const svg = host.querySelector("svg");
  const cursor = host.querySelector(".cursor");
  const tip = host.querySelector(".tip");

  const move = (ev) => {
    const box = svg.getBoundingClientRect();
    const clientX = ev.touches ? ev.touches[0].clientX : ev.clientX;
    const rel = (clientX - box.left) / box.width;          // 0..1 across the plot
    const px = rel * W;
    let idx = Math.round(((px - PAD_L) / (W - PAD_L - PAD_R)) * (pts.length - 1));
    idx = Math.max(0, Math.min(pts.length - 1, idx));
    const p = pts[idx];
    cursor.setAttribute("x1", x(idx));
    cursor.setAttribute("x2", x(idx));
    cursor.setAttribute("opacity", "0.55");
    tip.hidden = false;
    tip.innerHTML = `<b>${fmt(p.v, spec.digits)} ${spec.unit}</b><br>
      <span class="muted">${new Date(p.ts).toLocaleTimeString()}</span>`;
    tip.style.left = `${(x(idx) / W) * 100}%`;
    tip.style.top = `${(y(p.v) / H) * 100}%`;
  };
  const leave = () => { cursor.setAttribute("opacity", "0"); tip.hidden = true; };

  host.addEventListener("mousemove", move);
  host.addEventListener("touchmove", move, { passive: true });
  host.addEventListener("mouseleave", leave);
  host.addEventListener("touchend", leave);
}

function renderHistoryTable(rows) {
  const body = $("#history-table tbody");
  // Newest first, and capped -- this is a relief/accessibility view, not a log.
  body.innerHTML = rows.slice(-60).reverse().map((r) => `<tr>
      <td>${new Date(r.ts).toLocaleTimeString()}</td>
      <td>${fmt(r.battery_voltage, 2)}</td>
      <td>${fmt(r.battery_capacity, 0)}</td>
      <td>${fmt(r.pv_charging_power, 0)}</td>
      <td>${fmt(r.ac_output_active_power, 0)}</td>
      <td>${fmt(r.grid_voltage, 1)}</td></tr>`).join("");
}

/* ---------- polling ---------- */

function setConn(ok, text) {
  const el = $("#conn");
  el.className = `conn ${ok ? "ok" : "bad"}`;
  $("#conn-text").textContent = text;
}

async function tick() {
  const data = await api("/api/status");
  if (!data.ok) { setConn(false, "server error"); showError(data.error || "status failed"); return; }

  if (data.status) {
    latestStatus = data.status;
    renderTiles(data.status);
    $("#mode-badge").textContent = data.status.mode_label || "—";
  }
  if (data.error) {
    setConn(false, "serial error");
    showError(`Inverter link: ${data.error}`);
  } else {
    const age = data.last_success ? Math.round((Date.now() - new Date(data.last_success)) / 1000) : null;
    setConn(true, age === null ? "connected" : `updated ${age}s ago`);
    showError(null);
  }

  const hist = await api("/api/history?limit=240");
  if (hist.ok && hist.history.length) {
    CHARTS.forEach((spec) => drawChart(spec, hist.history));
    renderHistoryTable(hist.history);
    const first = new Date(hist.history[0].ts).toLocaleTimeString();
    const last = new Date(hist.history[hist.history.length - 1].ts).toLocaleTimeString();
    $("#trend-range").textContent = `${first} – ${last} (${hist.history.length} samples)`;
  }
  loadAudit();
  loadSchedule();
  loadGridCharge();
}

async function loadAudit() {
  const data = await api("/api/audit");
  if (!data.ok) return;
  $("#audit-table tbody").innerHTML = data.audit.length
    ? data.audit.map((a) => `<tr>
        <td>${new Date(a.at).toLocaleString()}</td>
        <td class="muted">${a.source || "manual"}</td>
        <td><code>${a.command}</code></td>
        <td class="${a.ok ? "" : "err"}">${a.response || "—"}</td></tr>`).join("")
    : `<tr><td colspan="4" class="muted">No set commands sent yet.</td></tr>`;
}

/* ---------- device + ratings (static, fetched once) ---------- */

async function loadDevice() {
  const d = await api("/api/device");
  if (!d.ok) return;
  const x = d.device;
  $("#device-line").textContent =
    [x.model && `model ${x.model}`, x.protocol && `protocol ${x.protocol}`,
     x.firmware && `fw ${x.firmware}`, x.serial_number && `s/n ${x.serial_number}`,
     x.port].filter(Boolean).join(" · ");
}

async function loadRatings() {
  const d = await api("/api/ratings");
  if (!d.ok) return;
  $("#ratings-table tbody").innerHTML = Object.entries(d.ratings).map(([k, v]) => {
    const extra = v.actual_volts !== undefined
      ? ` <span class="muted">→ ${v.actual_volts} V pack</span>`
      : (v.label ? ` <span class="muted">(${v.label})</span>` : "");
    return `<tr><td class="name">${k.replace(/_/g, " ")}</td>
            <td>${v.value} <span class="muted">${v.unit || ""}</span>${extra}</td></tr>`;
  }).join("");
}

/* ---------- console + controls ---------- */

function logConsole(text, cls = "") {
  const out = $("#console-out");
  const stamp = new Date().toLocaleTimeString();
  out.insertAdjacentHTML("afterbegin",
    `<span class="${cls}">[${stamp}] ${text}</span>\n`);
}

function describe(res) {
  if (res.ok) {
    let msg = `${res.command} → ${res.response}`;
    if (res.kind === "set") msg += `\n         ${res.note}`;
    return [msg, "ok"];
  }
  let msg = `${res.command || ""} FAILED: ${res.error}`;
  if (res.hint) msg += `\n         hint: ${res.hint}`;
  if (res.valid) msg += `\n         valid: ${Object.keys(res.valid).join(", ")}`;
  return [msg, "err"];
}

async function sendCommand(command, confirm) {
  const res = await api("/api/command", {
    method: "POST", body: JSON.stringify({ command, confirm: !!confirm }),
  });
  const [msg, cls] = describe(res);
  logConsole(msg, cls);
  return res;
}

async function setPriority(kind, value, confirm) {
  const res = await api(`/api/${kind}`, {
    method: "POST", body: JSON.stringify({ value, confirm: !!confirm }),
  });
  const [msg, cls] = describe(res);
  logConsole(msg, cls);
  return res;
}

/* A rejected write comes back with an explanatory reason. When the reason is
   "needs confirmation", offer exactly that -- one retry with the flag set --
   rather than silently confirming on the user's behalf. */
async function withConfirmRetry(runner, prompt) {
  let res = await runner(false);
  if (!res.ok && res.code === "confirmation_required") {
    if (window.confirm(`${prompt}\n\n${res.error}\n\nSend it anyway?`)) {
      res = await runner(true);
    } else {
      logConsole("cancelled", "muted");
    }
  }
  return res;
}

function wireControls() {
  document.querySelectorAll(".opt[data-kind]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const { kind, value } = btn.dataset;
      const label = btn.textContent.trim();
      if (!window.confirm(`Set ${kind.replace("-", " ")} to ${label}?`)) return;
      btn.disabled = true;
      try {
        const res = await withConfirmRetry((c) => setPriority(kind, value, c), `Set ${label}`);
        if (res.ok) {
          btn.parentElement.querySelectorAll(".opt").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          // QPIRI never reflects a change, so re-read live status instead,
          // and re-derive the highlight from the audit log.
          markCurrentPriorities();
          setTimeout(() => api("/api/status?live=1").then(tick), 1200);
        }
      } finally { btn.disabled = false; }
    });
  });

  document.querySelectorAll(".opt[data-cmd]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const cmd = btn.dataset.cmd;
      if (!window.confirm(`Send ${cmd} to the inverter?`)) return;
      btn.disabled = true;
      try {
        // Dangerous commands come back as confirmation_required on the
        // first try; withConfirmRetry then asks and re-sends with confirm.
        await withConfirmRetry((c) => sendCommand(cmd, c), `Send ${cmd}`);
      } finally { btn.disabled = false; loadAudit(); }
    });
  });
}

/* Mark the priority buttons.

   Confirmed on this hardware: QPIRI does NOT track these writes. Sending
   PCP02 returns (ACK, yet QPIRI's charger_source_priority still reports the
   old value -- the same "QPIRI reports static rated values" trap documented
   in the README, and it applies to the priority code fields too.

   So the only trustworthy record of the current setting is what this server
   has actually set and had acknowledged: the audit log. QPIRI is shown only
   as the rated/default value, and never as a confirmed current state. */
async function markCurrentPriorities() {
  const d = await api("/api/audit");
  if (!d.ok) return;
  const kinds = [["PCP", "charger-priority"], ["POP", "output-priority"]];
  kinds.forEach(([prefix, kind]) => {
    const last = d.audit.find((a) => a.ok && a.command.startsWith(prefix));
    const buttons = document.querySelectorAll(`.opt[data-kind="${kind}"]`);
    if (!last) {
      // Nothing set since this server started, and the inverter won't tell
      // us. Claiming a value here would be a guess.
      buttons.forEach((b) => b.classList.remove("active"));
      const note = document.querySelector(`#note-${kind}`);
      if (note) note.textContent = "Current setting unknown — the inverter does not report it back.";
      return;
    }
    const code = last.command.slice(prefix.length, prefix.length + 2);
    buttons.forEach((b) => b.classList.toggle("active", b.dataset.value === code));
    const note = document.querySelector(`#note-${kind}`);
    if (note) {
      note.textContent = `Last set by this server at ${new Date(last.at).toLocaleString()}.`;
    }
  });
}

/* ---------- schedule ----------
   Time-of-day PCP scheduling, running inside the server (see
   webapp/scheduler.py) instead of a separate cron job -- the server
   already owns the serial port exclusively, so nothing else could hold it
   at the same time anyway. This panel edits the rule list and shows what
   the scheduler most recently did; it never claims a "current setting"
   read back from the inverter, because the hardware doesn't report one
   (same reason markCurrentPriorities() above uses the audit log). */

function fmtRule(r) {
  if (!r) return "no rule matches the current time";
  return `${r.from}–${r.to} → PCP${r.pcp}${r.why ? ` (${r.why})` : ""}`;
}

function renderSchedule(state) {
  if (!document.querySelector("#sched-enabled")) return;   // section not rendered
  $("#sched-poll").textContent = state.poll_interval;
  $("#sched-enabled").checked = state.enabled;
  $("#sched-status").textContent = state.enabled ? "enabled" : "disabled";
  $("#sched-status").className = `badge ${state.enabled ? "" : "muted"}`;
  if (!state.allow_writes) {
    $("#sched-status").textContent += " (server read-only — not applied)";
  }

  const body = $("#sched-table tbody");
  body.innerHTML = state.rules.length
    ? state.rules.map((r, i) => `<tr>
        <td>${r.from}</td><td>${r.to}</td>
        <td><span class="code">PCP${r.pcp}</span></td>
        <td class="name">${r.why || ""}</td>
        <td><button class="ghost" data-del="${i}">Remove</button></td></tr>`).join("")
    : `<tr><td colspan="5" class="muted">No rules yet — add one below.</td></tr>`;
  body.querySelectorAll("[data-del]").forEach((b) => {
    b.addEventListener("click", () => deleteRule(+b.dataset.del));
  });

  let cur = `<span class="muted">Matches now:</span> ${fmtRule(state.current_rule)}`;
  if (state.override_reason) cur += `<br><span class="warn small">OVERRIDE: ${state.override_reason}</span>`;
  if (state.last_run) {
    const t = new Date(state.last_run.at).toLocaleTimeString();
    const cls = state.last_run.applied ? "ok" : (state.last_run.note.startsWith("error") ? "err" : "");
    cur += `<br><span class="muted">Last check ${t}:</span> <span class="${cls}">${state.last_run.note}</span>`;
  }
  $("#sched-current").innerHTML = cur;
}

let scheduleState = { enabled: false, rules: [] };

async function loadSchedule() {
  const d = await api("/api/schedule");
  if (!d.ok) return;
  scheduleState = d;
  renderSchedule(d);
}

async function saveSchedule(enabled, rules) {
  const d = await api("/api/schedule", {
    method: "PUT", body: JSON.stringify({ enabled, rules }),
  });
  if (d.ok) { scheduleState = d; renderSchedule(d); }
  else {
    logConsole(`schedule save failed: ${d.error}`, "err");
    renderSchedule(scheduleState);   // revert the enabled checkbox etc. to the last-saved state
  }
  return d;
}

function deleteRule(index) {
  const rules = scheduleState.rules.slice();
  rules.splice(index, 1);
  saveSchedule(scheduleState.enabled, rules);
}

function wireSchedule() {
  const enabledBox = $("#sched-enabled");
  if (!enabledBox) return;   // scheduler_available was false; section not rendered

  enabledBox.addEventListener("change", () => {
    saveSchedule(enabledBox.checked, scheduleState.rules);
  });

  $("#sched-add-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const rule = {
      from: $("#sched-from").value, to: $("#sched-to").value,
      pcp: $("#sched-pcp").value, why: $("#sched-why").value.trim(),
    };
    if (!rule.from || !rule.to) return;
    const rules = scheduleState.rules.concat([rule]);
    saveSchedule(scheduleState.enabled, rules).then((d) => {
      if (d.ok) { $("#sched-from").value = ""; $("#sched-to").value = ""; $("#sched-why").value = ""; }
    });
  });

  $("#sched-apply-now").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      const d = await api("/api/schedule/apply-now", { method: "POST", body: JSON.stringify({ force: true }) });
      if (d.ok) { logConsole(`schedule apply-now: ${d.result.note}`, d.result.applied ? "ok" : "err"); }
      await loadSchedule();
    } finally { e.target.disabled = false; }
  });
}

/* ---------- grid-export charging ----------
   Mirrors the schedule panel's shape (see webapp/grid_charge.py), but the
   trigger is the auto-energy dashboard's grid-meter reading instead of a
   time window. Config has many fields, so unlike the schedule table
   (which auto-saves every edit) this uses an explicit "Save settings"
   button -- only the enabled toggle and "Apply now" act immediately. */

let gridChargeState = null;

/* export_threshold_w is stored negative internally (net_balance < 0 means
   exporting) but shown to the user as a plain positive "watts exported"
   number -- "start once exporting more than 50W" reads far better than
   "start once net balance drops below -50W". Type "negnum" flips the sign
   on the way in and out; everything else passes straight through. */
const GC_FIELDS = [
  ["gc-source-url", "source_url", "str"],
  ["gc-poll-interval", "poll_interval", "num"],
  ["gc-export-threshold", "export_threshold_w", "negnum"],
  ["gc-import-threshold", "import_threshold_w", "num"],
  ["gc-min-switch", "min_switch_interval", "num"],
  ["gc-stale-after", "stale_after", "num"],
  ["gc-charge-pcp", "charge_pcp", "str"],
  ["gc-idle-pcp", "idle_pcp", "str"],
];

function renderGridCharge(state) {
  if (!document.querySelector("#gc-enabled")) return;   // section not rendered
  $("#gc-enabled").checked = state.enabled;
  $("#gc-status").textContent = state.enabled ? "enabled" : "disabled";
  $("#gc-status").className = `badge ${state.enabled ? "" : "muted"}`;
  if (!state.allow_writes) $("#gc-status").textContent += " (server read-only — not applied)";

  const popWarnEl = document.querySelector("#gc-pop-warning");
  if (popWarnEl) {
    if (state.pop_warning) {
      popWarnEl.textContent = `⚠ ${state.pop_warning}`;
      popWarnEl.hidden = false;
    } else {
      popWarnEl.hidden = true;
    }
  }

  GC_FIELDS.forEach(([id, key, type]) => {
    const el = document.querySelector(`#${id}`);
    if (!el || document.activeElement === el) return;
    el.value = type === "negnum" ? Math.abs(state[key]) : state[key];
  });

  const c = state.current || {};
  let cur = c.net_balance_w === null || c.net_balance_w === undefined
    ? `<span class="muted">No reading yet.</span>`
    : `<span class="muted">Grid:</span> ${c.net_balance_w > 0 ? "importing" : "exporting"} `
      + `${Math.abs(c.net_balance_w).toFixed(0)} W`
      + (c.age_s !== null ? ` <span class="muted">(${c.age_s.toFixed(0)}s ago)</span>` : "");
  if (state.last_run) {
    const t = new Date(state.last_run.at).toLocaleTimeString();
    const cls = state.last_run.applied ? "ok" : (state.last_run.note.startsWith("error") ? "err" : "");
    cur += `<br><span class="muted">Last check ${t}:</span> <span class="${cls}">${state.last_run.note}</span>`;
    if (state.last_run.why) cur += `<br><span class="warn small">${state.last_run.why}</span>`;
  }
  $("#gc-current").innerHTML = cur;
}

async function loadGridCharge() {
  const d = await api("/api/grid-charge");
  if (!d.ok) return;
  gridChargeState = d;
  renderGridCharge(d);
}

function readGridChargeForm() {
  const out = { enabled: gridChargeState.enabled };
  GC_FIELDS.forEach(([id, key, type]) => {
    const el = document.querySelector(`#${id}`);
    if (type === "negnum") out[key] = -Math.abs(Number(el.value));
    else out[key] = type === "num" ? Number(el.value) : el.value;
  });
  return out;
}

async function saveGridCharge(overrides) {
  const body = { ...readGridChargeForm(), ...overrides };
  const d = await api("/api/grid-charge", { method: "PUT", body: JSON.stringify(body) });
  if (d.ok) { gridChargeState = d; renderGridCharge(d); }
  else {
    logConsole(`grid-charge save failed: ${d.error}`, "err");
    renderGridCharge(gridChargeState);
  }
  return d;
}

function wireGridCharge() {
  const enabledBox = $("#gc-enabled");
  if (!enabledBox) return;   // grid_charge_available was false

  enabledBox.addEventListener("change", () => {
    saveGridCharge({ enabled: enabledBox.checked });
  });

  $("#gc-save").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try { await saveGridCharge({}); }
    finally { e.target.disabled = false; }
  });

  $("#gc-apply-now").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      const d = await api("/api/grid-charge/apply-now", { method: "POST", body: JSON.stringify({ force: true }) });
      if (d.ok) logConsole(`grid-charge apply-now: ${d.result.note}`, d.result.applied ? "ok" : "err");
      await loadGridCharge();
    } finally { e.target.disabled = false; }
  });
}

/* ---------- boot ---------- */

document.addEventListener("DOMContentLoaded", async () => {
  $("#console-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("#console-input");
    const cmd = input.value.trim().toUpperCase();
    if (!cmd) return;
    const btn = e.target.querySelector("button");
    btn.disabled = true;
    try {
      await withConfirmRetry((c) => sendCommand(cmd, c || $("#console-confirm").checked),
                             `Send ${cmd}`);
      input.value = "";
    } finally { btn.disabled = false; loadAudit(); }
  });

  $("#refresh-btn").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try { await api("/api/status?live=1"); await tick(); }
    finally { e.target.disabled = false; }
  });

  wireControls();
  wireSchedule();
  wireGridCharge();
  await tick();
  loadDevice();
  loadRatings();
  markCurrentPriorities();

  const interval = 5000;
  pollTimer = setInterval(() => tick().catch(() => {}), interval);
  // Don't keep polling a hidden tab -- it's a serial link, not a web API.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { clearInterval(pollTimer); }
    else { tick().catch(() => {}); pollTimer = setInterval(() => tick().catch(() => {}), interval); }
  });
});
