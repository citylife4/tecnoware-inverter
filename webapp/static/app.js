/* Dashboard front-end. No build step, no dependencies -- this runs on a
   Raspberry Pi serving a handful of clients on the LAN.

   The page reads the poller's cached snapshot rather than forcing a serial
   read per request: at 2400 baud a QPIGS exchange takes about half a
   second, so per-request reads would queue up behind each other as soon as
   a second tab opened. "Refresh" is the explicit escape hatch. */

/* Etiquetas traduzidas injetadas pelo template (ver webapp/ui_labels.py).
   A API REST continua em inglês -- estável para quem automatiza; só o que
   aparece no browser é que é traduzido. */
const UI_LABELS = (() => {
  try { return JSON.parse(document.getElementById("ui-labels").textContent); }
  catch (e) { return { modes: {}, ratings: {}, batteryTypes: {} }; }
})();

const KIND_LABELS = {
  "charger-priority": "prioridade da fonte de carga",
  "output-priority": "prioridade da fonte de saída",
};

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
  let battSub = "inativa";
  if (typeof netA === "number" && netA > 0) battSub = `a carregar ${netA} A`;
  else if (typeof netA === "number" && netA < 0) battSub = `a descarregar ${-netA} A`;
  // Em bypass (POP=00, modo "L") o inversor NÃO mede a carga da saída:
  // devolve 1 W fixo, aconteça o que acontecer. Confirmado em 251 de 252
  // amostras consecutivas -- todas exactamente 1.0 W, com uma única
  // excepção de 1223 W (arranque do compressor do frigorífico, quando o
  // inversor assume por instantes). Mostrar "1 W" seria apresentar um
  // valor de preenchimento como se fosse medição.
  const bypass = s.mode !== "B";
  return [
    { k: "Bateria", v: fmt(s.battery_voltage, 2), u: "V", sub: battSub, cls: "accent-batt" },
    { k: "Estado de carga", v: fmt(s.battery_capacity, 0), u: "%", sub: "indicado pelo inversor" },
    { k: "Potência da bateria", v: fmt(s.battery_power_w, 0), u: "W",
      sub: battSub, cls: "accent-batt" },
    { k: "Potência PV", v: fmt(s.pv_charging_power, 0), u: "W",
      sub: `painéis a ${fmt(s.pv_input_voltage, 1)} V`, cls: "accent-pv" },
    { k: "Carga na saída",
      v: bypass ? "—" : fmt(s.ac_output_active_power, 0),
      u: bypass ? "" : "W",
      sub: bypass ? "não medida em bypass" : `${fmt(s.output_load_percent, 0)}% do nominal`,
      cls: "accent-load" },
    { k: "Entrada da rede", v: fmt(s.grid_voltage, 1), u: "V", sub: `${fmt(s.grid_frequency, 1)} Hz` },
    { k: "Saída AC", v: fmt(s.ac_output_voltage, 1), u: "V", sub: `${fmt(s.ac_output_frequency, 1)} Hz` },
    { k: "Dissipador", v: fmt(s.heatsink_temperature, 0), u: "°C", sub: "temperatura interna" },
    { k: "Barramento", v: fmt(s.bus_voltage, 0), u: "V", sub: "barramento DC" },
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
  // Era pv_charging_power, mas nesta instalação a entrada PV do inversor
  // não está ligada: 293 amostras seguidas, todas 0 W -- um gráfico plano
  // e inútil. A potência da bateria varia mesmo (108-136 W observados) e é
  // o número que interessa aqui. A produção solar real continua visível,
  // medida pelo Shelly, no painel do auto-energy.
  { el: "#chart-pv", label: "#lbl-pv", key: "battery_power_w",
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
    host.innerHTML = `<div class="chart-empty">a recolher dados…</div>`;
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
  if (!data.ok) { setConn(false, "erro do servidor"); showError(data.error || "falha ao obter estado"); return; }

  renderEnergy(data.energy);
  if (data.status) {
    latestStatus = data.status;
    renderTiles(data.status);
    // Traduz a partir da LETRA do modo (QMOD), não do texto inglês, para
    // não depender de uma tradução do lado do servidor.
    $("#mode-badge").textContent =
      UI_LABELS.modes[data.status.mode] || data.status.mode_label || "—";
  }
  if (data.error) {
    setConn(false, "erro na ligação série");
    showError(`Ligação ao inversor: ${data.error}`);
  } else {
    const age = data.last_success ? Math.round((Date.now() - new Date(data.last_success)) / 1000) : null;
    setConn(true, age === null ? "ligado" : `atualizado há ${age}s`);
    showError(null);
  }

  const hist = await api("/api/history?limit=240");
  if (hist.ok && hist.history.length) {
    CHARTS.forEach((spec) => drawChart(spec, hist.history));
    renderHistoryTable(hist.history);
    const first = new Date(hist.history[0].ts).toLocaleTimeString();
    const last = new Date(hist.history[hist.history.length - 1].ts).toLocaleTimeString();
    $("#trend-range").textContent = `${first} – ${last} (${hist.history.length} amostras)`;
  }
  const loadNote = document.querySelector("#load-bypass-note");
  if (loadNote && data.status) loadNote.hidden = data.status.mode === "B";

  markCurrentPriorities(data.last_known_priorities);
  loadAudit();
  loadSchedule();
  loadGridCharge();
  loadBatteryWindow();
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
    : `<tr><td colspan="4" class="muted">Ainda não foram enviados comandos de escrita.</td></tr>`;
}

/* ---------- device + ratings (static, fetched once) ---------- */

async function loadDevice() {
  const d = await api("/api/device");
  if (!d.ok) return;
  const x = d.device;
  $("#device-line").textContent =
    [x.model && `modelo ${x.model}`, x.protocol && `protocolo ${x.protocol}`,
     x.firmware && `fw ${x.firmware}`, x.serial_number && `n/s ${x.serial_number}`,
     x.port].filter(Boolean).join(" · ");
}

async function loadRatings() {
  const d = await api("/api/ratings");
  if (!d.ok) return;
  $("#ratings-table tbody").innerHTML = Object.entries(d.ratings).map(([k, v]) => {
    const extra = v.actual_volts !== undefined
      ? ` <span class="muted">→ ${v.actual_volts} V no conjunto</span>`
      : (v.label ? ` <span class="muted">(${UI_LABELS.batteryTypes[v.label] || v.label})</span>` : "");
    return `<tr><td class="name">${UI_LABELS.ratings[k] || k.replace(/_/g, " ")}</td>
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
  let msg = `${res.command || ""} FALHOU: ${res.error}`;
  if (res.hint) msg += `\n         sugestão: ${res.hint}`;
  if (res.valid) msg += `\n         válidos: ${Object.keys(res.valid).join(", ")}`;
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
    if (window.confirm(`${prompt}\n\n${res.error}\n\nEnviar mesmo assim?`)) {
      res = await runner(true);
    } else {
      logConsole("cancelado", "muted");
    }
  }
  return res;
}

function wireControls() {
  document.querySelectorAll(".opt[data-kind]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const { kind, value } = btn.dataset;
      const label = btn.textContent.trim();
      if (!window.confirm(`Definir ${KIND_LABELS[kind] || kind} para ${label}?`)) return;
      btn.disabled = true;
      try {
        const res = await withConfirmRetry((c) => setPriority(kind, value, c), `Definir ${label}`);
        if (res.ok) {
          btn.parentElement.querySelectorAll(".opt").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          // Realce otimista imediato; o tick() a seguir confirma-o a
          // partir do estado persistido no servidor.
          setTimeout(() => api("/api/status?live=1").then(tick), 1200);
        }
      } finally { btn.disabled = false; }
    });
  });

  document.querySelectorAll(".opt[data-cmd]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const cmd = btn.dataset.cmd;
      if (!window.confirm(`Enviar ${cmd} para o inversor?`)) return;
      btn.disabled = true;
      try {
        // Dangerous commands come back as confirmation_required on the
        // first try; withConfirmRetry then asks and re-sends with confirm.
        await withConfirmRetry((c) => sendCommand(cmd, c), `Enviar ${cmd}`);
      } finally { btn.disabled = false; loadAudit(); }
    });
  });
}

/* Mark the priority buttons.

   Confirmed on this hardware: QPIRI does NOT track these writes. Sending
   PCP02 returns (ACK, yet QPIRI's charger_source_priority still reports the
   old value -- the same "QPIRI reports static rated values" trap documented
   in the README, and it applies to the priority code fields too. So the
   only trustworthy record is what this server set and had acknowledged.

   Reads `last_known_priorities` from /api/status, NOT the audit log. The
   audit log is in-memory and empties on every restart, which meant the POP
   button showed nothing while PCP showed fine: the grid-export automation
   rewrites PCP every few minutes so it was always in the recent log, but
   POP only changes when a person sets it, so after a restart there was no
   POP entry to find. The server-side value is persisted, so it survives.

   Called from tick(), i.e. on every poll -- so a change made by the
   automation (or from another browser tab, or by curl) shows up on the
   buttons within one poll, instead of only after a manual click or a page
   reload. */
function markCurrentPriorities(lastKnown) {
  const kinds = [["PCP", "charger-priority"], ["POP", "output-priority"]];
  kinds.forEach(([prefix, kind]) => {
    const entry = (lastKnown || {})[prefix];
    const buttons = document.querySelectorAll(`.opt[data-kind="${kind}"]`);
    const note = document.querySelector(`#note-${kind}`);
    if (!entry) {
      // Never set through this server, and the inverter won't tell us.
      // Claiming a value here would be a guess.
      buttons.forEach((b) => b.classList.remove("active"));
      if (note) note.textContent = "Definição atual desconhecida — o inversor não a devolve.";
      return;
    }
    buttons.forEach((b) => b.classList.toggle("active", b.dataset.value === entry.value));
    if (note) {
      note.textContent = `Definido por este servidor em ${new Date(entry.at).toLocaleString()}.`;
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
   (same reason markCurrentPriorities() above uses the server's persisted
   last-known values rather than reading QPIRI). */

function fmtRule(r) {
  if (!r) return "nenhuma regra corresponde à hora atual";
  return `${r.from}–${r.to} → PCP${r.pcp}${r.why ? ` (${r.why})` : ""}`;
}

function renderSchedule(state) {
  if (!document.querySelector("#sched-enabled")) return;   // section not rendered
  $("#sched-poll").textContent = state.poll_interval;
  $("#sched-enabled").checked = state.enabled;
  $("#sched-status").textContent = state.enabled ? "ativado" : "desativado";
  $("#sched-status").className = `badge ${state.enabled ? "" : "muted"}`;
  if (!state.allow_writes) {
    $("#sched-status").textContent += " (servidor só-leitura — não aplicado)";
  }

  const body = $("#sched-table tbody");
  body.innerHTML = state.rules.length
    ? state.rules.map((r, i) => `<tr>
        <td>${r.from}</td><td>${r.to}</td>
        <td><span class="code">PCP${r.pcp}</span></td>
        <td class="name">${r.why || ""}</td>
        <td><button class="ghost" data-del="${i}">Remover</button></td></tr>`).join("")
    : `<tr><td colspan="5" class="muted">Ainda sem regras — adicione uma abaixo.</td></tr>`;
  body.querySelectorAll("[data-del]").forEach((b) => {
    b.addEventListener("click", () => deleteRule(+b.dataset.del));
  });

  let cur = `<span class="muted">Regra ativa agora:</span> ${fmtRule(state.current_rule)}`;
  if (state.override_reason) cur += `<br><span class="warn small">${state.override_reason}</span>`;
  if (state.last_run) {
    const t = new Date(state.last_run.at).toLocaleTimeString();
    const cls = state.last_run.applied ? "ok" : (state.last_run.note.startsWith("error") ? "err" : "");
    cur += `<br><span class="muted">Última verificação às ${t}:</span> <span class="${cls}">${state.last_run.note}</span>`;
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
    logConsole(`falha ao guardar o agendamento: ${d.error}`, "err");
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
      if (d.ok) { logConsole(`agendamento — aplicar agora: ${d.result.note}`, d.result.applied ? "ok" : "err"); }
      await loadSchedule();
    } finally { e.target.disabled = false; }
  });
}

/* ---------- grid-export charging ----------
   Mirrors the schedule panel's shape (see webapp/grid_charge.py), but the
   trigger is the auto-energy dashboard's grid-meter reading instead of a
   time window. Config has many fields, so unlike the schedule table
   (which auto-saves every edit) this uses an explicit "Save settings"
   button -- only the enabled toggle and "Apply now" act immedi/* Campos do formulário de excedente.

   O tipo "negnum" existia aqui para o limiar de exportação: quando a
   automação disparava com net_balance negativo, o campo mostrava o valor
   absoluto e gravava-o com sinal trocado, para o utilizador não ter de
   escrever um menos.

   Deixou de servir a 2026-08-25, quando os limiares passaram a ser
   POSITIVOS de propósito (arrancar o carregador enquanto a casa ainda
   importa ~50W, para a exportação nunca chegar a acontecer). Com a troca de
   sinal ainda aplicada, escrever 30 gravava -30 e a automação nunca mais
   disparava -- foi o que aconteceu na instalação. O campo é agora um número
   com sinal, e a etiqueta explica os dois lados. */
const GC_FIELDS = [
  ["gc-mode", "mode", "str"],
  ["gc-source-url", "source_url", "str"],
  ["gc-poll-interval", "poll_interval", "num"],
  ["gc-export-threshold", "export_threshold_w", "num"],
  ["gc-import-threshold", "import_threshold_w", "num"],
  ["gc-min-switch", "min_switch_interval", "num"],
  ["gc-stale-after", "stale_after", "num"],
  ["gc-charge-pcp", "charge_pcp", "str"],
  ["gc-idle-pcp", "idle_pcp", "str"],
];

function renderGridCharge(state) {
  if (!document.querySelector("#gc-enabled")) return;   // section not rendered
  $("#gc-enabled").checked = state.enabled;
  $("#gc-status").textContent = state.enabled ? "ativado" : "desativado";
  $("#gc-status").className = `badge ${state.enabled ? "" : "muted"}`;
  if (!state.allow_writes) $("#gc-status").textContent += " (servidor só-leitura — não aplicado)";

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
    el.value = state[key];
  });

  const c = state.current || {};
  const v = c.net_balance_w;
  let cur;
  if (v === null || v === undefined) {
    cur = `<span class="muted">Ainda sem leitura da rede.</span>`;
  } else {
    // 0 W não é importar nem exportar. Antes isto era
    // `v > 0 ? "importing" : "exporting"`, que mostrava "a exportar 0 W"
    // -- afirmação errada sobre o sentido da energia.
    const dir = v > 0 ? "a importar" : (v < 0 ? "a exportar" : "equilibrada");
    const amount = v === 0 ? "" : ` ${Math.abs(v).toFixed(0)} W`;
    cur = `<span class="muted">Rede:</span> ${dir}${amount}`
      + (c.age_s !== null && c.age_s !== undefined
          ? ` <span class="muted">(leitura de há ${c.age_s.toFixed(0)}s)</span>` : "");
  }
  // A decisão da automação é coisa DIFERENTE do sentido instantâneo da
  // energia: a histerese mantém a decisão anterior enquanto a leitura
  // estiver entre os dois limiares, por isso "carregar" pode manter-se
  // enquanto a rede está momentaneamente a importar. Mostrar as duas em
  // linhas separadas evita que isso pareça uma contradição -- foi
  // exatamente essa confusão que motivou esta alteração.
  if (c.desired_state) {
    const decision = c.desired_state === "charging" ? "carregar" : "não carregar";
    cur += `<br><span class="muted">Decisão da automação:</span> ${decision}`;
  }
  if (state.last_run) {
    const t = new Date(state.last_run.at).toLocaleTimeString();
    const cls = state.last_run.applied ? "ok" : (state.last_run.note.startsWith("error") ? "err" : "");
    cur += `<br><span class="muted">Última verificação às ${t}:</span> <span class="${cls}">${state.last_run.note}</span>`;
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

/* Devolve {values, invalid}. `invalid` lista os campos vazios ou não
   numéricos em vez de os deixar passar: `Number("")` é 0, e a validação do
   servidor aceita 0 em vários destes campos, por isso um campo vazio podia
   gravar silenciosamente um limiar de 0 W -- foi o que aconteceu ao
   export_threshold_w a 2026-08-24. Melhor recusar do que adivinhar. */
function readGridChargeForm() {
  const out = { enabled: gridChargeState.enabled };
  const invalid = [];
  GC_FIELDS.forEach(([id, key, type]) => {
    const el = document.querySelector(`#${id}`);
    if (!el) return;
    const raw = String(el.value).trim();
    if (type === "str") {
      if (!raw) invalid.push(id); else out[key] = raw;
      return;
    }
    const n = Number(raw);
    if (raw === "" || !Number.isFinite(n)) { invalid.push(id); return; }
    out[key] = n;
  });
  return { values: out, invalid };
}

async function saveGridCharge(overrides) {
  const { values, invalid } = readGridChargeForm();
  if (invalid.length) {
    logConsole(`definições não guardadas — campos por preencher: ${invalid.join(", ")}`, "err");
    renderGridCharge(gridChargeState);   // repõe os valores guardados
    return { ok: false, code: "invalid_form" };
  }
  const body = { ...values, ...overrides };
  const d = await api("/api/grid-charge", { method: "PUT", body: JSON.stringify(body) });
  if (d.ok) { gridChargeState = d; renderGridCharge(d); }
  else {
    logConsole(`falha ao guardar o carregamento por excedente: ${d.error}`, "err");
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
      if (d.ok) logConsole(`excedente — aplicar agora: ${d.result.note}`, d.result.applied ? "ok" : "err");
      await loadGridCharge();
    } finally { e.target.disabled = false; }
  });
}

/* ---------- janela noturna de bateria ---------- */

let batteryWindowState = null;

const BW_FIELDS = [
  ["bw-from", "from", "str"],
  ["bw-to", "to", "str"],
  ["bw-floor", "floor_voltage", "num"],
  ["bw-resume", "resume_voltage", "num"],
  ["bw-poll-interval", "poll_interval", "num"],
  ["bw-min-switch", "min_switch_interval", "num"],
  ["bw-floor-confirmations", "floor_confirmations", "num"],
  ["bw-daytime-from", "daytime_from", "str"],
  ["bw-daytime-to", "daytime_to", "str"],
  ["bw-daytime-enter", "daytime_enter_w", "num"],
  ["bw-daytime-exit", "daytime_exit_w", "num"],
];

/* Qual dos travões está a decidir. `detail` já vem em pt-PT do servidor, por
   isso não se duplica aqui a tabela de razões -- só a cor. */
const BW_REASON_TONE = {
  window: "ok",
  outside: "muted",
  disabled: "muted",
  forbidden: "err",
  floor: "err",
  recovering: "warn",
  unknown_voltage: "err",
  yielding: "warn",
};

function renderBatteryWindow(state) {
  if (!document.querySelector("#bw-enabled")) return;   // secção não renderizada
  const cfg = state.config || {};
  $("#bw-enabled").checked = cfg.enabled;
  $("#bw-status").textContent = cfg.enabled ? "ativada" : "desativada";
  $("#bw-status").className = `badge ${cfg.enabled ? "" : "muted"}`;

  BW_FIELDS.forEach(([id, key]) => {
    const el = document.querySelector(`#${id}`);
    if (!el || document.activeElement === el) return;
    el.value = cfg[key];
  });

  const dayBox = document.querySelector("#bw-daytime-enabled");
  if (dayBox && document.activeElement !== dayBox) dayBox.checked = !!cfg.daytime_enabled;

  const absEl = document.querySelector("#bw-abs-floor");
  if (absEl && state.absolute_floor_v !== undefined) absEl.textContent = state.absolute_floor_v;

  // O horario da bomba e editavel: um bloqueio congelado nas horas erradas
  // le-se como protecao no painel enquanto a bomba corre fora dele.
  const pump = (state.pump_window || [])[0];
  ["bw-pump-from", "bw-pump-to"].forEach((id, i) => {
    const el = document.querySelector(`#${id}`);
    if (!el || document.activeElement === el) return;
    el.value = pump ? (i === 0 ? pump.from : pump.to) : "";
  });

  const hf = document.querySelector("#bw-hard-forbidden");
  if (hf) {
    hf.textContent = state.pump_unprotected
      ? "\u26a0 Sem horário de bomba definido: nada impede a bateria de "
        + "alimentar as cargas a qualquer hora. Correto só se a bomba já não "
        + "estiver na saída protegida."
      : "";
    hf.hidden = !state.pump_unprotected;
  }

  // Estado atual: o alvo, a razão, e a tensão contra o piso.
  const v = state.battery_voltage;
  const parts = [];
  if (state.detail) {
    const tone = BW_REASON_TONE[state.reason] || "muted";
    parts.push(`<span class="${tone === "muted" ? "muted" : ""}">${state.detail}</span>`);
  }
  if (v !== null && v !== undefined && cfg.floor_voltage !== undefined) {
    const margin = v - cfg.floor_voltage;
    parts.push(`<span class="muted">Bateria:</span> ${v.toFixed(2)} V `
      + `<span class="muted">(${margin >= 0 ? "+" : ""}${margin.toFixed(2)} V do piso)</span>`);
  }
  if (state.below_floor_readings) {
    parts.push(`<span class="muted">Abaixo do piso há</span> `
      + `${state.below_floor_readings}/${cfg.floor_confirmations} leituras`);
  }
  $("#bw-current").innerHTML = parts.join(" &middot; ") || `<span class="muted">Sem dados.</span>`;

  const latched = document.querySelector("#bw-latched");
  if (latched) {
    latched.textContent = state.recovering
      ? "\u26a0 Já descarregou nesta janela. Só volta à bateria na janela seguinte, "
        + "depois de a atual fechar e o pack recuperar."
      : "";
    latched.hidden = !state.recovering;
  }

  // O que o servidor pensa que aplicou vs. o que o inversor diz de si
  // próprio (QMOD) -- podem discordar (o inversor sai da bateria por
  // conta própria, ou entra nela sozinho por falha de rede). Ver
  // last_run.device_mismatch, preenchido só quando um tick real detetou
  // a discordância, não em cada leitura.
  const mismatchEl = document.querySelector("#bw-mismatch");
  if (mismatchEl) {
    const lr = state.last_run;
    const mismatch = lr && lr.device_mismatch;
    if (mismatch === "pop_drift_stuck") {
      mismatchEl.textContent = "\u26a0 O inversor ignorou várias tentativas de repor "
        + "a prioridade de saída. Precisa de intervenção manual no painel frontal.";
    } else if (mismatch === "pop_drift") {
      mismatchEl.textContent = "\u26a0 O inversor está em modo bateria com a rede "
        + "presente -- a prioridade de saída não era a que julgávamos. A reescrever.";
    } else if (mismatch === "hardware_override") {
      mismatchEl.textContent = "\u26a0 O inversor saiu do modo bateria por conta "
        + "própria -- a assumir que a descarga desta noite já terminou.";
    } else if (mismatch === "unexpected_battery") {
      mismatchEl.textContent = "\u26a0 O inversor está em modo bateria sem ter sido "
        + "pedido -- possível falha de rede.";
    } else {
      mismatchEl.textContent = "";
    }
    mismatchEl.hidden = !mismatch;
  }
}

async function loadBatteryWindow() {
  const d = await api("/api/battery-window");
  if (!d.ok) return;
  batteryWindowState = d;
  renderBatteryWindow(d);
}

function readBatteryWindowForm() {
  const out = { enabled: (batteryWindowState.config || {}).enabled };
  const invalid = [];
  BW_FIELDS.forEach(([id, key, type]) => {
    const el = document.querySelector(`#${id}`);
    if (!el) return;
    const raw = String(el.value).trim();
    if (type === "str") {
      if (!raw) invalid.push(id); else out[key] = raw;
      return;
    }
    const n = Number(raw);
    if (raw === "" || !Number.isFinite(n)) { invalid.push(id); return; }
    out[key] = n;
  });
  return { values: out, invalid };
}

async function saveBatteryWindow(overrides) {
  const { values, invalid } = readBatteryWindowForm();
  if (invalid.length) {
    logConsole(`janela de bateria não guardada — campos por preencher: ${invalid.join(", ")}`, "err");
    renderBatteryWindow(batteryWindowState);
    return { ok: false, code: "invalid_form" };
  }
  const from = (document.querySelector("#bw-pump-from") || {}).value || "";
  const to = (document.querySelector("#bw-pump-to") || {}).value || "";
  const existing = ((batteryWindowState.config || {}).pump_window || [])[0] || {};
  // Ambos vazios = a bomba saiu da saida protegida. Um so preenchido e um
  // engano, e gravar isso silenciosamente removia a protecao -- por isso
  // mantem-se o que estava.
  let pump_window;
  if (from && to) pump_window = [{ from, to, why: existing.why || "bomba de água" }];
  else if (!from && !to) pump_window = [];
  else pump_window = (batteryWindowState.config || {}).pump_window || [];

  const dayEl = document.querySelector("#bw-daytime-enabled");
  const body = { ...values, ...overrides, pump_window,
                 daytime_enabled: dayEl ? dayEl.checked : false,
                 forbidden: (batteryWindowState.config || {}).forbidden || [] };
  const d = await api("/api/battery-window", { method: "PUT", body: JSON.stringify(body) });
  if (d.ok) { batteryWindowState = d; renderBatteryWindow(d); }
  else {
    logConsole(`falha ao guardar a janela de bateria: ${d.error}`, "err");
    renderBatteryWindow(batteryWindowState);
  }
  return d;
}

function wireBatteryWindow() {
  const enabledBox = $("#bw-enabled");
  if (!enabledBox) return;   // battery_window_available era falso

  enabledBox.addEventListener("change", () => {
    saveBatteryWindow({ enabled: enabledBox.checked });
  });

  $("#bw-save").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try { await saveBatteryWindow({}); }
    finally { e.target.disabled = false; }
  });

  $("#bw-apply-now").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      const d = await api("/api/battery-window/apply-now", { method: "POST", body: JSON.stringify({ force: true }) });
      if (d.ok) logConsole(`janela de bateria — aplicar agora: ${d.result.note}`, d.result.applied ? "ok" : "err");
      await loadBatteryWindow();
    } finally { e.target.disabled = false; }
  });
}

