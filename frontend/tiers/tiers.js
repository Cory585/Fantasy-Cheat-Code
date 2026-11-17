"use strict";

// Tier lists: pick a position (or flex), this week or rest of season, and a league size;
// players come back grouped into tiers from /tiers. Settings live in the URL so a list can
// be shared, e.g. /tiers/?pos=RB&h=week&teams=10.

(() => {
  const { el, plural, profileUrl, getJson, loadMeta, errorBlock, noteDataStatus } = window.PlayerUI;
  const $ = (id) => document.getElementById(id);
  const els = {
    position: $("tier-position"),
    horizon: $("tier-horizon"),
    teams: $("tier-teams"),
    list: $("tier-list"),
    note: $("tier-note"),
    status: $("tier-status"),
  };

  const POSITIONS = ["QB", "RB", "WR", "TE", "FLEX", "K", "DST"];
  const HORIZONS = ["week", "ros"];
  const TEAM_OPTIONS = [8, 10, 12, 14, 16];
  const POSITION_NAMES = { QB: "QBs", RB: "RBs", WR: "WRs", TE: "TEs", FLEX: "flex players", K: "kickers", DST: "defenses" };

  // `horizonChosen`: whether the horizon came from the person (URL or a click) rather than
  // the in-season default, which falls back to rest of season when there's no weekly data.
  const state = { position: "RB", horizon: null, horizonChosen: false, teams: 12, currentWeek: null, fellBack: false };
  let requestId = 0;

  const fmt = (n) => (Math.round(n * 10) / 10).toLocaleString(undefined, { maximumFractionDigits: 1 });

  // ---------- URL ----------

  function readUrl() {
    const params = new URLSearchParams(location.search);
    const position = (params.get("pos") || "").toUpperCase();
    state.position = POSITIONS.includes(position) ? position : "RB";
    const horizon = params.get("h");
    state.horizonChosen = HORIZONS.includes(horizon);
    state.horizon = state.horizonChosen ? horizon : null;
    const teams = Number(params.get("teams"));
    state.teams = TEAM_OPTIONS.includes(teams) ? teams : 12;
  }

  function writeUrl() {
    const params = new URLSearchParams({ pos: state.position });
    if (state.horizonChosen) params.set("h", state.horizon);
    if (state.teams !== 12) params.set("teams", state.teams);
    history.replaceState(null, "", `${location.pathname}?${params}`);
  }

  // ---------- Controls ----------

  function renderControls() {
    for (const button of els.position.querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.position === state.position));
    }
    for (const button of els.horizon.querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.horizon === state.horizon));
    }
    els.teams.value = String(state.teams);
  }

  // ---------- Tiers ----------

  function playerCard(player, flex) {
    const card = el("li", `tier-player pos-${player.position}`);
    const head = el("div", "tier-player-head");
    head.append(el("span", "tier-rank", String(player.rank)));
    const name = el("a", "tier-player-name profile-link", player.name);
    name.href = profileUrl(player.player_id);
    head.append(name);
    if (player.injury_status) {
      const injury = el("span", "tier-injury", player.injury_status);
      injury.title = `Injury status: ${player.injury_status}`;
      head.append(injury);
    }
    card.append(head);

    const meta = [flex ? player.position : null, player.team, player.bye_week ? `bye ${player.bye_week}` : null]
      .filter(Boolean).join(" · ");
    const line = el("p", "tier-player-meta player-meta");
    line.append(el("span", null, meta));
    const points = el("span", "tier-points",
      flex ? `+${fmt(player.value)} (${fmt(player.points)} pts)` : `${fmt(player.points)} pts`);
    if (flex) points.title = `${fmt(player.value)} points over a replacement-level ${player.position}`;
    line.append(points);
    card.append(line);

    const actions = el("p", "tier-actions");
    const trade = el("a", null, "Trade");
    trade.href = `/#a=${encodeURIComponent(player.player_id)}`;
    trade.setAttribute("aria-label", `Trade for ${player.name}`);
    const startSit = el("a", null, "Start/sit");
    startSit.href = `/start-sit/?mode=compare&p=${encodeURIComponent(player.player_id)}`;
    startSit.setAttribute("aria-label", `Start/sit with ${player.name}`);
    actions.append(trade, startSit);
    card.append(actions);
    return card;
  }

  function renderTiers(result) {
    const flex = result.metric === "value_over_replacement";
    const unit = flex ? "over replacement" : "pts";
    els.list.replaceChildren(...result.tiers.map((tier) => {
      const row = el("li", "tier-row");
      const label = el("div", "tier-label");
      label.append(el("h2", "tier-name", `Tier ${tier.tier}`));
      label.append(el("span", "tier-range player-meta",
        tier.high === tier.low ? `${fmt(tier.high)} ${unit}` : `${fmt(tier.high)}–${fmt(tier.low)} ${unit}`));
      const players = el("ol", "tier-players");
      players.setAttribute("aria-label", `Tier ${tier.tier}, ${plural(tier.players.length, "player")}`);
      players.append(...tier.players.map((p) => playerCard(p, flex)));
      row.append(label, players);
      if (tier.drop_to_next != null) {
        row.append(el("p", "tier-drop", `${fmt(tier.drop_to_next)} ${flex ? "points" : "pts"} drop to tier ${tier.tier + 1}`));
      }
      return row;
    }));
    const count = result.tiers.reduce((n, t) => n + t.players.length, 0);
    const when = result.horizon === "week" ? `week ${result.week}` : "the rest of the season";
    els.status.textContent = `${plural(count, POSITION_NAMES[result.position].replace(/s$/, ""))} in ` +
      `${plural(result.tiers.length, "tier")}, for ${when}.`;
    els.note.textContent = [
      state.fellBack && result.horizon === "ros" ? "There are no weekly projections yet, so these are rest-of-season tiers." : null,
      flex ? `Flex ranks RBs, WRs, and TEs together by projected points over a replacement-level player at their ` +
        `position, for a ${result.teams}-team league.` : null,
    ].filter(Boolean).join(" ");
  }

  function renderMessage(text, className = "player-meta") {
    els.list.replaceChildren(el("li", className, text));
  }

  async function load() {
    const id = ++requestId;
    renderControls();
    writeUrl();
    renderMessage("Loading tiers…");
    const params = new URLSearchParams({ position: state.position, horizon: state.horizon, teams: state.teams });
    let result;
    try {
      result = await getJson(`/tiers?${params}`, "Couldn't load tiers");
    } catch (error) {
      if (id !== requestId) return;
      els.list.replaceChildren(errorBlock(error, load, { tag: "li" }));
      els.status.textContent = ""; // the error block announces itself
      return;
    }
    if (id !== requestId) return;

    if (!result.tiers.length && result.horizon === "week") {
      if (!state.horizonChosen) {
        // The in-season default found no weekly projections: show the season instead.
        state.horizon = "ros";
        state.fellBack = true;
        load();
        return;
      }
      const text = `No weekly projections for week ${result.week} yet. Rest of season has tiers now.`;
      renderMessage(text);
      els.status.textContent = text;
      els.note.textContent = "";
      return;
    }
    if (!result.tiers.length) {
      renderMessage(`No projections for ${POSITION_NAMES[result.position]} yet.`);
      els.status.textContent = "";
      return;
    }
    renderTiers(result);
  }

  // In season, open on this week; before week 2, on the rest of the season.
  async function defaultHorizon() {
    const meta = await loadMeta();
    state.currentWeek = meta?.current_week ?? null;
    return state.currentWeek > 1 ? "week" : "ros";
  }

  async function init() {
    readUrl();
    els.position.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-position]");
      if (!button || button.dataset.position === state.position) return;
      state.position = button.dataset.position;
      load();
    });
    els.horizon.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-horizon]");
      if (!button) return;
      state.horizon = button.dataset.horizon;
      state.horizonChosen = true;
      state.fellBack = false;
      load();
    });
    els.teams.addEventListener("change", () => {
      state.teams = Number(els.teams.value);
      load();
    });
    $("tier-controls").addEventListener("submit", (event) => event.preventDefault());

    renderControls();
    noteDataStatus(); // shares the /meta request with defaultHorizon
    if (!state.horizon) state.horizon = await defaultHorizon();
    load();
  }

  init();
})();
