"use strict";

// Site-wide motion. Users who ask for reduced motion get none of it.
(() => {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  // ---------- Smooth scrolling ----------

  // Wheel scrolling over anything that scrolls on its own (search results, the draft
  // board, the lineup panel) stays native, so Lenis only smooths the page itself.
  function scrollsOnItsOwn(node) {
    if (!(node instanceof Element) || node === document.body || node === document.documentElement) return false;
    const style = getComputedStyle(node);
    return (/auto|scroll/.test(style.overflowY) && node.scrollHeight > node.clientHeight + 1)
      || (/auto|scroll/.test(style.overflowX) && node.scrollWidth > node.clientWidth + 1);
  }
  if (window.Lenis) {
    new window.Lenis({ autoRaf: true, lerp: 0.12, anchors: true, prevent: scrollsOnItsOwn });
  }
})();
