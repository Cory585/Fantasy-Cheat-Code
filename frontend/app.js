"use strict";

const SIDES = ["a", "b"];

const state = {
  players: new Map(), // id -> player
  sides: { a: [], b: [] }, // player ids each side sends
  numTeams: 12,
  values: new Map(), // player id -> trade value from the latest analysis
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
  meta.textContent = [
    player.position,
    player.team,
    `${formatPoints(player.ros_projection)} proj. pts`,
    player.bye_week ? `bye week ${player.bye_week}` : null,
  ]
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
  refreshValues();
  onTradeChanged();
}

// ---------- Analysis ----------

let analysisTimer = null;
let analysisSeq = 0;

function teamName(side) {
  return sideEl(side).querySelector(".team-name").value.trim() || `Team ${side.toUpperCase()}`;
}

function onTradeChanged() {
  clearTimeout(analysisTimer);
  analysisSeq++; // discard any in-flight response for the previous trade
  if (SIDES.some((side) => state.sides[side].length === 0)) {
    state.values = new Map();
    renderEmptyResult();
    return;
  }
  analysisTimer = setTimeout(analyze, 150);
}

async function analyze() {
  const seq = ++analysisSeq;
  const body = {
    side_a: { team_name: teamName("a"), player_ids: state.sides.a },
    side_b: { team_name: teamName("b"), player_ids: state.sides.b },
    league: { num_teams: state.numTeams },
  };
  try {
    const response = await fetch("/trades/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (seq !== analysisSeq) return; // a newer request superseded this one
    if (!response.ok) throw new Error(errorDetail(data));
    renderAnalysis(data);
  } catch (error) {
    if (seq !== analysisSeq) return;
    renderError(`Couldn't analyze this trade: ${error.message}`);
  }
}

function errorDetail(data) {
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg).join("; ");
  return "unexpected response from the server.";
}

function renderEmptyResult() {
  const result = document.getElementById("result");
  const message = document.createElement("p");
  message.className = "result-empty";
  message.textContent = "Add at least one player to each side to see who wins the trade.";
  result.replaceChildren(message);
  refreshValues();
}

function renderError(text) {
  const result = document.getElementById("result");
  const message = document.createElement("p");
  message.className = "result-error";
  message.textContent = text;
  result.replaceChildren(message);
}

function verdictText(analysis) {
  const pct = analysis.value_difference_pct.toFixed(1);
  if (analysis.verdict === "fair") return { headline: "Even trade", detail: `The two sides are ${pct}% apart in value.` };
  const winner = analysis.verdict === "favors_a" ? teamName("a") : teamName("b");
  return { headline: `${winner} wins this trade`, detail: `The value gap is ${pct}% in ${winner}'s favor.` };
}

// Ball position in yards from Team A's goal line (0) to Team B's (100).
// It starts at midfield and moves half a yard toward the winner per percentage point.
function ballYard(analysis) {
  const offset = Math.min(analysis.value_difference_pct, 100) / 2;
  if (analysis.verdict === "favors_a" || (analysis.verdict === "fair" && analysis.side_a.net_value > 0)) {
    return 50 - offset;
  }
  if (analysis.verdict === "favors_b" || (analysis.verdict === "fair" && analysis.side_b.net_value > 0)) {
    return 50 + offset;
  }
  return 50;
}

function fieldMarkup(analysis) {
  const yard = ballYard(analysis);
  const lines = [10, 20, 30, 40, 50, 60, 70, 80, 90]
    .map((y) => `<span class="yard-line" style="left:${y}%"><span>${y <= 50 ? y : 100 - y}</span></span>`)
    .join("");
  const fairHalf = 5; // 10% fair threshold = 5 yards either side of midfield
  return `
    <div class="field">
      <div class="endzone endzone-a"><span></span></div>
      <div class="field-play">
        <div class="fair-zone" style="left:${50 - fairHalf}%;right:${50 - fairHalf}%"></div>
        ${lines}
        <div class="ball" style="left:${yard}%"></div>
      </div>
      <div class="endzone endzone-b"><span></span></div>
    </div>`;
}

