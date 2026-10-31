/* Tela inicial TV Tuner — tvOS de verdade.
 * Apps: catálogo conferido contra o que a TV tem (getAppInfo pelo ID Tizen, ou pelo
 * nome na lista de getAppsInfo). Arte: o ícone da própria TV centralizado num fundo
 * da cor do canto do ícone — o tile vira "borda a borda" seja qual for o ícone.
 * Ícone genérico do Tizen (117px, o do YouTube nesta TV) é trocado por arte nossa.
 * Desempenho: navegar só troca a classe de dois tiles (sai/entra o contorno).
 * A tecla Home física continua abrindo a Home da Samsung (Tizen não deixa trocar). */
(function () {
  "use strict";

  var KEY = { LEFT: 37, UP: 38, RIGHT: 39, DOWN: 40, ENTER: 13, BACK: 10009, ESC: 27 };
  var COLS = 5;
  var hasTizen = typeof tizen !== "undefined";

  var YT_SVG =
    '<svg viewBox="0 0 300 180"><rect width="300" height="180" fill="#fff"/>' +
    '<rect x="40" y="60" width="86" height="60" rx="17" fill="#ff0033"/>' +
    '<path d="M74 75v30l25-15z" fill="#fff"/>' +
    '<text x="136" y="104" font-family="Helvetica Neue,Arial,sans-serif" font-size="33" font-weight="700" ' +
    'letter-spacing="-1.4" fill="#0f0f0f">YouTube</text></svg>';
  var HDMI_SVG =
    '<svg viewBox="0 0 300 180"><rect width="300" height="180" fill="#2c2c2e"/>' +
    '<path d="M112 52h76l-8 26h-60z" fill="none" stroke="#fff" stroke-width="5" stroke-linejoin="round"/>' +
    '<path d="M126 62h48" stroke="#fff" stroke-width="4" stroke-linecap="round" opacity=".6"/></svg>';

  // Ordem = ordem na grade. IDs Tizen medidos na AU7700 (os da Smart Hub não servem aqui).
  var CATALOG = [
    { name: "Netflix", tint: "#e50914", ids: ["org.tizen.netflix-app"], match: /^netflix$/i },
    { name: "YouTube", tint: "#ff0033", ids: ["9Ur5IzDKqV.TizenYouTube"], match: /^youtube$/i, svg: YT_SVG },
    { name: "Globoplay", tint: "#ff4b1f", ids: ["LnLzvqrcEY.globo", "org.tizen.globoplayaria"], match: /globoplay/i },
    { name: "Max", tint: "#002be7", ids: [], match: /^(max|hbo\s*max)$/i, bg: "#002be7" },
    { name: "Disney+", tint: "#1f4fd1", ids: ["MCmYXNxgcu.DisneyPlus"], match: /^disney\+$/i, bg: "#0b1546" },
    { name: "Prime Video", tint: "#00a8e1", ids: ["org.tizen.primevideo"], match: /^prime video$/i, bg: "#00a8e1" },
    { name: "Spotify", tint: "#1db954", ids: ["rJeHak5zRg.Spotify"], match: /^spotify/i, bg: "#121212" },
    { name: "Apple TV", tint: "#8e8e93", ids: ["com.samsung.tv.aria-video"], match: /^apple tv$/i, bg: "#1c1c1e" },
    { name: "Twitch", tint: "#9146ff", ids: ["Jz7aUrV2Q4.Twitch"], match: /^twitch$/i, bg: "#9146ff" },
    { name: "Internet", tint: "#0a84ff", ids: ["org.tizen.browser"], match: null, bg: "#1c1c1e" },
    { name: "Loja", tint: "#5e5ce6", ids: ["com.samsung.tv.store"], match: null, bg: "#1c1c1e" }
  ];

  var $ = function (id) { return document.getElementById(id); };
  var allApps = [];
  var items = [];          // na ordem da grade
  var tiles = [];
  var cur = 0;
  var tvOpen = false;

  function diag(msg) { $("diag").textContent = msg; }

  function tick() {
    var d = new Date();
    $("clock").textContent = ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
    var dt = d.toLocaleDateString("pt-BR", { weekday: "long", day: "numeric", month: "long" });
    $("date").textContent = dt.charAt(0).toUpperCase() + dt.slice(1);
  }

  function find(app) {
    if (!hasTizen) return { id: app.ids[0] || app.name, icon: null };
    for (var i = 0; i < app.ids.length; i++) {
      try {
        var info = tizen.application.getAppInfo(app.ids[i]);
        return { id: info.id, icon: info.iconPath || null };
      } catch (e) {}
    }
    if (app.match) {
      for (var j = 0; j < allApps.length; j++) {
        var a = allApps[j];
        if (a.show && app.match.test(a.name)) return { id: a.id, icon: a.iconPath || null };
      }
    }
    return null;
  }

  function load(k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } }
  function save(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }

  // Cor do canto do ícone (o fundo dele) e se o ícone é o genérico do Tizen.
  // Fica em cache por caminho: o canvas só roda na primeira vez que o ícone aparece.
  function inspect(img) {
    var k = "tvtuner.icon:" + img.src;
    var hit = load(k);
    if (hit) return hit;
    var info = measure(img);
    save(k, info);
    return info;
  }

  function measure(img) {
    var c = document.createElement("canvas");
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    var ctx = c.getContext("2d");
    ctx.drawImage(img, 0, 0);
    var p = ctx.getImageData(2, 2, 1, 1).data;
    return { bg: p[3] < 200 ? null : "rgb(" + p[0] + "," + p[1] + "," + p[2] + ")",
             generic: img.naturalWidth <= 120 };
  }

  function word(item, art) {
    var w = document.createElement("div");
    w.className = "word";
    w.textContent = item.name;
    art.appendChild(w);
  }

  function makeTile(item) {
    var el = document.createElement("div");
    el.className = "tile";
    el.innerHTML = '<div class="art"></div><div class="ring"></div><div class="name"></div>';
    var art = el.querySelector(".art");
    el.querySelector(".name").textContent = item.name;
    if (item.bg) art.style.setProperty("--bg", item.bg);

    if (item.svg) {
      art.innerHTML = item.svg;
    } else if (item.icon) {
      var img = new Image();
      img.onload = function () {
        var info = { bg: null, generic: false };
        try { info = inspect(img); } catch (e) {}
        if (info.generic) { word(item, art); return; }
        // Ícone opaco já é um pôster: preenche o card. Transparente: centraliza.
        if (info.bg) { art.style.setProperty("--bg", info.bg); img.className = "cover"; }
        art.appendChild(img);
      };
      img.onerror = function () { word(item, art); };
      img.src = item.icon;
    } else {
      word(item, art);
    }
    return el;
  }

  // Abrir rápido: desenha o que ficou salvo da última vez, confere pelos IDs
  // (getAppInfo é imediato) e só então, se algum app ainda faltar, lê a lista
  // completa da TV (getAppsInfo: 377 apps, era o que segurava a abertura).
  function signature(list) {
    return list.map(function (i) { return i.kind + ":" + (i.id || i.name) + ":" + (i.icon || ""); }).join("|");
  }

  function resolve() {
    var out = [];
    var missing = false;
    CATALOG.forEach(function (app) {
      var hit = find(app);
      if (hit) {
        out.push({ kind: "app", id: hit.id, icon: hit.icon, name: app.name, bg: app.bg, svg: app.svg, tint: app.tint });
      } else if (app.match) {
        missing = true;
      }
    });
    return { apps: out, missing: missing };
  }

  var sources = [];
  var scannedAll = false;

  function build() {
    var r = resolve();
    var next = r.apps.concat(sources);
    if (signature(next) !== signature(items)) {
      items = next;
      draw();
      save("tvtuner.grid", items);
    }
    if (r.missing && !scannedAll && hasTizen) {
      scannedAll = true;
      setTimeout(function () {
        try {
          tizen.application.getAppsInfo(function (list) { allApps = list; build(); }, function () {});
        } catch (e) {}
      }, 1500);
    }
  }

  function hdmiOnly(list) {
    return list.filter(function (s) { return s.type === "HDMI"; }).map(function (s) {
      return { kind: "source", name: "HDMI " + s.number, src: s, tint: "#636366",
               svg: HDMI_SVG.replace("</svg>", '<text x="150" y="134" text-anchor="middle" font-family="Helvetica Neue,Arial,sans-serif" ' +
                                     'font-size="26" font-weight="600" fill="#fff">HDMI ' + s.number + "</text></svg>") };
    });
  }

  function loadSources(done) {
    if (!hasTizen) { done(hdmiOnly([{ type: "HDMI", number: 1 }])); return; }
    try {
      tizen.systeminfo.getPropertyValue("VIDEOSOURCE", function (vs) { done(hdmiOnly(vs.connected)); },
                                         function () { done([]); });
    } catch (e) { done([]); }
  }

  function draw() {
    var grid = $("grid");
    grid.innerHTML = "";
    tiles = [];
    var row = null;
    items.forEach(function (item, i) {
      if (i % COLS === 0) {
        row = document.createElement("div");
        row.className = "row";
        grid.appendChild(row);
      }
      var t = makeTile(item);
      row.appendChild(t);
      tiles.push(t);
    });
    cur = Math.min(cur, tiles.length - 1);
    focus();
  }

  function focus() {
    if (!tiles.length) return;
    var prev = document.querySelector(".tile.focus");
    if (prev) prev.classList.remove("focus");
    tiles[cur].classList.add("focus");
    // Grade inteira na tela (até 3 fileiras); além disso, sobe uma fileira por vez.
    var r = Math.floor(cur / COLS);
    $("grid").style.top = (36 - Math.max(0, r - 2) * 214) + "px";
  }

  function move(dx, dy) {
    var n = cur;
    if (dx) {
      n = cur + dx;
      if (Math.floor(n / COLS) !== Math.floor(cur / COLS) || n < 0 || n >= tiles.length) return;
    } else {
      n = cur + dy * COLS;
      if (n < 0) return;
      if (n >= tiles.length) {
        if (Math.floor((tiles.length - 1) / COLS) === Math.floor(cur / COLS)) return;
        n = tiles.length - 1;
      }
    }
    cur = n;
    focus();
  }

  function openSource(item) {
    $("tv-hint").textContent = item.name + "  ·  Voltar para sair";
    $("tv-hint").style.opacity = "1";
    try {
      tizen.tvwindow.setSource(item.src, function () {
        tizen.tvwindow.show(function () {
          $("tv-layer").className = "on";
          tvOpen = true;
          setTimeout(function () { $("tv-hint").style.opacity = "0"; }, 3000);
        }, function (e) { diag("tvwindow: " + e.name); }, ["0px", "0px", "1920px", "1080px"], "MAIN");
      }, function (e) { diag("setSource: " + e.name); });
    } catch (e) { diag("tvwindow: " + e.name); }
  }

  function closeSource() {
    try { tizen.tvwindow.hide(function () {}, null, "MAIN"); } catch (e) {}
    $("tv-layer").className = "";
    tvOpen = false;
  }

  function open(item) {
    if (!hasTizen) { diag("(prévia) abrir " + item.name); return; }
    if (item.kind === "source") { openSource(item); return; }
    tizen.application.launch(item.id, null, function (e) { diag(item.name + ": " + e.name); });
  }

  document.addEventListener("keydown", function (e) {
    if (tvOpen) {
      if (e.keyCode === KEY.BACK || e.keyCode === KEY.ESC || e.keyCode === KEY.ENTER) closeSource();
      e.preventDefault();
      return;
    }
    switch (e.keyCode) {
      case KEY.LEFT: move(-1, 0); break;
      case KEY.RIGHT: move(1, 0); break;
      case KEY.UP: move(0, -1); break;
      case KEY.DOWN: move(0, 1); break;
      case KEY.ENTER: if (items[cur]) open(items[cur]); break;
      case KEY.BACK: if (hasTizen) tizen.application.getCurrentApplication().exit(); break;
      default: return;
    }
    e.preventDefault();
  });

  function refresh() {
    build();
    loadSources(function (src) {
      sources = src;
      build();
    });
  }

  tick();
  setInterval(tick, 20000);
  var cached = load("tvtuner.grid");
  if (cached && cached.length) {
    // svg não vai para o cache (é do catálogo); recoloca antes de desenhar.
    cached.forEach(function (i) {
      CATALOG.forEach(function (a) { if (a.name === i.name) i.svg = a.svg; });
    });
    items = cached;
    sources = cached.filter(function (i) { return i.kind === "source"; });
    draw();
  }
  setTimeout(refresh, 0);
  document.addEventListener("visibilitychange", function () { if (!document.hidden && !tvOpen) refresh(); });
})();
