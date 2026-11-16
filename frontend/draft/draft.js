"use strict";

// Draft guide: set league size, your draft slot, and rounds, then see when you pick
// (timeline), a mock draft by ADP (board), and who to target at each pick (cards).
// Connect an ESPN league to follow its draft live: picks arrive over Server-Sent Events,
// fill the board, and the plan is redone around the players already taken.

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
  liveForm: $("live-form"),
  liveLeague: $("live-league"),
  liveS2: $("live-s2"),
  liveSwid: $("live-swid"),
  liveConnect: $("live-connect"),
  liveConnected: $("live-connected"),
  liveName: $("live-name"),
  liveProgress: $("live-progress"),
  liveDot: $("live-dot"),
  liveTeam: $("live-team"),
  liveDisconnect: $("live-disconnect"),
  livePicks: $("live-picks"),
  liveMessage: $("live-message"),
};

const TEAM_OPTIONS = [8, 10, 12, 14, 16];
const ROUND_OPTIONS = [13, 14, 15, 16, 17, 18];
const POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"];
const TARGETS_SHOWN = 5;
const TARGETS_FETCHED = 15; // extra, so a position filter still has players to show

// `plan` is for the current settings only (cleared when they change); `board` is the ADP
// ranking, which doesn't depend on settings, so it's kept to avoid flicker while reloading.
// `boardOffset` is how many picks the board starts after (picks already made in a live draft).
// `live` is the followed ESPN draft: { sessionId, draft, teamId, source }, or null.
const state = {
  teams: 12, slot: 1, rounds: 15, position: "", plan: null, board: [], boardOffset: 0, error: null, live: null,
};
let requestId = 0; // ignore responses to settings that have since changed

// ---------- Helpers ----------

const profileUrl = (playerId) => `/player/?id=${encodeURIComponent(playerId)}`;

const pickLabel = (round, pickInRound) => `${round}.${String(pickInRound).padStart(2, "0")}`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

