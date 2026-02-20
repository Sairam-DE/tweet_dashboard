(function () {
  function initSentimentWidget(widget) {
    if (!widget) return;

    var total = 0;
    try {
      total = parseInt(widget.getAttribute("data-total") || "0", 10) || 0;
    } catch (e) {
      total = 0;
    }

    var pieLabel = widget.querySelector("[data-pie-label]");
    var pieValue = widget.querySelector("[data-pie-value]");
    var pieExtra = widget.querySelector("[data-pie-extra]");
    var hoverBox = widget.querySelector("[data-sentiment-hover]");
    var hoverLabel = widget.querySelector("[data-hover-label]");
    var hoverMeta = widget.querySelector("[data-hover-meta]");
    var hoverFill = widget.querySelector("[data-hover-fill]");

    var defaultPieLabel = pieLabel ? pieLabel.textContent : "";
    var defaultPieValue = pieValue ? pieValue.textContent : "";
    var rowSelector = "[data-sentiment-row]";

    function applyFromRow(row, mode) {
      if (!row) return;
      var isBaseline = mode === "baseline";

      var label = row.getAttribute("data-label") || "Sentiment";
      var count = row.getAttribute("data-count") || "0";
      var pct = row.getAttribute("data-pct") || "0";
      var tone = row.getAttribute("data-tone") || "";

      if (widget && widget.dataset) {
        widget.dataset.focusTone = tone;
      }

      if (pieLabel) pieLabel.textContent = label;
      if (pieValue) pieValue.textContent = pct + "%";
      if (pieExtra) pieExtra.textContent = count + " / " + String(total) + " tweets";

      if (hoverBox) {
        if (hoverBox.dataset) hoverBox.dataset.tone = tone;
      }
      if (hoverLabel) hoverLabel.textContent = label;
      if (hoverMeta) {
        hoverMeta.textContent = (isBaseline ? "Selected: " : "") + count + " tweets (" + pct + "%)";
      }
      if (hoverFill) hoverFill.style.width = pct + "%";
    }

    var rows = widget.querySelectorAll(rowSelector);
    if (!rows || !rows.length) return;

    var selectedLabel = (widget.getAttribute("data-selected") || "").trim();
    var baselineRow = null;
    if (selectedLabel) {
      rows.forEach(function (row) {
        if ((row.getAttribute("data-label") || "").trim() === selectedLabel) {
          baselineRow = row;
        }
      });
    }

    function reset() {
      if (baselineRow) {
        applyFromRow(baselineRow, "baseline");
        return;
      }

      if (widget && widget.dataset) {
        delete widget.dataset.focusTone;
      }
      if (pieLabel) pieLabel.textContent = defaultPieLabel || "All tweets";
      if (pieValue) pieValue.textContent = defaultPieValue || String(total);
      if (pieExtra) pieExtra.textContent = "";

      if (hoverBox) {
        if (hoverBox.dataset) delete hoverBox.dataset.tone;
      }
      if (hoverLabel) hoverLabel.textContent = "Hover a sentiment";
      if (hoverMeta) hoverMeta.textContent = "Click a bar to filter tweets.";
      if (hoverFill) hoverFill.style.width = "0%";
    }

    var activeRow = null;

    function findRow(target) {
      var node = target;
      while (node && node !== widget) {
        if (node.getAttribute && node.getAttribute("data-sentiment-row") !== null) {
          return node;
        }
        node = node.parentNode;
      }
      return null;
    }

    function setActiveRow(row, mode) {
      if (!row) return;
      if (row === activeRow) return;
      activeRow = row;
      applyFromRow(row, mode || "hover");
    }

    function handleOver(event) {
      var row = findRow(event.target);
      if (!row) return;
      setActiveRow(row, "hover");
    }

    function handleLeave() {
      activeRow = null;
      reset();
    }

    function handleFocusIn(event) {
      var row = findRow(event.target);
      if (!row) return;
      setActiveRow(row, "hover");
    }

    function handleFocusOut(event) {
      var next = event.relatedTarget;
      if (next && widget.contains(next)) return;
      activeRow = null;
      reset();
    }

    if ("PointerEvent" in window) {
      widget.addEventListener("pointerover", handleOver);
      widget.addEventListener("pointerleave", handleLeave);
    } else {
      widget.addEventListener("mouseover", handleOver);
      widget.addEventListener("mouseleave", handleLeave);
    }
    widget.addEventListener("focusin", handleFocusIn);
    widget.addEventListener("focusout", handleFocusOut);

    reset();
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".sentiment-widget").forEach(initSentimentWidget);
  });
})();
