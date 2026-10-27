// Cash desk pages: operation form behaviour and confirmations.
document.addEventListener("DOMContentLoaded", function () {
  "use strict";

  // Select2 (when loaded) makes long lists searchable: type to find a counterparty or article.
  var $ = window.jQuery;
  var hasSelect2 = !!($ && $.fn.select2);

  function makeSearchable(select) {
    if (!hasSelect2) return;
    $(select).select2({
      width: "100%",
      language: document.documentElement.lang,
      // Hide options (and whole groups) disabled for the chosen type, like the plain <select> does.
      templateResult: function (item) {
        return item.element && item.element.disabled ? null : item.text;
      },
    });
  }

  // Select2 announces changes through jQuery, which plain addEventListener doesn't hear.
  function onChange(element, handler) {
    if (hasSelect2) $(element).on("change", handler);
    else element.addEventListener("change", handler);
  }

  function refresh(select) {
    if (hasSelect2) $(select).trigger("change.select2");
  }

  // Operation form: show only the categories of the chosen type (Приход / Расход / Перевод),
  // and the "to account" fields only for transfers.
  document.querySelectorAll("[data-operation-form]").forEach(function (form) {
    var kindInputs = form.querySelectorAll("[data-operation-kind]");
    var categorySelect = form.querySelector("[data-category-select]");
    var transferFields = form.querySelectorAll("[data-transfer-only]");

    function selectedKind() {
      var checked = form.querySelector("[data-operation-kind]:checked");
      return checked ? checked.value : "";
    }

    // An article of type "both" fits income and expense (e.g. sales and their refunds).
    function fits(articleKind, kind) {
      return articleKind === kind || (articleKind === "both" && kind !== "transfer");
    }

    function update() {
      var kind = selectedKind();
      categorySelect.querySelectorAll("optgroup").forEach(function (group) {
        var matches = fits(group.dataset.kind, kind);
        group.hidden = !matches;
        group.disabled = !matches;
      });
      var current = categorySelect.selectedOptions[0];
      if (current && current.dataset.kind && !fits(current.dataset.kind, kind)) {
        categorySelect.value = "";
      }
      refresh(categorySelect);
      transferFields.forEach(function (field) {
        field.classList.toggle("d-none", kind !== "transfer");
      });
    }

    kindInputs.forEach(function (input) {
      input.addEventListener("change", update);
    });

    // Bank: picking a counterparty selects its article's category (the one named like the
    // article, else the article's first), if it fits the chosen type.
    var counterpartySelect = form.querySelector("[data-counterparty-select]");
    if (counterpartySelect) {
      onChange(counterpartySelect, function () {
        var article = counterpartySelect.selectedOptions[0] && counterpartySelect.selectedOptions[0].dataset.article;
        if (!article) return;
        var options = Array.prototype.filter.call(categorySelect.options, function (option) {
          return option.dataset.article === article && !option.parentElement.disabled;
        });
        var sameName = options.filter(function (option) {
          return option.textContent.trim() === option.parentElement.label;
        });
        var choice = sameName[0] || options[0];
        if (choice) {
          categorySelect.value = choice.value;
          refresh(categorySelect);
        }
      });
    }
    form.querySelectorAll("[data-searchable]").forEach(makeSearchable);
    update();
  });

  // Ledger column groups (Статья, Приход, Расход, Остаток) collapse and expand like
  // Excel's outline buttons. The choice is remembered in this browser.
  var ledger = document.querySelector("[data-ledger]");
  if (ledger) {
    var storageKey = "ledger-collapsed-groups";
    var collapsed = [];
    try {
      collapsed = JSON.parse(localStorage.getItem(storageKey)) || [];
    } catch (e) {
      // Storage unavailable or corrupt: start with every group expanded.
    }

    function applyGroups() {
      ["article", "income", "expense", "balance"].forEach(function (group) {
        var isCollapsed = collapsed.indexOf(group) !== -1;
        ledger.classList.toggle("collapse-" + group, isCollapsed);
        ledger.querySelectorAll('[data-ledger-toggle="' + group + '"]').forEach(function (button) {
          button.setAttribute("aria-expanded", isCollapsed ? "false" : "true");
        });
      });
    }

    ledger.querySelectorAll("[data-ledger-toggle]").forEach(function (button) {
      button.addEventListener("click", function () {
        var group = button.dataset.ledgerToggle;
        var index = collapsed.indexOf(group);
        if (index === -1) {
          collapsed.push(group);
        } else {
          collapsed.splice(index, 1);
        }
        try {
          localStorage.setItem(storageKey, JSON.stringify(collapsed));
        } catch (e) {
          // Not remembered; still applies to this page.
        }
        applyGroups();
      });
    });
    applyGroups();
  }

  // Ask before submitting buttons that carry data-confirm (e.g. cancelling an operation).
  document.querySelectorAll("[data-confirm]").forEach(function (button) {
    button.addEventListener("click", function (event) {
      if (!window.confirm(button.dataset.confirm)) {
        event.preventDefault();
      }
    });
  });
});
