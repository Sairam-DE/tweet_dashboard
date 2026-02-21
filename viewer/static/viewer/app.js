(function () {
  function prefersReducedMotionEnabled() {
    try {
      return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch (e) {
      return false;
    }
  }

  function parseNumber(value, fallback) {
    var parsed = parseFloat(value);
    if (Number.isNaN(parsed)) return fallback;
    return parsed;
  }

  function formatCounter(value, decimals) {
    if (decimals > 0) {
      return value.toFixed(decimals);
    }
    try {
      return Math.round(value).toLocaleString();
    } catch (e) {
      return String(Math.round(value));
    }
  }

  function animateCounter(counter, immediate) {
    if (!counter || counter.dataset.counterAnimated === "1") return;
    counter.dataset.counterAnimated = "1";

    var rawTarget = counter.getAttribute("data-count-to") || counter.textContent || "0";
    var target = parseNumber(rawTarget, 0);
    var decimals = rawTarget.indexOf(".") !== -1 ? 1 : 0;

    if (immediate) {
      counter.textContent = formatCounter(target, decimals);
      return;
    }

    var duration = 900;
    var startTime = null;
    var startValue = 0;

    function tick(timestamp) {
      if (startTime === null) startTime = timestamp;
      var elapsed = timestamp - startTime;
      var progress = Math.min(elapsed / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3);
      var current = startValue + (target - startValue) * eased;
      counter.textContent = formatCounter(current, decimals);
      if (progress < 1) {
        window.requestAnimationFrame(tick);
      }
    }

    counter.textContent = "0";
    window.requestAnimationFrame(tick);
  }

  function animateLandingBars(scope, immediate) {
    if (!scope) return;
    var bars = scope.querySelectorAll(".landing-bar-fill[data-width]");
    bars.forEach(function (bar, index) {
      if (bar.dataset.barAnimated === "1") return;
      bar.dataset.barAnimated = "1";
      var width = Math.max(0, Math.min(parseNumber(bar.getAttribute("data-width"), 0), 100));
      if (immediate) {
        bar.style.width = width + "%";
        return;
      }
      window.setTimeout(function () {
        bar.style.width = width + "%";
      }, 110 + index * 90);
    });
  }

  function revealDelay(node) {
    var raw = node.getAttribute("data-reveal-delay");
    if (raw !== null && raw !== "") {
      return Math.max(0, parseInt(raw, 10) || 0);
    }
    var indexFromStyle = parseInt((node.style.getPropertyValue("--card-index") || "0").trim(), 10) || 0;
    return Math.max(0, indexFromStyle * 70);
  }

  function prepareTypewriterCards(root, immediate) {
    if (!root) return;
    var cards = root.querySelectorAll("[data-typewriter-card]");
    if (!cards.length) return;

    cards.forEach(function (card) {
      var targets = card.querySelectorAll("[data-type-target]");
      targets.forEach(function (target) {
        if (!target.dataset.fullText) {
          target.dataset.fullText = target.textContent || "";
        }
        if (immediate) {
          target.textContent = target.dataset.fullText;
        } else {
          target.textContent = "";
        }
        target.classList.remove("type-caret");
      });
      if (immediate) {
        card.dataset.typePlayed = "1";
      }
    });
  }

  function runTypewriterCard(card, immediate) {
    if (!card || !card.hasAttribute("data-typewriter-card")) return;
    if (card.dataset.typePlayed === "1") return;
    card.dataset.typePlayed = "1";

    var targets = card.querySelectorAll("[data-type-target]");
    if (!targets.length) return;

    if (immediate) {
      targets.forEach(function (target) {
        target.textContent = target.dataset.fullText || target.textContent || "";
        target.classList.remove("type-caret");
      });
      return;
    }

    function typeTarget(index) {
      if (index >= targets.length) return;
      var target = targets[index];
      var fullText = target.dataset.fullText || "";
      var speed = index === 0 ? 18 : 12;
      var cursor = 0;
      target.classList.add("type-caret");

      function step() {
        target.textContent = fullText.slice(0, cursor);
        cursor += 1;
        if (cursor <= fullText.length) {
          window.setTimeout(step, speed);
          return;
        }
        target.classList.remove("type-caret");
        window.setTimeout(function () {
          typeTarget(index + 1);
        }, 80);
      }

      step();
    }

    typeTarget(0);
  }

  function revealNode(node, immediate) {
    if (!node || node.dataset.revealed === "1") return;
    node.dataset.revealed = "1";

    var makeVisible = function () {
      node.classList.add("is-visible");
      runTypewriterCard(node, immediate);
      if (node.hasAttribute("data-counter-group")) {
        node.querySelectorAll(".landing-counter[data-count-to]").forEach(function (counter) {
          animateCounter(counter, immediate);
        });
      }
      if (node.hasAttribute("data-bar-group")) {
        animateLandingBars(node, immediate);
      }
    };

    if (immediate) {
      makeVisible();
      return;
    }

    window.setTimeout(makeVisible, revealDelay(node));
  }

  function initLandingMotion() {
    var root = document.querySelector("[data-landing-root]");
    if (!root) return;

    var prefersReducedMotion = prefersReducedMotionEnabled();

    document.body.classList.add("landing-motion-ready");
    var revealNodes = root.querySelectorAll("[data-reveal]");
    if (!revealNodes.length) return;

    prepareTypewriterCards(root, prefersReducedMotion);

    if (prefersReducedMotion || !("IntersectionObserver" in window)) {
      revealNodes.forEach(function (node) {
        revealNode(node, true);
      });
      root.querySelectorAll(".landing-counter[data-count-to]").forEach(function (counter) {
        animateCounter(counter, true);
      });
      animateLandingBars(root, true);
      return;
    }

    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          revealNode(entry.target, false);
          observer.unobserve(entry.target);
        });
      },
      {
        threshold: 0.28,
        rootMargin: "0px 0px -20% 0px",
      }
    );

    revealNodes.forEach(function (node) {
      observer.observe(node);
    });
  }

  function initLandingType() {
    var typedNodes = document.querySelectorAll(".typed[data-type-text]");
    if (!typedNodes.length) return;

    var prefersReducedMotion = prefersReducedMotionEnabled();

    typedNodes.forEach(function (node) {
      if (node.dataset.typedInit === "1") return;
      node.dataset.typedInit = "1";

      var targetText = node.getAttribute("data-type-text") || "";
      if (prefersReducedMotion) {
        node.textContent = targetText;
        return;
      }

      node.textContent = "";
      var index = 0;
      var speed = 22;

      function typeNext() {
        node.textContent = targetText.slice(0, index);
        index += 1;
        if (index <= targetText.length) {
          window.setTimeout(typeNext, speed);
        }
      }

      window.setTimeout(typeNext, 220);
    });
  }

  function initLandingCharts() {
    var root = document.querySelector("[data-landing-root]");
    if (!root) return;

    var prefersReducedMotion = prefersReducedMotionEnabled();

    root.querySelectorAll("[data-pie-progress]").forEach(function (ring) {
      if (ring.dataset.pieAnimated === "1") return;
      ring.dataset.pieAnimated = "1";

      var target = Math.max(0, Math.min(parseNumber(ring.getAttribute("data-target"), 0), 100));
      if (prefersReducedMotion) {
        ring.style.setProperty("--p", target.toFixed(2));
        return;
      }

      var start = null;
      var duration = 900;

      function animate(ts) {
        if (start === null) start = ts;
        var progress = Math.min((ts - start) / duration, 1);
        var eased = 1 - Math.pow(1 - progress, 3);
        var value = target * eased;
        ring.style.setProperty("--p", value.toFixed(2));
        if (progress < 1) {
          window.requestAnimationFrame(animate);
        }
      }

      ring.style.setProperty("--p", "0");
      window.requestAnimationFrame(animate);
    });

    root.querySelectorAll("[data-pie-spin]").forEach(function (pie, index) {
      if (pie.dataset.pieSpinAnimated === "1") return;
      pie.dataset.pieSpinAnimated = "1";

      if (prefersReducedMotion) {
        pie.classList.add("is-ready");
        return;
      }

      window.setTimeout(function () {
        pie.classList.add("is-ready");
      }, 220 + index * 120);
    });
  }

  function initTiltCards() {
    var cards = document.querySelectorAll("[data-tilt-card]");
    if (!cards.length) return;

    var prefersReducedMotion = prefersReducedMotionEnabled();
    if (prefersReducedMotion) return;

    cards.forEach(function (card) {
      var rect = null;

      function onMove(event) {
        rect = rect || card.getBoundingClientRect();
        var x = (event.clientX - rect.left) / rect.width;
        var y = (event.clientY - rect.top) / rect.height;
        var rotateY = (x - 0.5) * 6;
        var rotateX = (0.5 - y) * 6;
        card.style.transform = "perspective(900px) rotateX(" + rotateX.toFixed(2) + "deg) rotateY(" + rotateY.toFixed(2) + "deg) translateY(-3px)";
      }

      function reset() {
        rect = null;
        card.style.transform = "";
      }

      card.addEventListener("pointermove", onMove);
      card.addEventListener("pointerleave", reset);
      card.addEventListener("pointercancel", reset);
      card.addEventListener("blur", reset, true);
    });
  }

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

  function formatCountdown(totalSeconds) {
    var seconds = Math.max(0, Math.floor(totalSeconds));
    var hours = Math.floor(seconds / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    var secs = seconds % 60;

    if (hours > 0) {
      return hours + "h " + String(minutes).padStart(2, "0") + "m " + String(secs).padStart(2, "0") + "s";
    }
    return minutes + "m " + String(secs).padStart(2, "0") + "s";
  }

  function initRateLimitCountdown() {
    var nodes = document.querySelectorAll("[data-countdown-until]");
    if (!nodes.length) return;

    nodes.forEach(function (node) {
      var targetRaw = node.getAttribute("data-countdown-until") || "";
      var target = parseInt(targetRaw, 10);
      if (!target || Number.isNaN(target)) return;

      function render() {
        var now = Math.floor(Date.now() / 1000);
        var remaining = target - now;
        if (remaining <= 0) {
          node.textContent = "ready now";
          return false;
        }
        node.textContent = formatCountdown(remaining);
        return true;
      }

      if (!render()) return;
      var timer = window.setInterval(function () {
        if (!render()) window.clearInterval(timer);
      }, 1000);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initLandingMotion();
    initLandingCharts();
    initLandingType();
    initTiltCards();
    document.querySelectorAll(".sentiment-widget").forEach(initSentimentWidget);
    initRateLimitCountdown();
  });
})();
