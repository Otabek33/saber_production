// Dashboard chart: money in and out per day (or month) for the chosen period.
(function () {
  "use strict";

  var source = document.getElementById("cash-chart-data");
  var target = document.getElementById("cash-chart");
  if (!source || !target || !window.ApexCharts) return;
  var data = JSON.parse(source.textContent);
  var ru = document.documentElement.lang === "ru";

  function short(value) {
    var abs = Math.abs(value);
    if (abs >= 1e9) return (value / 1e9).toFixed(1) + (ru ? " млрд" : "B");
    if (abs >= 1e6) return (value / 1e6).toFixed(1) + (ru ? " млн" : "M");
    if (abs >= 1e3) return (value / 1e3).toFixed(0) + (ru ? " тыс" : "K");
    return String(Math.round(value));
  }

  new ApexCharts(target, {
    chart: { type: "bar", height: 300, toolbar: { show: false }, fontFamily: "inherit", foreColor: "#adb0bb" },
    series: [
      { name: ru ? "Поступления" : "Money in", data: data.in },
      { name: ru ? "Расходы" : "Money out", data: data.out },
    ],
    colors: ["#13deb9", "#fa896b"],
    plotOptions: { bar: { columnWidth: "55%", borderRadius: 3 } },
    dataLabels: { enabled: false },
    xaxis: { categories: data.labels, tickAmount: Math.min(data.labels.length, 16), labels: { rotate: -45 } },
    yaxis: { labels: { formatter: short } },
    tooltip: { theme: "dark", y: { formatter: function (v) { return v.toLocaleString("ru-RU"); } } },
    grid: { borderColor: "rgba(0,0,0,0.08)", strokeDashArray: 3 },
    legend: { position: "top", horizontalAlign: "right" },
  }).render();
})();
