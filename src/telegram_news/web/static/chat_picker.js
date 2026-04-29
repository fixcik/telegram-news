(function () {
  // Tabs
  document.addEventListener("click", function (e) {
    const tabBtn = e.target.closest(".picker-tab");
    if (!tabBtn) return;
    e.preventDefault();
    const picker = tabBtn.closest(".chat-picker");
    if (!picker) return;
    const tab = tabBtn.dataset.tab;
    picker.querySelectorAll(".picker-tab").forEach((b) => b.classList.toggle("active", b === tabBtn));
    picker.querySelectorAll(".picker-pane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== tab));
  });

  // Result row → add chip locally.
  // mousedown (not click) so it fires before any label-forwarding or focus change.
  document.addEventListener("mousedown", function (e) {
    const row = e.target.closest(".picker-result");
    if (!row) return;
    e.preventDefault();
    e.stopPropagation();

    const picker = row.closest(".chat-picker");
    if (!picker) return;
    const name = picker.dataset.name;
    const mode = picker.dataset.mode;
    const chips = picker.querySelector(".chip-list");
    if (!chips) return;
    if (mode === "single") chips.innerHTML = "";

    const peerId = row.dataset.peerId;
    const title = row.dataset.title || "";
    const username = row.dataset.username || "";
    const kind = row.dataset.kind || "";
    const icon = kind === "channel" ? "📢" : "👥";
    const usernameHtml = username ? `<small class="chip-username">@${escapeHtml(username)}</small>` : "";

    chips.insertAdjacentHTML("beforeend", `
      <span class="chip" data-peer-id="${peerId}">
        <span class="chip-icon">${icon}</span>
        <span class="chip-title">${escapeHtml(title)}</span>
        ${usernameHtml}
        <button type="button" onclick="removeChip(this)" aria-label="Удалить">✕</button>
        <input type="hidden" name="${name}" value="${peerId}">
        <input type="hidden" name="${name}__original" value="">
        <input type="hidden" name="${name}__title" value="${escapeHtml(title)}">
      </span>
    `);

    // Close popover and clear search.
    const search = picker.querySelector(".picker-search");
    const results = picker.querySelector(".picker-results");
    if (results) results.innerHTML = "";
    if (search) search.value = "";
  });

  // After /api/resolve appends a chip into chip-list, prune old chips in single-mode
  // and clear the link input on success.
  document.body.addEventListener("htmx:afterSwap", function (evt) {
    const target = evt.detail.target;
    if (!target || !target.classList.contains("chip-list")) return;
    const picker = target.closest(".chat-picker");
    if (!picker) return;
    if (picker.dataset.mode === "single") {
      const chips = target.querySelectorAll(".chip");
      while (chips.length > 1) {
        chips[0].remove();
      }
    }
    // Clear link input + error after successful resolve
    const linkInput = picker.querySelector(".picker-link-input");
    if (linkInput) linkInput.value = "";
    const linkErr = picker.querySelector(".picker-link-error");
    if (linkErr) linkErr.innerHTML = "";
  });

  // Close popover when clicking outside the picker.
  document.addEventListener("mousedown", function (e) {
    document.querySelectorAll(".chat-picker").forEach((picker) => {
      if (picker.contains(e.target)) return;
      const results = picker.querySelector(".picker-results");
      if (results) results.innerHTML = "";
    });
  });

  window.removeChip = function (btn) {
    const chip = btn.closest(".chip");
    if (chip) chip.remove();
  };

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])
    );
  }
})();
