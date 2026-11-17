"use strict";

// Profile: the leagues on this browser's profile (see /league-store.js). Add, rename, and
// remove leagues, open one on My team or Start/sit, and copy or switch the profile key.

(() => {
  const Store = window.LeagueStore;
  const $ = (id) => document.getElementById(id);
  const els = {
    status: $("profile-status"),
    count: $("league-count"),
    create: $("league-create"),
    name: $("league-name"),
    add: $("league-add"),
    list: $("league-list"),
    key: $("profile-key"),
    keyShow: $("key-show"),
    keyCopy: $("key-copy"),
    keyForm: $("key-form"),
    keyInput: $("key-input"),
  };
  els.keySwitch = els.keyForm.querySelector('button[type="submit"]');

  const { el } = window.PlayerUI;
  const state = { leagues: [], max: Store.MAX_LEAGUES, loaded: false, busy: false, renaming: null };
  let removeArmed = null; // { id, timer }

  function say(message, isError = false) {
    els.status.textContent = message;
    els.status.classList.toggle("is-error", isError);
  }

  const isFull = () => state.leagues.length >= state.max;
  const starters = (league) => Object.values(league.slots).reduce((a, b) => a + b, 0);
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  function updated(iso) {
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? "" : `updated ${date.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
  }

  // ---------- Leagues ----------

  function render() {
    els.count.textContent = state.loaded ? `${state.leagues.length} of ${state.max} leagues` : "Loading…";
    const full = isFull();
    els.add.disabled = !state.loaded || full || state.busy;
    els.name.disabled = !state.loaded || full;
    els.name.placeholder = full ? `Limit of ${state.max} reached` : "League name";
    if (!state.loaded) return;
    if (!state.leagues.length) {
      els.list.replaceChildren(el("li", "roster-empty", "No leagues yet. Name one above to start."));
      return;
    }
    const active = Store.activeLeague(state.leagues);
    els.list.replaceChildren(...state.leagues.map((league) => card(league, league.id === active?.id)));
    if (full) {
      els.list.append(el("li", "section-note league-full",
        `That's the limit of ${state.max} leagues. Remove one to add another.`));
    }
  }

  function card(league, isActive) {
    const item = el("li", `league-card${isActive ? " is-active" : ""}`);
    item.dataset.id = league.id;
    const head = el("div", "league-card-head");
    if (state.renaming === league.id) {
      head.append(renameForm(league));
    } else {
      const title = el("h3", "league-card-name", league.name);
      head.append(title);
      if (isActive) head.append(el("span", "league-badge", "Last used"));
    }
    const meta = el("p", "league-card-meta",
      [plural(league.roster.length, "player"), plural(starters(league), "starter"), updated(league.updated)]
        .filter(Boolean).join(" · "));
    const actions = el("div", "league-card-actions");
    const edit = el("a", "btn btn-quiet btn-small", "Edit roster");
    edit.href = `/team/?league=${encodeURIComponent(league.id)}`;
    const lineup = el("a", "btn btn-quiet btn-small", "Set lineup");
    lineup.href = `/start-sit/?mode=lineup&league=${encodeURIComponent(league.id)}`;
    for (const link of [edit, lineup]) link.addEventListener("click", () => Store.setActive(league.id));
    const rename = el("button", "btn btn-quiet btn-small", "Rename");
    rename.type = "button";
    rename.setAttribute("aria-label", `Rename ${league.name}`);
    rename.addEventListener("click", () => startRename(league.id));
    const armed = removeArmed?.id === league.id;
    const remove = el("button", "btn btn-quiet btn-small league-remove", armed ? "Click again to remove" : "Remove");
    remove.type = "button";
    remove.setAttribute("aria-label", armed ? `Confirm removing ${league.name}` : `Remove ${league.name}`);
    remove.disabled = state.busy;
    remove.addEventListener("click", () => removeLeague(league));
    actions.append(edit, lineup, rename, remove);
    item.append(head, meta, actions);
    return item;
  }

  function renameForm(league) {
    const form = el("form", "league-rename");
    const input = el("input", "team-name-input");
    input.value = league.name;
    input.maxLength = Store.MAX_NAME;
    input.required = true;
    input.setAttribute("aria-label", `New name for ${league.name}`);
    const save = el("button", "btn btn-cta btn-small", "Save");
    save.type = "submit";
    const cancel = el("button", "btn btn-quiet btn-small", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", () => endRename(league.id));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") endRename(league.id);
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      renameLeague(league, input.value);
    });
    form.append(input, save, cancel);
    requestAnimationFrame(() => {
      input.focus();
      input.select();
    });
    return form;
  }

  function startRename(id) {
    state.renaming = id;
    render();
  }

  function endRename(id) {
    state.renaming = null;
    render();
    els.list.querySelector(`[data-id="${CSS.escape(id)}"] button[aria-label^="Rename"]`)?.focus();
  }

  // Runs one change at a time, so a double click can't send it twice.
  async function busy(work) {
    if (state.busy) return;
    state.busy = true;
    render();
    try {
      await work();
    } catch (error) {
      say(error.message, true);
    } finally {
      state.busy = false;
      render();
    }
  }

  function addLeague(event) {
    event.preventDefault();
    const name = els.name.value.trim();
    if (!name) {
      say("Give the league a name.", true);
      els.name.focus();
      return;
    }
    busy(async () => {
      const league = await Store.create({ name });
      state.leagues.push(league);
      Store.setActive(league.id);
      els.name.value = "";
      say(`Added ${league.name}. Open Edit roster to fill in your team.`);
    }).then(() => els.name.focus());
  }

  function renameLeague(league, value) {
    const name = value.trim();
    if (!name) {
      say("Give the league a name.", true);
      return;
    }
    if (name === league.name) {
      endRename(league.id);
      return;
    }
    busy(async () => {
      const saved = await Store.save(league.id, { ...league, name });
      state.leagues = state.leagues.map((x) => (x.id === saved.id ? saved : x));
      state.renaming = null;
      say(`Renamed to ${saved.name}.`);
    });
  }

  // Two clicks to remove, so a stray click can't throw away a roster.
  function removeLeague(league) {
    if (removeArmed?.id !== league.id) {
      if (removeArmed) clearTimeout(removeArmed.timer);
      removeArmed = { id: league.id, timer: setTimeout(() => { removeArmed = null; render(); }, 3000) };
      render();
      els.list.querySelector(`[data-id="${CSS.escape(league.id)}"] .league-remove`)?.focus();
      return;
    }
    clearTimeout(removeArmed.timer);
    removeArmed = null;
    busy(async () => {
      await Store.remove(league.id);
      state.leagues = state.leagues.filter((x) => x.id !== league.id);
      say(`Removed ${league.name}.`);
    });
  }

  // Loads can overlap (a key switch here plus a change in another tab); only the latest one
  // may show its leagues, or an older profile's list could replace the current one.
  let loadId = 0;

  async function load() {
    const id = ++loadId;
    try {
      const { leagues, max } = await Store.list();
      if (id !== loadId) return;
      state.leagues = leagues;
      state.max = max;
      state.loaded = true;
      if (!Store.isPersistent()) {
        say("This browser isn't letting the page keep your profile key, so these leagues are only reachable until you leave.", true);
      }
    } catch (error) {
      if (id !== loadId) return;
      els.list.replaceChildren(el("li", "result-error", error.message));
      els.count.textContent = "";
      return;
    }
    render();
  }

  // ---------- Profile key ----------

  function showKey() {
    els.key.value = Store.profileKey();
  }

  function setKeyShown(shown) {
    els.key.type = shown ? "text" : "password";
    els.keyShow.textContent = shown ? "Hide" : "Show";
    els.keyShow.setAttribute("aria-pressed", String(shown));
  }

  function revealKey() {
    setKeyShown(true);
    els.key.select();
  }

  let switchArmed = null; // the key a switch is waiting to be confirmed for

  function disarmSwitch() {
    switchArmed = null;
    els.keySwitch.textContent = "Switch profile";
  }

  function bindKey() {
    els.keyShow.addEventListener("click", () => setKeyShown(els.key.type !== "text"));
    els.keyCopy.addEventListener("click", async () => {
      if (await window.PlayerUI.copyText(Store.profileKey())) {
        say("Profile key copied. Paste it on this page on your other device.");
      } else {
        // Show and select the key so it can be copied by hand.
        revealKey();
        say("Copy the selected key.");
      }
    });
    els.keyInput.addEventListener("input", disarmSwitch);
    els.keyForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const key = els.keyInput.value.trim();
      if (key === Store.profileKey()) {
        disarmSwitch();
        say("That's already this browser's profile.");
        return;
      }
      if (!Store.isValidKey(key)) {
        disarmSwitch();
        say("That doesn't look like a profile key. Copy it again from the other device.", true);
        els.keyInput.focus();
        return;
      }
      // The current key is the only way back to this profile's leagues, so show it and ask
      // first (unless there's nothing here to lose).
      const nothingToLose = state.loaded && !state.leagues.length;
      if (!nothingToLose && switchArmed !== key) {
        switchArmed = key;
        els.keySwitch.textContent = "Confirm switch";
        revealKey();
        say("Switching leaves this profile's leagues behind. Copy its key (shown above) first if you want to come back "
          + "to them, then press Confirm switch.");
        return;
      }
      disarmSwitch();
      Store.useProfileKey(key);
      els.keyInput.value = "";
      setKeyShown(false);
      showKey();
      say("Switched profiles.");
      state.loaded = false;
      render();
      load();
    });
  }

  els.create.addEventListener("submit", addLeague);
  bindKey();
  showKey();
  // Another tab changed the leagues or switched profiles.
  Store.onChange(() => {
    showKey();
    load();
  });
  render();
  load();
})();
