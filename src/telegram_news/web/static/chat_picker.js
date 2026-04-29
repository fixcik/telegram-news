(function () {
  // Tabs
  document.addEventListener("click", function (e) {
    const tabBtn = e.target.closest(".picker-tab");
    if (tabBtn) {
      const picker = tabBtn.closest(".chat-picker");
      const tab = tabBtn.dataset.tab;
      picker.querySelectorAll(".picker-tab").forEach((b) => b.classList.toggle("active", b === tabBtn));
      picker.querySelectorAll(".picker-pane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== tab));
    }
  });

  // Click on a result row → add chip locally
  document.addEventListener("click", function (e) {
    const row = e.target.closest(".picker-result");
    if (!row) return;
    const picker = row.closest(".chat-picker");
    const name = picker.dataset.name;
    const mode = picker.dataset.mode;
    const chips = picker.querySelector(".chip-list");
    if (mode === "single") chips.innerHTML = "";

    const peerId = row.dataset.peerId;
    const title = row.dataset.title;
    const username = row.dataset.username;
    const kind = row.dataset.kind;
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

    const search = picker.querySelector(".picker-search");
    if (search) {
      search.value = "";
      picker.querySelector(".picker-results").innerHTML = "";
      search.focus();
    }
  });

  // After /api/resolve returns a chip, prune older chips in single-mode
  document.body.addEventListener("htmx:afterSwap", function (evt) {
    const target = evt.detail.target;
    if (!target || !target.classList.contains("chip-list")) return;
    const picker = target.closest(".chat-picker");
    if (picker && picker.dataset.mode === "single") {
      const chips = target.querySelectorAll(".chip");
      while (chips.length > 1) {
        chips[0].remove();
      }
    }
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
