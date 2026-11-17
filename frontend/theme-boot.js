"use strict";

// Set the theme before first paint to avoid a flash of the wrong one: the saved choice from
// the theme switch, or else the system preference. styles.css only has [data-theme="dark"]
// rules, so dark mode is defined once; without JavaScript the page stays light. Loaded as a
// blocking script in <head> (not inline) so the Content Security Policy can forbid inline scripts.
(() => {
  let saved = null;
  try {
    saved = localStorage.getItem("theme");
  } catch (e) {}
  const system = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.dataset.theme = saved === "light" || saved === "dark" ? saved : system;
})();

// Each page has its own single-hue landscape (see /backdrop.js). Tag the page now so the
// hue-tinted colors in styles.css are right on first paint.
document.documentElement.dataset.land =
  { tiers: "tiers", "start-sit": "start-sit", draft: "draft", defense: "defense", team: "team", profile: "profile", finder: "finder" }[
    location.pathname.split("/")[1]
  ] || "trade";
