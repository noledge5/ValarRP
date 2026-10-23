// ValarRP im Browser (Entscheidungen 184 und 186). Nur Anzeige: Regeln prüft der Server, ohne dieses Skript
// funktioniert jede Seite als Formular. Züge gehen als dasselbe Formular an /spiel/ID/zug; der Server schickt die
// fertigen, maskierten Teile der Seite zurück. Die CSP verbietet Inline-Skripte und Ereignis-Attribute.
"use strict";

document.documentElement.classList.add("js");

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const calm = window.matchMedia("(prefers-reduced-motion: reduce)");

// --- Spielen: Chat ohne Neuladen ---

function initPlay() {
  if (!document.body.classList.contains("seite-spiel")) return;
  const parts = {};
  for (const el of $$("[data-teil]")) parts[el.dataset.teil] = el;
  const side = parts.seite;
  let tab = ($("[data-reiter].aktiv", side) || {}).dataset?.reiter || "status";
  let busy = false;
  let reveal = null;

  function showTab(name) {
    tab = name;
    for (const link of $$("[data-reiter]", side)) {
      const on = link.dataset.reiter === name;
      link.classList.toggle("aktiv", on);
      if (on) link.setAttribute("aria-current", "true");
      else link.removeAttribute("aria-current");
    }
    for (const panel of $$("[data-tafel]", side)) panel.hidden = panel.dataset.tafel !== name;
  }

  function openSide(open) {
    side.classList.toggle("offen", open);
    if (open) ($("[data-reiter].aktiv", side) || side).focus();
  }

  function input() {
    return $("textarea[name=eingabe]", parts.unten);
  }

  function grow(area) {
    area.style.height = "auto";
    area.style.height = Math.min(area.scrollHeight + 2, window.innerHeight * 0.4) + "px";
  }

  function toBottom() {
    const last = parts.verlauf.lastElementChild;
    if (last) last.scrollIntoView({ block: "end", behavior: calm.matches ? "auto" : "smooth" });
  }

  function setBusy(on) {
    busy = on;
    parts.unten.classList.toggle("wartend", on);
    for (const el of $$("button, textarea, input", parts.unten)) {
      if (el.tagName === "BUTTON") el.disabled = on;
      else el.readOnly = on;
    }
  }

  function notice(text) {
    $$(".hinweis", parts.unten).forEach((el) => el.remove());
    const p = document.createElement("p");
    p.className = "hinweis";
    p.setAttribute("role", "status");
    p.textContent = text;
    parts.unten.prepend(p);
  }

  function pending(said, retry) {
    const article = document.createElement("article");
    article.className = "wechsel wartet";
    if (said && !retry) {
      const du = document.createElement("p");
      du.className = "du";
      du.textContent = said;
      article.append(du);
    }
    const thinking = document.createElement("p");
    thinking.className = "denkt";
    thinking.textContent = retry ? "Die Erzählung wird nachgeholt" : "Der Spielleiter überlegt";
    article.append(thinking);
    $(".leer", parts.verlauf)?.remove();
    parts.verlauf.append(article);
    toBottom();
    return article;
  }

  // Text erscheint nach und nach; ein Klick oder eine Taste zeigt ihn ganz.
  function revealNewest() {
    const story = $(".wechsel.neu .erzaehlung", parts.verlauf);
    if (!story || calm.matches) return;
    const paragraphs = $$("p", story).map((p) => [p, p.textContent]);
    for (const [p] of paragraphs) p.textContent = "";
    let index = 0;
    let shown = 0;
    const finish = () => {
      for (const [p, text] of paragraphs) p.textContent = text;
      reveal = null;
    };
    const step = () => {
      if (reveal !== finish) return;
      const [p, text] = paragraphs[index];
      shown = Math.min(text.length, shown + 4);
      p.textContent = text.slice(0, shown);
      if (shown >= text.length) {
        index += 1;
        shown = 0;
      }
      if (index < paragraphs.length) requestAnimationFrame(step);
      else reveal = null;
    };
    reveal = finish;
    requestAnimationFrame(step);
  }

  function apply(answer) {
    reveal?.();
    document.title = answer.titel + " · ValarRP";
    // Vom Server gebaut und maskiert, wie beim Laden der Seite; die CSP lässt darin nichts ausführen.
    for (const [name, html] of Object.entries(answer.teile)) if (parts[name]) parts[name].innerHTML = html;
    showTab(tab);
    revealNewest();
    toBottom();
    const field = $(".wurf input[name=wurf]", parts.unten) || input();
    if (field && window.matchMedia("(hover: hover)").matches) field.focus({ preventScroll: true });
  }

  async function send(form) {
    if (busy) return;
    const data = new URLSearchParams(new FormData(form));
    const roll = (data.get("wurf") || "").trim();
    const said = roll ? "wurf " + roll : (data.get("eingabe") || "").trim();
    if (!said) return;
    setBusy(true);
    const waiting = pending(said, form.classList.contains("nachholen"));
    try {
      const response = await fetch(form.getAttribute("action") + "/zug", {
        method: "POST",
        body: data,
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      let answer;
      try {
        answer = await response.json();
      } catch {
        throw new Error(`Antwort nicht lesbar (${response.status}).`);
      }
      if (!response.ok) throw new Error(answer.fehler || `Fehler ${response.status}.`);
      setBusy(false);
      apply(answer);
    } catch (error) {
      waiting.remove();
      setBusy(false);
      const text = error instanceof TypeError ? "Keine Verbindung zum Server." : error.message;
      notice(text + " Noch einmal senden wirkt nur einmal.");
    }
  }

  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-zug]");
    if (!form) return;
    event.preventDefault();
    send(form);
  });

  document.addEventListener("keydown", (event) => {
    const area = event.target;
    if (area.matches?.("textarea[name=eingabe]") && event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      const form = area.form;
      if (form.requestSubmit) form.requestSubmit();
      else form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));  // Safari vor 16
    } else if (event.key === "Escape" && side.classList.contains("offen")) {
      openSide(false);
    } else if (reveal && !event.target.matches?.("textarea, input")) {
      reveal();
    }
  });

  document.addEventListener("input", (event) => {
    if (event.target.matches("textarea[name=eingabe]")) grow(event.target);
  });

  document.addEventListener("click", (event) => {
    const target = event.target;
    const link = target.closest("[data-reiter]");
    if (link) {
      event.preventDefault();
      showTab(link.dataset.reiter);
      return;
    }
    if (target.closest("[data-seite-auf]")) {
      event.preventDefault();
      openSide(true);
      return;
    }
    if (target.closest("[data-seite-zu]")) {
      openSide(false);
      return;
    }
    const fill = target.closest("[data-eingabe]");
    if (fill) {
      const area = input();
      area.value = fill.dataset.eingabe;
      grow(area);
      openSide(false);
      area.focus();
      return;
    }
    if (side.classList.contains("offen") && !side.contains(target)) openSide(false);
    if (reveal && parts.verlauf.contains(target)) reveal();
  });

  showTab(tab);
  if (location.hash === "#seite" && window.matchMedia("(max-width: 860px)").matches) openSide(true);
}

