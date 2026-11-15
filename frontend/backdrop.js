"use strict";

// Page backdrop: a single-hue landscape behind every page, and on the main pages a star
// player's headshot that dissolves in and out of the sky in ordered (Bayer) dithering.
// Purely decorative, so it's hidden from assistive tech and never blocks the page.
// Colors come from styles.css (--sky-*, --ridge-*, --land-portrait), keyed by the
// data-land attribute that /theme-boot.js sets on <html> before first paint.
(() => {
  const land = document.documentElement.dataset.land || "trade";
  const SVG_NS = "http://www.w3.org/2000/svg";
  const HEIGHT = 600; // viewBox height; the width follows the window so features keep their size
  const LAYERS = 5; // far to near
  const PORTRAIT_AFTER = 2; // ridges drawn behind the portrait; the rest are drawn in front
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  // ---------- Landscape ----------

  // Small seeded PRNG so each page's landscape is the same on every visit.
  function mulberry32(seed) {
    return () => {
      seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // 1-D value noise in [0, 1], cosine-interpolated, with octaves.
  function noise1d(seed) {
    const rand = mulberry32(seed);
    const table = Array.from({ length: 512 }, rand);
    const at = (x) => {
      const i = Math.floor(x);
      const f = (1 - Math.cos((x - i) * Math.PI)) / 2;
      const a = table[((i % 512) + 512) % 512];
      const b = table[(((i + 1) % 512) + 512) % 512];
      return a + (b - a) * f;
    };
    return (x, octaves = 1, gain = 0.5) => {
      let sum = 0, amp = 1, norm = 0, freq = 1;
      for (let o = 0; o < octaves; o++) {
        sum += at(x * freq + o * 37.7) * amp;
        norm += amp;
        amp *= gain;
        freq *= 2;
      }
      return sum / norm;
    };
  }

  const clamp01 = (v) => Math.min(1, Math.max(0, v));
  const smooth = (v) => v * v * (3 - 2 * v);

  // Each terrain returns a height in [0, 1] for x (viewBox units) on a given layer.
  const TERRAINS = {
    // Rolling hills: long, smooth swells.
    hills: (n, x) => n(x / 520, 2, 0.35),
    // Mountains: ridged noise with fine detail.
    mountains: (n, x) => {
      const v = 1 - Math.abs(n(x / 300, 5, 0.5) * 2 - 1);
      return v * v;
    },
    // Dunes: skewed waves (long windward slope, steep slip face).
    dunes: (n, x) => {
      const p = x / 190 + n(x / 900) * 6;
      return (Math.sin(p + 0.8 * Math.sin(p)) + 1) / 2 * (0.55 + 0.45 * n(x / 700 + 9));
    },
    // Mesas: flat tops and steep sides, with a little erosion.
    mesas: (n, x) => smooth(clamp01((n(x / 380, 2, 0.3) - 0.42) * 5)) * 0.85 + n(x / 40, 2) * 0.12,
    // Peaks: tall, jagged summits.
    peaks: (n, x) => {
      const v = 1 - Math.abs(n(x / 230, 6, 0.55) * 2 - 1);
      return Math.pow(v, 2.6);
    },
    // Valley: hills that rise toward both edges.
    valley: (n, x, width) => {
      const edge = Math.pow(Math.abs(x / width - 0.5) * 2, 2);
      return clamp01(n(x / 460, 3, 0.4) * 0.6 + edge * 0.55);
    },
  };

  const PAGES = {
    trade: { terrain: "hills", seed: 11 },
    tiers: { terrain: "mountains", seed: 23 },
    "start-sit": { terrain: "dunes", seed: 5 },
    draft: { terrain: "mesas", seed: 41 },
    defense: { terrain: "peaks", seed: 7 },
    team: { terrain: "valley", seed: 19 },
  };
  const page = PAGES[land] || PAGES.trade;
  const terrain = TERRAINS[page.terrain];

  function ridgePath(layer, width) {
    const n = noise1d(page.seed * 101 + layer * 13);
    const base = 250 + layer * 72; // where the ridge's lowest points sit
    const rise = 190 - layer * 26; // how far its peaks reach above that
    const offset = layer * 977; // so the layers don't line up
    let d = `M0 ${HEIGHT} `;
    for (let x = 0; x <= width + 8; x += 8) {
      const y = base - terrain(n, x + offset, width) * rise;
      d += `L${x} ${y.toFixed(1)} `;
    }
    return d + `L${width + 8} ${HEIGHT} Z`;
  }

  function makeSvg(className) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", `land ${className}`);
    svg.setAttribute("preserveAspectRatio", "none");
    return svg;
  }

  const backdrop = document.createElement("div");
  backdrop.className = "backdrop";
  backdrop.setAttribute("aria-hidden", "true");
  const far = makeSvg("land-far");
  const near = makeSvg("land-near");
  const paths = [];
  for (let i = 0; i < LAYERS; i++) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("class", `ridge ridge-${i}`);
    (i < PORTRAIT_AFTER ? far : near).appendChild(path);
    paths.push(path);
  }
  backdrop.append(far, near);
  document.body.prepend(backdrop);

  let drawnWidth = 0;
  function drawLandscape() {
    const box = far.getBoundingClientRect();
    if (!box.height) return;
    const width = Math.round((HEIGHT * box.width) / box.height);
    if (width === drawnWidth) return;
    drawnWidth = width;
    for (const svg of [far, near]) svg.setAttribute("viewBox", `0 0 ${width} ${HEIGHT}`);
    paths.forEach((path, i) => path.setAttribute("d", ridgePath(i, width)));
  }
  drawLandscape();
  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(drawLandscape, 150);
  });
})();
