"use strict";

// Shared helpers used across the site: requests and error messages, player avatars, and a
// player search combobox.
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

  // ---------- Requests and errors ----------
  // Every page turns a failed request into the same kind of plain-language message, and
  // shows it in the same error block with a "Try again" button (errorBlock below).

  const OFFLINE_MESSAGE = "Couldn't reach the server. Check your connection, then try again.";
  const MAX_RETRY_WAIT = 120; // seconds; a longer Retry-After is capped

  // An error whose message can be shown to the person as is. `retryAfter` is the number of
  // seconds to wait before trying again (from a 429's Retry-After), or 0.
  class RequestError extends Error {
    constructor(message, { status = 0, retryAfter = 0 } = {}) {
      super(message);
      this.status = status;
      this.retryAfter = retryAfter;
    }
  }

  // fetch() that turns network failures into a plain-language error.
  async function request(url, options) {
    try {
      return await fetch(url, options);
    } catch {
      throw new RequestError(OFFLINE_MESSAGE);
    }
  }

  // Retry-After is either seconds or an HTTP date.
  function retryAfterSeconds(response) {
    const value = response.headers.get("Retry-After");
    if (!value) return 0;
    let seconds = Number(value);
    if (!Number.isFinite(seconds)) {
      const date = Date.parse(value);
      seconds = Number.isNaN(date) ? 0 : (date - Date.now()) / 1000;
    }
    return Math.min(Math.max(Math.ceil(seconds), 0), MAX_RETRY_WAIT);
  }

  // FastAPI's `detail`: a sentence, or (for invalid input) a list of {msg}.
  function detailText(body) {
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail)) {
      const messages = body.detail.map((d) => d?.msg).filter((m) => typeof m === "string");
      if (messages.length) return messages.join("; ");
    }
    return null;
  }

  // A response that wasn't ok, as a RequestError. `what` says what failed ("Couldn't load
  // tiers") for when the server gives no reason of its own.
  async function responseError(response, what = "Something went wrong") {
    if (response.status === 429) {
      const wait = retryAfterSeconds(response);
      const when = wait ? `in ${wait} second${wait === 1 ? "" : "s"}` : "in a moment";
      return new RequestError(`Too many requests just now. Try again ${when}.`, { status: 429, retryAfter: wait });
    }
    let body = null;
    try {
      body = await response.json();
    } catch {
      // not JSON
    }
    const reason = detailText(body);
    if (reason) return new RequestError(reason, { status: response.status });
    const tail = response.status >= 500 ? "Something went wrong on our end. Try again in a moment." : "Try again in a moment.";
    return new RequestError(`${what}. ${tail}`, { status: response.status });
  }

  async function errorMessage(response, what) {
    return (await responseError(response, what)).message;
  }

  // GET (or `options`) a URL and return its JSON, or throw a RequestError to show the person.
  async function getJson(url, what, options) {
    const response = await request(url, options);
    if (!response.ok) throw await responseError(response, what);
    try {
      return await response.json();
    } catch {
      throw new RequestError("The server sent a response this page couldn't read. Try again in a moment.");
    }
  }

  // Any thrown value as a message for the person: RequestErrors (and our own Errors) are
  // already worded for them; anything else is a bug, so say something generic.
  function messageOf(error) {
    if (error instanceof RequestError) return error.message;
    if (error instanceof TypeError || !error?.message) return "Something went wrong. Try again in a moment.";
    return error.message;
  }

  /**
   * The error block every page uses: the message, and a "Try again" button that calls
   * `retry` (left out when there's nothing to retry). After a 429 the button counts down
   * the Retry-After wait before it can be pressed. `tag` is "li" inside a list.
   */
  function errorBlock(error, retry, { tag = "div" } = {}) {
    const block = el(tag, "error-block");
    const text = el("p", "error-block-text", typeof error === "string" ? error : messageOf(error));
    block.append(text);
    if (tag === "div") block.setAttribute("role", "alert");
    if (!retry) return block;
    const button = el("button", "btn btn-quiet btn-small error-block-retry", "Try again");
    button.type = "button";
    button.addEventListener("click", () => {
      button.disabled = true;
      retry();
    });
    let wait = error?.retryAfter || 0;
    if (wait) {
      const tick = () => {
        button.textContent = wait > 0 ? `Try again in ${wait}s` : "Try again";
        button.disabled = wait > 0;
        wait -= 1;
      };
      tick();
      const timer = setInterval(() => {
        // Stop once the wait is over, or if the block has been replaced.
        if (wait < 0 || !button.isConnected) clearInterval(timer);
        else tick();
      }, 1000);
    }
    block.append(button);
    return block;
  }

  async function loadPlayers() {
    const players = await getJson("/players", "Couldn't load players");
    return new Map(players.map((p) => [p.id, p]));
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

  return {
    POSITIONS, el, request, RequestError, responseError, errorMessage, getJson, messageOf, errorBlock,
    loadPlayers, loadMeta, avatar, profileUrl, createSearch,
  };
})();
