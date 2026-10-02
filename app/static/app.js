// Winzige Hilfsfunktionen für serverseitig gerenderte Partials.
//
// Bewusst ohne externe Bibliothek (der Kiosk-PC braucht laut Konzept keinen
// Internetzugang, und der Server soll ohne CDN-Abhängigkeit auskommen). Deckt zwei
// Muster ab, die diese App braucht:
//  - periodisches Nachladen eines Fragments: [data-poll-url] + [data-poll-interval] (ms)
//  - Live-Suche mit Debounce: [data-search-url] + [data-swap-target]
// Zustandsändernde Aktionen (Besucher anlegen/ein-/auschecken) laufen bewusst über
// normale HTML-Formulare mit Server-Redirect, nicht über JS.

function hatUngespeicherteEingabe(el) {
  // Verhindert, dass ein Polling-Tick z.B. das Selbstregistrierungs-Formular
  // überschreibt, während dort gerade der Name eingetippt wird. Wichtig: nicht nur auf
  // Fokus prüfen -- das Vorname-Feld hat "autofocus" und wäre damit direkt nach dem
  // Rendern fokussiert, obwohl noch niemand etwas eingegeben hat. Ohne die
  // Wert-Prüfung würde ein liegen gelassenes/verwaistes Formular jeden weiteren
  // Refresh blockieren, auch wenn längst eine neuere Karte gescannt wurde. Blockiert
  // wird daher nur, wenn tatsächlich schon Text in einem fokussierten Feld steht.
  const aktiv = document.activeElement;
  if (!aktiv || !el.contains(aktiv)) return false;
  if (!["INPUT", "SELECT", "TEXTAREA"].includes(aktiv.tagName)) return false;
  return aktiv.value.trim() !== "";
}

function swapFragment(url, targetSelector) {
  const vorher = document.querySelector(targetSelector);
  if (vorher && hatUngespeicherteEingabe(vorher)) return;

  fetch(url)
    .then((r) => r.text())
    .then((html) => {
      const el = document.querySelector(targetSelector);
      if (el && !hatUngespeicherteEingabe(el)) {
        const geaendert = el.dataset.letztesHtml !== html;
        el.dataset.letztesHtml = html;
        el.innerHTML = html;
        // Ändert sich dieses Fragment (z.B. neues Scan-Feedback), laden alle Fragmente
        // mit data-refresh-on="<id>" sofort nach, statt auf ihren nächsten Poll zu warten.
        if (geaendert) {
          document.querySelectorAll('[data-refresh-on="' + el.id + '"]').forEach((abh) =>
            swapFragment(abh.getAttribute("data-poll-url"), "#" + abh.id)
          );
        }
      }
    })
    .catch(() => {
      /* Kiosk pollt weiter, ein einzelner fehlgeschlagener Request ist kein Problem */
    });
}

function startPolling() {
  document.querySelectorAll("[data-poll-url]").forEach((el) => {
    const url = el.getAttribute("data-poll-url");
    const interval = parseInt(el.getAttribute("data-poll-interval") || "5000", 10);
    const tick = () => swapFragment(url, "#" + el.id);
    tick();
    setInterval(tick, interval);
  });
}

function bindSearchInputs() {
  document.querySelectorAll("[data-search-url]").forEach((input) => {
    let timer = null;
    const targetSelector = input.getAttribute("data-swap-target");
    const paramName = input.getAttribute("data-search-param") || "q";
    const trigger = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const url = new URL(input.getAttribute("data-search-url"), window.location.origin);
        url.searchParams.set(paramName, input.value);
        swapFragment(url.toString(), targetSelector);
      }, 300);
    };
    input.addEventListener("input", trigger);
  });
}

function beep(frequenz) {
  // Tiefer Ton fürs Ablehnen einer Karte, heller fürs Ein-/Auschecken -- am Kiosk hört
  // man den Unterschied, auch ohne auf den Bildschirm zu schauen.
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const ctx = new AudioCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = frequenz || 880;
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    osc.start();
    osc.stop(ctx.currentTime + 0.15);
  } catch (err) {
    /* Autoplay-Policy o.ä. -- kein Ton ist kein Fehler */
  }
}

function startIdleRedirect() {
  // Inaktivitäts-Rückkehr: Besucher-Maske springt nach N Sekunden ohne Eingabe zurück
  // zur Übersicht, damit keine eingetippten Daten (Datenschutz) stehen bleiben.
  const el = document.querySelector("[data-idle-redirect]");
  if (!el) return;
  const ziel = el.getAttribute("data-idle-redirect");
  const ms = parseInt(el.getAttribute("data-idle-seconds") || "60", 10) * 1000;
  let timer = null;
  const reset = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      window.location.href = ziel;
    }, ms);
  };
  ["pointerdown", "keydown", "input", "touchstart", "scroll"].forEach((ev) =>
    document.addEventListener(ev, reset, { passive: true })
  );
  reset();
}

document.addEventListener("DOMContentLoaded", () => {
  startIdleRedirect();
  startPolling();
  bindSearchInputs();
});
