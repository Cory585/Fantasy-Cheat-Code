"use strict";

// Shared player helpers and a search combobox, used by the start/sit and team pages.
// Exposes `window.PlayerUI`. Player names come from outside providers, so everything
// here inserts them as text, never as markup.

window.PlayerUI = (() => {
  const POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"];
  const MAX_RESULTS = 25;
  const ESPN_LOGO_CODES = { WAS: "wsh" };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  // fetch() that turns network failures into a plain-language error.
  async function request(url, options) {
    try {
      return await fetch(url, options);
    } catch {
      throw new Error("Couldn't reach the server.");
    }
  }

  async function errorMessage(response, fallback) {
    try {
      const body = await response.json();
      if (typeof body.detail === "string") return body.detail;
    } catch {
      // not JSON
    }
    return `${fallback} (the server returned HTTP ${response.status}).`;
  }

  async function loadPlayers() {
    const response = await request("/players");
    if (!response.ok) throw new Error(await errorMessage(response, "Couldn't load players"));
    return new Map((await response.json()).map((p) => [p.id, p]));
  }

  async function loadMeta() {
    try {
      const response = await request("/meta");
      return response.ok ? await response.json() : null;
    } catch {
      return null;
    }
  }

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
    const wrap = el("span", `avatar avatar-${size}`);
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

  const profileUrl = (id) => `/player/?id=${encodeURIComponent(id)}`;

  function normalize(text) {
    return text.toLowerCase().normalize("NFD").replace(/[^a-z0-9 ]/g, "");
  }

  /**
   * A combobox that searches players by name or team.
   *   input, list:  the <input role="combobox"> and its <ul role="listbox">
   *   players():    Map or array of players to search
   *   allowed(p):   whether a player can be picked (e.g. eligible and not already chosen)
   *   onPick(p):    called with the chosen player
   *   full():       optional; a message to show instead of results when nothing more can be added
   */
  function createSearch({ input, list, players, allowed = () => true, onPick, full = () => null }) {
    const state = { active: -1, results: [], open: false };
    const idPrefix = `${list.id || "results"}-opt`;

    function search() {
      const q = normalize(input.value.trim());
      return [...players().values()]
        .filter(allowed)
        .filter((p) => !q || normalize(p.name).includes(q) || p.team.toLowerCase() === q)
        .sort((x, y) => y.ros_projection - x.ros_projection)
        .slice(0, MAX_RESULTS);
    }

    function option(player, index) {
      const item = el("li", "search-option");
      item.id = `${idPrefix}-${index}`;
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", String(index === state.active));
      const text = el("span", "search-option-text");
      text.append(
        el("span", "search-option-name", player.name),
        el("span", "search-option-meta", [player.position, player.team, player.injury_status].filter(Boolean).join(", ")),
      );
      item.append(avatar(player, "sm"), text, el("span", "search-option-proj", player.ros_projection.toFixed(1)));
      // mousedown (not click) so the input doesn't blur and close the list first.
      item.addEventListener("mousedown", (event) => {
        event.preventDefault();
        pick(player);
      });
      return item;
    }

    function render() {
      state.results = search();
      if (state.active >= state.results.length) state.active = state.results.length - 1;
      const show = state.open && document.activeElement === input;
      list.hidden = !show;
      input.setAttribute("aria-expanded", String(show));
      input.removeAttribute("aria-activedescendant");
      if (!show) return;
      const fullMessage = full();
      if (fullMessage) {
        list.replaceChildren(el("li", "search-empty", fullMessage));
        return;
      }
      if (state.results.length === 0) {
        const query = input.value.trim();
        list.replaceChildren(el("li", "search-empty", query ? `No players match "${query}".` : "No players left to add."));
        return;
      }
      list.replaceChildren(...state.results.map(option));
      if (state.active >= 0) {
        input.setAttribute("aria-activedescendant", `${idPrefix}-${state.active}`);
        list.children[state.active]?.scrollIntoView({ block: "nearest" });
      }
    }

    function setOpen(open) {
      state.open = open;
      if (!open) state.active = -1;
      render();
    }

    function pick(player) {
      input.value = "";
      onPick(player);
      state.active = -1;
      render();
    }

    input.addEventListener("input", () => {
      state.active = -1;
      setOpen(true);
    });
    input.addEventListener("click", () => setOpen(true));
    input.addEventListener("blur", () => setOpen(false));
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (!state.open) {
          state.open = true;
          render();
        }
        const total = state.results.length;
        const step = event.key === "ArrowDown" ? 1 : -1;
        state.active = total ? (state.active + step + total) % total : -1;
        if (event.key === "ArrowUp" && state.active === -1) state.active = total - 1;
        render();
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (full()) return;
        // Search again: the players may have arrived since the list was last drawn.
        state.results = search();
        const choice = state.results[state.active >= 0 ? state.active : 0];
        if (choice) pick(choice);
      } else if (event.key === "Escape" && state.open) {
        event.preventDefault();
        setOpen(false);
      }
    });

    return { render, setOpen };
  }

  return { POSITIONS, el, request, errorMessage, loadPlayers, loadMeta, avatar, profileUrl, createSearch };
})();
