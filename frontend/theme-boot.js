"use strict";

// Apply the saved theme before first paint to avoid a flash of the wrong one. Loaded as a
// blocking script in <head> (not inline) so the Content Security Policy can forbid inline scripts.
try {
  const saved = localStorage.getItem("theme");
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
} catch (e) {}
