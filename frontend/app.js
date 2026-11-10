"use strict";

const SIDES = ["a", "b"];

const state = {
  players: new Map(), // id -> player
  sides: { a: [], b: [] }, // player ids each side sends
  numTeams: 12,
  values: new Map(), // player id -> valuation (value, projection, outlook) from the latest analysis
  search: { a: { position: "", active: -1, results: [], open: false }, b: { position: "", active: -1, results: [], open: false } },
};

const MAX_RESULTS = 8;

const sideEl = (side) => document.querySelector(`.side[data-side="${side}"]`);

// ---------- Data ----------

// fetch() that turns network failures into a plain-language error.
async function request(url, options) {
  try {
    return await fetch(url, options);
  } catch {
    throw new Error("Couldn't reach the server.");
  }
}

async function loadPlayers() {
  const response = await request("/players");
  if (!response.ok) throw new Error(`Couldn't load players (the server returned HTTP ${response.status}).`);
  for (const player of await response.json()) state.players.set(player.id, player);
}

function inTrade(id) {
  return SIDES.some((side) => state.sides[side].includes(id));
}

// ---------- Rendering ----------

function formatPoints(value) {
  return value.toFixed(1);
}

// ESPN's image CDN, resized server-side so thumbnails stay small.
const ESPN_LOGO_CODES = { WAS: "wsh" };

function teamLogoUrl(team) {
  const code = (ESPN_LOGO_CODES[team] || team).toLowerCase();
  return `https://a.espncdn.com/combiner/i?img=/i/teamlogos/nfl/500/${code}.png&w=80&h=80`;
}

function headshotUrl(player) {
  if (player.position === "DST") return teamLogoUrl(player.team);
  if (!player.espn_id) return null;
  return `https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/${player.espn_id}.png&w=96&h=70`;
}

// Headshot (or team logo for defenses), falling back to initials if the image is missing.
function avatar(player, size = "md") {
  const wrap = document.createElement("span");
  wrap.className = `avatar avatar-${size}`;
  wrap.setAttribute("aria-hidden", "true");
  const initials = player.name.split(" ").map((w) => w[0]).join("").slice(0, 2);
  const url = headshotUrl(player);
  if (!url) {
    wrap.textContent = initials;
    return wrap;
  }
  const img = document.createElement("img");
  img.src = url;
  img.alt = "";
  img.loading = "lazy";
  img.addEventListener("error", () => {
    img.remove();
    wrap.textContent = initials;
  });
  wrap.append(img);
  return wrap;
}

// ---------- Player search ----------

function normalize(text) {
  return text.toLowerCase().normalize("NFD").replace(/[^a-z0-9 ]/g, "");
}

function searchPlayers(query, position) {
  const q = normalize(query.trim());
  return [...state.players.values()]
    .filter((p) => !inTrade(p.id))
    .filter((p) => !position || p.position === position)
    .filter((p) => !q || normalize(p.name).includes(q) || p.team.toLowerCase() === q)
    .sort((x, y) => y.ros_projection - x.ros_projection)
    .slice(0, MAX_RESULTS);
}

function searchOption(side, player, index) {
  const option = document.createElement("li");
  option.id = `result-${side}-${index}`;
  option.className = "search-option";
  option.setAttribute("role", "option");
  option.setAttribute("aria-selected", String(index === state.search[side].active));

  const text = document.createElement("span");
  text.className = "search-option-text";
  const name = document.createElement("span");
  name.className = "search-option-name";
  name.textContent = player.name;
  const meta = document.createElement("span");
  meta.className = "search-option-meta";
  meta.textContent = [player.position, player.team, player.injury_status].filter(Boolean).join(", ");
  text.append(name, meta);

  const proj = document.createElement("span");
  proj.className = "search-option-proj";
  proj.textContent = formatPoints(player.ros_projection);

  option.append(avatar(player, "sm"), text, proj);
  // mousedown (not click) so the input doesn't blur and close the list first.
  option.addEventListener("mousedown", (event) => {
    event.preventDefault();
    addPlayer(side, player.id);
  });
  return option;
}

// Open or close a side's results list, then redraw it.
function setSearchOpen(side, open) {
  state.search[side].open = open;
  if (!open) state.search[side].active = -1;
  renderSearch(side);
}

