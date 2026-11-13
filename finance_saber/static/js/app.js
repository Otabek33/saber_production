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

  // «Новое» badges on new menu pages: gone once this person has opened the page (remembered in
  // this browser); a section's dot goes when none of its new pages is left unseen.
  var seenNew = [];
  try { seenNew = JSON.parse(localStorage.getItem("seenNew") || "[]"); } catch (e) { seenNew = []; }
  var newBadges = document.querySelectorAll("[data-new]");
  newBadges.forEach(function (badge) {
    if (badge.dataset.new === window.location.pathname && seenNew.indexOf(badge.dataset.new) === -1) {
      seenNew.push(badge.dataset.new);
      try { localStorage.setItem("seenNew", JSON.stringify(seenNew)); } catch (e) { /* not remembered */ }
    }
  });
  newBadges.forEach(function (badge) {
    if (seenNew.indexOf(badge.dataset.new) !== -1) badge.hidden = true;
  });
  document.querySelectorAll("[data-new-dot]").forEach(function (dot) {
    var section = dot.closest(".sidebar-item");
    dot.hidden = !(section && section.querySelector("[data-new]:not([hidden])"));
  });

  // Links that only open a menu or switch something (href="#") don't go anywhere.
  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest('a[href="#"]');
    if (link) event.preventDefault();
  });

  // Inputs with data-autosubmit send their form when changed (a date or month switcher).
  document.addEventListener("change", function (event) {
    if (event.target.matches && event.target.matches("[data-autosubmit]") && event.target.form) {
      event.target.form.submit();
    }
  });

  // Buttons and links with data-confirm="<question>" ask before they act.
  document.addEventListener("click", function (event) {
    var target = event.target.closest && event.target.closest("[data-confirm]");
    if (target && !window.confirm(target.dataset.confirm)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  // Confirmation modals (data-action-modal) shared by several buttons: the button that opens one
  // carries data-action="<url the modal's form posts to>" and data-name="<what is affected>".
  document.addEventListener("show.bs.modal", function (event) {
    var modal = event.target;
    var button = event.relatedTarget;
    if (!button || !modal.matches("[data-action-modal]")) {
      return;
    }
    modal.querySelector("form").action = button.dataset.action;
    modal.querySelectorAll("[data-modal-name]").forEach(function (element) {
      element.textContent = button.dataset.name || "";
    });
  });

  // "Show 20 more" (partials/_load_more.html): the link's page is fetched and its rows are added
  // to the list (data-load-more="<selector of the tbody>"); its own "show more" block replaces
  // this one, with the next link and the new count. If anything goes wrong, the page just opens.
  // Rows with data-empty-row ("nothing found") are never added.
  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("a[data-load-more]");
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
      return; // a new tab or window: the link opens as it is
    }
    event.preventDefault();
    if (link.getAttribute("aria-busy") === "true") {
      return;
    }
    var selector = link.dataset.loadMore;
    var list = document.querySelector(selector);
    var block = link.closest("[data-load-more-block]");
    if (!list || !block) {
      window.location.href = link.href;
      return;
    }
    link.setAttribute("aria-busy", "true");
    link.classList.add("disabled");
    var spinner = link.querySelector(".spinner-border");
    var icon = link.querySelector(".load-more-icon");
    if (spinner) spinner.classList.remove("d-none");
    if (icon) icon.classList.add("d-none");

    fetch(link.href, { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.text();
      })
      .then(function (html) {
        var page = new DOMParser().parseFromString(html, "text/html");
        var rows = page.querySelector(selector);
        if (!rows) throw new Error("no rows on the next page");
        var first = null;
        // A group heading row (data-group, e.g. raw materials / packaging) starts each page:
        // it is left out when the list already ends in that group.
        var headings = list.querySelectorAll(":scope > [data-group]");
        var group = headings.length ? headings[headings.length - 1].dataset.group : null;
        Array.prototype.slice.call(rows.children).forEach(function (row) {
          if (row.hasAttribute("data-empty-row")) return;
          if (row.hasAttribute("data-group")) {
            if (row.dataset.group === group) return;
            group = row.dataset.group;
          }
          var added = document.importNode(row, true);
          list.appendChild(added);
          first = first || added;
        });
        var next = page.querySelector("[data-load-more-block]");
        if (next) {
          next = document.importNode(next, true);
          var start = next.querySelector(".load-more-start"); // the first rows are already here
          if (start) start.remove();
          block.replaceWith(next);
        } else {
          block.remove();
        }
        if (first) { // keyboard and screen-reader users carry on from the first new row
          first.setAttribute("tabindex", "-1");
          first.focus({ preventScroll: true });
        }
      })
      .catch(function () {
        window.location.href = link.href;
      });
  });

  // Number inputs (data-number="<decimals>"): digits grouped by spaces and a decimal comma
  // as they are typed, "1250.5" -> "1 250,5", the way the server reads them (QuantityField).
  function formatNumber(text, decimals) {
    var clean = text.replace(/[\s  ']/g, "").replace(/\./g, ",").replace(/[^\d,]/g, "");
    var comma = clean.indexOf(",");
    var whole = comma === -1 ? clean : clean.slice(0, comma);
    var fraction = comma === -1 ? "" : clean.slice(comma + 1).replace(/,/g, "").slice(0, decimals);
    whole = whole.replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+(?!\d))/g, " ");
    if (comma === -1 || decimals === 0) return whole;
    return (whole || "0") + "," + fraction;
  }
  // How many digits and commas come before the caret: the caret goes back after as many.
  function significant(text) {
    return text.replace(/[^\d,.]/g, "").length;
  }
  function reformat(input) {
    var decimals = Number(input.dataset.number) || 0;
    var caret = input.selectionStart === null ? input.value.length : input.selectionStart;
    var before = significant(input.value.slice(0, caret));
    var formatted = formatNumber(input.value, decimals);
    if (formatted === input.value) return;
    input.value = formatted;
    var position = 0;
    for (var seen = 0; position < formatted.length && seen < before; position++) {
      if (/[\d,]/.test(formatted[position])) seen++;
    }
    if (document.activeElement === input) input.setSelectionRange(position, position);
  }
  document.addEventListener("input", function (event) {
    if (event.target.matches && event.target.matches("input[data-number]")) reformat(event.target);
  });
  document.querySelectorAll("input[data-number]").forEach(reformat);

  // Receiving an order: "all" fills a line with what is still to come, "All arrived" every line.
  function fill(button) {
    var input = document.getElementById(button.dataset.fill);
    if (!input) return;
    input.value = button.dataset.value.replace(".", ",");
    input.dispatchEvent(new Event("input", { bubbles: true })); // app.js groups the digits
  }
  document.querySelectorAll("[data-fill]").forEach(function (button) {
    button.addEventListener("click", function () { fill(button); });
  });
  document.querySelectorAll("[data-fill-all]").forEach(function (button) {
    button.addEventListener("click", function () {
      // The button may sit outside its form (in the card's title): data-fill-all names the form's id.
      var form = document.getElementById(button.dataset.fillAll) || button.closest("form");
      if (form) form.querySelectorAll("[data-fill]").forEach(fill);
    });
  });
});