const { el, ordinal, request, errorMessage, getJson, messageOf, errorBlock } = window.PlayerUI;

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
  // A live draft sets the league size, rounds, and (once you pick your team) your slot.
  const live = Boolean(state.live);
  const slotFromLive = live && liveTeamSlot() != null;
  for (const button of els.teams.querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(Number(button.dataset.teams) === state.teams));
    button.setAttribute("aria-label", `${button.dataset.teams} teams`);
    button.disabled = live;
  }
  if (![...els.rounds.options].some((option) => Number(option.value) === state.rounds)) {
    els.rounds.append(new Option(`${state.rounds} rounds`, state.rounds));
  }
  els.rounds.disabled = live;
  els.slot.replaceChildren(
    ...Array.from({ length: state.teams }, (_, i) => new Option(`${ordinal(i + 1)} pick`, i + 1)),
  );
  els.slot.value = String(state.slot);
  els.slot.disabled = slotFromLive;
  els.slotPrev.disabled = slotFromLive || state.slot <= 1;
  els.slotNext.disabled = slotFromLive || state.slot >= state.teams;
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
    width, height, viewBox: `0 0 ${width} ${height}`, class: "timeline-svg", role: "group",
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

  // Each dot is focusable and carries the wait to the next pick, so keyboard and
  // screen-reader users get what the arcs show.
  picks.forEach((pick, i) => {
    const next = picks[i + 1];
    const wait = next ? next.overall - pick.overall - 1 : null;
    const label = `${pick.label}: round ${pick.round}, ${ordinal(pick.overall)} overall. ` +
      (wait == null ? "Your last pick." : wait === 0 ? "You pick again right after." : `${plural(wait, "pick")} until your next.`);
    const past = pick.overall <= livePicksMade();
    chart.append(svg("circle", {
      cx: x(pick.overall), cy: baseline, r: 6, class: past ? "my-pick-dot is-past" : "my-pick-dot",
      tabindex: 0, role: "img", "aria-label": label, "data-tip": label,
    }));
  });
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
  const board = state.board;
  const taken = new Map((state.live?.draft.picks || []).map((p) => [p.overall, p]));
  const grid = el("div", "draft-grid");
  grid.style.setProperty("--teams", teams);
  grid.setAttribute("role", "table");
  grid.setAttribute("aria-label", `Mock draft, ${teams} teams, ${rounds} rounds`);
  // Rows use display: contents, so they group cells for assistive tech without
  // affecting the CSS grid.
  const row = () => {
    const r = el("div", "grid-row");
    r.setAttribute("role", "row");
    grid.append(r);
    return r;
  };

  const headRow = row();
  const corner = el("span", "grid-corner");
  corner.setAttribute("role", "columnheader");
  corner.setAttribute("aria-label", "Round");
  headRow.append(corner);
  for (let team = 1; team <= teams; team++) {
    const th = el("span", "team-head-cell");
    th.setAttribute("role", "columnheader");
    const head = el("button", "team-head" + (team === slot ? " is-mine" : ""), team === slot ? "You" : `Team ${team}`);
    head.type = "button";
    head.dataset.slot = team;
    head.setAttribute("aria-label", team === slot ? `Your slot, ${ordinal(team)} pick` : `Draft from the ${ordinal(team)} pick`);
    th.append(head);
    headRow.append(th);
  }

  for (let round = 1; round <= rounds; round++) {
    const roundRow = row();
    const label = el("span", "round-label", `R${round}`);
    label.setAttribute("role", "rowheader");
    label.setAttribute("aria-label", `Round ${round}`);
    roundRow.append(label);
    for (let team = 1; team <= teams; team++) {
      const pickInRound = round % 2 ? team : teams + 1 - team;
      const overall = (round - 1) * teams + pickInRound;
      const cell = el("div", "pick" + (team === slot ? " is-mine" : ""));
      cell.setAttribute("role", "cell");
      cell.append(el("span", "pick-num", pickLabel(round, pickInRound)));
      const made = taken.get(overall);
      const player = made ? null : board[overall - state.boardOffset - 1];
      if (made) {
        cell.classList.add("is-taken");
        if (made.position) cell.classList.add(`pos-${made.position}`);
        const line = el("span", "pick-player");
        const label = made.name ? shortName(made.name) : `ESPN player ${made.espn_player_id}`;
        const name = made.player_id ? el("a", "pick-name profile-link", label) : el("span", "pick-name", label);
        if (made.player_id) name.href = profileUrl(made.player_id);
        if (made.position) line.append(el("span", "pos-tag", made.position));
        line.append(name);
        cell.append(line);
        cell.dataset.tip = `${pickLabel(round, pickInRound)}: drafted ${made.name || label}` +
          (made.position ? ` (${made.position}, ${made.team})` : "") + (made.keeper ? ", keeper" : "");
      } else if (overall <= state.boardOffset) {
        cell.classList.add("is-taken");
      } else if (player) {
        cell.classList.add(`pos-${player.position}`);
        const line = el("span", "pick-player");
        const name = el("a", "pick-name profile-link", shortName(player.name));
        name.href = profileUrl(player.player_id);
        line.append(el("span", "pos-tag", player.position), name);
        cell.append(line);
        cell.dataset.tip = `${pickLabel(round, pickInRound)}: ${player.name} (${player.position}, ${player.team}), ` +
          (player.adp != null ? `ADP ${player.adp.toFixed(1)}` : "no ADP") +
          (player.tier != null ? `, ${player.position} tier ${player.tier}` : "");
      }
      roundRow.append(cell);
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
  const ranked = entries.reduce((sum, [, n]) => sum + n, 0);
  const unranked = picksUntilNext - ranked;
  wrap.append(el("p", "mix-caption",
    `Before your next pick, other teams take ${plural(ranked, "ranked player")}` +
    (unranked > 0 ? ` (the board runs out of ranked players for the other ${plural(unranked, "pick")})` : "") + ":"));
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

// ---------- Tiers ----------

const POSITION_PLURALS = { QB: "QBs", RB: "RBs", WR: "WRs", TE: "TEs", K: "kickers", DST: "defenses" };
const ALERTS_SHOWN = 3;

function tierAlertText(alert, nextLabel) {
  const group = `Tier ${alert.tier} ${POSITION_PLURALS[alert.position] || alert.position}`;
  if (alert.left_at_next === 0) {
    return alert.left === 1
      ? `Last chance: the last ${group.replace(/s$/, "")} left is likely gone before ${nextLabel}.`
      : `Last chance: all ${alert.left} ${group} left are likely gone before ${nextLabel}.`;
  }
  return `${group} are running dry: ${alert.left} left now, about ${alert.left_at_next} at ${nextLabel}.`;
}

function tierAlerts(alerts, nextLabel) {
  if (!alerts?.length || !nextLabel) return null;
  const list = el("ul", "tier-alerts");
  list.setAttribute("aria-label", "Tiers running dry");
  list.append(...alerts.slice(0, ALERTS_SHOWN).map((alert) => {
    const item = el("li", `tier-alert pos-${alert.position}` + (alert.left_at_next === 0 ? " is-last" : ""));
    item.append(el("span", "pos-tag", alert.position), el("span", null, tierAlertText(alert, nextLabel)));
    return item;
  }));
  return list;
}

function targetItem(target, overall, dry = new Set()) {
  const item = el("li", `target pos-${target.position}`);
  const name = el("span", "target-name");
  const link = el("a", "profile-link", target.name);
  link.href = profileUrl(target.player_id);
  name.append(el("span", "pos-tag", target.position), link);
  if (target.tier != null) {
    const runningDry = dry.has(`${target.position}${target.tier}`);
    const chip = el("span", "tier-chip" + (runningDry ? " is-dry" : ""), `T${target.tier}`);
    chip.dataset.tip = `${target.position} tier ${target.tier}` +
      (runningDry ? ": this tier is likely gone, or nearly, by your next pick" : "");
    chip.setAttribute("aria-label", chip.dataset.tip);
    name.append(chip);
  }
  const meta = el("span", "player-meta",
    `${target.team}, ${adpPhrase(target, overall)}, ${Math.round(target.projection)} proj. pts`);
  item.append(name, meta);
  return item;
}

function renderPlan() {
  const plan = state.plan;
  if (!plan) {
    if (!state.error) els.planList.replaceChildren(el("li", "plan-loading player-meta", "Loading your picks…"));
    return;
  }
  if (!plan.picks.length && state.live) {
    els.planList.replaceChildren(el("li", "player-meta", "You've made all your picks."));
    return;
  }
  els.planList.replaceChildren(
    ...plan.picks.map(({ pick, targets, position_mix, picks_until_next, tier_alerts }, i) => {
      const card = el("li", "plan-pick");
      const head = el("div", "plan-pick-head");
      head.append(el("strong", null, pick.label), el("span", null, `Round ${pick.round}, ${ordinal(pick.overall)} overall`));
      card.append(head, mixBar(position_mix, picks_until_next));
      const alerts = (tier_alerts || []).filter((a) => !state.position || a.position === state.position);
      const alertList = tierAlerts(alerts, plan.picks[i + 1]?.pick.label);
      if (alertList) card.append(alertList);
      const dry = new Set(alerts.map((a) => `${a.position}${a.tier}`));

      const shown = targets.filter((t) => !state.position || t.position === state.position).slice(0, TARGETS_SHOWN);
      if (shown.length) {
        const plot = dotPlot(shown, pick.overall);
        if (plot) card.append(plot);
        const list = el("ol", "target-list");
        list.append(...shown.map((t) => targetItem(t, pick.overall, dry)));
        card.append(list);
      } else {
        card.append(el("p", "player-meta", targets.length
          ? `No ${state.position}s ranked near this pick. Choose All to see everyone.`
          : "No ranked players left this late."));
      }
      return card;
    }),
  );
  // A one-line summary for screen readers (this element is the live region, not the cards).
  const first = plan.picks[0]?.pick.label;
  const left = state.live ? " left" : "";
  els.status.textContent =
    `Plan for the ${ordinal(plan.slot)} pick: ${plural(plan.picks.length, "pick")}${left}` +
    (first ? `, ${state.live ? "next" : "first"} at ${first}. ` : ". ") +
    (plan.adp_source === "espn"
      ? "Ranked by ESPN average draft position."
      : "No average draft position data yet, so players are ranked by projection.");
}

function renderError(error) {
  state.error = error;
  els.planList.replaceChildren(errorBlock(error, () => update(), { tag: "li" }));
  els.status.textContent = messageOf(error);
}

async function loadPlan() {
  const id = ++requestId;
  const params = new URLSearchParams({
    teams: state.teams, slot: state.slot, rounds: state.rounds, targets: TARGETS_FETCHED,
  });
  const picks = state.live?.draft.picks || [];
  if (picks.length) {
    params.set("picks_made", livePicksMade());
    for (const pick of picks) if (pick.player_id) params.append("drafted", pick.player_id);
  }
  let plan;
  try {
    plan = await getJson(`/draft/plan?${params}`, "Couldn't load the draft plan");
  } catch (error) {
    if (id === requestId) renderError(error);
    return;
  }
  if (id !== requestId) return;
  state.plan = plan;
  state.board = plan.board || [];
  state.boardOffset = plan.picks_made || 0;
  const focusedSlot = document.activeElement?.dataset?.slot;
  renderBoard();
  restoreBoardFocus(focusedSlot);
  renderPlan();
}

// Re-rendering the board replaces its buttons; put focus back on the same team.
function restoreBoardFocus(slot) {
  if (slot) els.board.querySelector(`button[data-slot="${slot}"]`)?.focus();
}

// ---------- Live ESPN draft ----------

const LIVE_KEY = "fcc-live-draft"; // { sessionId, teamId } in sessionStorage; cookies are never stored
const STOP_CODES = new Set(["espn_private_league", "espn_league_not_found", "live_draft_not_found"]);

function livePicksMade() {
  const picks = state.live?.draft?.picks || [];
  return picks.length ? picks[picks.length - 1].overall : 0;
}

function liveTeamSlot() {
  const team = state.live?.draft?.teams.find((t) => t.id === state.live.teamId);
  return team?.slot ?? null;
}

function saveLive() {
  try {
    if (state.live) {
      sessionStorage.setItem(LIVE_KEY, JSON.stringify({ sessionId: state.live.sessionId, teamId: state.live.teamId }));
    } else {
      sessionStorage.removeItem(LIVE_KEY);
    }
  } catch { /* storage unavailable: the session just won't survive a reload */ }
}

function loadSavedLive() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(LIVE_KEY) || "null");
    return saved && /^[A-Za-z0-9_-]{32}$/.test(saved.sessionId) ? saved : null;
  } catch {
    return null;
  }
}

