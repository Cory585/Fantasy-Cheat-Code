"use strict";

// My team: a roster and lineup spots saved in this browser (see /team-store.js). Every
// change saves right away; the start/sit page reads the same entry to set a lineup.

(() => {
  const { el, loadPlayers, loadMeta, avatar, profileUrl, createSearch, POSITIONS } = window.PlayerUI;
  const Store = window.TeamStore;
  const $ = (id) => document.getElementById(id);
  const els = {
    status: $("save-status"),
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

  let team = Store.load();
  let players = new Map();
  let clearArmed = null;
  let firstWeek = 1; // byes before the current week are already past

  function persist(message = "Saved in this browser.") {
    const ok = Store.save(team);
    els.status.textContent = ok ? message : "This browser isn't allowing the page to save. Changes last until you leave.";
    els.status.classList.toggle("is-error", !ok);
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
      minus.disabled = team.slots[slot] <= 0;
      const count = el("output", "stepper-count", String(team.slots[slot]));
      count.setAttribute("aria-label", `${slot} spots`);
      const plus = el("button", "stepper-btn", "+");
      plus.type = "button";
      plus.dataset.slot = slot;
      plus.dataset.step = "1";
      plus.setAttribute("aria-label", `More ${slot} spots`);
      plus.disabled = team.slots[slot] >= Store.MAX_SLOT;
      row.append(label, minus, count, plus);
      return row;
    }));
    const starters = Object.values(team.slots).reduce((a, b) => a + b, 0);
    els.summary.textContent = starters
      ? `${starters} starters: ${Store.SLOTS.filter((s) => team.slots[s]).map((s) => `${team.slots[s]} ${s}`).join(", ")}.`
      : "Add at least one lineup spot.";
  }

  function changeSlot(slot, step) {
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
    els.clear.disabled = team.roster.length === 0 && !team.name;
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
    if (team.roster.includes(player.id) || team.roster.length >= Store.MAX_ROSTER) return;
    team.roster.push(player.id);
    persist(`Added ${player.name}.`);
    renderRoster();
    renderLink();
  }

  function removePlayer(id) {
    const name = players.get(id)?.name || "player";
    team.roster = team.roster.filter((x) => x !== id);
    persist(`Removed ${name}.`);
    renderRoster();
    renderLink();
    els.search.focus();
  }

  function renderLink() {
    const ready = team.roster.length > 0 && Object.values(team.slots).some((n) => n > 0);
    els.toLineup.classList.toggle("is-disabled", !ready);
    els.toLineup.setAttribute("aria-disabled", String(!ready));
  }

  // ---------- Events ----------

  function bind() {
    els.name.value = team.name;
    els.name.addEventListener("input", () => {
      team.name = els.name.value.slice(0, Store.MAX_NAME);
      persist();
      els.clear.disabled = team.roster.length === 0 && !team.name;
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
          els.clear.textContent = "Clear team";
        }, 3000);
        return;
      }
      clearTimeout(clearArmed);
      clearArmed = null;
      els.clear.textContent = "Clear team";
      Store.clear();
      team = Store.load();
      els.name.value = "";
      els.status.textContent = "Team cleared.";
      renderSteppers();
      renderRoster();
      renderLink();
    });
    els.toLineup.addEventListener("click", (event) => {
      if (els.toLineup.getAttribute("aria-disabled") === "true") {
        event.preventDefault();
        els.status.textContent = "Add players to your roster first.";
        els.search.focus();
      }
    });
    // Another tab changed the team: pick up its version.
    window.addEventListener("storage", (event) => {
      if (event.key !== Store.KEY) return;
      team = Store.load();
      els.name.value = team.name;
      renderSteppers();
      renderRoster();
      renderLink();
    });
  }

  async function init() {
    bind();
    renderSteppers();
    renderRoster();
    renderLink();
    createSearch({
      input: els.search,
      list: els.results,
      players: () => players,
      allowed: (p) => !team.roster.includes(p.id),
      onPick: addPlayer,
      full: () => (team.roster.length >= Store.MAX_ROSTER
        ? `A roster can hold up to ${Store.MAX_ROSTER} players. Remove one to add another.` : null),
    });
    try {
      const [loaded, meta] = await Promise.all([loadPlayers(), loadMeta()]);
      players = loaded;
      firstWeek = meta?.current_week ?? 1;
    } catch (error) {
      els.roster.replaceChildren(el("p", "result-error", error.message));
      return;
    }
    renderRoster();
  }

  init();
})();
