"use strict";

// Light/dark mode switch shared by every page. The saved choice is applied before
// first paint by a small inline script in each page's <head>.
(() => {
  const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

  function currentTheme() {
    return document.documentElement.dataset.theme || (darkQuery.matches ? "dark" : "light");
  }

  function render() {
    const button = document.getElementById("theme-toggle");
    if (!button) return;
    const dark = currentTheme() === "dark";
    // The label names the action ("Light mode" while dark is on), so don't also expose
    // aria-pressed: "Light mode, pressed" would announce the wrong theme as active.
    button.classList.toggle("is-dark", dark);
    button.querySelector(".theme-label").textContent = dark ? "Light mode" : "Dark mode";
    button.title = dark ? "Switch to light mode" : "Switch to dark mode";
  }

  function toggle() {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("theme", next);
    } catch (e) {
      // Storage can be unavailable (private mode); the switch still works for this visit.
    }
    render();
  }

  document.getElementById("theme-toggle")?.addEventListener("click", toggle);
  darkQuery.addEventListener("change", render);
  render();

  // On narrow screens the nav scrolls sideways: keep the current page's tab in view, and
  // fade the edge only while there are more tabs to scroll to.
  const nav = document.querySelector(".nav-links");
  if (nav) {
    const current = nav.querySelector("[aria-current=page]");
    if (current && nav.scrollWidth > nav.clientWidth) {
      nav.scrollLeft = current.offsetLeft - (nav.clientWidth - current.offsetWidth) / 2;
    }
    const update = () => {
      nav.classList.toggle("has-more", nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 2);
    };
    nav.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    update();
  }
})();
