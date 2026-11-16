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
const MAX_PER_SIDE = 40; // matches the API's limit

// Starting lineup slots per team (FLEX = RB/WR/TE). Matches the backend default.
const DEFAULT_SLOTS = { QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1, K: 1, DST: 1 };
const SLOT_LIMIT = 4;
state.slots = { ...DEFAULT_SLOTS };

function slotsSummary() {
  return Object.entries(state.slots)
    .filter(([, n]) => n > 0)
    .map(([slot, n]) => `${n} ${slot}`)
    .join(", ");
}

function renderLineupSettings() {
  const grid = document.getElementById("lineup-slots");
  grid.replaceChildren(...Object.keys(DEFAULT_SLOTS).map((slot) => {
    const row = document.createElement("div");
    row.className = "stepper";
    const label = document.createElement("span");
    label.className = "stepper-label";
    label.textContent = slot;
    const minus = document.createElement("button");
    minus.type = "button";
    minus.className = "stepper-btn";
    minus.textContent = "−";
    minus.setAttribute("aria-label", `Fewer ${slot} starters`);
    minus.disabled = state.slots[slot] <= 0;
    const count = document.createElement("output");
    count.className = "stepper-count";
    count.textContent = String(state.slots[slot]);
    count.setAttribute("aria-label", `${slot} starters`);
    const plus = document.createElement("button");
    plus.type = "button";
    plus.className = "stepper-btn";
    plus.textContent = "+";
    plus.setAttribute("aria-label", `More ${slot} starters`);
    plus.disabled = state.slots[slot] >= SLOT_LIMIT;
    minus.addEventListener("click", () => changeSlot(slot, -1, minus));
    plus.addEventListener("click", () => changeSlot(slot, 1, plus));
    row.append(label, minus, count, plus);
    return row;
  }));
  const summary = document.querySelector(".lineup-settings summary");
  const custom = Object.keys(DEFAULT_SLOTS).some((k) => state.slots[k] !== DEFAULT_SLOTS[k]);
  summary.textContent = custom ? "Starting lineup (custom)" : "Starting lineup";
}

function changeSlot(slot, delta, button) {
  state.slots[slot] = Math.min(SLOT_LIMIT, Math.max(0, state.slots[slot] + delta));
  renderLineupSettings();
  // Re-rendering replaced the button; keep focus on its replacement for keyboard users.
  const label = button.getAttribute("aria-label");
  document.querySelector(`#lineup-slots [aria-label="${label}"]`)?.focus();
  onTradeChanged();
}

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
  state.players = await PlayerUI.loadPlayers();
}

// Where the data comes from and how fresh it is. Not essential, so failures are quiet.
async function loadMeta() {
  try {
    const response = await request("/meta");
    if (!response.ok) return;
    state.meta = await response.json();
  } catch {
    return;
  }
  const el = document.getElementById("data-status");
  const meta = state.meta;
  if (meta.source !== "synced") {
    el.textContent = "Showing sample players, not real projections.";
    return;
  }
  const synced = new Date(meta.synced_at);
  const when = Number.isNaN(synced.getTime()) ? null : synced.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
  const [start, end] = Array.isArray(meta.weeks) ? meta.weeks : [];
  const parts = [when ? `Projections updated ${when}` : "Projections loaded"];
  if (start && end) parts.push(`weeks ${start}–${end}`);
  if (meta.scoring) parts.push(meta.scoring.replace("_", "-").toUpperCase());
  el.textContent = `${parts.join(", ")}.${meta.outlooks ? " Includes schedule and recent form." : ""}`;
  // Redraw rosters now that past bye weeks are known (without re-running the analysis).
  for (const side of SIDES) renderRoster(side);
  refreshValues();
}

