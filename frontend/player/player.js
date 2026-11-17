"use strict";

// Player profile: /player/?id=<app player id>. Every value from the API is inserted as
// text (never as HTML), and images are only loaded from ESPN's image CDN.

const ID_PATTERN = /^[A-Za-z0-9_-]{1,32}$/; // same rule the API enforces
const IMAGE_HOST = "https://a.espncdn.com/";

const $ = (id) => document.getElementById(id);
const els = {
  main: $("profile"),
  photo: $("profile-photo"),
  name: $("profile-name"),
  sub: $("profile-sub"),
  notice: $("profile-notice"),
  noticeText: $("profile-notice-text"),
  retry: $("profile-retry"),
  facts: $("bio-facts"),
  combineSection: $("combine-section"),
  combineNote: $("combine-note"),
  combineDrills: $("combine-drills"),
  nflSection: $("nfl-section"),
  nflStats: $("nfl-stats"),
  collegeSection: $("college-section"),
  collegeStats: $("college-stats"),
  credit: $("profile-credit"),
};

const { el, svg } = window.PlayerUI;

function formatStat(value) {
  if (value == null) return "–";
  return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(1);
}

function initials(name) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0].toUpperCase()).join("");
}

// ---------- States ----------

// Stops the one retry countdown; called whenever the notice changes, so timers never stack.
let stopCountdown = () => {};

function showNotice(text, { retryAfter = null, retry = false } = {}) {
  stopCountdown();
  stopCountdown = () => {};
  els.notice.querySelector(".notice-link")?.remove();
  els.noticeText.textContent = text;
  els.notice.hidden = false;
  els.retry.hidden = !retry;
  els.retry.disabled = false;
  els.retry.textContent = "Try again";
  if (retryAfter) stopCountdown = PlayerUI.countdown(els.retry, retryAfter);
}

function showFatal(title, text, { retry = false, retryAfter = null } = {}) {
  document.title = `${title} | Fantasy Cheat Code`;
  els.name.textContent = title;
  els.sub.textContent = "";
  els.photo.replaceChildren();
  els.facts.replaceChildren();
  for (const section of [els.combineSection, els.nflSection, els.collegeSection]) section.hidden = true;
  showNotice(text, { retry, retryAfter });
  if (!retry) {
    const link = el("a", "btn btn-quiet notice-link", "Go to the trade analyzer");
    link.href = "/";
    els.notice.append(link);
  }
  els.main.setAttribute("aria-busy", "false");
}

// ---------- Header and bio ----------

function renderPhoto(url, name) {
  els.photo.replaceChildren();
  const fallback = () => els.photo.replaceChildren(el("span", "profile-initials", initials(name)));
  if (typeof url !== "string" || !url.startsWith(IMAGE_HOST)) return fallback();
  const img = new Image();
  img.alt = "";
  img.referrerPolicy = "no-referrer";
  img.decoding = "async";
  img.addEventListener("error", fallback, { once: true });
  img.src = url;
  els.photo.append(img);
}

function renderHeader(profile) {
  const { player, bio } = profile;
  document.title = `${player.name} | Fantasy Cheat Code`;
  els.name.textContent = player.name;
  els.sub.textContent = [player.position, player.team, bio.jersey, bio.status].filter(Boolean).join(", ");
  renderPhoto(profile.headshot_url, player.name);

  const facts = [
    ["Height", bio.height],
    ["Weight", bio.weight],
    ["Age", bio.age],
    ["College", bio.college],
    ["Draft", bio.draft],
    ["Experience", bio.experience],
    ["Born", bio.birth_place],
  ].filter(([, value]) => value != null && value !== "");
  els.facts.replaceChildren(
    ...facts.map(([label, value]) => {
      const item = el("div", "bio-fact");
      item.append(el("dt", null, label), el("dd", null, String(value)));
      return item;
    }),
  );
}

function renderEspnStatus(espn) {
  stopCountdown();
  els.notice.hidden = true;
  if (espn.status === "unavailable") {
    const why = espn.code === "upstream_busy" ? "ESPN is busy right now" : "ESPN couldn't be reached";
    showNotice(`${why}, so career and college stats aren't shown. Saved details are below.`, {
      retry: true, retryAfter: espn.retry_after,
    });
  } else if (espn.status === "stale") {
    showNotice("ESPN couldn't be reached, so these stats are from the last successful update.");
  } else if (espn.status === "not_found") {
    showNotice("ESPN doesn't have a profile for this player, so only saved details are shown.");
  }
  if (espn.fetched_at) {
    const when = new Date(espn.fetched_at * 1000);
    els.credit.textContent = `Bio and stats from ESPN, updated ${when.toLocaleString()}. Combine results from nflverse.`;
  } else {
    els.credit.textContent = "Combine results from nflverse.";
  }
}