function liveMessage(text, isError = false) {
  els.liveMessage.textContent = text || "";
  els.liveMessage.classList.toggle("is-error", Boolean(isError));
}

async function errorText(response) {
  if (response.status === 422) {
    return "Check the league ID and cookies. The league ID is digits only, espn_s2 is a long code, " +
      "and SWID looks like {XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}.";
  }
  return errorMessage(response, "Couldn't connect to ESPN");
}

function liveProgressText(draft) {
  const total = draft.team_count * draft.rounds;
  const updated = new Date(draft.updated_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
  const progress = {
    pre_draft: `Draft hasn't started. ${draft.team_count} teams, ${draft.rounds} rounds.`,
    in_progress: `${livePicksMade()} of ${total} picks in.`,
    complete: "Draft complete.",
  }[draft.status];
  return `${progress} Updated ${updated}.`;
}

function renderLive() {
  const live = state.live;
  els.liveForm.hidden = Boolean(live);
  els.liveConnected.hidden = !live;
  if (!live) return;
  const { draft } = live;
  els.liveName.textContent = draft.name;
  els.liveProgress.textContent = liveProgressText(draft);
  els.liveDot.className = `live-dot is-${draft.status}`;

  els.liveTeam.replaceChildren(...draft.teams.map((team) =>
    new Option(team.slot ? `${team.name} (${ordinal(team.slot)} pick)` : team.name, team.id)));
  els.liveTeam.value = String(live.teamId);

  const teamName = new Map(draft.teams.map((t) => [t.id, t.name]));
  const latest = draft.picks.slice(-6).reverse();
  if (!latest.length) {
    els.livePicks.replaceChildren(el("li", "player-meta", "No picks yet."));
    return;
  }
  els.livePicks.replaceChildren(...latest.map((pick) => {
    const item = el("li", "live-pick" + (pick.position ? ` pos-${pick.position}` : ""));
    item.append(el("span", "pick-num", pickLabel(pick.round, pick.round_pick)));
    if (pick.position) item.append(el("span", "pos-tag", pick.position));
    item.append(el("span", "live-pick-name", pick.name || `ESPN player ${pick.espn_player_id}`),
      el("span", "player-meta", teamName.get(pick.team_id) || `Team ${pick.team_id}`));
    return item;
  }));
}

function applyDraft(draft) {
  const live = state.live;
  const first = !live.draft;
  const before = livePicksMade();
  live.draft = draft;
  if (!draft.teams.some((t) => t.id === live.teamId)) {
    live.teamId = (draft.teams.find((t) => t.slot === state.slot) || draft.teams[0])?.id ?? null;
  }
  saveLive();
  if (draft.team_count >= 4 && draft.team_count <= 20) state.teams = draft.team_count;
  state.rounds = Math.min(Math.max(draft.rounds, 1), 25);
  state.slot = liveTeamSlot() ?? Math.min(state.slot, state.teams);
  renderLive();
  if (draft.draft_type !== "SNAKE") {
    liveMessage(`ESPN lists this as a ${draft.draft_type.toLowerCase()} draft. Picks still show up here, ` +
      "but the plan assumes a snake draft.");
  }
  if (draft.status === "complete") closeStream();
  if (first) update();
  else if (livePicksMade() !== before) update({ keepPlan: true });
}

function closeStream() {
  state.live?.source?.close();
  if (state.live) state.live.source = null;
}

function openStream() {
  const live = state.live;
  closeStream();
  const source = new EventSource(`/draft/live/${encodeURIComponent(live.sessionId)}/events`);
  live.source = source;
  source.addEventListener("draft", (event) => {
    let draft;
    try { draft = JSON.parse(event.data); } catch { return; }
    if (state.live !== live) return;
    if (els.liveMessage.classList.contains("is-error")) liveMessage("");
    applyDraft(draft);
  });
  source.addEventListener("problem", (event) => {
    let problem;
    try { problem = JSON.parse(event.data); } catch { return; }
    if (state.live !== live) return;
    if (STOP_CODES.has(problem.code)) endLive(problem.detail, true);
    else liveMessage(`${problem.detail} Still trying.`, true);
  });
  source.addEventListener("error", () => {
    if (state.live !== live || live.source !== source) return;
    if (source.readyState === EventSource.CLOSED) checkSession(live);
    else liveMessage("Lost the live connection. Reconnecting…", true);
  });
}

// The stream was refused outright, e.g. the session expired or the server restarted.
async function checkSession(live) {
  let response = null;
  try {
    response = await fetch(`/draft/live/${encodeURIComponent(live.sessionId)}`);
  } catch { /* offline: retry below */ }
  if (state.live !== live) return;
  if (response?.status === 404) {
    endLive("That live session ended. Connect your league again.", true);
    return;
  }
  liveMessage("Lost the live connection. Reconnecting…", true);
  setTimeout(() => {
    if (state.live === live && live.draft?.status !== "complete") openStream();
  }, 5000);
}

function startLive(sessionId, draft, teamId = null) {
  closeStream();
  state.live = { sessionId, draft: null, teamId, source: null };
  applyDraft(draft);
  if (draft.status !== "complete") openStream();
}

function endLive(message, isError = false) {
  closeStream();
  state.live = null;
  saveLive();
  state.boardOffset = 0;
  renderLive();
  liveMessage(message, isError);
  update();
}

async function connectLive(event) {
  event.preventDefault();
  const leagueId = els.liveLeague.value.trim();
  const s2 = els.liveS2.value.trim();
  const swid = els.liveSwid.value.trim();
  if (!/^\d{1,12}$/.test(leagueId)) {
    liveMessage("Enter your league ID: the number after leagueId= in your ESPN league's address.", true);
    els.liveLeague.focus();
    return;
  }
  if (Boolean(s2) !== Boolean(swid)) {
    liveMessage("For a private league, paste both espn_s2 and SWID.", true);
    return;
  }
  const body = { league_id: leagueId };
  if (s2) Object.assign(body, { espn_s2: s2, swid });
  els.liveConnect.disabled = true;
  liveMessage("Connecting to ESPN…");
  let response;
  try {
    response = await request("/draft/live", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
  } catch (error) {
    liveMessage(error.message, true);
    return;
  } finally {
    els.liveConnect.disabled = false;
  }
  if (!response.ok) {
    liveMessage(await errorText(response), true);
    return;
  }
  let session;
  try {
    session = await response.json();
  } catch {
    liveMessage("The server sent a response this page couldn't read. Try again.", true);
    return;
  }
  // The server holds the cookies now; don't leave them in the page.
  els.liveS2.value = "";
  els.liveSwid.value = "";
  liveMessage("");
  startLive(session.session_id, session.draft);
  els.liveTeam.focus();
}

async function disconnectLive() {
  const sessionId = state.live?.sessionId;
  endLive("Disconnected. The server no longer has your league's cookies.");
  els.liveLeague.focus();
  if (sessionId) {
    try {
      await fetch(`/draft/live/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    } catch { /* it expires on its own */ }
  }
}

// Pick a live draft back up after a reload (the session id is kept for this tab only).
async function resumeLive() {
  const saved = loadSavedLive();
  if (!saved) return;
  let response;
  try {
    response = await fetch(`/draft/live/${encodeURIComponent(saved.sessionId)}`);
  } catch {
    return;
  }
  if (!response.ok) {
    try { sessionStorage.removeItem(LIVE_KEY); } catch { /* ignore */ }
    if (response.status === 404) liveMessage("Your live draft session ended. Connect your league again.");
    return;
  }
  let draft;
  try { draft = await response.json(); } catch { return; }
  startLive(saved.sessionId, draft, saved.teamId);
}

// ---------- Wiring ----------

function update({ refetch = true, keepPlan = false } = {}) {
  const focusedSlot = document.activeElement?.dataset?.slot;
  const focusedStep = document.activeElement === els.slotPrev || document.activeElement === els.slotNext
    ? document.activeElement : null;
  renderControls();
  writeSettings();
  // A disabled button can't hold focus; move it to the select instead of losing it.
  if (focusedStep?.disabled) els.slot.focus();
  if (refetch && keepPlan) {
    // A new pick in a live draft: keep showing the current plan until the new one arrives.
    renderTimeline();
    loadPlan();
  } else if (refetch) {
    state.plan = null; // never show cards for the previous settings
    state.error = null;
    renderTimeline();
    renderBoard(); // shows the new slot right away; players come from the last ranking
    restoreBoardFocus(focusedSlot);
    renderPlan();
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
  els.liveForm.addEventListener("submit", connectLive);
  els.liveDisconnect.addEventListener("click", disconnectLive);
  els.liveTeam.addEventListener("change", () => {
    if (!state.live) return;
    state.live.teamId = Number(els.liveTeam.value);
    saveLive();
    const slot = liveTeamSlot();
    if (slot != null) setSlot(slot);
    else update();
  });

  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(renderTimeline, 150);
  });

  update();
  resumeLive();
  PlayerUI.noteSampleData();
}

init();
