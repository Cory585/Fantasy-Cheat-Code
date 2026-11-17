"use strict";

// My team: the roster and lineup spots for one of the profile's leagues (see
// /league-store.js), picked at the top. Changes save to the server a moment after the last
// edit; the start/sit page reads the same leagues to set a lineup. With no leagues yet, the
// first change makes one. Nothing can be edited until the leagues have loaded, so an edit
// can't make a second league for someone who already has one.

(() => {
  const { el, loadPlayers, loadMeta, avatar, profileUrl, createSearch, POSITIONS } = window.PlayerUI;
  const Store = window.LeagueStore;
  const $ = (id) => document.getElementById(id);
  const els = {
    status: $("save-status"),
    picker: $("league-picker"),
    name: $("team-name-input"),
    steppers: $("slot-steppers"),
    reset: $("slots-reset"),
    summary: $("lineup-summary"),
    count: $("roster-count"),
    clear: $("clear-team"),
    search: $("team-search"),
    results: $("team-results"),
    roster: $("team-roster"),
    toLineup: $("to-lineup"),
  };
  const SLOT_LABELS = { FLEX: "FLEX (RB/WR/TE)", SUPERFLEX: "SUPERFLEX (QB/RB/WR/TE)" };
  const SAVE_DELAY = 600;
  const NEW_LEAGUE_NAME = "My league";

  const blank = () => ({ id: null, name: "", slots: { ...Store.DEFAULT_SLOTS }, roster: [] });

  let leagues = [];
  let team = blank(); // the league being edited; id is null until the first save makes it
  let savedName = "";
  let players = new Map();
  let clearArmed = null;
  let firstWeek = 1; // byes before the current week are already past
  let loaded = false; // the leagues have loaded; until then nothing can be edited
  let loadError = null; // why the leagues didn't load (shown with a Try again button)
  let profile = null; // the profile key the leagues were loaded under; saves go there
  let playersReady = false;

  // ---------- Saving ----------

  let dirty = false;
  let saveTimer = null;
  let inFlight = null;
  let doneMessage = "Saved.";

  const snapshot = () => ({
    name: team.name.trim() || savedName || NEW_LEAGUE_NAME,
    slots: { ...team.slots },
    roster: [...team.roster],
  });

  function say(message, isError = false) {
    els.status.textContent = message;
    els.status.classList.toggle("is-error", isError);
  }

  function persist(message = "Saved.") {
    if (!loaded) return;
    dirty = true;
    doneMessage = message;
    say("Saving…");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, SAVE_DELAY);
  }

  // Sends the latest edits. Saves run one at a time, so they reach the server in order.
  async function flush() {
    clearTimeout(saveTimer);
    saveTimer = null;
    if (inFlight) await inFlight;
    if (!dirty || !loaded) return;
    dirty = false;
    const body = snapshot();
    const editing = team;
    const options = { key: profile };
    inFlight = (async () => {
      try {
        const saved = editing.id ? await Store.save(editing.id, body, options) : await Store.create(body, options);
        if (!editing.id) {
          editing.id = saved.id;
          Store.setActive(saved.id);
          writeUrl();
        }
        leagues = editing.id && leagues.some((x) => x.id === saved.id)
          ? leagues.map((x) => (x.id === saved.id ? saved : x))
          : [...leagues, saved];
        if (editing === team) savedName = saved.name;
        renderPicker();
        renderLink();
        if (!dirty) say(doneMessage);
      } catch (error) {
        if (error.conflict && editing === team) {
          await adoptServerCopy(error.league);
          return;
        }
        if (editing === team) dirty = true; // try again with the next change
        say(`${error.message} Your last change isn't saved yet.`, true);
      } finally {
        inFlight = null;
      }
    })();
    await inFlight;
  }

  // The league was saved in another tab or on another device since this page loaded it: show
  // that version instead, dropping the edits here (saving them would overwrite it). Reloading
  // the list also picks up its new version, so the next edit saves on top of it.
  async function adoptServerCopy(serverCopy) {
    clearTimeout(saveTimer);
    saveTimer = null;
    dirty = false;
    try {
      leagues = (await Store.list()).leagues;
    } catch {
      leagues = leagues.map((x) => (x.id === serverCopy.id ? serverCopy : x));
    }
    useLeague(leagues.find((x) => x.id === serverCopy.id) || serverCopy);
    say("This league was changed in another tab or on another device, so your last change here wasn't saved. "
      + "Showing the latest version.", true);
  }

  // Leaving with an edit still waiting: send it anyway. The first edit of a profile with no
  // leagues makes its league here, unless that league is already being made (its id isn't
  // known yet, so there's nothing to save the edit to).
  function flushOnLeave() {
    if (!dirty || !loaded) return;
    const options = { keepalive: true, key: profile };
    if (team.id) {
      dirty = false;
      Store.save(team.id, snapshot(), options).catch(() => {});
    } else if (!inFlight) {
      dirty = false;
      Store.create(snapshot(), options).catch(() => {});
    }
  }

  // ---------- Which league ----------

  function writeUrl() {
    const params = new URLSearchParams(location.search);
    if (!team.id) return; // keep a ?league= link until the leagues load
    params.set("league", team.id);
    const query = params.toString();
    history.replaceState(null, "", query ? `${location.pathname}?${query}` : location.pathname);
  }

  function useLeague(league) {
    team = league ? { id: league.id, ...Store.clean(league) } : blank();
    savedName = league?.name || "";
    els.name.value = team.name;
    writeUrl();
    renderPicker();
    renderSteppers();
    renderRoster();
    renderLink();
  }

  function renderPicker() {
    if (loadError) {
      const retry = el("button", "btn btn-quiet btn-small", "Try again");
      retry.type = "button";
      retry.id = "leagues-retry";
      retry.addEventListener("click", () => loadLeagues(true));
      els.picker.replaceChildren(
        el("p", "result-error", `Couldn't load your leagues: ${loadError} Editing is off until they load.`), retry);
      return;
    }
    if (!loaded) {
      els.picker.replaceChildren(el("p", "section-note", "Loading your leagues…"));
      return;
    }
    if (!leagues.length) {
      const note = el("p", "section-note", "Your first change here makes a league. Add more on your ");
      const link = el("a", null, "profile");
      link.href = "/profile/";
      note.append(link, ".");
      els.picker.replaceChildren(note);
      return;
    }
    els.picker.replaceChildren(Store.picker({
      id: "league-select",
      leagues,
      active: leagues.find((x) => x.id === team.id) || null,
      onSelect: switchLeague,
    }));
  }

  async function switchLeague(id) {
    await flush();
    const league = leagues.find((x) => x.id === id);
    if (!league) return;
    useLeague(league);
    say(`Editing ${league.name}.`);
    $("league-select")?.focus();
  }

  // ---------- Lineup spots ----------

  function renderSteppers() {
    els.steppers.replaceChildren(...Store.SLOTS.map((slot) => {
      const row = el("div", "slot-stepper");
      const label = el("span", "stepper-label", SLOT_LABELS[slot] || slot);
      const minus = el("button", "stepper-btn", "−");
      minus.type = "button";
      minus.dataset.slot = slot;
      minus.dataset.step = "-1";
      minus.setAttribute("aria-label", `Fewer ${slot} spots`);
      minus.disabled = !loaded || team.slots[slot] <= 0;
      const count = el("output", "stepper-count", String(team.slots[slot]));
      count.setAttribute("aria-label", `${slot} spots`);
      const plus = el("button", "stepper-btn", "+");
      plus.type = "button";
      plus.dataset.slot = slot;
      plus.dataset.step = "1";
      plus.setAttribute("aria-label", `More ${slot} spots`);
      plus.disabled = !loaded || team.slots[slot] >= Store.MAX_SLOT;
      row.append(label, minus, count, plus);
      return row;
    }));
    const starters = Object.values(team.slots).reduce((a, b) => a + b, 0);
    els.summary.textContent = starters
      ? `${starters} starters: ${Store.SLOTS.filter((s) => team.slots[s]).map((s) => `${team.slots[s]} ${s}`).join(", ")}.`
      : "Add at least one lineup spot.";
  }

  function changeSlot(slot, step) {
    if (!loaded) return;
    team.slots[slot] = Math.min(Store.MAX_SLOT, Math.max(0, team.slots[slot] + step));
    renderSteppers();
    // Re-rendering replaced the button; keep focus on its replacement for keyboard users.
    const target = els.steppers.querySelector(`button[data-slot="${slot}"][data-step="${step}"]`);
    (target && !target.disabled ? target : els.steppers.querySelector(`button[data-slot="${slot}"]:not(:disabled)`))?.focus();
    persist();
    renderLink();
  }

  // ---------- Roster ----------

  function renderRoster() {
    const known = team.roster.filter((id) => players.has(id));
    const missing = team.roster.length - known.length;
    els.count.textContent = `${team.roster.length} of ${Store.MAX_ROSTER} players`
      + (missing && players.size ? ` (${missing} no longer in the player pool)` : "");
    els.clear.disabled = !loaded || team.roster.length === 0;
    if (!team.roster.length) {
      els.roster.replaceChildren(el("p", "roster-empty", "No players yet. Search above to add your roster."));
      return;
    }
    const groups = POSITIONS.map((pos) => {
      const ids = known.filter((id) => players.get(id).position === pos)
        .sort((a, b) => players.get(b).ros_projection - players.get(a).ros_projection);
      if (!ids.length) return null;
      const group = el("section", `team-group pos-${pos}`);
      const head = el("h3", "team-group-head");
      head.append(el("span", "pos-swatch"), el("span", null, `${pos} · ${ids.length}`));
      const list = el("ul", "team-list");
      for (const id of ids) list.append(rosterItem(players.get(id)));
      group.append(head, list);
      return group;
    }).filter(Boolean);
    els.roster.replaceChildren(...groups);
  }

  function rosterItem(player) {
    const item = el("li", "team-item");
    const text = el("span", "ss-chip-text");
    const name = el("a", "ss-chip-name", player.name);
    name.href = profileUrl(player.id);
    const upcomingBye = player.bye_week && player.bye_week >= firstWeek ? `bye ${player.bye_week}` : null;
    const meta = [player.team, upcomingBye].filter(Boolean).join(" · ");
    const metaEl = el("span", "player-meta", meta);
    if (player.injury_status) metaEl.append(el("span", "injury", player.injury_status));
    text.append(name, metaEl);
    const remove = el("button", "btn-remove", "×");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${player.name}`);
    remove.addEventListener("click", () => removePlayer(player.id));
    item.append(avatar(player, "sm"), text, remove);
    return item;
  }

  function addPlayer(player) {
    if (!loaded || team.roster.includes(player.id) || team.roster.length >= Store.MAX_ROSTER) return;
    team.roster.push(player.id);
    persist(`Added ${player.name}.`);
    renderRoster();
    renderLink();
  }

  function removePlayer(id) {
    if (!loaded) return;
    const name = players.get(id)?.name || "player";
    team.roster = team.roster.filter((x) => x !== id);
    persist(`Removed ${name}.`);
    renderRoster();
    renderLink();
    els.search.focus();
  }

  function renderLink() {
    const ready = Boolean(team.id) && team.roster.length > 0 && Object.values(team.slots).some((n) => n > 0);
    els.toLineup.href = team.id ? `/start-sit/?mode=lineup&league=${encodeURIComponent(team.id)}` : "/start-sit/?mode=lineup";
    els.toLineup.classList.toggle("is-disabled", !ready);
    els.toLineup.setAttribute("aria-disabled", String(!ready));
  }

  // ---------- Events ----------

  function bind() {
    els.name.addEventListener("input", () => {
      team.name = els.name.value.slice(0, Store.MAX_NAME);
      if (team.name.trim()) persist();
      else say("Give the league a name.", true);
    });
    els.name.addEventListener("blur", () => {
      if (!team.name.trim() && savedName) {
        team.name = savedName;
        els.name.value = savedName;
        say("");
      }
    });
    els.steppers.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-slot]");
      if (button && !button.disabled) changeSlot(button.dataset.slot, Number(button.dataset.step));
    });
    els.reset.addEventListener("click", () => {
      team.slots = { ...Store.DEFAULT_SLOTS };
      renderSteppers();
      persist("Lineup reset to the default.");
    });
    // Two clicks to clear, so a stray click can't wipe a roster.
    els.clear.addEventListener("click", () => {
      if (!clearArmed) {
        els.clear.textContent = "Click again to clear";
        clearArmed = setTimeout(() => {
          clearArmed = null;
          els.clear.textContent = "Clear roster";
        }, 3000);
        return;
      }
      clearTimeout(clearArmed);
      clearArmed = null;
      els.clear.textContent = "Clear roster";
      team.roster = [];
      persist("Roster cleared.");
      renderRoster();
      renderLink();
    });
    els.toLineup.addEventListener("click", async (event) => {
      if (els.toLineup.getAttribute("aria-disabled") === "true") {
        event.preventDefault();
        say(team.roster.length ? "Saving your team…" : "Add players to your roster first.");
        if (!team.roster.length) els.search.focus();
        return;
      }
      if (dirty || inFlight) {
        // Save first, so the lineup uses the latest roster.
        event.preventDefault();
        await flush();
        location.href = els.toLineup.href;
      }
    });
    window.addEventListener("pagehide", flushOnLeave);
    // Another tab changed the leagues: pick up their version, unless this one has unsaved edits.
    // If it switched profiles, first save the edits here to the profile they were made in.
    Store.onChange(async () => {
      if (!loaded) {
        if (loadError) loadLeagues();
        return;
      }
      if (Store.profileKey() !== profile) await flush();
      else if (dirty || inFlight) return;
      const key = Store.profileKey();
      let result;
      try {
        result = await Store.list();
      } catch {
        return;
      }
      if (dirty || inFlight || key !== Store.profileKey()) return; // a newer edit or switch; try again then
      profile = key;
      leagues = result.leagues;
      useLeague(leagues.find((x) => x.id === team.id) || Store.activeLeague(leagues));
    });
  }

  // Name, steppers, and search are off until the leagues have loaded (and, for the search,
  // until there are players to find).
  function renderEditing() {
    els.name.disabled = !loaded;
    els.reset.disabled = !loaded;
    els.search.disabled = !loaded || !playersReady;
    renderSteppers();
    renderRoster();
  }

  async function loadLeagues(retrying = false) {
    loadError = null;
    renderPicker();
    const key = Store.profileKey();
    try {
      leagues = (await Store.list()).leagues;
    } catch (error) {
      loadError = error.message;
      renderPicker();
      if (retrying) $("leagues-retry")?.focus();
      return;
    }
    loaded = true;
    profile = key;
    useLeague(Store.activeLeague(leagues));
    renderEditing();
    if (!Store.isPersistent()) {
      say("This browser isn't letting the page keep your profile key, so your team is only reachable until you leave.", true);
    } else if (retrying) {
      say("Leagues loaded.");
      ($("league-select") || els.name).focus();
    }
  }

  async function init() {
    bind();
    useLeague(null);
    renderEditing();
    const search = createSearch({
      input: els.search,
      list: els.results,
      players: () => players,
      allowed: (p) => !team.roster.includes(p.id),
      onPick: addPlayer,
      full: () => (team.roster.length >= Store.MAX_ROSTER
        ? `A roster can hold up to ${Store.MAX_ROSTER} players. Remove one to add another.` : null),
    });
    loadLeagues();
    try {
      const [pool, meta] = await Promise.all([loadPlayers(), loadMeta()]);
      players = pool;
      firstWeek = meta?.current_week ?? 1;
    } catch (error) {
      els.roster.replaceChildren(el("p", "result-error", error.message));
      els.search.placeholder = "Players couldn't be loaded";
      return;
    }
    // The search stays disabled until there are players to find, so nothing typed is lost.
    playersReady = true;
    els.search.placeholder = "Search players or teams to add";
    renderEditing();
    search.render();
  }

  init();
})();
