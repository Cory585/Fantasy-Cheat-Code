"use strict";

// Draft guide: set league size, your draft slot, and rounds, then see when you pick
// (timeline), a mock draft by ADP (board), and who to target at each pick (cards).

const $ = (id) => document.getElementById(id);
const els = {
  teams: $("league-size"),
  slot: $("draft-slot"),
  slotPrev: $("slot-prev"),
  slotNext: $("slot-next"),
  rounds: $("rounds"),
  timeline: $("timeline"),
  timelineSummary: $("timeline-summary"),
  board: $("draft-board"),
  legend: $("position-legend"),
  filter: $("position-filter"),
  planList: $("plan-list"),
  status: $("data-status"),
  tooltip: $("viz-tooltip"),
};

const TEAM_OPTIONS = [8, 10, 12, 14, 16];
const ROUND_OPTIONS = [13, 14, 15, 16, 17, 18];
const POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"];
const TARGETS_SHOWN = 5;
const TARGETS_FETCHED = 15; // extra, so a position filter still has players to show

const state = { teams: 12, slot: 1, rounds: 15, position: "", plan: null };
let requestId = 0; // ignore responses to settings that have since changed

// ---------- Helpers ----------

function ordinal(n) {
  const suffix = { 1: "st", 2: "nd", 3: "rd" }[n % 100 >= 11 && n % 100 <= 13 ? 0 : n % 10] || "th";
  return `${n}${suffix}`;
}

const profileUrl = (playerId) => `/player/?id=${encodeURIComponent(playerId)}`;

const pickLabel = (round, pickInRound) => `${round}.${String(pickInRound).padStart(2, "0")}`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

const SVG_NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text != null) node.textContent = text;
  return node;
}

function shortName(name) {
  const parts = name.split(" ");
  if (parts.length < 2 || name.endsWith("D/ST")) return name;
  return `${parts[0][0]}. ${parts.slice(1).join(" ")}`;
}

function adpPhrase(target, overall) {
  if (target.adp == null) return "no ADP";
  const diff = Math.round(target.adp - overall);
  if (diff === 0) return `ADP ${target.adp.toFixed(1)} (right at your pick)`;
  const picks = plural(Math.abs(diff), "pick");
  return `ADP ${target.adp.toFixed(1)} (${picks} ${diff > 0 ? "after" : "before"} you)`;
}

// ---------- Tooltip (any element with data-tip) ----------

function showTip(text, x, y) {
  const tip = els.tooltip;
  tip.textContent = text;
  tip.hidden = false;
  const { width, height } = tip.getBoundingClientRect();
  const left = Math.min(Math.max(8, x + 14), window.innerWidth - width - 8);
  const top = y + 18 + height > window.innerHeight ? y - height - 12 : y + 18;
  tip.style.transform = `translate(${left}px, ${top}px)`;
}

function hideTip() {
  els.tooltip.hidden = true;
}

document.addEventListener("pointermove", (event) => {
  const target = event.target.closest?.("[data-tip]");
  if (target) showTip(target.dataset.tip, event.clientX, event.clientY);
  else hideTip();
});
document.addEventListener("focusin", (event) => {
  const target = event.target.closest?.("[data-tip]");
  if (!target) return hideTip();
  const box = target.getBoundingClientRect();
  showTip(target.dataset.tip, box.left, box.bottom);
});
document.addEventListener("scroll", hideTip, { passive: true });
document.addEventListener("keydown", (event) => event.key === "Escape" && hideTip());

// ---------- Settings (kept in the URL so a plan can be shared or bookmarked) ----------

function readSettings() {
  const params = new URLSearchParams(location.search);
  const pick = (name, allowed, fallback) => {
    const value = Number(params.get(name));
    return allowed.includes(value) ? value : fallback;
  };
  state.teams = pick("teams", TEAM_OPTIONS, 12);
  const slot = Number(params.get("slot"));
  state.slot = Number.isInteger(slot) && slot >= 1 && slot <= state.teams ? slot : 1;
  state.rounds = pick("rounds", ROUND_OPTIONS, 15);
  const position = params.get("pos");
  state.position = POSITIONS.includes(position) ? position : "";
}

function writeSettings() {
  const params = new URLSearchParams({ teams: state.teams, slot: state.slot, rounds: state.rounds });
  if (state.position) params.set("pos", state.position);
  history.replaceState(null, "", `${location.pathname}?${params}`);
}

// ---------- Controls ----------

