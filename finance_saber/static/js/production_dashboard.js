// Production dashboard chart: units produced (bars) and shipped (line) per day, or per month.
(function () {
  "use strict";

  var source = document.getElementById("production-chart-data");
  var target = document.getElementById("production-chart");
  if (!source || !target || !window.ApexCharts) return;
  var data = JSON.parse(source.textContent);
  var ru = document.documentElement.lang === "ru";
  var dark = document.documentElement.getAttribute("data-bs-theme") === "dark";

  function short(value) {
    var abs = Math.abs(value);
    if (abs >= 1e6) return (value / 1e6).toFixed(1) + (ru ? " млн" : "M");
    if (abs >= 1e3) return (value / 1e3).toFixed(0) + (ru ? " тыс" : "K");
    return String(Math.round(value));
  }

  var series = [{ name: ru ? "Выпущено" : "Produced", type: "column", data: data.produced }];
  if (data.shipped) series.push({ name: ru ? "Отгружено" : "Shipped", type: "line", data: data.shipped });

  new ApexCharts(target, {
    chart: { type: "line", height: 300, toolbar: { show: false }, fontFamily: "inherit", foreColor: "#adb0bb" },
    series: series,
    colors: ["#5d87ff", "#13deb9"],
    stroke: { width: [0, 3], curve: "smooth" },
    plotOptions: { bar: { columnWidth: "55%", borderRadius: 3 } },
    markers: { size: data.labels.length > 31 ? 0 : 3 },
    dataLabels: { enabled: false },
    xaxis: { categories: data.labels, tickAmount: Math.min(data.labels.length, 16), labels: { rotate: -45 } },
    yaxis: { labels: { formatter: short } },
    tooltip: {
      theme: dark ? "dark" : "light",
      shared: true,
      intersect: false,
      y: { formatter: function (v) { return v.toLocaleString("ru-RU") + (ru ? " шт." : " units"); } },
    },
    grid: { borderColor: dark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)", strokeDashArray: 3 },
    legend: { position: "top", horizontalAlign: "right" },
    noData: { text: ru ? "Нет данных за период" : "No data for the period" },
  }).render();
})();
