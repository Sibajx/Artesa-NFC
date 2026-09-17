// ArtesaNFC — Home foundation behavior.
// Scope: hamburger menu open/close, aria sync, Escape to close,
// optional scroll lock, and a lightweight scroll-direction hook for
// future header/logo treatments. No animation choreography, no
// dependencies, no API calls.

(function () {
  "use strict";

  var menuToggle = document.getElementById("menu-toggle");
  var navPanel = document.getElementById("primary-navigation");
  var navBackdrop = document.getElementById("nav-backdrop");
  var header = document.querySelector(".site-header");
  var TRANSITION_MS = 300; // matches --duration-ui in tokens.css

  function openMenu() {
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
    document.body.classList.add("has-locked-scroll");

    var firstLink = navPanel.querySelector(".nav-panel__link");
    if (firstLink) {
      firstLink.focus();
    }
  }

  function closeMenu(options) {
    var returnFocus = !options || options.returnFocus !== false;

    navPanel.dataset.state = "closed";
    if (navBackdrop) {
      navBackdrop.dataset.state = "closed";
    }
    menuToggle.setAttribute("aria-expanded", "false");
    menuToggle.setAttribute("aria-label", "Abrir menú");
    document.body.classList.remove("has-locked-scroll");

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

  // Scroll direction/state hook — used by future issues to fade the
  // header/logo on scroll (DESIGN_SYSTEM.md §6). Only sets data
  // attributes here; no visual behavior beyond what components.css
  // already reads from data-scrolled.
  if (header) {
    var lastScrollY = window.scrollY;
    var ticking = false;

    function updateScrollState() {
      var currentScrollY = window.scrollY;
      header.dataset.scrolled = currentScrollY > 8 ? "true" : "false";
      header.dataset.scrollDir = currentScrollY > lastScrollY ? "down" : "up";
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
})();