function renderControls() {
  for (const button of els.teams.querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(Number(button.dataset.teams) === state.teams));
    button.setAttribute("aria-label", `${button.dataset.teams} teams`);
  }
  els.slot.replaceChildren(
    ...Array.from({ length: state.teams }, (_, i) => new Option(`${ordinal(i + 1)} pick`, i + 1)),
  );
  els.slot.value = String(state.slot);
  els.slotPrev.disabled = state.slot <= 1;
  els.slotNext.disabled = state.slot >= state.teams;
  els.rounds.value = String(state.rounds);
  for (const button of els.filter.querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.position === state.position));
  }
}

function renderLegend() {
  els.legend.replaceChildren(
    ...POSITIONS.map((position) => {
      const item = el("li", `legend-item pos-${position}`);
      item.append(el("span", "pos-swatch"), el("span", null, position));
      return item;
    }),
  );
}

// ---------- My picks (computed locally so the page responds before the API does) ----------

function myPicks() {
  const { teams, slot, rounds } = state;
  return Array.from({ length: rounds }, (_, i) => {
    const round = i + 1;
    const pickInRound = round % 2 ? slot : teams + 1 - slot;
    return { round, pickInRound, overall: i * teams + pickInRound, label: pickLabel(round, pickInRound) };
  });
}

// ---------- Timeline: every pick in the draft, yours marked, arcs showing the wait ----------

function renderTimeline() {
  const picks = myPicks();
  const total = state.teams * state.rounds;
  const width = Math.max(els.timeline.clientWidth, 320);
  const height = 132;
  const pad = 12;
  const baseline = 104;
  const x = (overall) => pad + ((overall - 0.5) / total) * (width - pad * 2);
  const maxGap = 2 * state.teams - 2;
  const chart = svg("svg", {
    width, height, viewBox: `0 0 ${width} ${height}`, class: "timeline-svg", role: "img",
    "aria-label": `Your picks: ${picks.map((p) => p.label).join(", ")}`,
  });

  // Round bands, alternating, with round numbers where there's room.
  const bandWidth = (width - pad * 2) / state.rounds;
  for (let r = 0; r < state.rounds; r++) {
    const left = pad + r * bandWidth;
    chart.append(svg("rect", {
      x: left, y: baseline - 6, width: bandWidth - 1, height: 12, rx: 2,
      class: r % 2 ? "band band-alt" : "band",
    }));
    if (bandWidth >= 22 || r % 2 === 0) {
      chart.append(svg("text", { x: left + bandWidth / 2, y: baseline + 24, class: "tick", "text-anchor": "middle" },
        `R${r + 1}`));
    }
  }

  // Arcs between consecutive picks, taller for longer waits.
  picks.slice(0, -1).forEach((pick, i) => {
    const next = picks[i + 1];
    const gap = next.overall - pick.overall - 1;
    const x1 = x(pick.overall);
    const x2 = x(next.overall);
    const rise = 14 + (gap / Math.max(maxGap, 1)) * 58;
    const mid = (x1 + x2) / 2;
    const tip = gap === 0 ? `${pick.label} and ${next.label} are back to back`
      : `${plural(gap, "pick")} by other teams between ${pick.label} and ${next.label}`;
    const group = svg("g", { class: "arc-group", "data-tip": tip });
    group.append(svg("path", {
      d: `M ${x1} ${baseline} Q ${mid} ${baseline - rise * 2} ${x2} ${baseline}`, class: "arc",
    }));
    if (x2 - x1 >= 18) {
      group.append(svg("text", { x: mid, y: baseline - rise - 6, class: "arc-label", "text-anchor": "middle" },
        gap === 0 ? "0" : String(gap)));
    }
    chart.append(group);
  });

  for (const pick of picks) {
    chart.append(svg("circle", {
      cx: x(pick.overall), cy: baseline, r: 6, class: "my-pick-dot",
      "data-tip": `${pick.label}: round ${pick.round}, ${ordinal(pick.overall)} overall`,
    }));
  }
  els.timeline.replaceChildren(chart);

  const gaps = picks.slice(0, -1).map((p, i) => ({ gap: picks[i + 1].overall - p.overall - 1, from: p, to: picks[i + 1] }));
  const longest = gaps.reduce((a, b) => (b.gap > a.gap ? b : a), gaps[0] || { gap: 0 });
  const backToBack = gaps.filter((g) => g.gap === 0).length;
  els.timelineSummary.textContent = [
    `You pick ${plural(picks.length, "time")}.`,
    longest && longest.gap > 0
      ? `Longest wait: ${plural(longest.gap, "pick")}, between ${longest.from.label} and ${longest.to.label}.`
      : null,
    backToBack ? `You get ${plural(backToBack, "pair")} of back-to-back picks at the turn.` : null,
    "Numbers on the arcs are the picks other teams make in between.",
  ].filter(Boolean).join(" ");
}