function sideSummary(side, analysis) {
  const data = analysis[`side_${side}`];
  const net = data.net_value;
  const sign = net > 0 ? "+" : "";
  const wrap = document.createElement("div");
  wrap.className = `summary-side summary-${side}`;
  wrap.innerHTML = `
    <h3></h3>
    <dl>
      <div><dt>Receives</dt><dd>${formatPoints(data.total_value_received)}</dd></div>
      <div><dt>Sends</dt><dd>${formatPoints(data.total_value_given)}</dd></div>
      <div class="net ${net > 0 ? "net-up" : net < 0 ? "net-down" : ""}"><dt>Net</dt><dd>${sign}${formatPoints(net)}</dd></div>
    </dl>`;
  wrap.querySelector("h3").textContent = teamName(side);
  return wrap;
}

function renderAnalysis(analysis) {
  state.values = new Map();
  for (const side of SIDES) {
    for (const v of analysis[`side_${side}`].gives) state.values.set(v.player.id, v.value);
  }
  refreshValues();

  const { headline, detail } = verdictText(analysis);
  const result = document.getElementById("result");
  result.innerHTML = `
    <div class="verdict verdict-${analysis.verdict}">
      <h2></h2>
      <p></p>
    </div>
    ${fieldMarkup(analysis)}
    <div class="summary"></div>
    <p class="value-note">Trade value is a player's projected rest-of-season points above a
      replacement-level player at the same position in a ${state.numTeams}-team league.</p>`;
  result.querySelector(".verdict h2").textContent = headline;
  result.querySelector(".verdict p").textContent = detail;
  result.querySelector(".endzone-a span").textContent = teamName("a");
  result.querySelector(".endzone-b span").textContent = teamName("b");
  result.querySelector(".field").setAttribute("role", "img");
  result.querySelector(".field").setAttribute("aria-label", `${headline}. ${detail}`);
  result.querySelector(".summary").append(sideSummary("a", analysis), sideSummary("b", analysis));

  if (analysis.warnings.length) {
    const list = document.createElement("ul");
    list.className = "warnings";
    for (const warning of analysis.warnings) {
      const item = document.createElement("li");
      item.textContent = warning;
      list.append(item);
    }
    result.append(list);
  }
}

// Show trade value next to each rostered player once the trade has been analyzed.
function refreshValues() {
  for (const item of document.querySelectorAll(".roster-item")) {
    const player = state.players.get(item.dataset.id);
    const value = state.values.get(item.dataset.id);
    const cell = item.querySelector(".player-value");
    cell.innerHTML = value === undefined
      ? `${formatPoints(player.ros_projection)}<small>proj. pts</small>`
      : `${formatPoints(value)}<small>trade value</small>`;
  }
}

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

// ---------- Theme ----------

const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

function currentTheme() {
  return document.documentElement.dataset.theme || (darkQuery.matches ? "dark" : "light");
}

function renderThemeToggle() {
  const button = document.getElementById("theme-toggle");
  const dark = currentTheme() === "dark";
  // The label names the action ("Light mode" while dark is on), so don't also expose
  // aria-pressed: "Light mode, pressed" would announce the wrong theme as active.
  button.classList.toggle("is-dark", dark);
  button.querySelector(".theme-label").textContent = dark ? "Light mode" : "Dark mode";
  button.title = dark ? "Switch to light mode" : "Switch to dark mode";
}

function toggleTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem("theme", next);
  } catch (e) {
    // Storage can be unavailable (private mode); the switch still works for this visit.
  }
  renderThemeToggle();
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
  document.getElementById("theme-toggle").addEventListener("click", toggleTheme);
  darkQuery.addEventListener("change", renderThemeToggle);
  document.getElementById("swap").addEventListener("click", swapSides);
  document.getElementById("clear").addEventListener("click", clearTrade);
  document.getElementById("league-size").addEventListener("change", (e) => {
    state.numTeams = Number(e.target.value);
    onTradeChanged();
  });
}

async function init() {
  bindEvents();
  renderThemeToggle();
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
