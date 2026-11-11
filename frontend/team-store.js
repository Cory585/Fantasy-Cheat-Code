"use strict";

// The user's saved team (name, lineup spots, and roster), kept in this browser's storage.
// There are no accounts: the team lives on this device only. Every read and write is
// guarded, since storage can be blocked (private windows, strict settings) or hold junk.
// Exposes `window.TeamStore`.

window.TeamStore = (() => {
  const KEY = "fcc.team.v1";
  const MAX_ROSTER = 40;
  const MAX_SLOT = 5;
  const MAX_NAME = 40;
  const SLOTS = ["QB", "RB", "WR", "TE", "FLEX", "SUPERFLEX", "K", "DST"];
  const DEFAULT_SLOTS = { QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1, SUPERFLEX: 0, K: 1, DST: 1 };
  const ID_PATTERN = /^[A-Za-z0-9_-]{1,32}$/;

  const empty = () => ({ name: "", slots: { ...DEFAULT_SLOTS }, roster: [], updated: null });

  // Keep only well-formed values, so a hand-edited or old entry can't break the pages.
  function clean(raw) {
    const team = empty();
    if (!raw || typeof raw !== "object") return team;
    if (typeof raw.name === "string") team.name = raw.name.slice(0, MAX_NAME);
    if (raw.slots && typeof raw.slots === "object") {
      for (const slot of SLOTS) {
        const n = Number(raw.slots[slot]);
        if (Number.isInteger(n) && n >= 0 && n <= MAX_SLOT) team.slots[slot] = n;
      }
    }
    if (Array.isArray(raw.roster)) {
      team.roster = [...new Set(raw.roster.filter((id) => typeof id === "string" && ID_PATTERN.test(id)))]
        .slice(0, MAX_ROSTER);
    }
    if (typeof raw.updated === "string") team.updated = raw.updated;
    return team;
  }

  function load() {
    try {
      const text = localStorage.getItem(KEY);
      return text ? clean(JSON.parse(text)) : empty();
    } catch {
      return empty();
    }
  }

  // Returns false if the browser wouldn't save it.
  function save(team) {
    const value = clean({ ...team, updated: new Date().toISOString() });
    try {
      localStorage.setItem(KEY, JSON.stringify(value));
      return true;
    } catch {
      return false;
    }
  }

  function clear() {
    try {
      localStorage.removeItem(KEY);
      return true;
    } catch {
      return false;
    }
  }

  const hasTeam = (team = load()) => team.roster.length > 0;

  return { KEY, SLOTS, DEFAULT_SLOTS, MAX_ROSTER, MAX_SLOT, MAX_NAME, load, save, clear, clean, hasTeam };
})();
