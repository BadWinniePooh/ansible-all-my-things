// Click-to-sort for any <table data-sortable> with <th data-sort="text|number">
// header cells. Delegated on document rather than bound per-element, so it
// keeps working after htmx swaps a table back in (e.g. the server-size
// live/static toggle on the create page) without any rebinding step.
(function () {
  function cellValue(row, index, type) {
    var cell = row.children[index];
    var text = cell ? cell.textContent.trim() : "";
    if (type === "number") {
      var n = parseFloat(text.replace(/[^0-9.-]/g, ""));
      return isNaN(n) ? -Infinity : n;
    }
    return text.toLowerCase();
  }

  document.addEventListener("click", function (event) {
    var th = event.target.closest("th[data-sort]");
    if (!th) return;
    var table = th.closest("table[data-sortable]");
    if (!table) return;
    var headerRow = th.parentElement;
    var index = Array.prototype.indexOf.call(headerRow.children, th);
    var type = th.getAttribute("data-sort");
    var tbody = table.tBodies[0];
    if (!tbody) return;

    var ascending = th.getAttribute("data-sort-dir") !== "asc";
    Array.prototype.forEach.call(headerRow.children, function (other) {
      other.removeAttribute("data-sort-dir");
    });
    th.setAttribute("data-sort-dir", ascending ? "asc" : "desc");

    var rows = Array.prototype.slice.call(tbody.rows);
    rows.sort(function (a, b) {
      var va = cellValue(a, index, type);
      var vb = cellValue(b, index, type);
      if (va < vb) return ascending ? -1 : 1;
      if (va > vb) return ascending ? 1 : -1;
      return 0;
    });
    rows.forEach(function (row) {
      tbody.appendChild(row);
    });
  });
})();