// ---------- Combine ----------

function renderCombine(combine, position) {
  if (!combine || !combine.drills.length) {
    els.combineSection.hidden = true;
    return;
  }
  els.combineSection.hidden = false;
  const pos = combine.position || position;
  els.combineNote.textContent =
    `${combine.season} combine${combine.school ? `, ${combine.school}` : ""}. ` +
    `Bars show the share of ${pos}s since 2000 each result beats.`;
  els.combineDrills.replaceChildren(
    ...combine.drills.map((drill) => {
      const item = el("li", "drill");
      const head = el("div", "drill-head");
      head.append(el("span", "drill-label", drill.label), el("strong", "drill-value", `${drill.value} ${drill.unit}`));
      const track = el("div", "drill-track");
      const pct = drill.percentile;
      track.setAttribute("role", "img");
      track.setAttribute("aria-label", pct == null ? "No comparison available"
        : `Better than ${pct}% of ${drill.compared_with} ${pos}s`);
      if (pct != null) {
        const fill = el("span", "drill-fill");
        fill.style.width = `${Math.max(pct, 1)}%`;
        track.append(fill);
      }
      const note = el("p", "drill-note", pct == null ? "Not enough results at this position to compare."
        : `Better than ${pct}% of ${drill.compared_with.toLocaleString()} ${pos}s${drill.lower_is_better ? " (lower is better)" : ""}`);
      item.append(head, track, note);
      return item;
    }),
  );
}

// ---------- Stats (shared by NFL and college) ----------

const KEY_STATS = ["YDS", "PTS", "FGM", "TOT", "REC"]; // charted column, in order of preference

function keyColumn(category) {
  const labels = category.columns.map((c) => c.label);
  for (const label of KEY_STATS) {
    const index = labels.indexOf(label);
    if (index >= 0) return index;
  }
  return labels.findIndex((label) => label !== "GP");
}

function seasonChart(category) {
  const index = keyColumn(category);
  if (index < 0 || category.seasons.length < 1) return null;
  const column = category.columns[index];
  const seasons = category.seasons.map((s) => ({ year: s.year, team: s.team, value: s.values[index] ?? 0 }));
  const max = Math.max(...seasons.map((s) => s.value), 1);
  const barWidth = 44;
  const gap = 14;
  const height = 150;
  const top = 18;
  const bottom = 24;
  const width = seasons.length * (barWidth + gap);
  const chart = svg("svg", {
    viewBox: `0 0 ${width} ${height}`, class: "season-chart", role: "img",
    "aria-label": `${column.name} by season: ${seasons.map((s) => `${s.year} ${formatStat(s.value)}`).join(", ")}`,
  });
  chart.style.maxWidth = `${width}px`;
  chart.append(svg("line", { x1: 0, x2: width, y1: height - bottom, y2: height - bottom, class: "baseline" }));
  seasons.forEach((s, i) => {
    const x = i * (barWidth + gap) + gap / 2;
    const barHeight = Math.max(((height - top - bottom) * s.value) / max, s.value > 0 ? 2 : 0);
    const y = height - bottom - barHeight;
    const group = svg("g", { class: "season-bar" });
    group.append(svg("title", {}, `${s.year}${s.team ? ` (${s.team})` : ""}: ${formatStat(s.value)} ${column.label}`));
    group.append(svg("rect", { x, y, width: barWidth, height: barHeight, rx: 3 }));
    group.append(svg("text", { x: x + barWidth / 2, y: y - 5, "text-anchor": "middle", class: "bar-value" }, formatStat(s.value)));
    group.append(svg("text", { x: x + barWidth / 2, y: height - 7, "text-anchor": "middle", class: "bar-year" }, String(s.year)));
    chart.append(group);
  });
  const wrap = el("figure", "season-figure");
  wrap.append(el("figcaption", null, `${column.name} by season`), chart);
  return wrap;
}

function statTable(category) {
  const wrap = el("div", "stat-table-wrap");
  const table = el("table", "stat-table");
  const caption = el("caption", "visually-hidden", `${category.label} by season`);
  const head = el("thead");
  const headRow = el("tr");
  const yearHead = el("th", null, "Season");
  yearHead.scope = "col";
  const teamHead = el("th", null, "Team");
  teamHead.scope = "col";
  headRow.append(yearHead, teamHead);
  for (const column of category.columns) {
    const th = el("th", "num", column.label);
    th.scope = "col";
    th.title = column.name;
    headRow.append(th);
  }
  head.append(headRow);
  const body = el("tbody");
  for (const season of category.seasons) {
    const row = el("tr");
    const year = el("th", null, String(season.year));
    year.scope = "row";
    row.append(year, el("td", null, season.team || "–"));
    for (const value of season.values) row.append(el("td", "num", formatStat(value)));
    body.append(row);
  }
  table.append(caption, head, body);
  if (category.totals.length) {
    const foot = el("tfoot");
    const row = el("tr");
    const label = el("th", null, "Career");
    label.scope = "row";
    row.append(label, el("td"));
    for (const value of category.totals) row.append(el("td", "num", formatStat(value)));
    foot.append(row);
    table.append(foot);
  }
  wrap.append(table);
  return wrap;
}

