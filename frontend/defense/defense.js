"use strict";

// Defense breakdown: one team's position groups, unit results, fantasy points allowed, and next
// DST projections, plus a sortable league table. Data comes from /defenses and /defenses/{team}.
// Names come from outside providers, so they're always inserted as text.

(() => {
  const { el, getJson, errorBlock, avatar, noteSampleData } = window.PlayerUI;
  const $ = (id) => document.getElementById(id);
  const els = {
    status: $("data-status"),
    select: $("team-select"),
    error: $("df-error"),
    team: $("df-team"),
    logo: $("df-logo"),
    name: $("df-team-name"),
    games: $("df-games"),
    tiles: $("df-tiles"),
    groups: $("df-groups"),
    units: $("df-units"),
    allowed: $("df-allowed"),
    upcoming: $("df-upcoming"),
    upcomingNote: $("df-upcoming-note"),
    league: $("df-league"),
    head: $("df-table-head"),
    body: $("df-table-body"),
  };
  const STORAGE_KEY = "fcc.defense.team";
  const GROUPS = [["DL", "Line"], ["LB", "Linebackers"], ["CB", "Corners"], ["S", "Safeties"]];
  const PERCENT_STATS = new Set(["sack_rate", "int_rate"]);
  const ABBREVIATIONS = { tfl: "TFL", qb_hits: "QB hits", pass_defended: "PD", ints: "INT",
                          forced_fumbles: "FF", sacks: "Sacks", tackles: "Tackles" };

  let league = null;
  let sort = { key: "overall_rank", ascending: true };
  let loading = 0; // id of the latest team request, so a slow older one can't overwrite it

  // ---------- helpers ----------

  function ordinal(n) {
    const s = ["th", "st", "nd", "rd"];
    const v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }

  // Top quarter of the league reads as a strength, bottom quarter as a weakness.
  function tone(rank, teams) {
    if (!teams) return "";
    if (rank <= Math.ceil(teams / 4)) return "is-good";
    if (rank > teams - Math.ceil(teams / 4)) return "is-bad";
    return "";
  }

  function rankPill(rank, teams, label) {
    const pill = el("span", `df-rank ${tone(rank, teams)}`, ordinal(rank));
    pill.title = `${ordinal(rank)} of ${teams}${label ? ` in ${label.toLowerCase()}` : ""}`;
    return pill;
  }

  function formatValue(key, value) {
    if (PERCENT_STATS.has(key)) return `${(value * 100).toFixed(1)}%`;
    if (key === "epa_per_dropback") return `${value > 0 ? "+" : ""}${value.toFixed(3)}`;
    if (Math.abs(value) >= 100) return value.toFixed(0);
    return value.toFixed(value >= 10 ? 1 : 2);
  }

  // A bar that fills further the better the rank (1st = full).
  function rankMeter(rank, teams) {
    const track = el("span", "df-meter");
    track.setAttribute("aria-hidden", "true");
    const fill = el("span", `df-meter-fill ${tone(rank, teams)}`);
    fill.style.width = `${teams > 1 ? Math.round(((teams - rank) / (teams - 1)) * 100) : 100}%`;
    track.append(fill);
    return track;
  }

  function teamName(summary) {
    return summary.name || summary.team;
  }

  // A failed request, with a button to try it again; showError(null) clears it.
  function showError(error, retry) {
    els.error.replaceChildren(...(error ? [errorBlock(error, retry)] : []));
    els.error.hidden = !error;
  }

  function remember(team) {
    try { localStorage.setItem(STORAGE_KEY, team); } catch { /* storage blocked: fine */ }
  }

  function recalled() {
    try { return localStorage.getItem(STORAGE_KEY); } catch { return null; }
  }

  // ---------- team breakdown ----------

  function renderHero(data) {
    const s = data.summary;
    els.logo.replaceChildren(avatar({ name: teamName(s), team: s.team, position: "DST" }, "md"));
    els.name.textContent = teamName(s);
    els.games.textContent = `${data.season} season, ${s.games} game${s.games === 1 ? "" : "s"} through week ${data.through_week}`;
    const tiles = [
      ["Position groups", `${ordinal(s.overall_rank)}`, s.overall_rank, "average of the four group ranks"],
      ["Points allowed", `${s.points_allowed_pg.toFixed(1)}`, s.points_allowed_rank, "per game"],
      ["DST points", `${s.dst_ppg.toFixed(1)}`, s.dst_rank, "per game, standard scoring"],
    ];
    els.tiles.replaceChildren(...tiles.map(([label, value, rank, note]) => {
      const tile = el("div", `df-tile ${tone(rank, league.teams.length)}`);
      const dd = el("dd", "df-tile-value", value);
      if (label !== "Position groups") dd.append(el("span", "df-tile-rank", ` · ${ordinal(rank)}`));
      tile.append(el("dt", "df-tile-label", label), dd, el("span", "df-tile-note", note));
      return tile;
    }));
  }

  function statRows(stats, groupLabel) {
    const list = el("ul", "df-stats");
    for (const stat of stats) {
      const row = el("li", "df-stat");
      const label = el("span", "df-stat-label", stat.label);
      if (stat.lower_is_better) label.title = "Lower is better";
      row.append(label, el("span", "df-stat-value", formatValue(stat.key, stat.value)),
                 rankPill(stat.rank, stat.teams, stat.label), rankMeter(stat.rank, stat.teams));
      list.append(row);
    }
    list.setAttribute("aria-label", `${groupLabel} stats with league ranks`);
    return list;
  }

  function renderGroups(groups) {
    els.groups.replaceChildren(...groups.map((group) => {
      const card = el("article", `df-card ${tone(group.rank, group.teams)}`);
      const head = el("div", "df-card-head");
      const title = el("div", "df-card-title");
      title.append(el("h3", "df-card-heading", group.label), el("span", "df-card-sub", `${group.key} · per game`));
      const big = el("div", "df-card-rank");
      big.append(el("span", "df-card-rank-num", ordinal(group.rank)), el("span", "df-card-rank-of", `of ${group.teams}`));
      head.append(title, big);
      card.append(head, statRows(group.stats, group.label));

      if (group.players.length) {
        const shown = group.stats.slice(0, 3).map((s) => s.key);
        const players = el("ol", "df-players");
        players.setAttribute("aria-label", `${group.label}: top players`);
        for (const p of group.players) {
          const item = el("li", "df-player");
          const who = el("span", "df-player-name", p.name);
          who.append(el("span", "df-player-pos", ` ${p.position}`));
          const total = (v) => (Number.isInteger(v) ? String(v) : v.toFixed(1));
          const line = shown.map((k) => `${total(p.stats[k] ?? 0)} ${ABBREVIATIONS[k] || k}`).join(" · ");
          item.append(who, el("span", "df-player-line", `${line} · ${p.games} G`));
          players.append(item);
        }
        card.append(el("h4", "df-players-heading", "Top players"), players);
      }
      return card;
    }));
  }

  function renderUnits(units) {
    els.units.replaceChildren(...units.map((unit) => {
      const card = el("article", "df-card df-unit");
      card.append(el("h3", "df-card-heading", unit.label), statRows(unit.stats, unit.label));
      return card;
    }));
  }

  function renderAllowed(rows) {
    els.allowed.replaceChildren(...rows.map((row) => {
      const tile = el("div", `df-allowed-tile ${tone(row.rank, row.teams)}`);
      tile.append(el("span", "df-allowed-pos", row.position), el("span", "df-allowed-value", row.points_per_game.toFixed(1)),
                  el("span", "df-allowed-rank", `${ordinal(row.rank)} toughest`));
      return tile;
    }));
  }

  function renderUpcoming(games) {
    const withModel = games.filter((g) => g.projection != null);
    els.upcomingNote.textContent = games.length && !withModel.length
      ? "Defense projections aren't available yet."
      : "Projected fantasy points from the DST matchup model, ranked against every defense playing that week.";
    if (!games.length) {
      els.upcoming.replaceChildren(el("li", "df-empty", "No upcoming games in this season's schedule."));
      return;
    }
    els.upcoming.replaceChildren(...games.map((g) => {
      const item = el("li", `df-game ${g.rank ? tone(g.rank, g.teams) : ""}`);
      const when = el("div", "df-game-when");
      when.append(el("span", "df-game-week", `Week ${g.week}`), el("span", "df-game-opp", `${g.home ? "vs" : "at"} ${g.opponent}`));
      const proj = el("div", "df-game-proj");
      if (g.projection != null) {
        proj.append(el("span", "df-game-points", g.projection.toFixed(1)), el("span", "df-game-rank", `${ordinal(g.rank)} of ${g.teams}`));
      } else {
        proj.append(el("span", "df-game-rank", "No projection"));
      }
      item.append(when, proj);
      if (g.factors.length) {
        const reasons = el("ul", "df-game-factors");
        for (const [text, points] of g.factors) {
          reasons.append(el("li", points >= 0 ? "is-plus" : "is-minus", `${points >= 0 ? "+" : "−"}${Math.abs(points).toFixed(1)} ${text}`));
        }
        item.append(reasons);
      }
      return item;
    }));
  }

  // While another team loads, dim the one on screen and mark it busy for screen readers.
  function setBusy(busy) {
    els.team.setAttribute("aria-busy", String(busy));
    els.team.classList.toggle("is-stale", busy);
  }

  async function showTeam(team, { push = true } = {}) {
    const id = ++loading;
    els.select.value = team;
    showError(null);
    setBusy(true);
    let data;
    try {
      data = await getJson(`/defenses/${encodeURIComponent(team)}`, "Couldn't load that defense");
    } catch (error) {
      if (id !== loading) return;
      setBusy(false);
      showError(error, () => showTeam(team, { push }));
      return;
    }
    if (id !== loading) return;
    setBusy(false);
    renderHero(data);
    renderGroups(data.groups);
    renderUnits(data.units);
    renderAllowed(data.fantasy_allowed);
    renderUpcoming(data.upcoming);
    els.team.hidden = false;
    document.title = `${teamName(data.summary)} Defense Breakdown`;
    remember(team);
    if (push) {
      const url = new URL(location.href);
      url.searchParams.set("team", team);
      history.replaceState(null, "", url);
    }
    renderTable();
  }

  // ---------- league table ----------

  const COLUMNS = [
    { key: "team", label: "Team", ascending: true, value: (t) => teamName(t) },
    { key: "overall_rank", label: "Groups", ascending: true, value: (t) => t.overall_rank, rank: true },
    ...GROUPS.map(([key, label]) => ({ key, label, ascending: true, value: (t) => t.group_ranks[key], rank: true })),
    { key: "points_allowed_rank", label: "Pts allowed", ascending: true, value: (t) => t.points_allowed_rank, rank: true,
      show: (t) => t.points_allowed_pg.toFixed(1) },
    { key: "dst_rank", label: "DST pts", ascending: true, value: (t) => t.dst_rank, rank: true,
      show: (t) => t.dst_ppg.toFixed(1) },
  ];

  function renderTableHead() {
    els.head.replaceChildren(...COLUMNS.map((col) => {
      const th = el("th");
      th.scope = "col";
      const button = el("button", "df-sort", col.label);
      button.type = "button";
      button.dataset.key = col.key;
      const active = sort.key === col.key;
      th.setAttribute("aria-sort", active ? (sort.ascending ? "ascending" : "descending") : "none");
      if (active) button.classList.add("is-active");
      th.append(button);
      return th;
    }));
  }

  function renderTable() {
    if (!league) return;
    renderTableHead();
    const col = COLUMNS.find((c) => c.key === sort.key) || COLUMNS[1];
    const rows = [...league.teams].sort((a, b) => {
      const x = col.value(a), y = col.value(b);
      const order = typeof x === "string" ? x.localeCompare(y) : x - y;
      return (sort.ascending ? order : -order) || a.overall_rank - b.overall_rank;
    });
    const n = league.teams.length;
    const current = els.select.value;
    els.body.replaceChildren(...rows.map((t) => {
      const tr = el("tr", t.team === current ? "is-current" : "");
      const name = el("th");
      name.scope = "row";
      const pick = el("button", "df-team-link", teamName(t));
      pick.type = "button";
      pick.dataset.team = t.team;
      if (t.team === current) pick.setAttribute("aria-current", "true");
      name.append(pick);
      tr.append(name);
      for (const c of COLUMNS.slice(1)) {
        const td = el("td");
        const rank = c.value(t);
        td.append(rankPill(rank, n, c.label));
        if (c.show) td.append(el("span", "df-cell-value", c.show(t)));
        tr.append(td);
      }
      return tr;
    }));
  }

  els.head.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-key]");
    if (!button) return;
    const col = COLUMNS.find((c) => c.key === button.dataset.key);
    sort = sort.key === col.key ? { key: col.key, ascending: !sort.ascending } : { key: col.key, ascending: col.ascending };
    renderTable();
    els.head.querySelector(`button[data-key="${col.key}"]`)?.focus();
  });

  els.body.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-team]");
    if (!button) return;
    showTeam(button.dataset.team);
    els.team.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  });

  els.select.addEventListener("change", () => showTeam(els.select.value));

  // ---------- start ----------

  async function start() {
    showError(null);
    try {
      league = await getJson("/defenses", "Couldn't load defenses");
    } catch (error) {
      els.select.replaceChildren(el("option", null, "No data"));
      showError(error, start);
      return;
    }
    els.status.textContent = `${league.season} season through week ${league.through_week}.`;
    const byName = [...league.teams].sort((a, b) => teamName(a).localeCompare(teamName(b)));
    els.select.replaceChildren(...byName.map((t) => {
      const option = el("option", null, teamName(t));
      option.value = t.team;
      return option;
    }));
    els.select.disabled = false;
    els.league.hidden = false;
    const known = new Set(league.teams.map((t) => t.team));
    const asked = (new URLSearchParams(location.search).get("team") || "").toUpperCase();
    const team = [asked, recalled()].find((t) => t && known.has(t)) || league.teams[0].team;
    await showTeam(team, { push: Boolean(asked) });
  }

  start();
  noteSampleData();
})();
