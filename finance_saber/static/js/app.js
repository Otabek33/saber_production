// Project behaviour on top of the MatDash theme scripts (static/matdash/js/theme).
// Replaces the theme's sidebarmenu.js: the active menu item is rendered server-side.
document.addEventListener("DOMContentLoaded", function () {
  "use strict";

  // Remember the light/dark choice; base.html applies it on the next page load.
  document.querySelectorAll(".dark-layout, .light-layout").forEach(function (toggle) {
    toggle.addEventListener("click", function () {
      try {
        localStorage.setItem("theme", toggle.classList.contains("dark-layout") ? "dark" : "light");
      } catch (e) {
        // Storage unavailable (private mode, blocked cookies): the choice just isn't remembered.
      }
    });
  });

  // Below the theme's 1300px breakpoint the horizontal menu becomes a drawer (css/app.css),
  // where submenus open on tap instead of hover. At desktop width the theme CSS opens them on hover.
  var drawerQuery = window.matchMedia("(max-width: 1299.98px)");
  document.querySelectorAll("#sidebarnavh .sidebar-link.has-arrow").forEach(function (link) {
    link.addEventListener("click", function (event) {
      event.preventDefault();
      if (!drawerQuery.matches) return;
      var submenu = link.nextElementSibling;
      if (!submenu) return;
      var open = submenu.classList.toggle("in");
      link.setAttribute("aria-expanded", open ? "true" : "false");
    });
  });

  // Full screen: the browser's full-screen mode, and the menu hidden so the page fills the window.
  // The menu hides even where the browser refuses full screen (the class alone does that).
  var fullscreenButtons = document.querySelectorAll(".fullscreen-toggle");
  function setFullscreenMode(on) {
    document.body.classList.toggle("fullscreen-mode", on);
    fullscreenButtons.forEach(function (button) {
      button.setAttribute("aria-pressed", on ? "true" : "false");
      button.querySelector(".fullscreen-on").style.display = on ? "none" : "";
      button.querySelector(".fullscreen-off").style.display = on ? "" : "none";
    });
  }
  fullscreenButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      var on = !document.body.classList.contains("fullscreen-mode");
      setFullscreenMode(on);
      if (on && document.documentElement.requestFullscreen) {
        document.documentElement.requestFullscreen().catch(function () {});
      } else if (!on && document.fullscreenElement && document.exitFullscreen) {
        document.exitFullscreen().catch(function () {});
      }
    });
  });
  // Leaving the browser's full screen (Esc, or the browser's own control) also shows the menu again.
  document.addEventListener("fullscreenchange", function () {
    if (!document.fullscreenElement && document.body.classList.contains("fullscreen-mode")) {
      setFullscreenMode(false);
    }
  });
});
