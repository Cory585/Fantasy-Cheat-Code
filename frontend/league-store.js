"use strict";

// The leagues on this browser's profile: up to 10, each with its own name, lineup spots,
// and roster, saved on the server under a random profile key that this browser makes and
// keeps. There are no accounts; the key is the profile (copy it from /profile/ to use the
// same leagues on another device). Exposes `window.LeagueStore`.
//
// A team saved by the old single-team version of the site (`fcc.team.v1`) is moved into
// a league the first time the profile is loaded with no leagues.

window.LeagueStore = (() => {
  const PROFILE_KEY = "fcc.profile.v1";
  const ACTIVE_KEY = "fcc.league.active";
  const CHANGED_KEY = "fcc.leagues.changed";
  const LEGACY_TEAM_KEY = "fcc.team.v1";
  const MAX_LEAGUES = 10;
  const MAX_ROSTER = 40;
  const MAX_SLOT = 5;
  const MAX_NAME = 40;
  const SLOTS = ["QB", "RB", "WR", "TE", "FLEX", "SUPERFLEX", "K", "DST"];
  const DEFAULT_SLOTS = { QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1, SUPERFLEX: 0, K: 1, DST: 1 };
  const ID_PATTERN = /^[A-Za-z0-9_-]{1,32}$/;
  const KEY_PATTERN = /^[A-Za-z0-9_-]{32,128}$/;

  // ---------- Browser storage (always guarded: it can be blocked or hold junk) ----------

  function read(key) {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  }

  function write(key, value) {
    try {
      if (value == null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
      return true;
    } catch {
      return false;
    }
  }

  // ---------- Profile key ----------

  function randomKey() {
    const bytes = crypto.getRandomValues(new Uint8Array(32));
    return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  let memoryKey = null;
  // False when the browser won't keep the key, so leagues made here last only for this visit.
  let persistent = true;

  function profileKey() {
    const stored = read(PROFILE_KEY);
    if (stored && KEY_PATTERN.test(stored)) return stored;
    if (!memoryKey) {
      memoryKey = randomKey();
      persistent = write(PROFILE_KEY, memoryKey);
    }
    return memoryKey;
  }

  const isValidKey = (key) => typeof key === "string" && KEY_PATTERN.test(key.trim());

  // Switch this browser to another profile (e.g. one made on another device).
  function useProfileKey(key) {
    if (!isValidKey(key)) return false;
    memoryKey = key.trim();
    persistent = write(PROFILE_KEY, memoryKey);
    write(ACTIVE_KEY, null);
    announce();
    return true;
  }

  // ---------- Requests ----------

  async function call(method, path, body, { keepalive = false } = {}) {
    const options = { method, headers: { "X-Profile-Key": profileKey() }, keepalive };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(path, options);
    } catch {
      throw new Error("Couldn't reach the server.");
    }
    if (response.ok) return response.status === 204 ? null : response.json();
    throw new Error(await problem(response));
  }

  async function problem(response) {
    if (response.status === 429) {
      const wait = Number(response.headers.get("Retry-After"));
      return Number.isFinite(wait) && wait > 0
        ? `Too many league changes at once. Try again in ${wait} second${wait === 1 ? "" : "s"}.`
        : "Too many league changes at once. Try again shortly.";
    }
    try {
      const body = await response.json();
      if (typeof body.detail === "string") return body.detail;
      if (response.status === 422) return "That league couldn't be saved: check its name, spots, and roster.";
    } catch {
      // not JSON
    }
    return `The server returned HTTP ${response.status}.`;
  }

  // Keep only well-formed values, so a hand-edited entry can't break a request.
  function clean(raw) {
    const league = { name: "", slots: { ...DEFAULT_SLOTS }, roster: [] };
    if (!raw || typeof raw !== "object") return league;
    if (typeof raw.name === "string") league.name = raw.name.trim().slice(0, MAX_NAME);
    if (raw.slots && typeof raw.slots === "object") {
      for (const slot of SLOTS) {
        const n = Number(raw.slots[slot]);
        if (Number.isInteger(n) && n >= 0 && n <= MAX_SLOT) league.slots[slot] = n;
      }
    }
    if (Array.isArray(raw.roster)) {
      league.roster = [...new Set(raw.roster.filter((id) => typeof id === "string" && ID_PATTERN.test(id)))]
        .slice(0, MAX_ROSTER);
    }
    return league;
  }

  const body = (league) => {
    const { name, slots, roster } = clean(league);
    return { name, slots, roster };
  };

  // Tell other tabs that the leagues changed (they listen with `onChange`).
  function announce() {
    write(CHANGED_KEY, `${Date.now()}-${Math.random()}`);
  }

  let migrated = false;

  async function migrateLegacyTeam(leagues) {
    if (migrated) return leagues;
    migrated = true;
    const text = read(LEGACY_TEAM_KEY);
    if (!text) return leagues;
    let legacy = null;
    try {
      legacy = clean(JSON.parse(text));
    } catch {
      write(LEGACY_TEAM_KEY, null); // unreadable: nothing to move
      return leagues;
    }
    if (leagues.length || (!legacy.roster.length && !legacy.name)) {
      if (!leagues.length) write(LEGACY_TEAM_KEY, null);
      return leagues;
    }
    const league = await call("POST", "/leagues", body({ ...legacy, name: legacy.name || "My league" }));
    write(LEGACY_TEAM_KEY, null);
    write(ACTIVE_KEY, league.id);
    return [league];
  }

  async function list() {
    const result = await call("GET", "/leagues");
    let leagues = result.leagues;
    try {
      leagues = await migrateLegacyTeam(leagues);
    } catch {
      // Leave the old team where it is and try again next time.
      migrated = false;
    }
    return { leagues, max: result.max_leagues ?? MAX_LEAGUES };
  }

  async function create(league) {
    const made = await call("POST", "/leagues", body(league));
    announce();
    return made;
  }

  async function save(id, league, options) {
    const saved = await call("PUT", `/leagues/${encodeURIComponent(id)}`, body(league), options);
    announce();
    return saved;
  }

  async function remove(id) {
    await call("DELETE", `/leagues/${encodeURIComponent(id)}`);
    if (read(ACTIVE_KEY) === id) write(ACTIVE_KEY, null);
    announce();
  }

  // ---------- The league a page shows ----------

  // A `?league=` link wins, then the last league picked in this browser, then the oldest.
  function activeLeague(leagues) {
    const wanted = [new URLSearchParams(location.search).get("league"), read(ACTIVE_KEY)];
    for (const id of wanted) {
      const found = id && leagues.find((league) => league.id === id);
      if (found) return found;
    }
    return leagues[0] || null;
  }

  function setActive(id) {
    write(ACTIVE_KEY, id);
  }

  const hasTeam = (league) => Boolean(league && league.roster.length > 0);

  // Runs `callback` when another tab changes the leagues or switches profiles.
  function onChange(callback) {
    window.addEventListener("storage", (event) => {
      if (event.key === CHANGED_KEY || event.key === PROFILE_KEY) callback();
    });
  }

  // A labeled league <select> plus a link to manage leagues, for pages that show a team.
  // Returns the wrapper; `onSelect(id)` runs when the choice changes.
  function picker({ id, leagues, active, onSelect }) {
    const wrap = document.createElement("div");
    wrap.className = "league-picker";
    const label = document.createElement("label");
    label.className = "league-picker-label";
    label.htmlFor = id;
    label.textContent = "League";
    const select = document.createElement("select");
    select.id = id;
    select.className = "league-select";
    for (const league of leagues) {
      const option = document.createElement("option");
      option.value = league.id;
      option.textContent = league.name;
      select.append(option);
    }
    if (active) select.value = active.id;
    select.disabled = leagues.length < 2;
    select.addEventListener("change", () => {
      setActive(select.value);
      onSelect(select.value);
    });
    const manage = document.createElement("a");
    manage.className = "league-manage";
    manage.href = "/profile/";
    manage.textContent = "Manage leagues";
    wrap.append(label, select, manage);
    return wrap;
  }

  return {
    SLOTS, DEFAULT_SLOTS, MAX_LEAGUES, MAX_ROSTER, MAX_SLOT, MAX_NAME,
    profileKey, useProfileKey, isValidKey, isPersistent: () => (profileKey(), persistent),
    list, create, save, remove, clean, activeLeague, setActive, hasTeam, onChange, picker,
  };
})();