function hasNumbers(category) {
  return category.seasons.some((s) => s.values.some((v) => v));
}

// Most useful category first for the player's position.
const PREFERRED = { QB: "passing", RB: "rushing", WR: "receiving", TE: "receiving", K: "kicking" };

function renderStats(section, container, categories, position) {
  const useful = categories.filter(hasNumbers);
  if (!useful.length) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  const preferred = useful.findIndex((c) => c.name === PREFERRED[position]);
  let active = preferred >= 0 ? preferred : 0;

  const tabs = el("div", "segmented stat-tabs");
  tabs.setAttribute("role", "group");
  tabs.setAttribute("aria-label", "Stat category");
  const panel = el("div", "stat-panel");

  const show = () => {
    for (const [i, button] of [...tabs.children].entries()) button.setAttribute("aria-pressed", String(i === active));
    const category = useful[active];
    const chart = seasonChart(category);
    panel.replaceChildren(...[chart, statTable(category)].filter(Boolean));
  };
  useful.forEach((category, i) => {
    const button = el("button", null, category.label);
    button.type = "button";
    button.addEventListener("click", () => {
      active = i;
      show();
    });
    tabs.append(button);
  });
  container.replaceChildren(tabs, panel);
  show();
}

// ---------- Loading ----------

async function load(id) {
  els.main.setAttribute("aria-busy", "true");
  let response;
  try {
    response = await PlayerUI.request(`/players/${encodeURIComponent(id)}/profile`, { headers: { accept: "application/json" } });
  } catch (error) {
    showFatal("Couldn't load this player", error.message, { retry: true });
    return;
  }
  if (response.status === 404) {
    showFatal("Player not found", "There's no player with this id. The link may be old or mistyped.");
    return;
  }
  if (response.status === 429) {
    // PlayerUI reads Retry-After (seconds or a date, capped); wait 10s if it's missing.
    const wait = (await PlayerUI.responseError(response)).retryAfter || 10;
    showFatal("Slow down for a moment", "You've opened a lot of profiles quickly. Profiles can be loaded again shortly.", {
      retry: true, retryAfter: wait,
    });
    return;
  }
  if (!response.ok) {
    showFatal("Couldn't load this player", await PlayerUI.errorMessage(response, "This player couldn't be loaded"), { retry: true });
    return;
  }
  let profile;
  try {
    profile = await response.json();
  } catch {
    showFatal("Couldn't load this player", "The server sent a response this page couldn't read.", { retry: true });
    return;
  }
  renderHeader(profile);
  loadTags(id, profile.player.position);
  renderEspnStatus(profile.espn);
  renderCombine(profile.combine, profile.player.position);
  renderStats(els.nflSection, els.nflStats, profile.nfl_stats, profile.player.position);
  renderStats(els.collegeSection, els.collegeStats, profile.college_stats, profile.player.position);
  els.main.setAttribute("aria-busy", "false");
}

// Tags (slot receiver, high target share...), each linking to the player finder. Optional:
// a failure just leaves them out.
async function loadTags(id, position) {
  const list = $("profile-tags");
  let index;
  try {
    const response = await fetch(`/tags?player=${encodeURIComponent(id)}`, { headers: { accept: "application/json" } });
    if (!response.ok) return;
    index = await response.json();
  } catch {
    return;
  }
  const defs = new Map(index.tags.map((t) => [t.key, t]));
  const tags = (index.players[id] || []).filter((t) => defs.has(t.key));
  list.hidden = tags.length === 0;
  list.replaceChildren(...tags.map((tag) => {
    const def = defs.get(tag.key);
    const item = el("li");
    const link = el("a", `tag-pill tag-${def.category}`, def.label);
    link.href = `/finder/?tag=${encodeURIComponent(tag.key)}&pos=${encodeURIComponent(position)}`;
    link.title = tag.detail ? `${tag.detail}. Find others like this.` : `${def.description} Find others like this.`;
    if (tag.detail) link.setAttribute("aria-label", `${def.label}: ${tag.detail}. Find other players with this tag.`);
    item.append(link);
    return item;
  }));
}

function init() {
  const id = new URLSearchParams(location.search).get("id") || "";
  if (!ID_PATTERN.test(id)) {
    showFatal("Player not found", "This link doesn't include a valid player. Open a profile from a player's name.");
    return;
  }
  els.retry.addEventListener("click", () => load(id));
  load(id);
}

init();
