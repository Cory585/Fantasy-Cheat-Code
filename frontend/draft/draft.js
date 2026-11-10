"use strict";

// Draft guide: choose league size, your draft slot, and rounds; see your snake-draft picks
// on a board and the players likely to be available at each.

const $ = (id) => document.getElementById(id);
const els = {
  teams: $("league-size"),
  slot: $("draft-slot"),
  rounds: $("rounds"),
  board: $("draft-board"),
  myPicks: $("my-picks"),
  planList: $("plan-list"),
  status: $("data-status"),
};

const TEAM_OPTIONS = [8, 10, 12, 14, 16];
const ROUND_OPTIONS = [13, 14, 15, 16, 17, 18];
let requestId = 0; // ignore responses to settings that have since changed

function ordinal(n) {
  const suffix = { 1: "st", 2: "nd", 3: "rd" }[n % 100 >= 11 && n % 100 <= 13 ? 0 : n % 10] || "th";
  return `${n}${suffix}`;
}

const pickLabel = (round, pickInRound) => `${round}.${String(pickInRound).padStart(2, "0")}`;

// ---------- Settings (kept in the URL so a plan can be shared or bookmarked) ----------

function readSettings() {
  const params = new URLSearchParams(location.search);
  const pick = (name, allowed, fallback) => {
    const value = Number(params.get(name));
    return allowed.includes(value) ? value : fallback;
  };
  const teams = pick("teams", TEAM_OPTIONS, 12);
  const slot = Number(params.get("slot"));
  return {
    teams,
    slot: Number.isInteger(slot) && slot >= 1 && slot <= teams ? slot : 1,
    rounds: pick("rounds", ROUND_OPTIONS, 15),
  };
}

function currentSettings() {
  return { teams: Number(els.teams.value), slot: Number(els.slot.value), rounds: Number(els.rounds.value) };
}

function writeSettings({ teams, slot, rounds }) {
  const params = new URLSearchParams({ teams, slot, rounds });
  history.replaceState(null, "", `${location.pathname}?${params}`);
}

function renderSlotOptions(teams, selected) {
  els.slot.replaceChildren(
    ...Array.from({ length: teams }, (_, i) => new Option(`${ordinal(i + 1)} pick`, i + 1)),
  );
  els.slot.value = String(Math.min(selected, teams));
}

// ---------- Board ----------

function renderBoard({ teams, slot, rounds }) {
  const grid = document.createElement("div");
  grid.className = "draft-grid";
  grid.style.setProperty("--teams", teams);

  grid.append(document.createElement("span"));
  for (let team = 1; team <= teams; team++) {
    const head = document.createElement("span");
    head.className = "team-head" + (team === slot ? " is-mine" : "");
    head.textContent = team === slot ? "You" : `Team ${team}`;
    grid.append(head);
  }

  const mine = [];
  for (let round = 1; round <= rounds; round++) {
    const label = document.createElement("span");
    label.textContent = `Round ${round}`;
    grid.append(label);
    // Columns are teams in slot order; in even rounds the pick order runs right to left.
    for (let team = 1; team <= teams; team++) {
      const pickInRound = round % 2 ? team : teams + 1 - team;
      const cell = document.createElement("div");
      cell.className = "pick";
      const num = document.createElement("span");
      num.className = "pick-num";
      num.textContent = pickLabel(round, pickInRound);
      cell.append(num);
      if (team === slot) {
        cell.classList.add("is-mine");
        cell.id = `board-${round}`;
        mine.push(pickLabel(round, pickInRound));
      }
      grid.append(cell);
    }
  }
  els.board.replaceChildren(grid);
  els.myPicks.textContent = `Your picks: ${mine.join(", ")}`;
}

// Fill your board cells with the top target once the plan arrives.
function fillBoard(plan) {
  for (const { pick, targets } of plan.picks) {
    const cell = $(`board-${pick.round}`);
    if (!cell || !targets.length) continue;
    const name = document.createElement("span");
    name.className = "pick-player";
    name.textContent = targets[0].name;
    name.title = `${targets[0].name} (${targets[0].position}, ${targets[0].team})`;
    cell.append(name);
  }
}

// ---------- Plan ----------

function targetItem(target) {
  const item = document.createElement("li");
  item.className = "target";
  const name = document.createElement("span");
  name.className = "target-name";
  name.textContent = target.name;
  const meta = document.createElement("span");
  meta.className = "player-meta";
  meta.textContent = [
    target.position,
    target.team,
    target.adp != null ? `ADP ${target.adp.toFixed(1)}` : "no ADP",
    `${target.projection.toFixed(1)} proj. pts`,
  ].join(", ");
  item.append(name, meta);
  return item;
}

function renderPlan(plan) {
  els.planList.replaceChildren(
    ...plan.picks.map(({ pick, targets }) => {
      const item = document.createElement("li");
      item.className = "plan-pick";
      const head = document.createElement("div");
      head.className = "plan-pick-head";
      head.innerHTML = `<strong>${pick.label}</strong><span>Round ${pick.round}, ${ordinal(pick.overall)} overall</span>`;
      const list = document.createElement("ol");
      list.className = "target-list";
      if (targets.length) {
        list.append(...targets.map(targetItem));
      } else {
        const empty = document.createElement("li");
        empty.className = "player-meta";
        empty.textContent = "No ranked players left this late.";
        list.append(empty);
      }
      item.append(head, list);
      return item;
    }),
  );
  els.status.textContent =
    plan.adp_source === "espn"
      ? "Ranked by ESPN average draft position."
      : "No ADP data yet, so players are ranked by projection. Run python -m app.data.collect to add ADP.";
}

function renderError(message) {
  const item = document.createElement("li");
  item.className = "result-error";
  item.textContent = message;
  els.planList.replaceChildren(item);
}

async function loadPlan(settings) {
  const id = ++requestId;
  const params = new URLSearchParams({ ...settings, targets: 5 });
  let response;
  try {
    response = await fetch(`/draft/plan?${params}`);
  } catch {
    if (id === requestId) renderError("Couldn't reach the server.");
    return;
  }
  if (id !== requestId) return;
  if (!response.ok) {
    renderError(`Couldn't load the draft plan (the server returned HTTP ${response.status}).`);
    return;
  }
  const plan = await response.json();
  if (id !== requestId) return;
  renderPlan(plan);
  fillBoard(plan);
}

// ---------- Wiring ----------

function update() {
  const settings = currentSettings();
  writeSettings(settings);
  renderBoard(settings);
  loadPlan(settings);
}

function init() {
  const settings = readSettings();
  els.teams.value = String(settings.teams);
  els.rounds.value = String(settings.rounds);
  renderSlotOptions(settings.teams, settings.slot);

  els.teams.addEventListener("change", () => {
    renderSlotOptions(Number(els.teams.value), Number(els.slot.value));
    update();
  });
  els.slot.addEventListener("change", update);
  els.rounds.addEventListener("change", update);
  update();
}

init();
