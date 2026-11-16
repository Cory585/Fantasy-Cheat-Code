"use strict";

// Start/sit, in two modes:
//   My lineup:       the best lineup for one of the profile's leagues (picked at the top;
//                    rosters are set on /team/), with each spot's closest alternative and
//                    how sure the call is.
//   Compare players: choose a week and the players competing for one spot, then see who to
//                    start, how confident the call is, each player's range, and the reasons.

(() => {
  const { el, request, errorMessage, loadPlayers, loadMeta, noteDataStatus, copyText, avatar, profileUrl, createSearch } = window.PlayerUI;
  const $ = (id) => document.getElementById(id);
  const els = {
    status: $("data-status"),
    week: $("week"),
    weekPrev: $("week-prev"),
    weekNext: $("week-next"),
    slot: $("slot"),
    starts: $("starts"),
    search: $("ss-search"),
    results: $("ss-results"),
    chosen: $("ss-chosen"),
    result: $("ss-result"),
    share: $("share"),
    clear: $("clear"),
    shareStatus: $("share-status"),
    mode: $("ss-mode"),
    fromTeam: $("ss-from-team"),
    quick: $("ss-quick"),
    lineup: $("ss-lineup"),
    league: $("ss-league"),
    fromTeamLabel: $("from-team-label"),
  };
  const Store = window.LeagueStore;
  const noTeam = () => ({ id: null, name: "", slots: { ...Store.DEFAULT_SLOTS }, roster: [] });

  const MIN_PLAYERS = 2;
  const MAX_PLAYERS = 8;
  const MAX_STARTS = 3;
  const SKILL = ["QB", "RB", "WR", "TE"];
  const SLOT_POSITIONS = {
    QB: ["QB"], RB: ["RB"], WR: ["WR"], TE: ["TE"], FLEX: ["RB", "WR", "TE"],
    SUPERFLEX: SKILL, K: ["K"], DST: ["DST"],
  };
  const SLOT_NAMES = { FLEX: "flex", SUPERFLEX: "superflex" };
  const CONFIDENCE = {
    confident: { label: "Confident call", tone: "good" },
    lean: { label: "Lean", tone: "lean" },
    toss_up: { label: "Toss-up", tone: "close" },
  };

  const state = {
    players: new Map(),
    meta: null,
    weeks: [1, 18], // replaced by /meta's weeks once it loads
    week: null, // null until the current week is known
    slot: "",
    starts: 1,
    chosen: [],
    analysis: null,
    error: null,
    loading: false,
    mode: "compare",
    leagues: [],
    leaguesLoaded: false,
    leaguesError: null,
    autoMode: false, // no mode in the link: pick one once the leagues load
    team: noTeam(),
    lineup: null,
    lineupError: null,
    lineupLoading: false,
  };
  let requestId = 0;
  let lineupRequestId = 0;

  // ---------- Eligibility ----------

  // Positions that can join the comparison: the chosen slot's, or (for "Any") whatever
  // can share a spot with the players already chosen.
  function allowedPositions() {
    if (state.slot) return SLOT_POSITIONS[state.slot];
    const positions = new Set(state.chosen.map((id) => state.players.get(id)?.position).filter(Boolean));
    if (positions.has("K")) return ["K"];
    if (positions.has("DST")) return ["DST"];
    if (positions.size) return SKILL;
    return window.PlayerUI.POSITIONS;
  }

  const isFull = () => state.chosen.length >= MAX_PLAYERS;

  // ---------- URL (so a comparison can be shared or bookmarked) ----------

  function readUrl() {
    const params = new URLSearchParams(location.search);
    const week = Number(params.get("week"));
    state.week = Number.isInteger(week) && week >= 1 && week <= 18 ? week : null;
    const slot = params.get("slot") || "";
    state.slot = slot in SLOT_POSITIONS ? slot : "";
    const starts = Number(params.get("starts"));
    state.starts = Number.isInteger(starts) && starts >= 1 && starts <= MAX_STARTS ? starts : 1;
    const ids = (params.get("p") || "").split(",").filter(Boolean);
    state.chosen = [...new Set(ids)].slice(0, MAX_PLAYERS);
    const mode = params.get("mode");
    // Without an explicit mode, open the saved lineup unless the link is a comparison
    // (settled in useLeagues once it's known whether there's a team).
    state.autoMode = mode !== "lineup" && mode !== "compare" && !state.chosen.length;
    state.mode = mode === "lineup" || mode === "compare" ? mode : state.chosen.length ? "compare" : "lineup";
  }

  function writeUrl() {
    const params = new URLSearchParams();
    params.set("mode", state.mode);
    if (state.chosen.length) params.set("p", state.chosen.join(","));
    if (state.week) params.set("week", state.week);
    if (state.slot) params.set("slot", state.slot);
    if (state.starts > 1) params.set("starts", state.starts);
    if (state.team.id) params.set("league", state.team.id);
    const query = params.toString();
    history.replaceState(null, "", query ? `${location.pathname}?${query}` : location.pathname);
  }

  // ---------- Controls ----------

  function renderControls() {
    document.body.dataset.mode = state.mode;
    for (const button of els.mode.querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.mode === state.mode));
    }
    const [first, last] = state.weeks;
    const weeks = Array.from({ length: last - first + 1 }, (_, i) => first + i);
    if (els.week.options.length !== weeks.length || Number(els.week.options[0]?.value) !== first) {
      els.week.replaceChildren(...weeks.map((w) => new Option(`Week ${w}`, w)));
    }
    els.week.value = String(state.week ?? first);
    els.weekPrev.disabled = (state.week ?? first) <= first;
    els.weekNext.disabled = (state.week ?? first) >= last;

    for (const button of els.slot.querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.slot === state.slot));
    }
    const maxStarts = Math.max(1, Math.min(MAX_STARTS, state.chosen.length - 1));
    for (const button of els.starts.querySelectorAll("button")) {
      const n = Number(button.dataset.starts);
      button.setAttribute("aria-pressed", String(n === state.starts));
      button.disabled = n > maxStarts;
    }
    els.clear.disabled = state.chosen.length === 0;
    els.share.disabled = state.chosen.length < MIN_PLAYERS;
  }

  function renderChosen() {
    const chosen = state.chosen.filter((id) => state.players.has(id)); // players load after the URL is read
    if (!chosen.length) {
      els.chosen.replaceChildren(el("li", "roster-empty", "No players yet. Search above to add the first one."));
      return;
    }
    els.chosen.replaceChildren(...chosen.map((id) => {
      const player = state.players.get(id);
      const item = el("li", `ss-chip pos-${player.position}`);
      const text = el("span", "ss-chip-text");
      const name = el("a", "ss-chip-name", player.name);
      name.href = profileUrl(id);
      text.append(name, el("span", "player-meta", `${player.position}, ${player.team}`));
      const remove = el("button", "btn-remove", "×");
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove ${player.name}`);
      remove.addEventListener("click", () => removePlayer(id));
      item.append(avatar(player, "sm"), text, remove);
      return item;
    }));
  }

  // ---------- Analysis ----------

  async function analyze() {
    const id = ++requestId;
    if (state.chosen.length < MIN_PLAYERS) {
      state.analysis = null;
      state.error = null;
      state.loading = false;
      renderResult();
      return;
    }
    const params = new URLSearchParams();
    for (const playerId of state.chosen) params.append("player", playerId);
    if (state.week) params.set("week", state.week);
    if (state.slot) params.set("slot", state.slot);
    params.set("starts", Math.min(state.starts, state.chosen.length - 1));
    state.loading = true;
    els.result.setAttribute("aria-busy", "true");
    renderResult();
    try {
      const response = await request(`/start-sit?${params}`);
      if (id !== requestId) return;
      if (!response.ok) throw new Error(await errorMessage(response, "Couldn't compare these players"));
      state.analysis = await response.json();
      state.error = null;
    } catch (error) {
      if (id !== requestId) return;
      state.analysis = null;
      state.error = error.message;
    }
    state.loading = false;
    els.result.setAttribute("aria-busy", "false");
    renderResult();
  }

  function renderResult() {
    const a = state.analysis;
    if (state.error) {
      els.result.replaceChildren(el("p", "result-error", state.error));
      return;
    }
    if (!a) {
      const need = MIN_PLAYERS - state.chosen.length;
      const text = state.loading ? "Comparing…" : need === MIN_PLAYERS
        ? "Add at least two players to get a call."
        : "Add one more player to compare.";
      els.result.replaceChildren(el("p", "result-empty", text));
      return;
    }
    els.result.classList.toggle("is-stale", state.loading);
    els.result.replaceChildren(verdict(a), rangeChart(a), playerCards(a), ...warnings(a), method());
  }

  function verdict(a) {
    const byId = new Map(a.players.map((pw) => [pw.player.id, pw]));
    const starters = a.recommendation.start.map((id) => byId.get(id).player.name);
    const confidence = CONFIDENCE[a.recommendation.confidence];
    const box = el("div", `ss-verdict tone-${confidence.tone}`);
    const head = el("div", "ss-verdict-head");
    head.append(
      el("span", "ss-badge", confidence.label),
      el("span", "ss-week", `Week ${a.week} · ${SLOT_NAMES[a.slot] || a.slot} spot`),
    );
    const title = el("h2", null, `Start ${listNames(starters)}`);
    box.append(head, title, el("p", "ss-summary", a.recommendation.summary), confidenceMeter(a));
    return box;
  }

  function confidenceMeter(a) {
    const pct = Math.round(a.recommendation.probability * 100);
    const wrap = el("div", "ss-meter");
    // The closest call: the last player in the lineup against the best one left out.
    const last = a.players[a.starts - 1].player.name;
    const next = a.players[a.starts].player.name;
    const label = el("span", "ss-meter-label", `${last} outscores ${next} in ${pct}% of simulated weeks`);
    const track = el("span", "ss-meter-track");
    track.setAttribute("role", "img");
    track.setAttribute("aria-label", `${pct}% of simulated weeks`);
    const fill = el("span", "ss-meter-fill");
    fill.style.width = `${pct}%`;
    const mid = el("span", "ss-meter-mid");
    mid.title = "50%: a coin flip";
    track.append(fill, mid);
    wrap.append(label, track);
    return wrap;
  }

  // Floor-to-ceiling range for each player, with the expected score marked.
  function rangeChart(a) {
    const figure = el("figure", "ss-ranges");
    figure.append(el("figcaption", null, "Range of outcomes if they play: about 1 week in 10 falls below the bar, 1 in 10 above it. The mark is the expected score."));
    const max = Math.max(10, ...a.players.map((pw) => pw.ceiling));
    const scale = (v) => `${(v / max) * 100}%`;
    const list = el("ol", "ss-range-list");
    for (const pw of a.players) {
      const row = el("li", `ss-range-row${pw.recommended ? " is-start" : ""}`);
      const label = el("span", "ss-range-name", pw.player.name);
      const track = el("span", "ss-range-track");
      const desc = pw.status === "bye" ? "on bye"
        : pw.status === "played" ? `final: ${fmt(pw.projection)} points`
        : `${fmt(pw.floor)} to ${fmt(pw.ceiling)} points, expected ${fmt(pw.if_active)}`;
      track.setAttribute("role", "img");
      track.setAttribute("aria-label", `${pw.player.name}: ${desc}`);
      if (pw.status !== "bye") {
        const bar = el("span", "ss-range-bar");
        bar.style.left = scale(pw.floor);
        bar.style.width = `max(3px, ${scale(pw.ceiling - pw.floor)})`;
        const mark = el("span", "ss-range-mark");
        mark.style.left = scale(pw.if_active);
        track.append(bar, mark);
      }
      const value = el("span", "ss-range-value", pw.status === "bye" ? "Bye" : fmt(pw.projection));
      row.append(label, track, value);
      list.append(row);
    }
    const axis = el("div", "ss-range-axis");
    axis.setAttribute("aria-hidden", "true");
    for (const tick of [0, 0.25, 0.5, 0.75, 1]) {
      const t = el("span", null, String(Math.round(max * tick)));
      t.style.left = `${tick * 100}%`;
      axis.append(t);
    }
    const axisRow = el("div", "ss-range-axis-row");
    axisRow.append(el("span", "ss-range-gutter"), axis, el("span", "ss-range-unit", "pts"));
    figure.append(list, axisRow);
    return figure;
  }

  function playerCards(a) {
    const grid = el("div", "ss-cards");
    for (const pw of a.players) grid.append(playerCard(pw, a));
    return grid;
  }

  function playerCard(pw, a) {
    const card = el("article", `ss-card${pw.recommended ? " is-start" : ""}`);
    const head = el("div", "ss-card-head");
    const title = el("div", "ss-card-title");
    const name = el("a", "player-name", pw.player.name);
    name.href = profileUrl(pw.player.id);
    const meta = [pw.player.position, pw.player.team];
    if (pw.matchup) meta.push(`${pw.matchup.home ? "vs" : "at"} ${pw.matchup.opponent}`);
    title.append(name, el("span", "player-meta", meta.join(" · ")));
    const call = el("span", `ss-call ${pw.recommended ? "ss-call-start" : "ss-call-sit"}`, pw.recommended ? "Start" : "Sit");
    head.append(avatar(pw.player), title, call);

    const stats = el("dl", "ss-stats");
    const stat = (label, value) => {
      const item = el("div");
      item.append(el("dt", null, label), el("dd", null, value));
      stats.append(item);
    };
    if (pw.status === "bye") {
      stat("Projected", "Bye");
    } else if (pw.status === "played") {
      stat("Scored", fmt(pw.projection));
    } else {
      stat("Expected", fmt(pw.projection));
      stat("Floor", fmt(pw.floor));
      stat("Ceiling", fmt(pw.ceiling));
      if (pw.start_probability != null) {
        stat(a.starts > 1 ? `In top ${a.starts}` : "Top scorer", `${Math.round(pw.start_probability * 100)}%`);
      }
    }

    const reasons = el("ul", "ss-reasons");
    for (const reason of pw.reasons) {
      const item = el("li", `ss-reason effect-${reason.effect}`);
      const icon = el("span", "ss-reason-icon", { plus: "+", minus: "−", neutral: "•" }[reason.effect]);
      icon.setAttribute("aria-hidden", "true");
      const sr = el("span", "visually-hidden", { plus: "Helps: ", minus: "Hurts: ", neutral: "" }[reason.effect]);
      item.append(icon, sr, el("span", null, reason.text));
      reasons.append(item);
    }
    card.append(head, stats, reasons);
    return card;
  }

  function warnings(a) {
    if (!a.warnings.length) return [];
    const list = el("ul", "warnings");
    for (const warning of a.warnings) list.append(el("li", null, warning));
    return [list];
  }

  function method() {
    return el("p", "value-note",
      "Expected points blend Sleeper and ESPN weekly projections with recent form against this opponent, "
      + "then account for injury designations. Ranges come from each player's week-to-week swings this season. "
      + "The confidence comes from simulating the week 4,000 times.");
  }

  const fmt = (n) => Number(n).toFixed(1);

  function listNames(names) {
    if (names.length <= 2) return names.join(" and ");
    return `${names.slice(0, -1).join(", ")}, and ${names[names.length - 1]}`;
  }

  // ---------- My lineup (from the saved team) ----------

  const teamSlots = () => Object.fromEntries(Object.entries(state.team.slots).filter(([, n]) => n > 0));

  async function analyzeLineup() {
    const id = ++lineupRequestId;
    if (!state.leaguesLoaded || !Store.hasTeam(state.team) || !Object.keys(teamSlots()).length) {
      state.lineup = null;
      state.lineupError = null;
      renderLineup();
      return;
    }
    state.lineupLoading = true;
    els.lineup.setAttribute("aria-busy", "true");
    renderLineup();
    try {
      const response = await request("/start-sit/lineup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ roster_ids: state.team.roster, slots: teamSlots(), week: state.week }),
      });
      if (id !== lineupRequestId) return;
      if (!response.ok) throw new Error(await errorMessage(response, "Couldn't set your lineup"));
      state.lineup = await response.json();
      state.lineupError = null;
    } catch (error) {
      if (id !== lineupRequestId) return;
      state.lineup = null;
      state.lineupError = error.message;
    }
    state.lineupLoading = false;
    els.lineup.setAttribute("aria-busy", "false");
    renderLineup();
  }

  function renderLineup() {
    if (!state.leaguesLoaded) {
      els.lineup.replaceChildren(el("p", "result-empty", "Loading your leagues…"));
      return;
    }
    if (state.leaguesError) {
      els.lineup.replaceChildren(el("p", "result-error", `Couldn't load your leagues: ${state.leaguesError}`));
      return;
    }
    if (!Store.hasTeam(state.team)) {
      const box = el("div", "result-empty ss-no-team");
      const link = el("a", "btn btn-cta", "Set up my team");
      link.href = state.team.id ? `/team/?league=${encodeURIComponent(state.team.id)}` : "/team/";
      box.append(el("p", null, state.team.id
        ? `${state.team.name} has no players yet. Add your roster, and this page sets your best lineup every week.`
        : "Save your roster and lineup spots once, and this page sets your best lineup every week."), link);
      els.lineup.replaceChildren(box);
      return;
    }
    if (state.lineupError) {
      els.lineup.replaceChildren(el("p", "result-error", state.lineupError));
      return;
    }
    const a = state.lineup;
    if (!a) {
      els.lineup.replaceChildren(el("p", "result-empty", state.lineupLoading ? "Setting your lineup…"
        : "Add at least one lineup spot on the My team page."));
      return;
    }
    els.lineup.classList.toggle("is-stale", state.lineupLoading);
    els.lineup.replaceChildren(lineupHead(a), ...missingNote(a), ...closeCalls(a), lineupTable(a), benchList(a), ...warnings(a), lineupNote());
  }

  function lineupHead(a) {
    const head = el("div", "ss-lineup-head");
    const title = el("div");
    const name = state.team.name.trim() || "Your team";
    title.append(el("h2", "section-heading", `${name}: week ${a.week} lineup`),
      el("p", "section-note", `Projected ${fmt(a.projected_total)} points from ${a.lineup.filter((s) => s.starter).length} starters.`));
    const edit = el("a", "btn btn-quiet", "Edit team");
    edit.href = `/team/?league=${encodeURIComponent(state.team.id)}`;
    head.append(title, edit);
    return head;
  }

  // Saved players the pool no longer has (e.g. released) are left out; same wording as My team.
  function missingNote(a) {
    const n = a.missing_ids?.length || 0;
    if (!n) return [];
    return [el("p", "ss-missing player-meta",
      `${n} player${n === 1 ? " on this roster is" : "s on this roster are"} no longer in the player pool, so `
      + `${n === 1 ? "it's" : "they're"} left out of the lineup.`)];
  }

  function closeCalls(a) {
    if (!a.close_calls.length) return [];
    const section = el("div", "ss-close-calls");
    section.append(el("h3", null, "Closest calls"));
    const list = el("ul", "ss-close-list");
    for (const index of a.close_calls) {
      const spot = a.lineup[index];
      const confidence = CONFIDENCE[spot.confidence];
      const item = el("li", `ss-close tone-${confidence.tone}`);
      const text = el("p", "ss-close-title", `${spot.slot}: ${spot.starter.player.name} over ${spot.alternative.player.name}`);
      const detail = el("p", "ss-close-detail",
        `${confidence.label} · ${Math.round(spot.probability * 100)}% of simulated weeks · `
        + `${fmt(spot.starter.projection)} vs ${fmt(spot.alternative.projection)} expected`);
      item.append(text, detail, compareButton(spot));
      list.append(item);
    }
    section.append(list);
    return [section];
  }

  function compareButton(spot) {
    const button = el("button", "btn btn-quiet btn-small", "Compare");
    button.type = "button";
    button.setAttribute("aria-label",
      `Compare ${spot.starter.player.name} and ${spot.alternative.player.name} for the ${spot.slot} spot`);
    button.addEventListener("click", () => {
      state.mode = "compare";
      state.chosen = [spot.starter.player.id, spot.alternative.player.id];
      state.slot = spot.slot;
      state.starts = 1;
      changed();
      window.scrollTo({ top: 0 });
    });
    return button;
  }

  function lineupTable(a) {
    const list = el("ol", "ss-lineup");
    list.setAttribute("aria-label", "Starting lineup");
    for (const spot of a.lineup) {
      const item = el("li", `ss-spot${spot.starter ? ` pos-${spot.starter.player.position}` : " is-empty"}`);
      item.append(el("span", "ss-spot-slot", spot.slot === "SUPERFLEX" ? "SFLEX" : spot.slot));
      if (!spot.starter) {
        item.append(el("span", "ss-spot-empty", "Nobody on your roster fits this spot."));
        list.append(item);
        continue;
      }
      const pw = spot.starter;
      const who = el("span", "ss-spot-player");
      const name = el("a", "ss-chip-name", pw.player.name);
      name.href = profileUrl(pw.player.id);
      who.append(name, el("span", "player-meta", weekLine(pw)));
      const points = el("span", "ss-spot-points", pw.status === "bye" ? "Bye" : fmt(pw.projection));
      const range = el("span", "ss-spot-range", pw.status === "upcoming" ? `${fmt(pw.floor)}–${fmt(pw.ceiling)}` : "");
      const alt = el("span", "ss-spot-alt");
      if (spot.alternative) {
        const confidence = CONFIDENCE[spot.confidence];
        const pct = Math.round(spot.probability * 100);
        alt.append(el("span", `ss-dot tone-${confidence.tone}`),
          el("span", null, `over ${spot.alternative.player.name} · ${pct}%`));
        alt.title = `${confidence.label}: ${pw.player.name} outscores ${spot.alternative.player.name} in ${pct}% of simulated weeks`;
      } else {
        alt.append(el("span", "ss-muted", "no one to swap in"));
      }
      item.append(avatar(pw.player, "sm"), who, points, range, alt);
      list.append(item);
    }
    return list;
  }

  function weekLine(pw) {
    const parts = [`${pw.player.position}, ${pw.player.team}`];
    if (pw.matchup) parts.push(`${pw.matchup.home ? "vs" : "at"} ${pw.matchup.opponent}`);
    if (pw.status === "played") parts.push("final");
    if (pw.player.injury_status) parts.push(pw.player.injury_status);
    return parts.join(" · ");
  }

  function benchList(a) {
    const section = el("div", "ss-bench");
    section.append(el("h3", null, `Bench (${a.bench.length})`));
    if (!a.bench.length) {
      section.append(el("p", "section-note", "Everyone on your roster starts."));
      return section;
    }
    const list = el("ul", "ss-bench-list");
    for (const pw of a.bench) {
      const item = el("li", `ss-bench-item pos-${pw.player.position}`);
      const name = el("a", "ss-chip-name", pw.player.name);
      name.href = profileUrl(pw.player.id);
      const text = el("span", "ss-chip-text");
      text.append(name, el("span", "player-meta", weekLine(pw)));
      item.append(text, el("span", "ss-spot-points", pw.status === "bye" ? "Bye" : fmt(pw.projection)));
      list.append(item);
    }
    section.append(list);
    return section;
  }

  function lineupNote() {
    return el("p", "value-note",
      "Each spot goes to the player with the most expected points who fits it, filling position spots before "
      + "FLEX and SUPERFLEX. The percentage is how often the starter outscores the best benched option in "
      + "simulated weeks. Pick Compare on a close call to see the reasons.");
  }

  // Players from the saved team who could join the current comparison.
  function renderQuick() {
    const roster = state.team.roster.map((id) => state.players.get(id)).filter(Boolean)
      .filter((p) => !state.chosen.includes(p.id) && allowedPositions().includes(p.position))
      .sort((x, y) => y.ros_projection - x.ros_projection);
    els.fromTeam.hidden = !roster.length || isFull();
    els.fromTeamLabel.textContent = state.leagues.length > 1 ? `From ${state.team.name}` : "From your team";
    els.quick.replaceChildren(...roster.map((player) => {
      const item = el("li");
      const button = el("button", `ss-quick-btn pos-${player.position}`, player.name);
      button.type = "button";
      button.setAttribute("aria-label", `Add ${player.name} (${player.position}) to the comparison`);
      button.addEventListener("click", () => addPlayer(player));
      item.append(button);
      return item;
    }));
  }

  // ---------- Changes ----------

  function changed({ reanalyze = true } = {}) {
    if (state.starts > Math.max(1, state.chosen.length - 1)) state.starts = Math.max(1, state.chosen.length - 1);
    writeUrl();
    renderControls();
    renderChosen();
    renderQuick();
    if (reanalyze) (state.mode === "lineup" ? analyzeLineup : analyze)();
  }

  function addPlayer(player) {
    if (isFull() || state.chosen.includes(player.id)) return;
    state.chosen.push(player.id);
    changed();
  }

  function removePlayer(id) {
    state.chosen = state.chosen.filter((x) => x !== id);
    changed();
    els.search.focus();
  }

  function setWeek(week) {
    const [first, last] = state.weeks;
    state.week = Math.min(last, Math.max(first, week));
    changed();
  }

  function bind() {
    els.mode.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-mode]");
      if (!button || button.dataset.mode === state.mode) return;
      state.mode = button.dataset.mode;
      changed();
    });
    // The leagues were edited in another tab: use the new versions.
    Store.onChange(loadLeagues);
    els.week.addEventListener("change", () => setWeek(Number(els.week.value)));
    els.weekPrev.addEventListener("click", () => setWeek((state.week ?? state.weeks[0]) - 1));
    els.weekNext.addEventListener("click", () => setWeek((state.week ?? state.weeks[0]) + 1));
    els.slot.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-slot]");
      if (!button) return;
      state.slot = button.dataset.slot;
      // Drop players who don't fit the new slot.
      const fits = SLOT_POSITIONS[state.slot];
      if (fits) state.chosen = state.chosen.filter((id) => fits.includes(state.players.get(id)?.position));
      changed();
    });
    els.starts.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-starts]");
      if (!button || button.disabled) return;
      state.starts = Number(button.dataset.starts);
      changed();
    });
    els.clear.addEventListener("click", () => {
      state.chosen = [];
      changed();
      els.search.focus();
    });
    els.share.addEventListener("click", async () => {
      els.shareStatus.textContent = (await copyText(location.href)) ? "Link copied" : "Copy the address bar to share";
      setTimeout(() => { els.shareStatus.textContent = ""; }, 2500);
    });
    $("ss-setup").addEventListener("submit", (event) => event.preventDefault());
  }

  // ---------- Leagues ----------

  function useLeagues(leagues) {
    state.leagues = leagues;
    const current = leagues.find((x) => x.id === state.team.id) || Store.activeLeague(leagues);
    state.team = current ? { id: current.id, ...Store.clean(current) } : noTeam();
    if (state.autoMode) {
      state.autoMode = false;
      if (!Store.hasTeam(state.team)) state.mode = "compare";
    }
    renderLeague();
  }

  function renderLeague() {
    els.league.hidden = state.leagues.length === 0;
    if (!state.leagues.length) {
      els.league.replaceChildren();
      return;
    }
    els.league.replaceChildren(Store.picker({
      id: "league-select",
      leagues: state.leagues,
      active: state.leagues.find((x) => x.id === state.team.id),
      onSelect: (id) => {
        const league = state.leagues.find((x) => x.id === id);
        if (!league) return;
        state.team = { id: league.id, ...Store.clean(league) };
        state.lineup = null;
        changed({ reanalyze: state.mode === "lineup" });
      },
    }));
  }

  async function loadLeagues() {
    try {
      useLeagues((await Store.list()).leagues);
      state.leaguesError = null;
    } catch (error) {
      state.leaguesError = error.message;
      if (state.autoMode) {
        state.autoMode = false;
        state.mode = "compare";
      }
    }
    state.leaguesLoaded = true;
    if (state.players.size) changed({ reanalyze: state.mode === "lineup" });
  }

  function renderStatus() {
    const meta = state.meta;
    if (!meta) return;
    noteDataStatus(meta);
    if (meta.source !== "synced") return;
    const parts = [`Week ${meta.current_week} is the current week`];
    if (!meta.matchups) parts.push("matchups and recent form aren't available yet");
    els.status.textContent = `${parts.join("; ")}.`;
  }

  async function init() {
    readUrl();
    bind();
    createSearch({
      input: els.search,
      list: els.results,
      players: () => state.players,
      allowed: (p) => !state.chosen.includes(p.id) && allowedPositions().includes(p.position),
      onPick: addPlayer,
      full: () => (isFull() ? `You can compare up to ${MAX_PLAYERS} players. Remove one to add another.` : null),
    });
    renderControls();
    renderChosen();
    renderResult();
    renderLineup();
    const leagues = loadLeagues();
    try {
      const [players, meta] = await Promise.all([loadPlayers(), loadMeta(), leagues]);
      state.players = players;
      state.meta = meta;
    } catch (error) {
      for (const target of [els.result, els.lineup]) target.replaceChildren(el("p", "result-error", error.message));
      return;
    }
    if (Array.isArray(state.meta?.weeks) && state.meta.weeks.length === 2) state.weeks = state.meta.weeks;
    const [first, last] = state.weeks;
    if (state.week != null && (state.week < first || state.week > last)) state.week = null;
    state.week = state.week ?? state.meta?.current_week ?? first;
    state.chosen = state.chosen.filter((id) => state.players.has(id));
    renderStatus();
    changed();
  }

  init();
})();