function byeLabel(player) {
  const firstWeek = state.meta?.weeks?.[0];
  if (!player.bye_week || (firstWeek && player.bye_week < firstWeek)) return null; // already past
  return `bye week ${player.bye_week}`;
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

  if (sideIsFull(side)) {
    const full = document.createElement("li");
    full.className = "search-empty";
    full.textContent = `A side can include up to ${MAX_PER_SIDE} players. Remove one to add another.`;
    list.replaceChildren(full);
    return;
  }

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
  const link = document.createElement("a");
  link.className = "profile-link";
  link.href = `/player/?id=${encodeURIComponent(player.id)}`;
  link.textContent = player.name;
  name.append(link);
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
    byeLabel(player),
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

// ---------- Shareable links ----------
// The trade lives in the URL hash, e.g. #a=9221,7564&b=6794&an=Aces&teams=10&slots=QB1.RB2...

function writeUrl() {
  const params = new URLSearchParams();
  for (const side of SIDES) {
    if (state.sides[side].length) params.set(side, state.sides[side].join(","));
    const name = sideEl(side).querySelector(".team-name").value.trim();
    if (name && name !== `Team ${side.toUpperCase()}`) params.set(`${side}n`, name);
  }
  if (state.numTeams !== 12) params.set("teams", String(state.numTeams));
  const custom = Object.keys(DEFAULT_SLOTS).some((k) => state.slots[k] !== DEFAULT_SLOTS[k]);
  if (custom) params.set("slots", Object.entries(state.slots).map(([k, n]) => `${k}${n}`).join("."));
  const hash = params.toString();
  history.replaceState(null, "", hash ? `#${hash}` : location.pathname + location.search);
}

// Anything a link leaves out goes back to its default, so opening a new link in a tab that
// already has a trade doesn't mix the two.
function readUrl() {
  const params = new URLSearchParams(location.hash.slice(1));
  const seen = new Set();
  for (const side of SIDES) {
    // Ignore unknown ids (e.g. a player dropped from the data) and duplicates.
    state.sides[side] = (params.get(side) || "").split(",").filter((id) => {
      const ok = id && state.players.has(id) && !seen.has(id);
      seen.add(id);
      return ok;
    }).slice(0, MAX_PER_SIDE);
    const name = params.get(`${side}n`);
    sideEl(side).querySelector(".team-name").value = name ? name.slice(0, 30) : `Team ${side.toUpperCase()}`;
  }
  const teams = Number(params.get("teams"));
  const select = document.getElementById("league-size");
  state.numTeams = [...select.options].some((o) => Number(o.value) === teams) ? teams : 12;
  select.value = String(state.numTeams);
  state.slots = { ...DEFAULT_SLOTS };
  for (const part of (params.get("slots") || "").split(".")) {
    const match = /^([A-Z]+)(\d)$/.exec(part);
    if (match && match[1] in DEFAULT_SLOTS) state.slots[match[1]] = Math.min(SLOT_LIMIT, Number(match[2]));
  }
}

async function copyShareLink(button) {
  writeUrl();
  const status = document.getElementById("share-status");
  try {
    await navigator.clipboard.writeText(location.href);
    status.textContent = "Link copied";
  } catch {
    // Clipboard can be blocked (e.g. insecure context); fall back to selecting the URL.
    window.prompt("Copy this link:", location.href);
    status.textContent = "";
  }
  clearTimeout(button._statusTimer);
  button._statusTimer = setTimeout(() => { status.textContent = ""; }, 2500);
}

function onTradeChanged() {
  writeUrl();
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
    league: { num_teams: state.numTeams, roster_slots: state.slots },
  };
  try {
    const data = await PlayerUI.getJson("/trades/analyze", "Couldn't analyze this trade", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (seq !== analysisSeq) return; // a newer request superseded this one
    renderAnalysis(data);
  } catch (error) {
    if (seq !== analysisSeq) return;
    renderError(error, analyze);
  }
}

function renderEmptyResult() {
  const result = document.getElementById("result");
  const message = document.createElement("p");
  message.className = "result-empty";
  message.textContent = "Add at least one player to each side to see who wins the trade.";
  result.replaceChildren(message);
  refreshValues();
}

function renderError(error, retry) {
  document.getElementById("result").replaceChildren(PlayerUI.errorBlock(error, retry));
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

// Why each side comes out ahead or behind: the API's reasons, marked plus, minus, or neutral.
const REASON_ICONS = { plus: "+", minus: "−", neutral: "•" };
const REASON_WORDS = { plus: "Helps", minus: "Hurts", neutral: "Neutral" };

function whyHeading(side, analysis) {
  if (analysis.verdict === "fair") return `${teamName(side)}: an even deal`;
  const wins = analysis.verdict === `favors_${side}`;
  return `Why ${teamName(side)} ${wins ? "wins" : "loses"}`;
}

function reasonList(reasons, className) {
  const list = document.createElement("ul");
  list.className = className;
  for (const reason of reasons) {
    const item = document.createElement("li");
    item.className = `tr-reason is-${reason.effect}`;
    const icon = document.createElement("span");
    icon.className = "tr-reason-icon";
    icon.textContent = REASON_ICONS[reason.effect] || "•";
    icon.setAttribute("aria-label", REASON_WORDS[reason.effect] || "");
    const text = document.createElement("span");
    text.textContent = reason.text;
    item.append(icon, text);
    list.append(item);
  }
  return list;
}

function whyPanel(analysis) {
  const wrap = document.createElement("section");
  wrap.className = "tr-why";
  wrap.setAttribute("aria-label", "Why each side wins or loses");
  for (const side of SIDES) {
    const data = analysis[`side_${side}`];
    if (!data.reasons?.length) continue;
    const column = document.createElement("div");
    const outcome = analysis.verdict === "fair" ? "even" : analysis.verdict === `favors_${side}` ? "wins" : "loses";
    column.className = `tr-why-side tr-why-${side} is-${outcome}`;
    const heading = document.createElement("h3");
    heading.textContent = whyHeading(side, analysis);
    column.append(heading, reasonList(data.reasons, "tr-reasons"));
    wrap.append(column);
  }
  return wrap.childElementCount ? wrap : null;
}

// One card per player in the trade: rank and tier, value, and what makes them worth more or less.
function traitList(items, effect, label) {
  if (!items?.length) return null;
  const list = reasonList(items.map((text) => ({ text, effect })), `tr-traits tr-traits-${effect}`);
  list.setAttribute("aria-label", label);
  return list;
}

function playerCard(valuation, sideTotal) {
  const v = valuation;
  const card = document.createElement("article");
  card.className = "tr-player";
  const head = document.createElement("div");
  head.className = "tr-player-head";
  const title = document.createElement("div");
  title.className = "tr-player-title";
  const name = document.createElement("a");
  name.className = "tr-player-name";
  name.href = `/player/?id=${encodeURIComponent(v.player.id)}`;
  name.textContent = v.player.name;
  const meta = document.createElement("span");
  meta.className = "tr-player-meta";
  meta.textContent = `${v.player.position} · ${v.player.team}`;
  title.append(name, meta);
  head.append(avatar(v.player, "sm"), title);
  if (v.tier) {
    const tier = document.createElement("span");
    tier.className = `tr-tier${v.starter ? " is-starter" : ""}`;
    tier.textContent = v.tier;
    tier.title = v.position_rank
      ? `${ordinal(v.position_rank)} at ${v.player.position}${v.starter ? ", a starter" : ", below the starting line"} in this league`
      : "";
    head.append(tier);
  }

  const stats = document.createElement("dl");
  stats.className = "tr-player-stats";
  const share = sideTotal > 0 ? Math.round((v.value / sideTotal) * 100) : 0;
  for (const [label, value] of [
    ["Trade value", formatPoints(v.value)],
    ["ROS proj.", formatPoints(v.projection)],
    ["Rank", v.position_rank ? `${ordinal(v.position_rank)} ${v.player.position}` : "—"],
    ["Share of side", `${share}%`],
  ]) {
    const cell = document.createElement("div");
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    cell.append(dt, dd);
    stats.append(cell);
  }
  card.append(head, stats);
  const strengths = traitList(v.strengths, "plus", `${v.player.name}: strengths`);
  const concerns = traitList(v.concerns, "minus", `${v.player.name}: concerns`);
  if (strengths) card.append(strengths);
  if (concerns) card.append(concerns);
  return card;
}

function ordinal(n) {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function playerBreakdown(analysis) {
  const wrap = document.createElement("section");
  wrap.className = "tr-breakdown";
  const heading = document.createElement("h3");
  heading.textContent = "Player breakdown";
  wrap.append(heading);
  for (const side of SIDES) {
    const data = analysis[`side_${side}`];
    const group = document.createElement("div");
    group.className = `tr-breakdown-side tr-breakdown-${side}`;
    const label = document.createElement("h4");
    label.textContent = `${teamName(side)} sends`;
    const grid = document.createElement("div");
    grid.className = "tr-player-grid";
    for (const v of [...data.gives].sort((x, y) => y.value - x.value)) {
      grid.append(playerCard(v, data.total_value_given));
    }
    group.append(label, grid);
    wrap.append(group);
  }
  return wrap;
}

// Players the winning side could add so the trade comes out even.
function balancePanel(analysis) {
  const balance = analysis.balance;
  if (!balance || balance.players.length === 0) return null;
  const side = balance.side;
  const wrap = document.createElement("section");
  wrap.className = `balance balance-${side}`;
  const heading = document.createElement("h3");
  heading.textContent = "Even it out";
  const intro = document.createElement("p");
  intro.textContent =
    `${teamName(side)} could add one of these players, each worth close to the ` +
    `${formatPoints(balance.value_gap)}-point gap.`;
  const list = document.createElement("ul");
  list.className = "balance-list";
  for (const v of balance.players) {
    const item = document.createElement("li");
    item.className = "balance-item";
    const text = document.createElement("span");
    text.className = "balance-text";
    const name = document.createElement("span");
    name.className = "balance-name";
    name.textContent = v.player.name;
    const meta = document.createElement("span");
    meta.className = "balance-meta";
    meta.textContent = `${v.player.position}, ${v.player.team}, ${formatPoints(v.value)} value`;
    text.append(name, meta);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-quiet btn-small";
    button.textContent = `Add to ${teamName(side)}`;
    button.disabled = sideIsFull(side);
    button.addEventListener("click", () => addPlayer(side, v.player.id));
    item.append(avatar(v.player, "sm"), text, button);
    list.append(item);
  }
  wrap.append(heading, intro, list);
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
      replacement-level player at the same position in a ${state.numTeams}-team league starting
      ${slotsSummary()}. When
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
  const why = whyPanel(analysis);
  if (why) result.querySelector(".summary").after(why);
  const balance = balancePanel(analysis);
  if (balance) result.querySelector(".value-bars").after(balance);
  (balance || result.querySelector(".value-bars")).after(playerBreakdown(analysis));

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

function sideIsFull(side) {
  return state.sides[side].length >= MAX_PER_SIDE;
}

function addPlayer(side, id) {
  if (!id || inTrade(id) || !state.players.has(id) || sideIsFull(side)) return;
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
  renderLineupSettings();
  // Close the lineup popover on an outside click or Escape.
  const lineup = document.querySelector(".lineup-settings");
  document.addEventListener("click", (event) => {
    // composedPath() is captured when the click happens, so it still includes the popover
    // even if the clicked stepper button was re-rendered (and detached) by then.
    if (lineup.open && !event.composedPath().includes(lineup)) lineup.open = false;
  });
  lineup.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && lineup.open) {
      lineup.open = false;
      lineup.querySelector("summary").focus();
    }
  });
  document.getElementById("lineup-reset").addEventListener("click", () => {
    state.slots = { ...DEFAULT_SLOTS };
    renderLineupSettings();
    onTradeChanged();
  });
  document.getElementById("share").addEventListener("click", (e) => copyShareLink(e.currentTarget));
  loadData();
}

// Players first (nothing works without them), then the trade in the URL.
async function loadData() {
  try {
    await loadPlayers();
  } catch (error) {
    renderError(error, () => {
      renderEmptyResult();
      loadData();
    });
    return;
  }
  readUrl();
  renderLineupSettings();
  render();
  loadMeta();
  // replaceState (our own URL updates) doesn't fire this; pasting or clicking a link does.
  window.addEventListener("hashchange", () => {
    readUrl();
    renderLineupSettings();
    render();
  });
}

init();
