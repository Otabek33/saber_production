// Production and sales forms: long material, product and client lists become searchable (Select2).
(function () {
  "use strict";

  if (!(window.jQuery && jQuery.fn.select2)) return;

  function makeSearchable(root) {
    $(root).find("select[data-searchable]").each(function () {
      if (this.name && this.name.indexOf("__prefix__") !== -1) return; // empty formset template
      // data-tags="true": a value that isn't in the list can be typed in (e.g. a new client).
      $(this).select2({ width: "100%", language: document.documentElement.lang, tags: this.dataset.tags === "true" });
    });
  }

  makeSearchable(document);

  // "Add a line" (recipes, receipts): copies the formset's empty form. The button names the
  // formset prefix; its rows are in [data-lines="<prefix>"], the empty form in #<prefix>-empty-line.
  document.querySelectorAll("[data-add-line]").forEach(function (button) {
    var prefix = button.dataset.addLine;
    button.addEventListener("click", function () {
      var total = document.getElementById("id_" + prefix + "-TOTAL_FORMS");
      var template = document.getElementById(prefix + "-empty-line");
      var body = document.querySelector("[data-lines='" + prefix + "']");
      var index = Number(total.value);
      var row = document.createElement("tbody");
      row.innerHTML = template.innerHTML.replace(/__prefix__/g, index);
      var line = row.firstElementChild;
      body.appendChild(line);
      total.value = index + 1;
      makeSearchable(line);
      showUnits(line);
      numberLines(body);
      $(line).find("select[data-searchable]").first().select2("open"); // straight on to choosing
    });
  });

  // "Remove the line": the line is hidden and its DELETE set, so the formset skips it (forms
  // keep their numbers). The last line left is only cleared: a receipt always has one to fill.
  function numberLines(body) {
    var number = 0;
    $(body).children("tr").each(function () {
      if (!this.hidden) $(this).find("[data-line-number]").text(++number);
    });
  }
  document.querySelectorAll("[data-lines]").forEach(numberLines);
  $(document).on("click", "[data-remove-line]", function () {
    var row = this.closest("tr");
    var body = row.parentElement;
    var shown = $(body).children("tr").filter(function () { return !this.hidden; });
    if (shown.length > 1) {
      $(row).find("[data-delete]").val("on");
      row.hidden = true;
    } else {
      $(row).find("select").val(null).trigger("change");
      $(row).find("input:not([type=hidden])").val("");
    }
    numberLines(body);
  });

  // Receipt form: the materials are entered once the supplier is chosen, and each line shows its unit.
  var units = document.getElementById("material-units");
  var unitOf = units ? JSON.parse(units.textContent) : {};
  function showUnits(root) {
    $(root).find("select[name$='-material']").each(function () {
      var cell = $(this).closest("tr").find("[data-unit]");
      var update = function () { cell.text(unitOf[this.value] || ""); }.bind(this);
      $(this).on("change", update);
      update();
    });
  }
  showUnits(document);

  var supplier = document.querySelector("[data-supplier-first]");
  if (supplier) {
    var lines = document.querySelector("[data-after-supplier]");
    var reveal = function () {
      lines.hidden = !supplier.value;
    };
    $(supplier).on("change", reveal); // Select2 reports changes through jQuery
    reveal();
  }

  // Production calculation: recompute every line as norms, defects and quantities are typed,
  // the same way the server writes them off (see RunLine in production/models.py).
  var calculation = document.querySelector("[data-calculation]");
  if (calculation) {
    var materials = JSON.parse(document.getElementById("calc-materials").textContent);
    var volume = Number(calculation.dataset.volume);
    var formatter = function (digits) {
      return new Intl.NumberFormat(document.documentElement.lang || "ru", { maximumFractionDigits: digits });
    };
    var amount = formatter(4);
    var norm = formatter(10);

    // "0,04752", "0.04752" or a fraction like "1/12", as NormField reads them.
    var parse = function (text) {
      text = String(text || "").replace(/[\s  ']/g, "").replace(",", ".");
      var fraction = text.match(/^(\d+(?:\.\d+)?)\/(\d+(?:\.\d+)?)$/);
      if (fraction) return Number(fraction[2]) ? Number(fraction[1]) / Number(fraction[2]) : NaN;
      return text === "" ? NaN : Number(text);
    };
    var field = function (row, name) {
      return row.querySelector("[data-calc='" + name + "']");
    };
    var out = function (row, name, text) {
      var cell = row.querySelector("[data-calc-out='" + name + "']");
      if (cell) cell.textContent = text;
      return cell;
    };

    var recalc = function () {
      var quantity = parse(calculation.querySelector("[data-calc='quantity']").value) || 0;
      var brewed = parse(calculation.querySelector("[data-calc='batch']").value) || quantity;
      var litres = calculation.querySelector("[data-calc-out='litres']");
      litres.textContent = brewed ? amount.format(brewed * volume) : "—";
      var number = 0;
      calculation.querySelectorAll("[data-calc-line]").forEach(function (row) {
        var removed = row.querySelector("input[name$='-DELETE']");
        row.classList.toggle("opacity-50", Boolean(removed && removed.checked));
        number += 1;
        out(row, "number", number);
        var material = materials[field(row, "material").value];
        var entered = parse(field(row, "norm").value);
        if (!material || isNaN(entered)) {
          ["basis", "per-unit", "use", "total", "loss"].forEach(function (name) { out(row, name, ""); });
          return;
        }
        var raw = material.kind === "raw";
        out(row, "basis", raw ? calculation.dataset.basisRaw : calculation.dataset.basisUnit);
        var perUnit = raw ? (entered * volume) / 1000 : entered;
        var use = perUnit * (raw ? brewed : quantity);
        var defect = parse(field(row, "defect").value) || 0;
        var allowed = parse(field(row, "allowed").value);
        if (isNaN(allowed)) allowed = 2;
        out(row, "per-unit", norm.format(perUnit));
        out(row, "use", amount.format(use) + " " + material.unit);
        out(row, "total", amount.format(use + defect) + " " + material.unit);
        var loss = use ? (defect / use) * 100 : 0;
        var lossCell = out(row, "loss", defect ? formatter(2).format(loss) + "%" : "—");
        lossCell.classList.toggle("text-danger", loss > allowed);
        lossCell.classList.toggle("fw-semibold", loss > allowed);
      });
    };

    // Select2 reports changes through jQuery, so listen there too.
    $(calculation).on("input change", recalc);
    recalc();

    var addCalcLine = document.querySelector("[data-calc-add-line]");
    if (addCalcLine) {
      addCalcLine.addEventListener("click", function () {
        var total = document.getElementById("id_lines-TOTAL_FORMS");
        var template = document.getElementById("calc-empty-line");
        var body = calculation.querySelector("[data-calc-lines]");
        var index = Number(total.value);
        var holder = document.createElement("tbody");
        holder.innerHTML = template.innerHTML.replace(/__prefix__/g, index);
        var line = holder.firstElementChild;
        body.appendChild(line);
        total.value = index + 1;
        makeSearchable(line);
        recalc();
      });
    }
  }
})();
