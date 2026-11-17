"use strict";

// The leagues on this browser's profile: up to 10, each with its own name, lineup spots,
// and roster, saved on the server under a random profile key that this browser makes and
// keeps. There are no accounts; the key is the profile (copy it from /profile/ to use the
// same leagues on another device). Exposes `window.LeagueStore`.
//
// A team saved by the old single-team version of the site (`fcc.team.v1`) is moved into
// a league the first time the profile is loaded with no leagues.
//
// Saves only overwrite the version of a league this page last saw: each save sends that
// version (If-Match), and if the league was saved somewhere else since (another tab or
// device), the server refuses with a 409. Saves of one league go out one at a time. `save` then rejects with an error whose `conflict`
// is true and whose `league` is the server's copy, and `onChange` listeners on this page run
// so they can reload. Nothing is overwritten until the page loads the newer copy.

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
  const STALE_EVENT = "fcc:leagues-stale";

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

  // `key` sends the request under that profile instead of the current one: a page that loaded
  // its leagues before another tab switched profiles keeps saving them where they live.
  async function call(method, path, body, { keepalive = false, headers = {}, key = null } = {}) {
    const options = { method, headers: { "X-Profile-Key": key || profileKey(), ...headers }, keepalive };
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
    throw await problem(response);
  }

  // An Error for a failed response, with its HTTP `status` and error `code`.
  async function problem(response) {
    let body = null;
    try {
      body = await response.json();
    } catch {
      // not JSON
    }
    const error = new Error(message(response, body));
    error.status = response.status;
    error.code = body && typeof body.code === "string" ? body.code : null;
    if (response.status === 409 && body && body.code === "league_conflict" && body.league) {
      error.conflict = true;
      error.league = body.league;
    }
    return error;
  }

  function message(response, body) {
    if (response.status === 429) {
      const wait = Number(response.headers.get("Retry-After"));
      return Number.isFinite(wait) && wait > 0
        ? `Too many league changes at once. Try again in ${wait} second${wait === 1 ? "" : "s"}.`
        : "Too many league changes at once. Try again shortly.";
    }
    if (body && body.code === "league_conflict") {
      return "This league was changed in another tab or on another device. Reload to see the latest version.";
    }
    // The server's own explanation, when it sent one.
    if (body && typeof body.detail === "string" && body.detail.trim()) return body.detail.trim();
    if (response.status === 422) return "That league couldn't be saved: check its name, spots, and roster.";
    if (response.status >= 500) return "Your leagues can't be loaded or saved right now. Try again in a moment.";
    return "Something went wrong with your leagues. Reload the page and try again.";
  }

  // ---------- Versions ----------

  // The version of each league this page last got from the server, and the save of each
  // league still on its way. A league's saves go out one at a time: a save made while another
  // is in flight (like the one sent as the page closes) waits for it, then sends the version
  // that save came back with. Versions are never guessed, so if the earlier save failed or was
  // refused, the later one is refused too instead of overwriting someone else's save.
  const versions = new Map();
  const inFlight = new Map();

  function remember(league) {
    if (league && typeof league.id === "string" && Number.isInteger(league.version)) {
      versions.set(league.id, league.version);
    }
    return league;
  }

  function ifMatch(id) {
    if (!versions.has(id)) return {}; // never loaded here: save as before, without a check
    return { "If-Match": `"${versions.get(id)}"` };
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
    const league = remember(await call("POST", "/leagues", body({ ...legacy, name: legacy.name || "My league" })));
    write(LEGACY_TEAM_KEY, null);
    write(ACTIVE_KEY, league.id);
    return [league];
  }

  async function list() {
    const result = await call("GET", "/leagues");
    result.leagues.forEach(remember);
    let leagues = result.leagues;
    try {
      leagues = await migrateLegacyTeam(leagues);
    } catch {
      // Leave the old team where it is and try again next time.
      migrated = false;
    }
    return { leagues, max: result.max_leagues ?? MAX_LEAGUES };
  }

  // `options`: `keepalive` (for a save sent as the page closes) and `key` (see `call`), as for `save`.
  async function create(league, options = {}) {
    const made = remember(await call("POST", "/leagues", body(league), options));
    announce();
    return made;
  }

  // Rejects with `error.conflict` (and the server's copy in `error.league`) if the league was
  // saved somewhere else since this page loaded it.
  function save(id, league, options = {}) {
    const earlier = inFlight.get(id);
    // With nothing in flight, send right away (synchronously, so a save made as the page
    // closes goes out before it does); otherwise once the earlier save has settled.
    const run = earlier ? earlier.then(() => send(id, league, options)) : send(id, league, options);
    const settled = run.catch(() => {});
    inFlight.set(id, settled);
    settled.then(() => {
      if (inFlight.get(id) === settled) inFlight.delete(id);
    });
    return run;
  }

  async function send(id, league, options) {
    let saved;
    try {
      saved = await call("PUT", `/leagues/${encodeURIComponent(id)}`, body(league), { ...options, headers: ifMatch(id) });
    } catch (error) {
      // Tell this page's listeners to reload, after the caller has handled the error.
      if (error.conflict) setTimeout(() => window.dispatchEvent(new Event(STALE_EVENT)), 0);
      throw error;
    }
    remember(saved);
    announce();
    return saved;
  }

  async function remove(id) {
    await call("DELETE", `/leagues/${encodeURIComponent(id)}`);
    versions.delete(id);
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

  // Runs `callback` when another tab changes the leagues or switches profiles, or when a save
  // here finds the league was changed somewhere else.
  function onChange(callback) {
    window.addEventListener("storage", (event) => {
      if (event.key === CHANGED_KEY || event.key === PROFILE_KEY) callback();
    });
    window.addEventListener(STALE_EVENT, () => callback());
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
