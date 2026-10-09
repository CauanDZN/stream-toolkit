// ==UserScript==
// @name         Controles de vídeo no player do site (hover)
// @namespace    https://github.com/CauanDZN/stream-toolkit
// @version      1.0.0
// @description  Ao passar o mouse sobre o vídeo, mostra uma barra com play, seek, volume (até 200%), velocidade, qualidade/áudio/legenda (quando o site usa hls.js), screenshot, PiP e tela cheia.
// @homepageURL  https://github.com/CauanDZN/stream-toolkit
// @downloadURL  https://raw.githubusercontent.com/CauanDZN/stream-toolkit/main/m3u8-controls.user.js
// @updateURL    https://raw.githubusercontent.com/CauanDZN/stream-toolkit/main/m3u8-controls.user.js
// @match        *://example.com/*
// @match        *://*.example.com/*
// @run-at       document-start
// @grant        none
// ==/UserScript==
//
// IMPORTANTE: troque as duas linhas "@match" acima pelo(s) domínio(s) do site onde você quer os controles
// (ex.: *://meusite.com/*). Pode ter quantas linhas "@match" precisar.
// Roda em todos os frames, então funciona também se o player estiver dentro de um <iframe> do mesmo padrão de URL.

(() => {
  "use strict";
  if (window.__w8ctl) return;
  window.__w8ctl = true;

  const HIDE_AFTER_MS = 2500;
  const MIN_W = 200, MIN_H = 110;              // ignora vídeos pequenos (thumbnails, previews)
  const SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 3];

  /* ---------- descobre a instância de hls.js do site (quando ela é global) ---------- */
  const hlsByMedia = new WeakMap();
  function patchHls(H) {
    try {
      if (!H || !H.prototype || H.prototype.__w8) return;
      const orig = H.prototype.attachMedia;
      if (typeof orig !== "function") return;
      H.prototype.attachMedia = function (media) {
        try { hlsByMedia.set(media, this); } catch (e) {}
        return orig.apply(this, arguments);
      };
      H.prototype.__w8 = true;
    } catch (e) {}
  }
  try {
    let _H = window.Hls;
    Object.defineProperty(window, "Hls", {
      configurable: true, enumerable: true,
      get() { return _H; },
      set(v) { _H = v; patchHls(v); }
    });
    patchHls(_H);
  } catch (e) {}
  const findHls = v => hlsByMedia.get(v) || (window.hls && window.hls.media === v ? window.hls : null);

  /* ---------- utilitários ---------- */
  const fmt = t => {
    if (!isFinite(t) || t < 0) t = 0; t = Math.floor(t);
    const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), s = t % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0");
  };
  const seekRange = v => {
    if (v.seekable && v.seekable.length) return [v.seekable.start(0), v.seekable.end(v.seekable.length - 1)];
    return isFinite(v.duration) ? [0, v.duration] : null;
  };
  const store = {
    get(k) { try { return localStorage.getItem("w8:" + k); } catch (e) { return null; } },
    set(k, x) { try { localStorage.setItem("w8:" + k, x); } catch (e) {} }
  };
  const ICON = {
    play: '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>',
    pause: '<svg viewBox="0 0 24 24"><path d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg>',
    vol: '<svg viewBox="0 0 24 24"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4z"/></svg>',
    muted: '<svg viewBox="0 0 24 24"><path d="M16.5 12A4.5 4.5 0 0 0 14 8v2.2l2.5 2.5V12zM4.3 3 3 4.3 7.7 9H3v6h4l5 5v-6.7l4.3 4.3c-.7.5-1.4.9-2.3 1.1v2.1a9 9 0 0 0 3.7-1.8l2 2 1.3-1.3L4.3 3zM12 4 9.9 6.1 12 8.2V4z"/></svg>',
    back: '<svg viewBox="0 0 24 24"><path d="M12 5V1L7 6l5 5V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z"/></svg>',
    fwd: '<svg viewBox="0 0 24 24"><path d="M12 5V1l5 5-5 5V7a6 6 0 1 0 6 6h2a8 8 0 1 1-8-8z"/></svg>',
    shot: '<svg viewBox="0 0 24 24"><path d="M9 3 7.2 5H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-3.2L15 3H9zm3 15a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm0-2a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"/></svg>',
    pip: '<svg viewBox="0 0 24 24"><path d="M19 7h-8v6h8V7zm2-4H3a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h18a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zm0 16H3V5h18v14z"/></svg>',
    fs: '<svg viewBox="0 0 24 24"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>'
  };
  const CSS = `
    :host { all: initial; }
    .box { position: fixed; pointer-events: none; z-index: 2147483647; font: 13px/1.3 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; color:#f2f2f2; }
    .bar { position: absolute; left: 0; right: 0; bottom: 0; padding: 26px 10px 8px; pointer-events: auto;
           background: linear-gradient(transparent, rgba(0,0,0,.85)); opacity: 0; transform: translateY(6px);
           transition: opacity .18s, transform .18s; }
    .box.on .bar { opacity: 1; transform: none; }
    .box:not(.on) .bar { pointer-events: none; }
    .row { display:flex; align-items:center; gap:5px; flex-wrap:wrap; }
    .sp { flex:1; }
    input[type=range] { margin:0; cursor:pointer; }
    #seek { -webkit-appearance:none; appearance:none; background:transparent; width:100%; height:15px; margin-bottom:5px; border-radius:8px;
      background: linear-gradient(to right,#ff4d5a var(--p,0%),transparent var(--p,0%)),
                  linear-gradient(to right,#8a8a8a var(--b,0%),#3a3a3a var(--b,0%));
      background-size:100% 5px; background-repeat:no-repeat; background-position:center; }
    #seek::-webkit-slider-runnable-track { background:transparent; height:15px; }
    #seek::-moz-range-track { background:transparent; height:15px; }
    #seek::-webkit-slider-thumb { -webkit-appearance:none; width:12px; height:12px; border-radius:50%; background:#ff4d5a; margin-top:1.5px; }
    #seek::-moz-range-thumb { width:12px; height:12px; border:0; border-radius:50%; background:#ff4d5a; }
    #vol { width:80px; height:14px; accent-color:#ff4d5a; }
    button, select { font:inherit; color:#f2f2f2; background:rgba(255,255,255,.12); border:1px solid transparent; border-radius:7px;
      padding:4px 8px; cursor:pointer; display:inline-flex; align-items:center; gap:3px; }
    button:hover, select:hover { background:rgba(255,255,255,.25); }
    button svg { width:19px; height:19px; fill:currentColor; }
    select { max-width:130px; padding:4px 5px; } select option { background:#1b1b1b; color:#fff; }
    #time { font-variant-numeric:tabular-nums; padding:0 5px; white-space:nowrap; color:#ddd; }
    #live { display:none; background:#333; } #live.show { display:inline-flex; } #live.edge { background:#ff4d5a; }
    [hidden] { display:none !important; }
    .toast { position:absolute; top:12px; left:50%; transform:translateX(-50%); background:rgba(0,0,0,.75); padding:5px 12px;
      border-radius:999px; opacity:0; transition:opacity .2s; font-weight:600; pointer-events:none; }
    .toast.show { opacity:1; }
    @media (max-width:560px){ #vol{display:none;} select{max-width:80px;} }
  `;

  /* ---------- UI de um vídeo ---------- */
  class Ui {
    constructor(video) {
      this.v = video;
      this.gain = null; this.ctx = null;
      this.seeking = false; this.hideT = 0; this.toastT = 0; this.qKey = "";
      this.host = document.createElement("div");
      this.host.setAttribute("data-w8", "");
      this.host.style.cssText = "all:initial;";
      const root = this.host.attachShadow({ mode: "closed" });
      root.innerHTML = `<style>${CSS}</style><div class="box">
        <div class="toast"></div>
        <div class="bar">
          <input id="seek" type="range" min="0" max="1000" value="0" step="1" aria-label="Posição">
          <div class="row">
            <button id="play" title="Reproduzir/Pausar (Espaço)"></button>
            <button id="back" title="-10s (J)">${ICON.back}10</button>
            <button id="fwd" title="+10s (L)">10${ICON.fwd}</button>
            <button id="mute" title="Mudo (M)"></button>
            <input id="vol" type="range" min="0" max="200" value="100" step="1" aria-label="Volume">
            <span id="time">0:00</span>
            <button id="live" title="Ir para o ao vivo (End)">AO VIVO</button>
            <span class="sp"></span>
            <select id="speed" title="Velocidade"></select>
            <select id="quality" title="Qualidade" hidden></select>
            <select id="audio" title="Faixa de áudio" hidden></select>
            <select id="subs" title="Legenda" hidden></select>
            <button id="shot" title="Screenshot (S)">${ICON.shot}</button>
            <button id="pip" title="Picture-in-Picture (P)">${ICON.pip}</button>
            <button id="fs" title="Tela cheia (F)">${ICON.fs}</button>
          </div>
        </div></div>`;
      this.root = root;
      this.box = root.querySelector(".box");
      this.$ = s => root.querySelector(s);
      // evita que cliques na barra cheguem no player do site (que costuma pausar/abrir menus)
      ["click", "dblclick", "mousedown", "mouseup", "pointerdown", "pointerup", "touchstart", "contextmenu", "wheel"]
        .forEach(ev => this.$(".bar").addEventListener(ev, e => e.stopPropagation()));
      this.$(".bar").addEventListener("mouseenter", () => this.keep());
      this.bind();
      this.syncPlay(); this.syncMute();
      const sp = this.$("#speed");
      SPEEDS.forEach(s => sp.appendChild(new Option(s + "x", s)));
      sp.value = String(video.playbackRate || 1);
      const sv = store.get("vol"); if (sv != null) this.setVolume(Math.min(100, Number(sv)), true);  // >100 exige gesto do usuário (AudioContext)
    }
    get parentForHost() { return document.fullscreenElement && document.fullscreenElement.tagName !== "VIDEO" ? document.fullscreenElement : document.documentElement; }
    attach() { const p = this.parentForHost; if (this.host.parentNode !== p) p.appendChild(this.host); }
    place() {
      const r = this.v.getBoundingClientRect();
      const s = this.box.style;
      s.left = r.left + "px"; s.top = r.top + "px"; s.width = r.width + "px"; s.height = r.height + "px";
    }
    show() { this.attach(); this.place(); this.refreshTracks(); this.paint(); this.box.classList.add("on"); this.keep(); }
    keep() { clearTimeout(this.hideT); this.hideT = setTimeout(() => this.hide(), HIDE_AFTER_MS); }
    hide() { clearTimeout(this.hideT); this.box.classList.remove("on"); }
    get visible() { return this.box.classList.contains("on"); }
    toast(t) {
      const el = this.$(".toast"); el.textContent = t; el.classList.add("show");
      clearTimeout(this.toastT); this.toastT = setTimeout(() => el.classList.remove("show"), 1100);
    }

    bind() {
      const v = this.v, $ = this.$;
      $("#play").onclick = () => this.togglePlay();
      $("#back").onclick = () => this.seekBy(-10);
      $("#fwd").onclick = () => this.seekBy(10);
      $("#mute").onclick = () => this.toggleMute();
      $("#vol").oninput = () => { v.muted = false; this.setVolume(Number($("#vol").value)); };
      $("#live").onclick = () => this.goLive();
      $("#shot").onclick = () => this.shot();
      $("#pip").onclick = () => this.pip();
      $("#fs").onclick = () => this.fullscreen();
      $("#speed").onchange = () => { this.setSpeed(Number($("#speed").value)); };
      $("#quality").onchange = () => { const h = findHls(v); if (h) { h.currentLevel = Number($("#quality").value); this.toast($("#quality").selectedOptions[0].textContent); } };
      $("#audio").onchange = () => { const h = findHls(v); if (h) h.audioTrack = Number($("#audio").value); };
      $("#subs").onchange = () => { const h = findHls(v); if (h) { h.subtitleDisplay = true; h.subtitleTrack = Number($("#subs").value); } };
      const seek = $("#seek");
      seek.addEventListener("input", () => {
        this.seeking = true; this.keep();
        const r = seekRange(v); if (r) $("#time").textContent = fmt(r[0] + (r[1] - r[0]) * seek.value / 1000);
      });
      seek.addEventListener("change", () => {
        this.seeking = false; const r = seekRange(v);
        if (r) v.currentTime = r[0] + (r[1] - r[0]) * seek.value / 1000;
      });
      ["play", "pause"].forEach(e => v.addEventListener(e, () => this.syncPlay()));
      ["volumechange"].forEach(e => v.addEventListener(e, () => this.syncMute()));
      ["timeupdate", "progress", "durationchange", "seeked", "loadedmetadata"].forEach(e => v.addEventListener(e, () => { if (this.visible) this.paint(); }));
      v.addEventListener("ratechange", () => { const sp = $("#speed"); const r = v.playbackRate;
        if (![...sp.options].some(o => Number(o.value) === r)) sp.appendChild(new Option(r + "x", r)); sp.value = String(r); });
    }
    syncPlay() { this.$("#play").innerHTML = this.v.paused ? ICON.play : ICON.pause; }
    syncMute() { this.$("#mute").innerHTML = (this.v.muted || this.v.volume === 0) ? ICON.muted : ICON.vol; }
    paint() {
      const v = this.v, r = seekRange(v), $ = this.$;
      const live = v.duration === Infinity;
      let p = 0, b = 0;
      if (r && r[1] > r[0]) {
        p = (v.currentTime - r[0]) / (r[1] - r[0]) * 100;
        for (let i = 0; i < v.buffered.length; i++)
          if (v.buffered.start(i) <= v.currentTime + .5 && v.buffered.end(i) >= v.currentTime) b = (v.buffered.end(i) - r[0]) / (r[1] - r[0]) * 100;
      }
      p = Math.max(0, Math.min(100, p)); b = Math.max(b, p);
      const seek = $("#seek");
      seek.style.setProperty("--p", p + "%"); seek.style.setProperty("--b", Math.min(100, b) + "%");
      if (!this.seeking) seek.value = Math.round(p * 10);
      if (!this.seeking) $("#time").textContent = live ? (r ? "-" + fmt(r[1] - v.currentTime) : "") : fmt(v.currentTime) + " / " + fmt(v.duration);
      const l = $("#live"); l.classList.toggle("show", live);
      l.classList.toggle("edge", live && !!r && r[1] - v.currentTime < 12);
    }
    togglePlay() { const v = this.v; if (v.paused) v.play().catch(() => {}); else v.pause(); }
    seekBy(d) { const r = seekRange(this.v); if (!r) return; this.v.currentTime = Math.min(Math.max(this.v.currentTime + d, r[0]), r[1]); this.toast((d > 0 ? "+" : "") + d + "s"); }
    goLive() { const r = seekRange(this.v); if (!r) return; const h = findHls(this.v); this.v.currentTime = (h && h.liveSyncPosition) || r[1]; }
    toggleMute() { this.v.muted = !this.v.muted; this.toast(this.v.muted ? "Mudo" : "Som ligado"); }
    setSpeed(s) { s = Math.max(0.1, Math.min(16, s)); this.v.playbackRate = s; this.toast(s + "x"); }
    speedStep(dir) {
      const list = [...this.$("#speed").options].map(o => Number(o.value)).sort((a, b) => a - b), c = this.v.playbackRate;
      const n = dir > 0 ? list.find(x => x > c + 1e-6) : [...list].reverse().find(x => x < c - 1e-6);
      if (n) this.setSpeed(n);
    }
    ensureGain() {
      if (this.gain) return true;
      try {
        const AC = window.AudioContext || window.webkitAudioContext;
        const ctx = new AC(); ctx.resume().catch(() => {});
        const src = ctx.createMediaElementSource(this.v);   // falha se o site já criou um, ou muda se o vídeo for cross-origin
        const g = ctx.createGain(); src.connect(g).connect(ctx.destination);
        this.ctx = ctx; this.gain = g; return true;
      } catch (e) { return false; }
    }
    setVolume(pct, silent) {
      pct = Math.max(0, Math.min(200, pct));
      const cross = this.v.currentSrc && !/^(blob:|data:)/.test(this.v.currentSrc) && new URL(this.v.currentSrc, location.href).origin !== location.origin;
      let eff = pct;
      if (pct > 100 && (cross || !this.ensureGain())) { eff = 100; if (!silent) this.toast("Volume acima de 100% indisponível neste vídeo"); }
      if (this.gain) this.gain.gain.value = eff > 100 ? eff / 100 : 1;
      this.v.volume = eff > 100 ? 1 : eff / 100;
      this.$("#vol").value = String(eff); store.set("vol", String(eff));
      if (!silent) this.toast("Volume " + eff + "%");
    }
    volBy(d) { this.v.muted = false; this.setVolume(Number(this.$("#vol").value) + d); }
    shot() {
      const v = this.v; if (!v.videoWidth) return this.toast("Sem imagem ainda");
      try {
        const c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight;
        c.getContext("2d").drawImage(v, 0, 0);
        c.toBlob(b => {
          if (!b) return this.toast("Falha no screenshot");
          const a = document.createElement("a"); a.href = URL.createObjectURL(b);
          a.download = "frame_" + fmt(v.currentTime).replace(/:/g, "-") + ".png"; a.click();
          setTimeout(() => URL.revokeObjectURL(a.href), 2000); this.toast("Screenshot salvo");
        }, "image/png");
      } catch (e) { this.toast("Bloqueado pelo site (vídeo cross-origin)"); }
    }
    pip() {
      if (document.pictureInPictureElement) return document.exitPictureInPicture();
      if (this.v.requestPictureInPicture) this.v.requestPictureInPicture().catch(() => this.toast("PiP indisponível"));
      else this.toast("PiP indisponível");
    }
    fullscreen() {
      if (document.fullscreenElement) return document.exitFullscreen();
      // Fullscreen no container do vídeo (não no <video>) para a barra continuar visível
      const target = this.v.parentElement || this.v;
      (target.requestFullscreen || target.webkitRequestFullscreen).call(target).catch(() => this.toast("Tela cheia bloqueada"));
    }
    refreshTracks() {
      const h = findHls(this.v), $ = this.$;
      const key = h ? [h.levels && h.levels.length, h.audioTracks && h.audioTracks.length, h.subtitleTracks && h.subtitleTracks.length].join("/") : "";
      const q = $("#quality"), a = $("#audio"), s = $("#subs");
      if (!h) { q.hidden = a.hidden = s.hidden = true; return; }
      if (key !== this.qKey) {
        this.qKey = key;
        const fill = (sel, items, cur) => { sel.innerHTML = ""; items.forEach(([v, l]) => sel.appendChild(new Option(l, v))); sel.value = String(cur); };
        const lv = h.levels || [];
        q.hidden = lv.length < 2;
        if (lv.length > 1) {
          const items = lv.map((l, i) => [String(i), l.height ? l.height + "p" : Math.round(l.bitrate / 1000) + " kbps"]).reverse();
          fill(q, [["-1", "Auto"]].concat(items), h.autoLevelEnabled ? -1 : h.currentLevel);
        }
        const at = h.audioTracks || []; a.hidden = at.length < 2;
        if (at.length > 1) fill(a, at.map((x, i) => [String(i), "Áudio: " + (x.name || x.lang || "#" + (i + 1))]), h.audioTrack);
        const st = h.subtitleTracks || []; s.hidden = st.length < 1;
        if (st.length) fill(s, [["-1", "Legenda: off"]].concat(st.map((x, i) => [String(i), "Legenda: " + (x.name || x.lang || "#" + (i + 1))])), h.subtitleTrack);
      }
    }
  }

  /* ---------- gerenciamento dos vídeos ---------- */
  const uis = new Map();
  let active = null;

  function scan() {
    document.querySelectorAll("video").forEach(v => { if (!uis.has(v)) uis.set(v, new Ui(v)); });
    for (const [v, ui] of uis) if (!v.isConnected) { ui.hide(); ui.host.remove(); uis.delete(v); if (active === ui) active = null; }
  }
  let scanT = 0;
  const schedScan = () => { clearTimeout(scanT); scanT = setTimeout(scan, 200); };
  new MutationObserver(schedScan).observe(document, { childList: true, subtree: true });
  document.addEventListener("DOMContentLoaded", scan);
  setInterval(scan, 3000);   // cobre vídeos criados em shadow DOM/frameworks que escapam do observer

  function videoAt(x, y) {
    let best = null, area = Infinity;
    for (const [v, ui] of uis) {
      const r = v.getBoundingClientRect();
      if (r.width < MIN_W || r.height < MIN_H) continue;
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) {
        const a = r.width * r.height; if (a < area) { area = a; best = ui; }
      }
    }
    return best;
  }
  function onMove(e) {
    const ui = videoAt(e.clientX, e.clientY);
    if (ui !== active && active) active.hide();
    active = ui;
    if (ui) ui.show();
  }
  window.addEventListener("mousemove", onMove, { capture: true, passive: true });
  window.addEventListener("pointerdown", onMove, { capture: true, passive: true });
  document.addEventListener("mouseleave", e => { if (active && (e.target === document || e.target === document.documentElement)) active.hide(); }, true);   // só quando o mouse sai da página
  window.addEventListener("scroll", () => { if (active && active.visible) active.place(); }, { capture: true, passive: true });
  window.addEventListener("resize", () => { if (active && active.visible) active.place(); });
  document.addEventListener("fullscreenchange", () => {
    for (const ui of uis.values()) ui.attach();
    if (active) active.show();
  });

  /* ---------- atalhos: só valem com o mouse sobre o vídeo ---------- */
  window.addEventListener("keydown", e => {
    if (!active || !active.visible || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target, tag = ((t && t.tagName) || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" || (t && t.isContentEditable)) return;
    const u = active, k = e.key;
    const map = {
      " ": () => u.togglePlay(), k: () => u.togglePlay(),
      ArrowLeft: () => u.seekBy(-5), ArrowRight: () => u.seekBy(5), j: () => u.seekBy(-10), l: () => u.seekBy(10),
      ArrowUp: () => u.volBy(5), ArrowDown: () => u.volBy(-5),
      m: () => u.toggleMute(), f: () => u.fullscreen(), p: () => u.pip(), s: () => u.shot(),
      "<": () => u.speedStep(-1), ">": () => u.speedStep(1),
      Home: () => { const r = seekRange(u.v); if (r) u.v.currentTime = r[0]; }, End: () => u.goLive()
    };
    let fn = map[k] || map[k.toLowerCase()];
    if (!fn && /^[0-9]$/.test(k) && u.v.duration !== Infinity) fn = () => { const r = seekRange(u.v); if (r) u.v.currentTime = r[0] + (r[1] - r[0]) * Number(k) / 10; };
    if (fn) { e.preventDefault(); e.stopImmediatePropagation(); fn(); u.keep(); }
  }, true);
})();