/* ---------- de onde vem a energia ---------- */

const ENERGY_SOURCE_PT = { grid: "Rede", battery: "Bateria" };

/* `null` significa "não sabemos" e tem de aparecer como tal. Escrever 0 W ou
   omitir a linha faria passar uma leitura em falta por uma medição -- o erro
   que este quadro existe para não repetir. */
function fmtW(v) {
  return (v === null || v === undefined) ? null : `${Math.round(v)} W`;
}

function renderEnergy(energy) {
  const box = document.querySelector("#energy-box");
  if (!box || !energy) return;

  const src = energy.output_source;
  const srcEl = $("#energy-source");
  srcEl.textContent = ENERGY_SOURCE_PT[src] || "desconhecida";
  srcEl.className = `energy-source ${src || "unknown"}`;

  const load = fmtW(energy.output_load_w);
  $("#energy-load").textContent = load
    ? `\u2014 ${load}`
    : "\u2014 carga desconhecida";

  const bits = [];
  if (energy.output_load_from === "shelly") bits.push("medido à entrada (Shelly)");
  else if (energy.output_load_from === "inverter") bits.push("medido pelo inversor");
  if (energy.output_load_note) bits.push(energy.output_load_note);
  $("#energy-note").textContent = bits.join(" · ");

  // Lado da casa. O sinal do balanço: positivo = a comprar, negativo = a vender.
  const split = [];
  const solar = fmtW(energy.solar_w);
  if (solar) split.push(`<span>Solar <b>${solar}</b></span>`);
  const g = energy.grid_w;
  if (g !== null && g !== undefined) {
    const dir = g > 0 ? "a importar" : (g < 0 ? "a exportar" : "equilibrada");
    split.push(`<span>Rede <b>${dir}${g === 0 ? "" : " " + fmtW(Math.abs(g))}</b></span>`);
  }
  const house = fmtW(energy.house_w);
  if (house) split.push(`<span>Casa <b>${house}</b></span>`);
  const inv = fmtW(energy.inverter_input_w);
  if (inv) split.push(`<span>Entrada do inversor <b>${inv}</b></span>`);
  $("#energy-split").innerHTML = split.join("");
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
                             `Enviar ${cmd}`);
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
  wireBatteryWindow();
  await tick();
  loadDevice();
  loadRatings();

  const interval = 5000;
  pollTimer = setInterval(() => tick().catch(() => {}), interval);
  // Don't keep polling a hidden tab -- it's a serial link, not a web API.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { clearInterval(pollTimer); }
    else { tick().catch(() => {}); pollTimer = setInterval(() => tick().catch(() => {}), interval); }
  });
});
