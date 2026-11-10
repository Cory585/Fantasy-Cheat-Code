"use strict";

const SIDES = ["a", "b"];

const state = {
  players: new Map(), // id -> player
  sides: { a: [], b: [] }, // player ids each side sends
  numTeams: 12,
};

const sideEl = (side) => document.querySelector(`.side[data-side="${side}"]`);

// ---------- Data ----------

async function loadPlayers() {
  const response = await fetch("/players");
  if (!response.ok) throw new Error(`Couldn't load players (HTTP ${response.status}).`);
  for (const player of await response.json()) state.players.set(player.id, player);
}

function inTrade(id) {
  return SIDES.some((side) => state.sides[side].includes(id));
}

// ---------- Rendering ----------

function formatPoints(value) {
  return value.toFixed(1);
}

function playerLabel(p) {
  return `${p.name} (${p.position}, ${p.team}) ${formatPoints(p.ros_projection)}`;
}

function renderPicker(side) {
  const el = sideEl(side);
  const position = el.querySelector(".position-filter").value;
  const select = el.querySelector(".player-select");
  const previous = select.value;

  const options = [...state.players.values()]
    .filter((p) => !position || p.position === position)
    .sort((x, y) => y.ros_projection - x.ros_projection);

  select.replaceChildren(
    new Option("Choose a player", ""),
    ...options.map((p) => {
      const option = new Option(playerLabel(p), p.id);
      option.disabled = inTrade(p.id);
      return option;
    }),
  );
  if (previous && !inTrade(previous)) select.value = previous;
  el.querySelector(".btn-add").disabled = !select.value;
}

function renderRoster(side) {
  const el = sideEl(side);
  const list = el.querySelector(".roster");
  const ids = state.sides[side];

  if (ids.length === 0) {
    const empty = document.createElement("li");
    empty.className = "roster-empty";
    empty.textContent = "No players added yet.";
    list.replaceChildren(empty);
  } else {
    list.replaceChildren(...ids.map((id) => rosterItem(side, state.players.get(id))));
  }

  const total = ids.reduce((sum, id) => sum + state.players.get(id).ros_projection, 0);
  el.querySelector(".side-total").innerHTML = ids.length
    ? `Projected rest of season <strong>${formatPoints(total)}</strong> pts`
    : "";
}

function rosterItem(side, player) {
  const item = document.createElement("li");
  item.className = "roster-item";
  item.dataset.id = player.id;

  const info = document.createElement("div");
  const name = document.createElement("div");
  name.className = "player-name";
  name.textContent = player.name;
  if (player.injury_status) {
    const tag = document.createElement("span");
    tag.className = "injury";
    tag.textContent = player.injury_status;
    tag.title = "Injury status";
    name.append(tag);
  }
  const meta = document.createElement("div");
  meta.className = "player-meta";
  meta.textContent = [player.position, player.team, player.bye_week ? `bye week ${player.bye_week}` : null]
    .filter(Boolean)
    .join(", ");
  info.append(name, meta);

  const value = document.createElement("div");
  value.className = "player-value";
  value.innerHTML = `${formatPoints(player.ros_projection)}<small>proj. pts</small>`;

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "btn-remove";
  remove.textContent = "×";
  remove.setAttribute("aria-label", `Remove ${player.name}`);
  remove.title = "Remove from trade";
  remove.addEventListener("click", () => removePlayer(side, player.id));

  item.append(info, value, remove);
  return item;
}

function render() {
  for (const side of SIDES) {
    renderPicker(side);
    renderRoster(side);
  }
  onTradeChanged();
}

// Hook for the analysis step (added in a later commit).
function onTradeChanged() {}

// ---------- Actions ----------

function addPlayer(side) {
  const select = sideEl(side).querySelector(".player-select");
  const id = select.value;
  if (!id || inTrade(id)) return;
  state.sides[side].push(id);
  select.value = "";
  render();
  select.focus();
}

function removePlayer(side, id) {
  state.sides[side] = state.sides[side].filter((x) => x !== id);
  render();
  // Keep keyboard focus in the side the player was removed from.
  sideEl(side).querySelector(".player-select").focus();
}

function swapSides() {
  [state.sides.a, state.sides.b] = [state.sides.b, state.sides.a];
  const [nameA, nameB] = SIDES.map((s) => sideEl(s).querySelector(".team-name"));
  [nameA.value, nameB.value] = [nameB.value, nameA.value];
  render();
}

function clearTrade() {
  state.sides = { a: [], b: [] };
  render();
}

// ---------- Wiring ----------

function bindEvents() {
  for (const side of SIDES) {
    const el = sideEl(side);
    el.querySelector(".position-filter").addEventListener("change", () => renderPicker(side));
    el.querySelector(".player-select").addEventListener("change", (e) => {
      el.querySelector(".btn-add").disabled = !e.target.value;
    });
    el.querySelector(".btn-add").addEventListener("click", () => addPlayer(side));
    el.querySelector(".team-name").addEventListener("change", onTradeChanged);
  }
  document.getElementById("swap").addEventListener("click", swapSides);
  document.getElementById("clear").addEventListener("click", clearTrade);
  document.getElementById("league-size").addEventListener("change", (e) => {
    state.numTeams = Number(e.target.value);
    onTradeChanged();
  });
}

async function init() {
  bindEvents();
  try {
    await loadPlayers();
    render();
  } catch (error) {
    const result = document.getElementById("result");
    result.innerHTML = "";
    const message = document.createElement("p");
    message.className = "result-error";
    message.textContent = `${error.message} Check that the backend is running, then reload the page.`;
    result.append(message);
  }
}

init();
