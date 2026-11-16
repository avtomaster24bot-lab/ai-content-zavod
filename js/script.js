(() => {
  const STORAGE_KEY = "zavod-progress-v1";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  function load() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    } catch {
      return {};
    }
  }

  function save(state) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }

  function updateProgress() {
    const boxes = $$(".step input[type=checkbox]");
    const total = boxes.length;
    const done = boxes.filter((b) => b.checked).length;
    const pct = total ? Math.round((done / total) * 100) : 0;
    const fill = $(".fill");
    const label = $(".progress-bar .label");
    if (fill) fill.style.width = pct + "%";
    if (label) label.textContent = `Прогресс: ${done} из ${total} (${pct}%)`;
    boxes.forEach((box) => {
      const step = box.closest(".step");
      if (step) step.classList.toggle("done", box.checked);
    });
  }

  function initChecks() {
    const state = load();
    $$(".step input[type=checkbox]").forEach((box) => {
      if (!box.id) return;
      if (state[box.id]) box.checked = true;
      box.addEventListener("change", () => {
        const next = load();
        if (box.checked) next[box.id] = true;
        else delete next[box.id];
        save(next);
        updateProgress();
      });
    });
    updateProgress();
  }

  function initReset() {
    const btn = $(".reset");
    if (!btn) return;
    btn.addEventListener("click", () => {
      if (!confirm("Сбросить все галочки на этом устройстве?")) return;
      localStorage.removeItem(STORAGE_KEY);
      $$(".step input[type=checkbox]").forEach((box) => {
        box.checked = false;
      });
      updateProgress();
    });
  }

  function initCopy() {
    $$(".pre").forEach((block) => {
      if (block.querySelector(".copy")) return;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "copy";
      btn.textContent = "Копировать";
      btn.addEventListener("click", async () => {
        const text = (block.dataset.copy || block.innerText || "")
          .replace(/^\s*Копировать\s*/, "")
          .trim();
        try {
          await navigator.clipboard.writeText(text);
          btn.textContent = "Скопировано";
          btn.classList.add("ok");
          setTimeout(() => {
            btn.textContent = "Копировать";
            btn.classList.remove("ok");
          }, 1600);
        } catch {
          btn.textContent = "Не вышло, выделите сами";
        }
      });
      block.appendChild(btn);
    });
  }

  function initBudget() {
    const key = "zavod-budget-v1";
    let state = {};
    try {
      state = JSON.parse(localStorage.getItem(key) || "{}");
    } catch {
      state = {};
    }
    $$(".budget-item input[type=checkbox]").forEach((box) => {
      if (state[box.id]) box.checked = true;
      box.addEventListener("change", () => {
        if (box.checked) state[box.id] = true;
        else delete state[box.id];
        localStorage.setItem(key, JSON.stringify(state));
      });
    });
  }

  initChecks();
  initReset();
  initCopy();
  initBudget();
})();
