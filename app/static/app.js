// Winzige Hilfsfunktionen für serverseitig gerenderte Partials.
//
// Bewusst ohne externe Bibliothek (der Kiosk-PC braucht laut Konzept keinen
// Internetzugang, und der Server soll ohne CDN-Abhängigkeit auskommen). Deckt zwei
// Muster ab, die diese App braucht:
//  - periodisches Nachladen eines Fragments: [data-poll-url] + [data-poll-interval] (ms)
//  - Live-Suche mit Debounce: [data-search-url] + [data-swap-target]
// Zustandsändernde Aktionen (Besucher anlegen/ein-/auschecken) laufen bewusst über
// normale HTML-Formulare mit Server-Redirect, nicht über JS.

function swapFragment(url, targetSelector) {
  fetch(url)
    .then((r) => r.text())
    .then((html) => {
      const el = document.querySelector(targetSelector);
      if (el) el.innerHTML = html;
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

function beep() {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const ctx = new AudioCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    osc.start();
    osc.stop(ctx.currentTime + 0.15);
  } catch (err) {
    /* Autoplay-Policy o.ä. -- kein Ton ist kein Fehler */
  }
}

document.addEventListener("DOMContentLoaded", () => {
  startPolling();
  bindSearchInputs();
});
