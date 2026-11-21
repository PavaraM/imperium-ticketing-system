(() => {
  "use strict";

  const readerEl = document.getElementById("reader");
  const statusEl = document.getElementById("camera-status");
  const toggleBtn = document.getElementById("camera-toggle");
  const resultEl = document.getElementById("result");
  const kickerEl = document.getElementById("result-kicker");
  const nameEl = document.getElementById("result-name");
  const batchEl = document.getElementById("result-batch");
  const noteEl = document.getElementById("result-note");
  const admitBtn = document.getElementById("admit");
  const dismissBtn = document.getElementById("dismiss");
  const searchForm = document.getElementById("search-form");
  const searchInput = document.getElementById("search-input");
  const searchSubmit = document.getElementById("search-submit");
  const searchStatus = document.getElementById("search-status");
  const searchResults = document.getElementById("search-results");

  const RESET_DELAY_MS = 1400;
  const DUPLICATE_WINDOW_MS = 4000;

  const KICKERS = {
    valid: "Valid ticket",
    checked_in: "Already checked in",
    revoked: "Ticket revoked",
    unknown: "Unknown ticket",
    payment_due: "Balance payment due",
    payment_overdue: "Balance payment overdue",
  };

  const STATE_WORDS = {
    valid: "Valid",
    checked_in: "Checked in",
    revoked: "Revoked",
    unknown: "Unknown",
    payment_due: "Payment due",
    payment_overdue: "Payment overdue",
  };

  let scanner = null;
  let cameraOn = false;
  let busy = false;
  let current = null;
  let lastScan = { value: "", at: 0 };
  let resetTimer = 0;
  let searching = false;

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  function batchLabel(value) {
    if (!value) {
      return "";
    }
    return /^batch\b/i.test(value) ? value : "Batch " + value;
  }

  function setStatus(text) {
    statusEl.textContent = text;
  }

  async function postJson(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    if (response.status === 401) {
      window.location.assign("/login?next=%2Fscan");
      throw new Error("signed-out");
    }
    const data = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, data };
  }

  function noteFor(data) {
    switch (data.state) {
      case "valid":
        return "Let them in only if this is the person in front of you.";
      case "checked_in": {
        const when = data.checked_in_at ? "at " + data.checked_in_at : "earlier";
        const who = data.checked_in_by ? " by " + data.checked_in_by : "";
        return "Already admitted " + when + who + ". Don’t let a second copy in.";
      }
      case "revoked":
        return "Do not admit. Ask the event admin to check this one.";
      case "payment_due":
        return "Do not admit. The half-ticket balance of " + data.balance_due_display + " is due before the event cutoff.";
      case "payment_overdue":
        return "Do not admit. The balance is overdue; only an admin can record a late payment.";
      default:
        return "No ticket matches this code. Try the manual search below.";
    }
  }

  function render(data) {
    const state = data.state || "unknown";
    resultEl.hidden = false;
    resultEl.className = "scan-result " + (state === "valid" ? "is-valid" : "is-danger");
    kickerEl.textContent = KICKERS[state] || "Ticket";
    nameEl.textContent = data.name || "Unknown ticket";
    batchEl.textContent = batchLabel(data.batch);
    batchEl.hidden = !data.batch;
    noteEl.textContent = noteFor(data);
    admitBtn.hidden = state !== "valid";
    admitBtn.disabled = false;
    dismissBtn.textContent = state === "valid" ? "Not this guest" : "Scan next";
    resultEl.scrollIntoView({
      behavior: reduceMotion.matches ? "auto" : "smooth",
      block: "nearest",
    });
  }

  function showFailure(status) {
    current = null;
    resultEl.hidden = false;
    resultEl.className = "scan-result is-danger";
    kickerEl.textContent = status === 422 ? "Not a ticket" : "Lookup failed";
    nameEl.textContent = status === 422 ? "Unreadable code" : "Could not reach the server";
    batchEl.hidden = true;
    noteEl.textContent = "Check the connection, then scan again or use manual search.";
    admitBtn.hidden = true;
    dismissBtn.textContent = "Scan next";
  }

  function resetPanel() {
    window.clearTimeout(resetTimer);
    resultEl.hidden = true;
    admitBtn.hidden = true;
    current = null;
    busy = false;
  }

  async function lookup(requestBody) {
    if (busy) {
      return;
    }
    busy = true;
    window.clearTimeout(resetTimer);
    try {
      const { ok, status, data } = await postJson("/api/scan/lookup", requestBody);
      if (!ok) {
        showFailure(status);
        return;
      }
      current = Object.assign({}, data, { request: requestBody });
      render(data);
    } catch (error) {
      if (error.message !== "signed-out") {
        showFailure(0);
      }
    } finally {
      busy = false;
    }
  }

  async function admit() {
    if (!current || current.state !== "valid" || busy) {
      return;
    }
    busy = true;
    admitBtn.disabled = true;
    const requestBody = current.request;
    try {
      const { ok, data } = await postJson("/api/scan/checkin", requestBody);
      current = Object.assign({}, data, { request: requestBody });
      if (ok && data.just_checked_in) {
        resultEl.className = "scan-result is-valid";
        kickerEl.textContent = "Let in";
        nameEl.textContent = data.name || "Guest";
        batchEl.textContent = batchLabel(data.batch);
        batchEl.hidden = !data.batch;
        noteEl.textContent =
          "Admitted at " + (data.checked_in_at || "now") + ". Ready for the next guest.";
        admitBtn.hidden = true;
        dismissBtn.textContent = "Next guest";
        resetTimer = window.setTimeout(resetPanel, RESET_DELAY_MS);
      } else {
        render(data);
        busy = false;
      }
    } catch (error) {
      busy = false;
      if (error.message !== "signed-out") {
        showFailure(0);
      }
    }
  }

  function onScan(text) {
    const value = String(text || "").trim();
    if (!value || busy) {
      return;
    }
    const now = Date.now();
    if (value === lastScan.value && now - lastScan.at < DUPLICATE_WINDOW_MS) {
      return;
    }
    lastScan = { value, at: now };
    lookup({ token: value });
  }

  function qrbox(width, height) {
    const edge = Math.floor(Math.min(width, height) * 0.72);
    return { width: Math.max(160, edge), height: Math.max(160, edge) };
  }

  async function startCamera() {
    if (cameraOn) {
      return;
    }
    if (!window.Html5Qrcode) {
      setStatus("Scanner library did not load. Use the manual search below.");
      return;
    }
    setStatus("Starting camera…");
    try {
      scanner = new window.Html5Qrcode("reader", {
        verbose: false,
        formatsToSupport: [window.Html5QrcodeSupportedFormats.QR_CODE],
      });
      await scanner.start(
        { facingMode: "environment" },
        { fps: 10, qrbox: qrbox },
        onScan,
        () => {},
      );
      cameraOn = true;
      toggleBtn.hidden = false;
      toggleBtn.textContent = "Stop camera";
      setStatus("Ready. Point the camera at a guest’s QR.");
    } catch (error) {
      scanner = null;
      cameraOn = false;
      toggleBtn.hidden = true;
      setStatus("Camera unavailable. Allow camera access, or use the manual search below.");
    }
  }

  async function stopCamera() {
    if (!scanner || !cameraOn) {
      return;
    }
    try {
      await scanner.stop();
      scanner.clear();
    } catch (error) {
      // The camera may already be gone (tab closed, permission revoked).
    }
    scanner = null;
    cameraOn = false;
    toggleBtn.hidden = false;
    toggleBtn.textContent = "Start camera";
  }

  toggleBtn.addEventListener("click", () => {
    if (cameraOn) {
      stopCamera();
    } else {
      startCamera();
    }
  });

  admitBtn.addEventListener("click", admit);
  dismissBtn.addEventListener("click", resetPanel);

  searchForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = searchInput.value.trim();
    if (!query || searching) {
      return;
    }
    searching = true;
    searchStatus.textContent = "Searching…";
    searchSubmit.disabled = true;
    searchSubmit.textContent = "Searching…";
    searchResults.setAttribute("aria-busy", "true");
    searchResults.replaceChildren();
    try {
      const response = await fetch("/api/scan/search?q=" + encodeURIComponent(query), {
        headers: { Accept: "application/json" },
      });
      if (response.status === 401) {
        window.location.assign("/login?next=%2Fscan");
        return;
      }
      const items = await response.json();
      if (!Array.isArray(items)) {
        throw new Error("unexpected payload");
      }
      renderSearch(items, query);
    } catch (error) {
      searchStatus.textContent = "Search failed. Check the connection and try again.";
    } finally {
      searching = false;
      searchSubmit.disabled = false;
      searchSubmit.textContent = "Search";
      searchResults.removeAttribute("aria-busy");
    }
  });

  function renderSearch(items, query) {
    searchResults.replaceChildren();
    if (!items.length) {
      searchStatus.textContent = "No guest named “" + query + "”.";
      return;
    }
    searchStatus.textContent =
      items.length === 1 ? "1 guest found." : items.length + " guests found.";
    for (const item of items) {
      const row = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ticket-link";

      const main = document.createElement("span");
      main.className = "ticket-main";
      const name = document.createElement("span");
      name.className = "ticket-name";
      name.textContent = item.name;
      main.append(name);
      if (item.batch) {
        const meta = document.createElement("span");
        meta.className = "ticket-meta";
        meta.textContent = batchLabel(item.batch);
        main.append(meta);
      }

      const badge = document.createElement("span");
      badge.className = "badge badge-" + (item.state || "unknown");
      badge.textContent = STATE_WORDS[item.state] || "Unknown";

      button.append(main, badge);
      button.addEventListener("click", () => {
        resetPanel();
        lookup({ ticket_id: item.ticket_id });
      });
      row.append(button);
      searchResults.append(row);
    }
  }

  window.addEventListener("pagehide", () => {
    if (cameraOn) {
      stopCamera();
    }
  });

  startCamera();
})();