function renderSearch(side) {
  const el = sideEl(side);
  const input = el.querySelector(".player-search");
  const list = el.querySelector(".search-results");
  const search = state.search[side];
  search.results = searchPlayers(input.value, search.position);
  if (search.active >= search.results.length) search.active = search.results.length - 1;

  const show = search.open && document.activeElement === input;
  list.hidden = !show;
  input.setAttribute("aria-expanded", String(show));
  input.removeAttribute("aria-activedescendant");
  if (!show) return;

  if (search.results.length === 0) {
    const empty = document.createElement("li");
    empty.className = "search-empty";
    const query = input.value.trim();
    empty.textContent = query ? `No players match "${query}".` : "No players left to add.";
    list.replaceChildren(empty);
    return;
  }

  list.replaceChildren(...search.results.map((p, i) => searchOption(side, p, i)));
  if (search.active >= 0) {
    input.setAttribute("aria-activedescendant", `result-${side}-${search.active}`);
    list.children[search.active]?.scrollIntoView({ block: "nearest" });
  }
}

function onSearchKey(side, event) {
  const search = state.search[side];
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    if (!search.open) {
      search.open = true;
      renderSearch(side); // refresh results before moving through them
    }
    const total = search.results.length;
    const step = event.key === "ArrowDown" ? 1 : -1;
    search.active = total ? (search.active + step + total) % total : -1;
    if (event.key === "ArrowUp" && search.active === -1) search.active = total - 1;
    renderSearch(side);
  } else if (event.key === "Enter") {
    event.preventDefault();
    const pick = search.results[search.active >= 0 ? search.active : 0];
    if (pick) addPlayer(side, pick.id);
  } else if (event.key === "Escape") {
    // First Escape closes the list; a second one (list already closed) clears the text.
    if (search.open) {
      event.preventDefault();
      setSearchOpen(side, false);
    }
  }
}

function setPositionFilter(side, position) {
  state.search[side].position = position;
  state.search[side].active = -1;
  for (const chipEl of sideEl(side).querySelectorAll(".pos-chip")) {
    chipEl.setAttribute("aria-pressed", String(chipEl.dataset.pos === position));
  }
  sideEl(side).querySelector(".player-search").focus();
  setSearchOpen(side, true);
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
  info.className = "player-info";
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
  const outlook = document.createElement("div");
  outlook.className = "player-outlook";
  info.append(name, meta, outlook);

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

  item.append(avatar(player), info, value, remove);
  return item;
}