// ---------- Board: mock draft, colored by position ----------

function renderBoard() {
  const { teams, slot, rounds } = state;
  const board = state.plan?.board || [];
  const grid = el("div", "draft-grid");
  grid.style.setProperty("--teams", teams);

  grid.append(el("span", "grid-corner"));
  for (let team = 1; team <= teams; team++) {
    const head = el("button", "team-head" + (team === slot ? " is-mine" : ""), team === slot ? "You" : `Team ${team}`);
    head.type = "button";
    head.dataset.slot = team;
    head.setAttribute("aria-label", team === slot ? `Your slot, ${ordinal(team)} pick` : `Draft from the ${ordinal(team)} pick`);
    grid.append(head);
  }

  for (let round = 1; round <= rounds; round++) {
    grid.append(el("span", "round-label", `R${round}`));
    for (let team = 1; team <= teams; team++) {
      const pickInRound = round % 2 ? team : teams + 1 - team;
      const overall = (round - 1) * teams + pickInRound;
      const player = board[overall - 1];
      const cell = el("div", "pick" + (team === slot ? " is-mine" : ""));
      cell.append(el("span", "pick-num", pickLabel(round, pickInRound)));
      if (player) {
        cell.classList.add(`pos-${player.position}`);
        const line = el("span", "pick-player");
        const name = el("a", "pick-name profile-link", shortName(player.name));
        name.href = profileUrl(player.player_id);
        line.append(el("span", "pos-tag", player.position), name);
        cell.append(line);
        cell.dataset.tip = `${pickLabel(round, pickInRound)}: ${player.name} (${player.position}, ${player.team}), ` +
          (player.adp != null ? `ADP ${player.adp.toFixed(1)}` : "no ADP");
      }
      grid.append(cell);
    }
  }
  els.board.replaceChildren(grid);
}

// ---------- Pick cards ----------

function mixBar(mix, picksUntilNext) {
  const wrap = el("div", "mix");
  if (picksUntilNext == null) {
    wrap.append(el("p", "mix-caption", "Your last pick of the draft."));
    return wrap;
  }
  if (picksUntilNext === 0) {
    wrap.append(el("p", "mix-caption", "You pick again right after this, so you can pair two players here."));
    return wrap;
  }
  const entries = POSITIONS.filter((p) => mix[p]).map((p) => [p, mix[p]]);
  if (!entries.length) {
    wrap.append(el("p", "mix-caption", "No ranked players left before your next pick."));
    return wrap;
  }
  wrap.append(el("p", "mix-caption", `Before your next pick, other teams take ${plural(picksUntilNext, "player")}:`));
  const bar = el("div", "mix-bar");
  bar.setAttribute("role", "img");
  bar.setAttribute("aria-label", entries.map(([p, n]) => `${n} ${p}`).join(", "));
  for (const [position, n] of entries) {
    const segment = el("span", `mix-seg pos-${position}`);
    segment.style.flexGrow = n;
    segment.dataset.tip = `${n} ${position} likely taken`;
    bar.append(segment);
  }
  const labels = el("p", "mix-labels");
  labels.textContent = entries.map(([p, n]) => `${n} ${p}`).join(", ");
  wrap.append(bar, labels);
  return wrap;
}

function dotPlot(targets, overall) {
  const withAdp = targets.filter((t) => t.adp != null);
  if (!withAdp.length) return null;
  const width = 300;
  const rowHeight = 16;
  const height = 24 + withAdp.length * rowHeight;
  const labelRoom = 92; // left gutter for player names
  const values = [overall, ...withAdp.map((t) => t.adp)];
  const spread = Math.max(Math.max(...values) - Math.min(...values), 4);
  const lo = Math.min(...values) - spread * 0.1;
  const hi = Math.max(...values) + spread * 0.1;
  const x = (v) => labelRoom + ((v - lo) / (hi - lo)) * (width - labelRoom - 8);

  const chart = svg("svg", {
    viewBox: `0 0 ${width} ${height}`, class: "dot-plot", role: "img",
    "aria-label": `Where these players are usually drafted, compared with your pick at ${overall}`,
  });
  const px = x(overall);
  chart.append(svg("line", { x1: px, x2: px, y1: 12, y2: height - 2, class: "dot-mine" }));
  chart.append(svg("text", { x: px, y: 9, class: "tick", "text-anchor": "middle" }, `Your pick, ${overall}`));
  withAdp.forEach((t, i) => {
    const cy = 24 + i * rowHeight;
    const cx = x(t.adp);
    chart.append(svg("text", { x: labelRoom - 10, y: cy + 3.5, class: "dot-label", "text-anchor": "end" },
      shortName(t.name)));
    chart.append(svg("line", { x1: px, x2: cx, y1: cy, y2: cy, class: "dot-stem" }));
    chart.append(svg("circle", {
      cx, cy, r: 5, class: `dot pos-${t.position}`, "data-tip": `${t.name}: ${adpPhrase(t, overall)}`,
    }));
  });
  return chart;
}

