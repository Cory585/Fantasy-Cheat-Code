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

  // ---------- Star portrait ----------

  // Only the main tool pages (the ones with a masthead) get the portrait.
  if (!document.querySelector(".masthead")) return;

  const COLS = 180, ROWS = 131; // dither resolution (ESPN headshots are 350x254)
  const FADE_MS = 1400, HOLD_MS = 5200, GAP_MS = 600, FADE_STEPS = 16;
  const STAR_COUNT = 16;
  const BAYER = [
    [0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38], [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21],
  ].map((row) => row.map((v) => (v + 0.5) / 64));

  const canvas = document.createElement("canvas");
  canvas.className = "star-portrait";
  canvas.width = COLS;
  canvas.height = ROWS;
  backdrop.insertBefore(canvas, near);
  const ctx = canvas.getContext("2d");
  const frame = ctx.createImageData(COLS, ROWS);

  // Each team's home jersey color as [hue, saturation %]. While a star is showing, the
  // landscape (and the page tint that follows it) shifts to their jersey's hue.
  const JERSEYS = {
    ARI: [345, 30], ATL: [352, 28], BAL: [250, 30], BUF: [218, 32], CAR: [200, 30], CHI: [222, 26],
    CIN: [16, 34], CLE: [26, 28], DAL: [216, 26], DEN: [18, 34], DET: [201, 32], GB: [158, 26],
    HOU: [204, 26], IND: [212, 30], JAX: [189, 30], KC: [352, 32], LAC: [201, 32], LAR: [222, 30],
    LV: [220, 4], MIA: [184, 32], MIN: [266, 28], NE: [212, 26], NO: [40, 26], NYG: [224, 30],
    NYJ: [158, 28], PHI: [186, 28], PIT: [42, 30], SEA: [206, 26], SF: [2, 30], TB: [0, 30],
    TEN: [211, 30], WAS: [356, 26],
  };
  const root = document.documentElement;
  let hue = parseFloat(getComputedStyle(root).getPropertyValue("--land-hue")) || 148;
  let sat = parseFloat(getComputedStyle(root).getPropertyValue("--land-sat")) || 22;

  function setTint(h, s) {
    hue = h;
    sat = s;
    root.style.setProperty("--land-hue", h.toFixed(1));
    root.style.setProperty("--land-sat", `${s.toFixed(1)}%`);
  }

  // Glide to a team's jersey hue the short way round the color wheel, recoloring the
  // portrait's ink as it goes. Instant when the user prefers reduced motion.
  function tintTo(team, ms) {
    const jersey = JERSEYS[team];
    if (!jersey) return;
    const toHue = hue + ((((jersey[0] - hue) % 360) + 540) % 360) - 180;
    if (reduceMotion.matches || !ms) {
      setTint(toHue, jersey[1]);
      readInk();
      return;
    }
    const fromHue = hue, fromSat = sat, start = performance.now();
    const tick = (now) => {
      const t = clamp01((now - start) / ms);
      const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; // ease in-out
      setTint(fromHue + (toHue - fromHue) * e, fromSat + (jersey[1] - fromSat) * e);
      readInk();
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  let ink = [0, 0, 0];
  function readInk() {
    const match = getComputedStyle(canvas).color.match(/[\d.]+/g);
    ink = match ? match.slice(0, 3).map(Number) : [0, 0, 0];
  }

  let density = null; // per-pixel ink density in [0, 1] for the current star
  let level = 0; // how far it has faded in, in [0, 1]

  function render() {
    const px = frame.data;
    for (let y = 0; y < ROWS; y++) {
      const row = BAYER[y & 7];
      for (let x = 0; x < COLS; x++) {
        const i = y * COLS + x;
        const on = density && density[i] * level > row[x & 7];
        px[i * 4] = ink[0];
        px[i * 4 + 1] = ink[1];
        px[i * 4 + 2] = ink[2];
        px[i * 4 + 3] = on ? 255 : 0;
      }
    }
    ctx.putImageData(frame, 0, 0);
  }

  // Separable box blur, used for local contrast.
  function boxBlur(src, radius) {
    const tmp = new Float32Array(src.length), out = new Float32Array(src.length);
    const span = radius * 2 + 1;
    for (let y = 0; y < ROWS; y++) {
      for (let x = 0; x < COLS; x++) {
        let sum = 0;
        for (let k = -radius; k <= radius; k++) sum += src[y * COLS + Math.min(COLS - 1, Math.max(0, x + k))];
        tmp[y * COLS + x] = sum / span;
      }
    }
    for (let y = 0; y < ROWS; y++) {
      for (let x = 0; x < COLS; x++) {
        let sum = 0;
        for (let k = -radius; k <= radius; k++) sum += tmp[Math.min(ROWS - 1, Math.max(0, y + k)) * COLS + x];
        out[y * COLS + x] = sum / span;
      }
    }
    return out;
  }

  // Sobel edge strength at every pixel.
  function edges(src) {
    const out = new Float32Array(src.length);
    const at = (x, y) => src[Math.min(ROWS - 1, Math.max(0, y)) * COLS + Math.min(COLS - 1, Math.max(0, x))];
    for (let y = 0; y < ROWS; y++) {
      for (let x = 0; x < COLS; x++) {
        const gx = at(x + 1, y - 1) + 2 * at(x + 1, y) + at(x + 1, y + 1) - at(x - 1, y - 1) - 2 * at(x - 1, y) - at(x - 1, y + 1);
        const gy = at(x - 1, y + 1) + 2 * at(x, y + 1) + at(x + 1, y + 1) - at(x - 1, y - 1) - 2 * at(x, y - 1) - at(x + 1, y - 1);
        out[y * COLS + x] = Math.hypot(gx, gy);
      }
    }
    return out;
  }

  // Turn a headshot into ink density, tuned so faces stay recognizable at dot size:
  // stretch the contrast (ignoring the extreme 2% at each end), boost local contrast so
  // eyes, brows, and mouth stand out, draw facial features as lines from their edges,
  // and outline the whole silhouette so the head and shoulders read at a glance.
  function toDensity(img) {
    const scratch = document.createElement("canvas");
    scratch.width = COLS;
    scratch.height = ROWS;
    const sctx = scratch.getContext("2d", { willReadFrequently: true });
    sctx.drawImage(img, 0, 0, COLS, ROWS);
    const { data } = sctx.getImageData(0, 0, COLS, ROWS);
    const n = COLS * ROWS;
    const lum = new Float32Array(n), alpha = new Float32Array(n);
    const opaque = [];
    for (let i = 0; i < n; i++) {
      lum[i] = (0.299 * data[i * 4] + 0.587 * data[i * 4 + 1] + 0.114 * data[i * 4 + 2]) / 255;
      alpha[i] = data[i * 4 + 3] / 255;
      if (alpha[i] > 0.5) opaque.push(lum[i]);
    }
    if (!opaque.length) return new Float32Array(n);
    opaque.sort((a, b) => a - b);
    const lo = opaque[Math.floor(opaque.length * 0.02)];
    const hi = opaque[Math.floor(opaque.length * 0.98)];
    const span = Math.max(hi - lo, 0.01);
    for (let i = 0; i < n; i++) lum[i] = clamp01((lum[i] - lo) / span);

    const blurred = boxBlur(lum, 5);
    const sharp = lum.map((v, i) => clamp01(v + 1.4 * (v - blurred[i])));
    const featureEdges = edges(sharp);
    const outline = edges(alpha);

    const out = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const tone = 0.03 + 0.82 * Math.pow(1 - sharp[i], 1.6);
      const feature = clamp01(featureEdges[i] * 0.9 - 0.25);
      out[i] = clamp01(alpha[i] * (tone + feature) + clamp01(outline[i] * 0.8));
    }
    return out;
  }

  function loadImage(player) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = "anonymous"; // ESPN's CDN allows it, so the canvas can read pixels
      img.decoding = "async";
      img.onload = () => resolve(img);
      img.onerror = reject;
      img.src = `https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/${player.espn_id}.png&w=350&h=254`;
    });
  }

  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = () =>
    document.hidden
      ? new Promise((resolve) =>
          document.addEventListener("visibilitychange", function on() {
            if (!document.hidden) {
              document.removeEventListener("visibilitychange", on);
              resolve();
            }
          }))
      : Promise.resolve();

  // Step the fade in chunky increments so it reads as a dither dissolve, not a blend.
  function fade(from, to) {
    return new Promise((resolve) => {
      const start = performance.now();
      const tick = (now) => {
        const t = clamp01((now - start) / FADE_MS);
        level = from + (to - from) * Math.round(t * FADE_STEPS) / FADE_STEPS;
        render();
        if (t < 1) requestAnimationFrame(tick);
        else resolve();
      };
      requestAnimationFrame(tick);
    });
  }

  async function stars() {
    const response = await fetch("/players", { headers: { accept: "application/json" } });
    if (!response.ok) return [];
    const players = (await response.json()).filter(
      (p) => ["QB", "RB", "WR", "TE"].includes(p.position) && Number.isInteger(p.espn_id) && p.espn_id > 0,
    );
    players.sort((a, b) => b.ros_projection - a.ros_projection);
    const top = players.slice(0, STAR_COUNT);
    for (let i = top.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [top[i], top[j]] = [top[j], top[i]];
    }
    return top;
  }

  async function run() {
    const list = await stars();
    if (!list.length) return;
    readInk();
    let failures = 0;
    for (let i = 0; failures < list.length; i = (i + 1) % list.length) {
      let img;
      try {
        img = await loadImage(list[i]);
      } catch (e) {
        failures++;
        continue;
      }
      failures = 0;
      density = toDensity(img);
      if (reduceMotion.matches) {
        tintTo(list[i].team, 0);
        level = 1;
        render();
        return;
      }
      await visible();
      tintTo(list[i].team, FADE_MS);
      await fade(0, 1);
      await wait(HOLD_MS);
      await visible();
      await fade(1, 0);
      await wait(GAP_MS);
    }
  }

  // Recolor when the theme changes.
  const recolor = () => {
    readInk();
    render();
  };
  new MutationObserver(recolor).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", recolor);

  run().catch(() => {
    // Decorative only: if players or images can't load, the landscape stays without a portrait.
  });
})();
