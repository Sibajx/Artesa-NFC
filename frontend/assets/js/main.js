// ArtesaNFC — Home foundation behavior.
// Scope: hamburger menu open/close, aria sync, Escape to close,
// optional scroll lock, a lightweight scroll-direction hook for
// future header/logo treatments, and a minimal .fade-in reveal.
// No scroll-jacking, no narrative scrub, no dependencies, no API calls.

(function () {
  "use strict";

  var menuToggle = document.getElementById("menu-toggle");
  var navPanel = document.getElementById("primary-navigation");
  var navBackdrop = document.getElementById("nav-backdrop");
  var header = document.querySelector(".site-header");
  var TRANSITION_MS = 300; // matches --duration-ui in tokens.css
  // Tracks whether the off-canvas nav is open so the scroll-driven header
  // hide/show below (Issue #23) never fights an open menu.
  var isNavOpen = false;
  // Off-canvas background accessibility (Issue #25) — content outside the
  // drawer that must be taken out of the accessibility tree/tab order
  // while it's open.
  var mainContent = document.getElementById("main-content");
  var siteFooter = document.querySelector(".site-footer");
  // Scroll position captured at open time so it can be restored exactly
  // on close (mobile/iOS scroll lock, Issue #25).
  var lockedScrollY = 0;

  // Off-canvas background accessibility (Issue #25). Tab is already
  // trapped inside the open panel below, but that alone doesn't stop
  // assistive tech that navigates outside the Tab order (e.g. a screen
  // reader's swipe/virtual cursor) from reaching background content.
  // inert removes these regions from the tab order and AT tree in
  // supporting browsers; aria-hidden is a fallback for browsers without
  // inert support.
  function setBackgroundInert(isInert) {
    [mainContent, siteFooter].forEach(function (el) {
      if (!el) {
        return;
      }
      el.inert = isInert;
      if (isInert) {
        el.setAttribute("aria-hidden", "true");
      } else {
        el.removeAttribute("aria-hidden");
      }
    });
  }

  // Mobile/iOS background scroll lock (Issue #25) — overflow:hidden alone
  // does not reliably stop iOS Safari from scrolling the page behind an
  // open drawer. Pinning body to its current scroll offset via
  // position:fixed (see .has-locked-scroll in components.css) blocks
  // that; the offset and scrollbar width are passed to CSS as custom
  // properties so main.js never sets layout styles directly, and the
  // exact scroll position is restored on unlock.
  function lockBodyScroll() {
    lockedScrollY = window.scrollY || window.pageYOffset || 0;
    var scrollbarWidth = window.innerWidth - document.documentElement.clientWidth;
    document.body.style.setProperty("--scroll-lock-offset", lockedScrollY + "px");
    document.body.style.setProperty("--scroll-lock-scrollbar-width", scrollbarWidth + "px");
    if (header) {
      document.body.style.setProperty("--header-height", header.getBoundingClientRect().height + "px");
    }
    document.body.classList.add("has-locked-scroll");
  }

  function unlockBodyScroll() {
    document.body.classList.remove("has-locked-scroll");
    document.body.style.removeProperty("--scroll-lock-offset");
    document.body.style.removeProperty("--scroll-lock-scrollbar-width");
    document.body.style.removeProperty("--header-height");
    window.scrollTo(0, lockedScrollY);
  }

  function openMenu() {
    isNavOpen = true;
    if (header) {
      header.dataset.hidden = "false";
    }
    navPanel.hidden = false;
    if (navBackdrop) {
      navBackdrop.hidden = false;
    }
    // Force layout so the transition runs after removing [hidden].
    requestAnimationFrame(function () {
      navPanel.dataset.state = "open";
      if (navBackdrop) {
        navBackdrop.dataset.state = "open";
      }
    });
    menuToggle.setAttribute("aria-expanded", "true");
    menuToggle.setAttribute("aria-label", "Cerrar menú");
    lockBodyScroll();
    setBackgroundInert(true);

    var firstLink = navPanel.querySelector(".nav-panel__link");
    if (firstLink) {
      firstLink.focus();
    }
  }

  function closeMenu(options) {
    var returnFocus = !options || options.returnFocus !== false;
    isNavOpen = false;

    navPanel.dataset.state = "closed";
    if (navBackdrop) {
      navBackdrop.dataset.state = "closed";
    }
    menuToggle.setAttribute("aria-expanded", "false");
    menuToggle.setAttribute("aria-label", "Abrir menú");
    setBackgroundInert(false);
    unlockBodyScroll();

    window.setTimeout(function () {
      if (navPanel.dataset.state === "closed") {
        navPanel.hidden = true;
        if (navBackdrop) {
          navBackdrop.hidden = true;
        }
      }
    }, TRANSITION_MS);

    if (returnFocus) {
      menuToggle.focus();
    }
  }

  if (menuToggle && navPanel) {
    menuToggle.addEventListener("click", function () {
      var isOpen = menuToggle.getAttribute("aria-expanded") === "true";
      if (isOpen) {
        closeMenu();
      } else {
        openMenu();
      }
    });

    navPanel.addEventListener("click", function (event) {
      if (event.target.matches(".nav-panel__link")) {
        // Let the anchor's own destination keep focus (in-page hash target
        // or the next page) instead of pulling it back to the toggle.
        closeMenu({ returnFocus: false });
      }
    });

    if (navBackdrop) {
      navBackdrop.addEventListener("click", function () {
        closeMenu();
      });
    }

    document.addEventListener("keydown", function (event) {
      var isOpen = menuToggle.getAttribute("aria-expanded") === "true";
      if (!isOpen) {
        return;
      }

      if (event.key === "Escape") {
        closeMenu();
        return;
      }

      // Focus containment: keep Tab/Shift+Tab cycling within the open panel.
      if (event.key === "Tab") {
        var focusable = navPanel.querySelectorAll(".nav-panel__link");
        if (!focusable.length) {
          return;
        }
        var first = focusable[0];
        var last = focusable[focusable.length - 1];

        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    });
  }

  // Scroll direction/state hook (DESIGN_SYSTEM.md §6). Sets data-scrolled
  // (read by components.css for the logo fade) and drives the header
  // hide-on-down/show-on-up behavior via data-hidden (Issue #23).
  if (header) {
    var lastScrollY = window.scrollY;
    var ticking = false;
    // Ignore deltas smaller than this — absorbs mobile momentum/rubber-band
    // jitter and tiny direction changes so the header doesn't flicker.
    var HEADER_HIDE_THRESHOLD = 8;
    // Stay visible until scrolled meaningfully past the top, so the header
    // never hides while still near the start of the page.
    var HEADER_REVEAL_MIN_SCROLL = 96;
    var headerReducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

    function setHeaderHidden(hidden) {
      header.dataset.hidden = hidden ? "true" : "false";
    }

    function updateScrollState() {
      var currentScrollY = window.scrollY;
      var delta = currentScrollY - lastScrollY;

      header.dataset.scrolled = currentScrollY > 8 ? "true" : "false";

      if (headerReducedMotionQuery.matches || isNavOpen) {
        // Reduced motion: header must stay statically visible, never
        // translated by scroll (approved decision, Issue #23). An open
        // nav menu must also never be left behind a hidden header.
        setHeaderHidden(false);
      } else if (currentScrollY <= HEADER_REVEAL_MIN_SCROLL) {
        setHeaderHidden(false);
      } else if (Math.abs(delta) > HEADER_HIDE_THRESHOLD) {
        header.dataset.scrollDir = delta > 0 ? "down" : "up";
        setHeaderHidden(delta > 0);
      }
      // Within the hysteresis band: keep the current hidden state and
      // scrollDir as-is instead of reacting to the tiny delta.

      lastScrollY = currentScrollY;
      ticking = false;
    }

    window.addEventListener(
      "scroll",
      function () {
        if (!ticking) {
          window.requestAnimationFrame(updateScrollState);
          ticking = true;
        }
      },
      { passive: true }
    );
  }

  // Hero media — muted decorative video, gated by prefers-reduced-motion
  // (DESIGN_SYSTEM.md §7, §18). No <source> may exist yet (Issue #19: a
  // placeholder video file was deferred, no local encoder available) —
  // play() is called defensively and any rejection (blocked autoplay or
  // no playable source) is swallowed, since the poster is always a
  // complete visual fallback.
  var heroVideo = document.querySelector(".hero__video");

  if (heroVideo) {
    var reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

    var syncHeroVideoMotion = function (prefersReduced) {
      if (prefersReduced) {
        heroVideo.pause();
        return;
      }
      var playResult = heroVideo.play();
      if (playResult && typeof playResult.catch === "function") {
        playResult.catch(function () {
          // Autoplay blocked or nothing to play yet — poster stays visible.
        });
      }
    };

    syncHeroVideoMotion(reducedMotionQuery.matches);

    if (typeof reducedMotionQuery.addEventListener === "function") {
      reducedMotionQuery.addEventListener("change", function (event) {
        syncHeroVideoMotion(event.matches);
      });
    } else if (typeof reducedMotionQuery.addListener === "function") {
      // Safari < 14 fallback — MediaQueryList predates addEventListener.
      reducedMotionQuery.addListener(function (event) {
        syncHeroVideoMotion(event.matches);
      });
    }
  }

  // Scroll reveal — minimal wiring for the .fade-in class already defined
  // in animations.css (DESIGN_SYSTEM.md §9: fade/reveal on cards).
  // prefers-reduced-motion is handled entirely in CSS; no branching needed
  // here.
  var revealTargets = document.querySelectorAll(".fade-in");

  if (revealTargets.length) {
    if ("IntersectionObserver" in window) {
      var revealObserver = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              entry.target.classList.add("is-visible");
              revealObserver.unobserve(entry.target);
            }
          });
        },
        { threshold: 0.2 }
      );

      for (var i = 0; i < revealTargets.length; i++) {
        revealObserver.observe(revealTargets[i]);
      }
    } else {
      for (var j = 0; j < revealTargets.length; j++) {
        revealTargets[j].classList.add("is-visible");
      }
    }
  }
})();