// --- Erschaffung: Schritte und Zähler ---

function initCreate() {
  const form = $("#bogen");
  if (!form) return;
  const rule = (name) => Number(form.dataset[name]);
  const start = rule("start");
  const points = rule("punkte");
  const max = rule("max");
  const limits = { haupt: rule("haupt"), neben: rule("neben") };
  const values = { haupt: rule("hauptWert"), neben: rule("nebenWert") };
  const counter = (name) => $(`[data-zaehler=${name}]`);
  const attrs = $$(".attr", form);
  const mod = (value) => Math.floor((value - 10) / 2);
  const signed = (n) => (n >= 0 ? "+" : "−") + Math.abs(n);

  function attributeValues() {
    const result = {};
    for (const row of attrs) {
      const value = parseInt($("input", row).value, 10);
      result[row.dataset.attr] = Number.isNaN(value) ? start : value;
    }
    return result;
  }

  function mark(el, good) {
    el.classList.toggle("gut", good);
    el.classList.toggle("schlecht", !good);
  }

  function update() {
    const current = attributeValues();
    const spent = Object.values(current).reduce((sum, v) => sum + v - start, 0);
    const left = points - spent;
    counter("punkte").textContent = left;
    mark(counter("punkte"), left === 0);
    for (const row of attrs) {
      const field = $("input", row);
      const value = current[row.dataset.attr];
      const wrong = value < start || value > max || field.value.trim() === "";
      field.setAttribute("aria-invalid", wrong);
      $(".mod", row).textContent = wrong ? "" : signed(mod(value));
      $("[data-delta='-1']", row).disabled = value <= start;
      $("[data-delta='1']", row).disabled = value >= max || left <= 0;
    }

    const chosen = { haupt: [], neben: [] };
    for (const box of $$(".wahl input:checked", form)) chosen[box.name].push(box.value);
    for (const kind of ["haupt", "neben"]) {
      const full = chosen[kind].length >= limits[kind];
      for (const box of $$(`.wahl input[name=${kind}]`, form)) box.disabled = full && !box.checked;
      counter(kind).textContent = `${chosen[kind].length} / ${limits[kind]}`;
      mark(counter(kind), chosen[kind].length === limits[kind]);
    }
    for (const row of $$(".skill", form)) {
      const kind = $(".wahl input:checked", row)?.name;
      row.classList.toggle("gewaehlt", Boolean(kind));
      const best = Math.max(...row.dataset.attrs.split(" ").map((a) => mod(current[a] ?? start)));
      $(".bonus", row).textContent = kind ? signed(Math.floor(values[kind] / 10) + best) : "";
      $(".bonus", row).title = kind ? "Bonus auf Proben mit diesem Skill" : "";
    }

    const kit = $(".kit input:checked", form);
    counter("ausruestung").textContent = kit ? $(".kit-kopf b", kit.closest(".kit")).textContent : "–";
    $(".zusammen-name").textContent = form.elements.name.value.trim() || "Neue Figur";

    const missing = [];
    if (left > 0) missing.push(`${left} ${left === 1 ? "Punkt" : "Punkte"} verteilen`);
    if (left < 0) missing.push(`${-left} ${left === -1 ? "Punkt" : "Punkte"} zu viel`);
    for (const [kind, title] of [["haupt", "Hauptskill"], ["neben", "Nebenskill"]]) {
      const open = limits[kind] - chosen[kind].length;
      if (open > 0) missing.push(`${open} ${title}${open === 1 ? "" : "s"} wählen`);
    }
    if (!kit) missing.push("Ausrüstung wählen");
    counter("offen").textContent = missing.length ? "Noch: " + missing.join(", ") + "." : "Alles beisammen.";
  }

  form.addEventListener("click", (event) => {
    const button = event.target.closest("[data-delta]");
    if (!button) return;
    const field = $("input", button.closest(".attr"));
    const value = parseInt(field.value, 10);
    field.value = Math.min(max, Math.max(start, (Number.isNaN(value) ? start : value) + Number(button.dataset.delta)));
    update();
  });
  form.addEventListener("change", (event) => {
    const box = event.target;
    if (box.matches(".wahl input") && box.checked) {
      // Ein Skill ist Haupt- oder Nebenskill, nicht beides.
      for (const other of $$(".wahl input", box.closest(".skill"))) if (other !== box) other.checked = false;
    }
    update();
  });
  form.addEventListener("input", update);

  // Schritte: ein Abschnitt nach dem anderen; geprüft wird trotzdem erst beim Erschaffen.
  const sections = $$(".schritt", form);
  const links = $$(".schritte a", form);
  let index = Math.max(0, sections.findIndex((s) => s.dataset.schrittName === form.dataset.startSchritt));
  form.classList.add("schrittweise");
  form.noValidate = true;

  function show(next, scroll = true) {
    index = Math.max(0, Math.min(sections.length - 1, next));
    sections.forEach((section, i) => section.classList.toggle("aktiv", i === index));
    links.forEach((link, i) => {
      link.classList.toggle("aktiv", i === index);
      link.classList.toggle("erledigt", i < index);
      if (i === index) link.setAttribute("aria-current", "step");
      else link.removeAttribute("aria-current");
    });
    form.classList.toggle("erster", index === 0);
    form.classList.toggle("letzter", index === sections.length - 1);
    if (scroll) form.scrollIntoView({ block: "start", behavior: calm.matches ? "auto" : "smooth" });
    const first = $("input:not([type=hidden]), textarea", sections[index]);
    if (first && scroll && window.matchMedia("(hover: hover)").matches) first.focus({ preventScroll: true });
  }

  form.addEventListener("click", (event) => {
    if (event.target.closest("[data-weiter]")) show(index + 1);
    else if (event.target.closest("[data-zurueck]")) show(index - 1);
    const link = event.target.closest("[data-zu]");
    if (link) {
      event.preventDefault();
      show(sections.findIndex((s) => s.dataset.schrittName === link.dataset.zu));
    }
  });
  form.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target.matches("input") && index < sections.length - 1) {
      event.preventDefault();
      show(index + 1);
    }
  });
  form.addEventListener("submit", (event) => {
    const wrong = $$("input, textarea", form).find((field) => !field.checkValidity());
    if (!wrong) return;
    event.preventDefault();
    show(sections.findIndex((section) => section.contains(wrong)), false);
    wrong.reportValidity();
  });

  show(index, false);
  update();
}

initPlay();
initCreate();