function render() {
  for (const side of SIDES) {
    renderSearch(side);
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
    const response = await request("/trades/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => null); // error pages may not be JSON
    if (seq !== analysisSeq) return; // a newer request superseded this one
    if (!response.ok) throw new Error(errorDetail(data) || `the server returned HTTP ${response.status}.`);
    if (!data) throw new Error("the server sent a response the page couldn't read.");
    renderAnalysis(data);
  } catch (error) {
    if (seq !== analysisSeq) return;
    renderError(`Couldn't analyze this trade: ${error.message}`);
  }
}

function errorDetail(data) {
  if (typeof data?.detail === "string") return data.detail;
  if (Array.isArray(data?.detail)) return data.detail.map((d) => d.msg).join("; ");
  return null;
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

// Two stacked bars (one per side), each split into the players that side sends.
// Widths are relative to the bigger side, so the gap between the bars is the value gap.
function valueBars(analysis) {
  const totals = SIDES.map((side) => analysis[`side_${side}`].total_value_given);
  const max = Math.max(...totals, 1);
  const wrap = document.createElement("div");
  wrap.className = "value-bars";
  const heading = document.createElement("h3");
  heading.textContent = "Value sent";
  wrap.append(heading);

  SIDES.forEach((side, i) => {
    const row = document.createElement("div");
    row.className = `value-row value-row-${side}`;
    const label = document.createElement("span");
    label.className = "value-row-label";
    label.textContent = teamName(side);
    const track = document.createElement("div");
    track.className = "value-track";
    const gives = [...analysis[`side_${side}`].gives].sort((x, y) => y.value - x.value);
    for (const v of gives) {
      const seg = document.createElement("span");
      seg.className = "value-seg";
      seg.style.width = `${(v.value / max) * 100}%`;
      seg.title = `${v.player.name}: ${formatPoints(v.value)}`;
      seg.textContent = v.value / max > 0.14 ? v.player.name.split(" ").slice(-1)[0] : "";
      track.append(seg);
    }
    const total = document.createElement("span");
    total.className = "value-row-total";
    total.textContent = formatPoints(totals[i]);
    row.append(label, track, total);
    wrap.append(row);
  });
  return wrap;
}

function renderAnalysis(analysis) {
  state.values = new Map();
  for (const side of SIDES) {
    for (const v of analysis[`side_${side}`].gives) state.values.set(v.player.id, v);
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
      replacement-level player at the same position in a ${state.numTeams}-team league. When
      schedule data is available, projections blend Sleeper and ESPN with recent form adjusted
      for each player's remaining opponents.</p>`;
  result.querySelector(".verdict h2").textContent = headline;
  result.querySelector(".verdict p").textContent = detail;
  result.querySelector(".endzone-a span").textContent = teamName("a");
  result.querySelector(".endzone-b span").textContent = teamName("b");
  result.querySelector(".field").setAttribute("role", "img");
  result.querySelector(".field").setAttribute("aria-label", `${headline}. ${detail}`);
  result.querySelector(".summary").append(sideSummary("a", analysis), sideSummary("b", analysis));
  result.querySelector(".summary").after(valueBars(analysis));

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
    const valuation = state.values.get(item.dataset.id);
    const cell = item.querySelector(".player-value");
    cell.innerHTML = valuation === undefined
      ? `${formatPoints(player.ros_projection)}<small>proj. pts</small>`
      : `${formatPoints(valuation.value)}<small>trade value</small>`;
    renderOutlook(item.querySelector(".player-outlook"), player, valuation);
  }
}

// Schedule and recent-form chips under a rostered player (after analysis).
function scheduleLabel(rank, teams) {
  if (!rank || !teams) return null;
  if (rank <= 8) return "easy";
  if (rank > teams - 8) return "hard";
  return "average";
}

function chip(text, title, modifier) {
  const el = document.createElement("span");
  el.className = `chip${modifier ? ` chip-${modifier}` : ""}`;
  el.textContent = text;
  if (title) el.title = title;
  return el;
}

function renderOutlook(container, player, valuation) {
  const outlook = valuation?.outlook;
  if (!outlook) {
    container.replaceChildren();
    return;
  }
  const chips = [];
  const label = scheduleLabel(outlook.sos_rank, outlook.sos_teams);
  if (label) {
    const games = outlook.games_remaining;
    chips.push(chip(
      `${label[0].toUpperCase()}${label.slice(1)} schedule`,
      `${games} game${games === 1 ? "" : "s"} left: ${outlook.remaining_opponents.join(", ")}. ` +
        `Ranked ${outlook.sos_rank} of ${outlook.sos_teams} for ${player.position}s (1 = easiest).`,
      label,
    ));
  }
  if (outlook.recent_ppg !== null && outlook.recent_games > 0) {
    chips.push(chip(
      `Last ${outlook.recent_games}: ${outlook.recent_ppg.toFixed(1)} pts/game`,
      "Average fantasy points over recent games",
    ));
  }
  if (Math.abs(outlook.adjusted_projection - outlook.provider_projection) >= 0.05) {
    chips.push(chip(
      `Adj. proj. ${formatPoints(outlook.adjusted_projection)}`,
      `Sleeper/ESPN projection ${formatPoints(outlook.provider_projection)}, ` +
        "blended with recent form adjusted for the remaining schedule",
    ));
  }
  container.replaceChildren(...chips);
}

// ---------- Actions ----------

function addPlayer(side, id) {
  if (!id || inTrade(id) || !state.players.has(id)) return;
  state.sides[side].push(id);
  sideEl(side).querySelector(".player-search").value = "";
  state.search[side].open = false;
  state.search[side].active = -1;
  render();
}

function removePlayer(side, id) {
  state.sides[side] = state.sides[side].filter((x) => x !== id);
  // Keep keyboard focus in the side the player was removed from.
  sideEl(side).querySelector(".player-search").focus();
  render();
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
    const input = el.querySelector(".player-search");
    input.addEventListener("input", () => {
      state.search[side].open = true;
      state.search[side].active = input.value.trim() ? 0 : -1;
      renderSearch(side);
    });
    input.addEventListener("click", () => setSearchOpen(side, true));
    input.addEventListener("blur", () => setSearchOpen(side, false));
    input.addEventListener("keydown", (event) => onSearchKey(side, event));
    for (const chipEl of el.querySelectorAll(".pos-chip")) {
      chipEl.addEventListener("click", () => setPositionFilter(side, chipEl.dataset.pos));
    }
    el.querySelector(".team-name").addEventListener("input", onTradeChanged);
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