function targetItem(target, overall) {
  const item = el("li", `target pos-${target.position}`);
  const name = el("span", "target-name");
  const link = el("a", "profile-link", target.name);
  link.href = profileUrl(target.player_id);
  name.append(el("span", "pos-tag", target.position), link);
  const meta = el("span", "player-meta",
    `${target.team}, ${adpPhrase(target, overall)}, ${Math.round(target.projection)} proj. pts`);
  item.append(name, meta);
  return item;
}

function renderPlan() {
  const plan = state.plan;
  if (!plan) return;
  els.planList.replaceChildren(
    ...plan.picks.map(({ pick, targets, position_mix, picks_until_next }) => {
      const card = el("li", "plan-pick");
      const head = el("div", "plan-pick-head");
      head.append(el("strong", null, pick.label), el("span", null, `Round ${pick.round}, ${ordinal(pick.overall)} overall`));
      card.append(head, mixBar(position_mix, picks_until_next));

      const shown = targets.filter((t) => !state.position || t.position === state.position).slice(0, TARGETS_SHOWN);
      if (shown.length) {
        const plot = dotPlot(shown, pick.overall);
        if (plot) card.append(plot);
        const list = el("ol", "target-list");
        list.append(...shown.map((t) => targetItem(t, pick.overall)));
        card.append(list);
      } else {
        card.append(el("p", "player-meta", targets.length
          ? `No ${state.position}s ranked near this pick. Choose All to see everyone.`
          : "No ranked players left this late."));
      }
      return card;
    }),
  );
  els.status.textContent =
    plan.adp_source === "espn"
      ? "Ranked by ESPN average draft position."
      : "No ADP data yet, so players are ranked by projection. Run python -m app.data.collect to add ADP.";
}

function renderError(message) {
  els.planList.replaceChildren(el("li", "result-error", message));
}

async function loadPlan() {
  const id = ++requestId;
  const params = new URLSearchParams({
    teams: state.teams, slot: state.slot, rounds: state.rounds, targets: TARGETS_FETCHED,
  });
  let response;
  try {
    response = await fetch(`/draft/plan?${params}`);
  } catch {
    if (id === requestId) renderError("Couldn't reach the server. Check that it's running, then change a setting to retry.");
    return;
  }
  if (id !== requestId) return;
  if (!response.ok) {
    renderError(`Couldn't load the draft plan (the server returned HTTP ${response.status}).`);
    return;
  }
  const plan = await response.json();
  if (id !== requestId) return;
  state.plan = plan;
  renderBoard();
  renderPlan();
}

// ---------- Wiring ----------

function update({ refetch = true } = {}) {
  renderControls();
  writeSettings();
  if (refetch) {
    renderTimeline();
    renderBoard(); // shows the new slot right away; players fill in when the plan arrives
    loadPlan();
  } else {
    renderPlan();
  }
}

function setSlot(slot) {
  state.slot = Math.min(Math.max(slot, 1), state.teams);
  update();
}

function init() {
  readSettings();
  renderLegend();

  els.teams.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-teams]");
    if (!button) return;
    state.teams = Number(button.dataset.teams);
    state.slot = Math.min(state.slot, state.teams);
    update();
  });
  els.slot.addEventListener("change", () => setSlot(Number(els.slot.value)));
  els.slotPrev.addEventListener("click", () => setSlot(state.slot - 1));
  els.slotNext.addEventListener("click", () => setSlot(state.slot + 1));
  els.rounds.addEventListener("change", () => {
    state.rounds = Number(els.rounds.value);
    update();
  });
  els.board.addEventListener("click", (event) => {
    const head = event.target.closest("button[data-slot]");
    if (head) setSlot(Number(head.dataset.slot));
  });
  els.filter.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-position]");
    if (!button) return;
    state.position = button.dataset.position;
    update({ refetch: false });
  });
  document.getElementById("draft-setup").addEventListener("submit", (event) => event.preventDefault());

  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(renderTimeline, 150);
  });

  update();
}

init();
