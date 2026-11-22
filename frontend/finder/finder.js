"use strict";

// Player finder: pick tags (slot receiver, high target share, goal-line back...) and a
// position to list the players who fit, best projection first. The choices live in the
// URL (?tag=slot,high_target_share&pos=WR&match=all&q=), so a search can be shared.

(() => {
  const { el, loadPlayers, avatar, profileUrl, getJson, errorBlock, noteDataStatus, matchesQuery } = window.PlayerUI;
  const $ = (id) => document.getElementById(id);
  const els = {
    status: $("finder-status"),
    pos: $("finder-pos"),
    match: $("finder-match"),
    q: $("finder-q"),
    groups: $("tag-groups"),
    clear: $("finder-clear"),
    note: $("results-note"),
    list: $("finder-list"),
    more: $("finder-more"),
    tagsNote: $("tags-note"),
  };
  const PAGE = 50;
  const CATEGORIES = [
    ["role", "Role"],
    ["usage", "Usage"],
    ["trend", "Trend"],
    ["status", "Status"],
  ];

  const state = {
    players: new Map(),
    catalog: [],
    byKey: new Map(),
    playerTags: {},
    season: null,
    selected: [],
    pos: "",
    match: "all",
    q: "",
    shown: PAGE,
    loaded: false, // players and tags are in; until then the list shows loading or the error
  };

  // ---------- URL ----------

  function readUrl() {
    const params = new URLSearchParams(location.search);
    state.selected = [...new Set((params.get("tag") || "").split(",").filter(Boolean))];
    const pos = params.get("pos") || "";
    state.pos = ["QB", "RB", "WR", "TE", "K", "DST"].includes(pos) ? pos : "";
    state.match = params.get("match") === "any" ? "any" : "all";
    state.q = (params.get("q") || "").slice(0, 40);
  }

  function writeUrl() {
    const params = new URLSearchParams();
    if (state.selected.length) params.set("tag", state.selected.join(","));
    if (state.pos) params.set("pos", state.pos);
    if (state.match === "any") params.set("match", "any");
    if (state.q) params.set("q", state.q);
    const query = params.toString();
    history.replaceState(null, "", query ? `${location.pathname}?${query}` : location.pathname);
  }

  // ---------- Filtering ----------

  const tagsOf = (id) => state.playerTags[id] || [];

  function matches(player) {
    if (state.pos && player.position !== state.pos) return false;
    if (state.q && !matchesQuery(player, state.q)) return false;
    if (!state.selected.length) return true;
    const keys = new Set(tagsOf(player.id).map((t) => t.key));
    return state.match === "all" ? state.selected.every((k) => keys.has(k)) : state.selected.some((k) => keys.has(k));
  }

  // Tags that apply at the chosen position, with how many of the matching players have each.
  function visibleTags() {
    return state.catalog.filter((tag) => !state.pos || tag.positions.includes(state.pos));
  }

  function countsFor(players) {
    const counts = new Map();
    for (const player of players) {
      for (const tag of tagsOf(player.id)) counts.set(tag.key, (counts.get(tag.key) || 0) + 1);
    }
    return counts;
  }

  // ---------- Rendering ----------

  function render() {
    for (const button of els.pos.querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.pos === state.pos));
    }
    for (const button of els.match.querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.match === state.match));
    }
    els.clear.disabled = state.selected.length === 0;
    const pool = [...state.players.values()].filter((p) => (!state.pos || p.position === state.pos));
    renderTags(countsFor(pool));
    renderResults();
  }

  function renderTags(counts) {
    const tags = visibleTags();
    const groups = CATEGORIES.map(([category, title]) => {
      const inGroup = tags.filter((tag) => tag.category === category);
      if (!inGroup.length) return null;
      const group = el("div", "tag-group");
      group.setAttribute("role", "group");
      group.setAttribute("aria-label", `${title} tags`);
      group.append(el("h3", "tag-group-head", title));
      const list = el("div", "tag-chips");
      for (const tag of inGroup) {
        const on = state.selected.includes(tag.key);
        const count = counts.get(tag.key) || 0;
        const chip = el("button", `tag-chip tag-${tag.category}`);
        chip.type = "button";
        chip.dataset.tag = tag.key;
        chip.setAttribute("aria-pressed", String(on));
        chip.title = tag.description;
        chip.append(el("span", "tag-chip-label", tag.label), el("span", "tag-chip-count", String(count)));
        chip.setAttribute("aria-label", `${tag.label}, ${count} player${count === 1 ? "" : "s"}. ${tag.description}`);
        chip.disabled = count === 0 && !on;
        list.append(chip);
      }
      group.append(list);
      return group;
    }).filter(Boolean);
    els.groups.replaceChildren(...groups);
  }

  function renderResults() {
    if (!state.loaded) return; // keep "Loading players…" or the Try again block
    const found = [...state.players.values()].filter(matches).sort((a, b) => b.ros_projection - a.ros_projection);
    const chosen = state.selected.map((k) => state.byKey.get(k)).filter(Boolean);
    const what = chosen.length
      ? chosen.map((t) => t.label).join(state.match === "all" ? " + " : " or ")
      : "all players";
    els.note.textContent = `${found.length} ${found.length === 1 ? "match" : "matches"} for ${what}`
      + `${state.pos ? ` at ${state.pos}` : ""}, best projection first.`;
    if (!found.length) {
      els.list.replaceChildren(el("li", "roster-empty", chosen.length > 1 && state.match === "all"
        ? "No one has all of these tags. Try Any tag, or drop one."
        : "No players match. Try another position or tag."));
      els.more.hidden = true;
      return;
    }
    els.list.replaceChildren(...found.slice(0, state.shown).map(row));
    els.more.hidden = found.length <= state.shown;
    els.more.textContent = `Show more (${found.length - state.shown} left)`;
  }

  function row(player) {
    const item = el("li", `finder-item pos-${player.position}`);
    const text = el("div", "finder-text");
    const name = el("a", "ss-chip-name", player.name);
    name.href = profileUrl(player.id);
    const meta = el("span", "player-meta", `${player.position}, ${player.team}`);
    text.append(name, meta);
    const tags = el("ul", "finder-tags-list");
    tags.setAttribute("aria-label", `${player.name}'s tags`);
    for (const tag of tagsOf(player.id)) {
      const def = state.byKey.get(tag.key);
      if (!def) continue;
      const on = state.selected.includes(tag.key);
      const chip = el("li", `tag-pill tag-${def.category}${on ? " is-on" : ""}`, def.label);
      if (tag.detail) {
        chip.title = tag.detail;
        chip.append(el("span", "visually-hidden", `: ${tag.detail}`));
      }
      tags.append(chip);
    }
    const proj = el("span", "finder-proj");
    proj.append(el("strong", null, player.ros_projection.toFixed(0)), el("span", null, " pts ROS"));
    item.append(avatar(player, "sm"), text, proj, tags);
    return item;
  }

  // ---------- Events ----------

  function changed() {
    state.shown = PAGE;
    writeUrl();
    render();
  }

  function bind() {
    els.pos.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-pos]");
      if (!button) return;
      state.pos = button.dataset.pos;
      // Drop tags that don't apply at the new position.
      const allowed = new Set(visibleTags().map((t) => t.key));
      state.selected = state.selected.filter((k) => allowed.has(k));
      changed();
    });
    els.match.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-match]");
      if (!button) return;
      state.match = button.dataset.match;
      changed();
    });
    els.groups.addEventListener("click", (event) => {
      const chip = event.target.closest("button[data-tag]");
      if (!chip) return;
      const key = chip.dataset.tag;
      state.selected = state.selected.includes(key) ? state.selected.filter((k) => k !== key) : [...state.selected, key];
      changed();
      els.groups.querySelector(`button[data-tag="${CSS.escape(key)}"]`)?.focus();
    });
    els.clear.addEventListener("click", () => {
      state.selected = [];
      changed();
    });
    els.q.addEventListener("input", () => {
      state.q = els.q.value.trim().slice(0, 40);
      changed();
    });
    els.more.addEventListener("click", () => {
      state.shown += PAGE;
      renderResults();
    });
    $("finder-setup").addEventListener("submit", (event) => event.preventDefault());
  }

  async function loadData() {
    els.list.replaceChildren(el("li", "result-empty", "Loading players…"));
    let index;
    try {
      [state.players, index] = await Promise.all([loadPlayers(), getJson("/tags", "Couldn't load tags")]);
    } catch (error) {
      els.list.replaceChildren(errorBlock(error, loadData, { tag: "li" }));
      return;
    }
    state.catalog = index.tags;
    state.byKey = new Map(index.tags.map((t) => [t.key, t]));
    state.playerTags = index.players;
    state.season = index.season;
    state.loaded = true;
    state.selected = state.selected.filter((k) => state.byKey.has(k));
    els.status.textContent = index.season
      ? `Role, usage, and trend tags come from the ${index.season} season so far.`
      : "Role, usage, and trend tags aren't available yet, so only status tags are shown.";
    writeUrl();
    render();
  }

  function init() {
    readUrl();
    els.q.value = state.q;
    bind();
    render();
    loadData();
    noteDataStatus();
  }

  init();
})();
