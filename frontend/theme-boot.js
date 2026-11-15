"use strict";

// Apply the saved theme before first paint to avoid a flash of the wrong one. Loaded as a
// blocking script in <head> (not inline) so the Content Security Policy can forbid inline scripts.
try {
  const saved = localStorage.getItem("theme");
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
} catch (e) {}

// Each page has its own single-hue landscape (see /backdrop.js). Tag the page now so the
// hue-tinted colors in styles.css are right on first paint.
document.documentElement.dataset.land =
  { tiers: "tiers", "start-sit": "start-sit", draft: "draft", defense: "defense", team: "team", profile: "profile", finder: "finder" }[
    location.pathname.split("/")[1]
  ] || "trade";
