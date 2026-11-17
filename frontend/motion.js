"use strict";

// Site-wide motion: Lenis smooth scrolling and GSAP feedback for presses, text entry,
// dropdowns, and content as it appears. Everything hooks in through event delegation and
// a MutationObserver, so page scripts don't need to know about it. Users who ask for
// reduced motion get none of it, including when they turn it on after the page loads.
(() => {
  if (!window.gsap) return;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  // With reduced motion every tween jumps straight to its end state (still running its
  // onComplete and clearProps), so the page ends up the same, just without the movement.
  const instant = (vars) => (reduceMotion.matches ? { ...vars, duration: 0, delay: 0, stagger: 0 } : vars);
  const gsap = {
    to: (targets, vars) => window.gsap.to(targets, instant(vars)),
    fromTo: (targets, from, vars) => window.gsap.fromTo(targets, from, instant(vars)),
  };
  const EASE_OUT = "power3.out";

  // ---------- Smooth scrolling ----------

  // Wheel scrolling over anything that scrolls on its own (search results, the draft
  // board, the lineup panel) stays native, so Lenis only smooths the page itself.
  function scrollsOnItsOwn(node) {
    if (!(node instanceof Element) || node === document.body || node === document.documentElement) return false;
    const style = getComputedStyle(node);
    return (/auto|scroll/.test(style.overflowY) && node.scrollHeight > node.clientHeight + 1)
      || (/auto|scroll/.test(style.overflowX) && node.scrollWidth > node.clientWidth + 1);
  }
  let lenis = null;
  function smoothScrolling(on) {
    if (on && !lenis && window.Lenis) {
      lenis = new window.Lenis({ autoRaf: true, lerp: 0.12, anchors: true, prevent: scrollsOnItsOwn });
    } else if (!on && lenis) {
      lenis.destroy();
      lenis = null;
    }
  }
  smoothScrolling(!reduceMotion.matches);

  // Turning reduced motion on mid-visit finishes anything still moving and stops smooth
  // scrolling; turning it off brings smooth scrolling back.
  reduceMotion.addEventListener("change", () => {
    if (reduceMotion.matches) {
      window.gsap.globalTimeline.getChildren(true, true, false).forEach((tween) => tween.progress(1));
    }
    smoothScrolling(!reduceMotion.matches);
  });

  // Elements whose CSS already uses transform: only fade those, never move or scale them.
  const hasTransform = (el) => getComputedStyle(el).transform !== "none";

  // ---------- Presses ----------

  const PRESSABLE = "button, .btn, summary";
  const pressable = (target) => {
    const el = target instanceof Element ? target.closest(PRESSABLE) : null;
    return el && !el.disabled && !el.closest(".backdrop") ? el : null;
  };

  document.addEventListener("pointerdown", (event) => {
    const el = pressable(event.target);
    if (!el || event.button !== 0 || hasTransform(el)) return;
    gsap.to(el, { scale: 0.96, duration: 0.1, ease: "power2.out", overwrite: "auto" });
    const release = () => {
      gsap.to(el, { scale: 1, duration: 0.45, ease: "back.out(3)", overwrite: "auto", clearProps: "transform" });
      el.removeEventListener("pointerup", release);
      el.removeEventListener("pointerleave", release);
      el.removeEventListener("pointercancel", release);
    };
    el.addEventListener("pointerup", release);
    el.addEventListener("pointerleave", release);
    el.addEventListener("pointercancel", release);
  });

  // ---------- Text boxes and selects ----------

  // A soft ring grows around a focused field and pulses as you type or change a value.
  // It's a box-shadow driven by --ring on the .is-ringed class (styles.css), separate from
  // the :focus-visible outline that keyboard users rely on.
  const FIELD = "input:not([type=checkbox]):not([type=radio]):not([type=range]), select, textarea";
  const field = (target) => (target instanceof Element && target.matches(FIELD) ? target : null);

  document.addEventListener("focusin", (event) => {
    const el = field(event.target);
    if (!el) return;
    el.classList.add("is-ringed");
    gsap.fromTo(el, { "--ring": "0px" }, { "--ring": "3px", duration: 0.25, ease: EASE_OUT, overwrite: "auto" });
  });

  document.addEventListener("focusout", (event) => {
    const el = field(event.target);
    if (!el) return;
    gsap.to(el, {
      "--ring": "0px", duration: 0.2, ease: "power2.in", overwrite: "auto",
      onComplete: () => el.classList.remove("is-ringed"),
    });
  });

  const pulse = (el, size) =>
    gsap.fromTo(el, { "--ring": size }, { "--ring": "3px", duration: 0.35, ease: EASE_OUT, overwrite: "auto" });

  document.addEventListener("input", (event) => {
    const el = field(event.target);
    if (el && el.tagName !== "SELECT") pulse(el, "5px");
  });

  document.addEventListener("change", (event) => {
    const el = field(event.target);
    if (!el || el.tagName !== "SELECT") return;
    pulse(el, "6px");
    if (!hasTransform(el)) {
      gsap.fromTo(el, { scale: 0.97 }, { scale: 1, duration: 0.4, ease: "back.out(3)", clearProps: "transform" });
    }
  });

  // ---------- Dropdowns ----------

  // <details> popovers (e.g. the starting lineup panel) drop open from their summary.
  // The toggle event doesn't bubble, so listen in the capture phase.
  document.addEventListener("toggle", (event) => {
    const details = event.target;
    if (!(details instanceof HTMLDetailsElement) || !details.open) return;
    const content = [...details.children].filter((child) => child.tagName !== "SUMMARY");
    gsap.fromTo(content, { opacity: 0, y: -6, scale: 0.98 }, {
      opacity: 1, y: 0, scale: 1, duration: 0.22, ease: EASE_OUT, transformOrigin: "top right",
      clearProps: "opacity,transform",
    });
  }, true);

  // Search result listboxes slide down when they open. Their options re-render on every
  // keystroke, so they only get a light, quick fade (see appear() below).
  function openListbox(list) {
    gsap.fromTo(list, { opacity: 0, y: -4, scaleY: 0.96 }, {
      opacity: 1, y: 0, scaleY: 1, duration: 0.18, ease: EASE_OUT, transformOrigin: "top center",
      clearProps: "opacity,transform",
    });
  }

  // ---------- Content as it appears ----------

  function appear(nodes) {
    const listOptions = [], items = [];
    for (const node of nodes) {
      if (!node.isConnected || node.closest(".backdrop")) continue;
      (node.closest("[role=listbox]") ? listOptions : items).push(node);
    }
    if (listOptions.length) {
      gsap.fromTo(listOptions.slice(0, 10), { opacity: 0.35, x: -4 }, {
        opacity: 1, x: 0, duration: 0.14, ease: "power2.out", stagger: 0.012, clearProps: "opacity,transform",
      });
    }
    const moving = items.filter((el) => !hasTransform(el));
    const fading = items.filter((el) => hasTransform(el));
    const stagger = { each: 0.03, amount: Math.min(0.3, items.length * 0.03) };
    if (moving.length) {
      gsap.fromTo(moving, { opacity: 0, y: 8 }, {
        opacity: 1, y: 0, duration: 0.35, ease: EASE_OUT, stagger, clearProps: "opacity,transform",
      });
    }
    if (fading.length) {
      gsap.fromTo(fading, { opacity: 0 }, { opacity: 1, duration: 0.3, ease: "power1.out", clearProps: "opacity" });
    }
  }

  // Collect what page scripts add during a frame, then animate only the outermost new
  // elements (a new card animates as one, not each of its children separately). Pages
  // often re-render a whole container when one thing changes, so anything that comes
  // back identical to what was just removed stays still.
  let pending = new Set();
  let removed = new Set();
  let scheduled = false;
  function flush() {
    scheduled = false;
    const added = [...pending].filter((el) => !removed.has(el.outerHTML));
    pending = new Set();
    removed = new Set();
    appear(added.filter((el) => !added.some((other) => other !== el && other.contains(el))));
  }

  const main = document.querySelector("main");
  if (main) {
    new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === "attributes") {
          const el = record.target;
          if (el.getAttribute("role") === "listbox" && !el.hidden && record.oldValue !== null) openListbox(el);
          continue;
        }
        for (const node of record.addedNodes) if (node instanceof HTMLElement) pending.add(node);
        for (const node of record.removedNodes) if (node instanceof HTMLElement) removed.add(node.outerHTML);
      }
      if (pending.size && !scheduled) {
        scheduled = true;
        requestAnimationFrame(flush);
      }
    }).observe(main, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"], attributeOldValue: true });
  }

  // ---------- Page entrance ----------

  const intro = [...document.querySelectorAll(".masthead-title > *, .masthead-controls, main > *, .profile-head")];
  if (intro.length) {
    gsap.fromTo(intro.filter((el) => !hasTransform(el)), { opacity: 0, y: 14 }, {
      opacity: 1, y: 0, duration: 0.55, ease: EASE_OUT, stagger: 0.06, clearProps: "opacity,transform",
    });
  }

  // The theme switch's icon spins as it changes between sun and moon.
  document.getElementById("theme-toggle")?.addEventListener("click", (event) => {
    const icon = event.currentTarget.querySelector(".theme-icon");
    if (icon) gsap.fromTo(icon, { rotate: -120 }, { rotate: 0, duration: 0.5, ease: "back.out(2)", clearProps: "transform" });
  });
})();
