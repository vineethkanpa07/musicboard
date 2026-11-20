// Musicboard front end. Talks to the Flask API in app.py.
(() => {
  // ---------- helpers ----------
  // Each score maps to one fixed color: 1 = red, about 5 = amber, 10 = green.
  function ratingColor(r) {
    const v = Math.min(10, Math.max(0.5, Number(r)));
    const hue = Math.max(0, 2 + (v - 1) * (128 / 9));
    const light = 50 + Math.sin(((v - 1) / 9) * Math.PI) * 6;
    return `hsl(${hue.toFixed(1)} 72% ${light.toFixed(1)}%)`;
  }
  const fmt = n => { const r = Math.round(n * 10) / 10; return Number.isInteger(r) ? String(r) : r.toFixed(1); };
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = s => document.querySelector(s);
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

  function toast(msg) {
    const t = document.createElement("div"); t.className = "toast"; t.textContent = msg;
    document.body.appendChild(t); setTimeout(() => t.remove(), 3200);
  }
  const setStatus = msg => { $("#status").textContent = msg; };

  async function api(method, url, body) {
    const opts = { method };
    if (body instanceof FormData) opts.body = body;
    else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers = { "Content-Type": "application/json" }; }
    let res;
    try { res = await fetch(url, opts); }
    catch (e) { toast("Couldn't reach the server. Is app.py still running?"); throw e; }
    if (!res.ok) {
      let msg = "Something went wrong saving that.";
      try { msg = (await res.json()).error || msg; } catch (_) { }
      toast(msg); throw new Error(msg);
    }
    return res.status === 204 ? null : res.json();
  }

  $("#scaleCells").innerHTML = Array.from({ length: 10 }, (_, i) =>
    `<span style="background:${ratingColor(i + 1)}">${i + 1}</span>`).join("");

  // ---------- state ----------
  const state = { artists: [], albums: new Map(), arrange: false, openId: null, ready: false };

  async function load() {
    const data = await api("GET", "/api/library");
    state.artists = data.artists;
    state.albums = new Map(data.albums.map(a => [a.id, a]));
    state.ready = true;
    renderDashboard();
  }

  function albumAvg(a) {
    const rs = a.songs.map(s => s.rating).filter(r => typeof r === "number");
    return rs.length ? rs.reduce((x, y) => x + y, 0) / rs.length : null;
  }
  // Albums grouped by artist, in the saved artist order, each group in saved album order.
  function groups() {
    const artists = [...state.artists].sort((a, b) => a.position - b.position || a.id - b.id);
    return artists.map(ar => ({
      ...ar,
      albums: [...state.albums.values()].filter(a => a.artist_id === ar.id)
        .sort((x, y) => x.position - y.position || x.created_at - y.created_at),
    })).filter(g => g.albums.length);
  }
  // Keep the local artist list in step after an album is created or moved.
  function syncArtist(album) {
    if (!state.artists.some(a => a.id === album.artist_id)) {
      const pos = state.artists.reduce((m, a) => Math.max(m, a.position), -1) + 1;
      state.artists.push({ id: album.artist_id, name: album.artist, position: pos });
    }
    pruneArtists();
  }
  function pruneArtists() {
    const used = new Set([...state.albums.values()].map(a => a.artist_id));
    state.artists = state.artists.filter(a => used.has(a.id));
  }

  // ---------- dashboard ----------
  function sleeveHtml(a) {
    const initial = esc((a.title || "?").trim().charAt(0).toUpperCase() || "?");
    return a.cover_url ? `<img src="${esc(a.cover_url)}" alt="">` : `<div class="ph">${initial}</div>`;
  }
  function cardHtml(a, ai, n) {
    const avg = albumAvg(a);
    const songs = a.songs.filter(s => s.name || s.rating != null);
    const bar = songs.length ? `<div class="mini-bar" aria-hidden="true">${songs.map(s =>
      `<i style="background:${s.rating != null ? ratingColor(s.rating) : "var(--surface-2)"}"></i>`).join("")}</div>` : "";
    return `<button class="card" type="button" data-id="${a.id}" ${state.arrange ? 'draggable="true"' : ""} aria-label="${esc(a.title)} by ${esc(a.artist)}">
      <div class="sleeve">${sleeveHtml(a)}${avg != null ? `<span class="avg" style="background:${ratingColor(avg)}">${fmt(avg)}</span>` : ""}</div>
      ${state.arrange ? `<div class="card-move">
        <span class="icon-btn" role="button" tabindex="0" data-act="left" data-id="${a.id}" aria-label="Move left" ${ai === 0 ? 'style="visibility:hidden"' : ""}>◀</span>
        <span class="icon-btn" role="button" tabindex="0" data-act="right" data-id="${a.id}" aria-label="Move right" ${ai === n - 1 ? 'style="visibility:hidden"' : ""}>▶</span></div>` : ""}
      ${bar}
      <div class="card-title">${esc(a.title || "Untitled")}</div>
      <div class="card-sub">${plural(songs.length, "track")}</div>
    </button>`;
  }
  function renderDashboard() {
    const el = $("#shelves");
    $("#arrangeBtn").hidden = state.albums.size < 2;
    $("#arrangeBtn").classList.toggle("on", state.arrange);
    $("#arrangeBtn").textContent = state.arrange ? "Done arranging" : "Arrange";
    el.classList.toggle("arranging", state.arrange);
    if (!state.ready) return;

    const gs = groups();
    const rated = [...state.albums.values()].reduce((n, a) => n + a.songs.filter(s => s.rating != null).length, 0);
    $("#summary").textContent = state.albums.size
      ? `${plural(state.albums.size, "album")} · ${plural(gs.length, "artist")} · ${plural(rated, "track")} rated`
      : "";
    setStatus(state.arrange ? "Drag albums or artists, or use the arrows." : "Saved automatically");

    if (!state.albums.size) {
      el.innerHTML = `<div class="empty"><h2>Your shelf is empty</h2>
        <p>Add an album with its artist and cover. Then rate each track from 0.5 to 10 and watch the colors fill in. Albums by the same artist line up together.</p>
        <button class="btn primary" type="button" data-act="add">+ Add your first album</button></div>`;
      return;
    }
    el.innerHTML = gs.map((g, gi) => {
      const avgs = g.albums.map(albumAvg).filter(v => v != null);
      const gAvg = avgs.length ? fmt(avgs.reduce((a, b) => a + b, 0) / avgs.length) : null;
      return `<section class="group" data-group="${g.id}" ${state.arrange ? 'draggable="true"' : ""}>
        <div class="group-head">
          <h2>${esc(g.name)}</h2>
          <span class="meta">${plural(g.albums.length, "album")}${gAvg ? ` · avg ${gAvg}` : ""}</span>
          ${state.arrange ? `<div class="group-tools">
            <button class="icon-btn" type="button" data-act="gup" data-key="${g.id}" ${gi === 0 ? "disabled" : ""} aria-label="Move ${esc(g.name)} up">▲</button>
            <button class="icon-btn" type="button" data-act="gdown" data-key="${g.id}" ${gi === gs.length - 1 ? "disabled" : ""} aria-label="Move ${esc(g.name)} down">▼</button></div>` : ""}
        </div>
        <div class="grid">${g.albums.map((a, ai) => cardHtml(a, ai, g.albums.length)).join("")}</div>
      </section>`;
    }).join("");
  }

  // ---------- reordering ----------
  function saveAlbumOrder(ids) {
    ids.forEach((id, i) => { state.albums.get(id).position = i; });
    renderDashboard();
    api("PUT", "/api/order", { albums: ids }).catch(() => { });
  }
  function saveArtistOrder(ids) {
    ids.forEach((id, i) => { const a = state.artists.find(x => x.id === id); if (a) a.position = i; });
    renderDashboard();
    api("PUT", "/api/order", { artists: ids }).catch(() => { });
  }
  const groupOf = albumId => groups().find(g => g.id === state.albums.get(albumId).artist_id);

  function moveAlbum(id, targetId, after) {
    const ids = groupOf(id).albums.map(x => x.id).filter(x => x !== id);
    ids.splice(ids.indexOf(targetId) + (after ? 1 : 0), 0, id);
    saveAlbumOrder(ids);
  }
  function shiftAlbum(id, delta) {
    const ids = groupOf(id).albums.map(x => x.id); const i = ids.indexOf(id); const j = i + delta;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    saveAlbumOrder(ids);
  }
  function moveGroup(key, targetKey, after) {
    const ids = groups().map(g => g.id).filter(k => k !== key);
    ids.splice(ids.indexOf(targetKey) + (after ? 1 : 0), 0, key);
    saveArtistOrder(ids);
  }
  function shiftGroup(key, delta) {
    const ids = groups().map(g => g.id); const i = ids.indexOf(key); const j = i + delta;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    saveArtistOrder(ids);
  }

  // ---------- dashboard events ----------
  const shelves = $("#shelves");
  shelves.addEventListener("click", e => {
    const act = e.target.closest("[data-act]");
    if (act) {
      e.stopPropagation();
      const a = act.dataset.act;
      if (a === "add") openAddAlbum();
      if (a === "left") shiftAlbum(Number(act.dataset.id), -1);
      if (a === "right") shiftAlbum(Number(act.dataset.id), 1);
      if (a === "gup") shiftGroup(Number(act.dataset.key), -1);
      if (a === "gdown") shiftGroup(Number(act.dataset.key), 1);
      return;
    }
    const card = e.target.closest(".card");
    if (card && !state.arrange) openAlbum(Number(card.dataset.id));
  });
  shelves.addEventListener("keydown", e => {
    const act = e.target.closest("span[data-act]");
    if (act && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); act.click(); }
  });

  let drag = null;
  const clearMarks = () => document.querySelectorAll(".drop-before,.drop-after,.drop-target,.dragging")
    .forEach(n => n.classList.remove("drop-before", "drop-after", "drop-target", "dragging"));
  shelves.addEventListener("dragstart", e => {
    if (!state.arrange) return;
    const card = e.target.closest(".card");
    if (card) { drag = { type: "album", id: Number(card.dataset.id), group: card.closest(".group").dataset.group }; card.classList.add("dragging"); }
    else { const g = e.target.closest(".group"); if (!g) return; drag = { type: "group", key: Number(g.dataset.group) }; g.classList.add("dragging"); }
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", "x"); } catch (_) { }
  });
  shelves.addEventListener("dragover", e => {
    if (!drag) return;
    if (drag.type === "album") {
      const card = e.target.closest(".card");
      if (!card || Number(card.dataset.id) === drag.id || card.closest(".group").dataset.group !== drag.group) return;
      e.preventDefault();
      const r = card.getBoundingClientRect();
      document.querySelectorAll(".drop-before,.drop-after").forEach(n => n.classList.remove("drop-before", "drop-after"));
      card.classList.add(e.clientX > r.left + r.width / 2 ? "drop-after" : "drop-before");
    } else {
      const g = e.target.closest(".group");
      if (!g || Number(g.dataset.group) === drag.key) return;
      e.preventDefault();
      document.querySelectorAll(".drop-target").forEach(n => n.classList.remove("drop-target"));
      g.classList.add("drop-target");
    }
  });
  shelves.addEventListener("drop", e => {
    if (!drag) return;
    e.preventDefault();
    if (drag.type === "album") {
      const card = e.target.closest(".card");
      if (card && Number(card.dataset.id) !== drag.id) {
        const r = card.getBoundingClientRect();
        moveAlbum(drag.id, Number(card.dataset.id), e.clientX > r.left + r.width / 2);
      }
    } else {
      const g = e.target.closest(".group");
      if (g && Number(g.dataset.group) !== drag.key) {
        const ids = groups().map(x => x.id);
        moveGroup(drag.key, Number(g.dataset.group), ids.indexOf(Number(g.dataset.group)) > ids.indexOf(drag.key));
      }
    }
    drag = null; clearMarks();
  });
  shelves.addEventListener("dragend", () => { drag = null; clearMarks(); });

  $("#arrangeBtn").addEventListener("click", () => { state.arrange = !state.arrange; renderDashboard(); });
  $("#addAlbumBtn").addEventListener("click", openAddAlbum);

  // ---------- cover images (shrunk in the browser before upload) ----------
  function shrinkImage(file) {
    return new Promise((resolve, reject) => {
      if (!file || !file.type.startsWith("image/")) return reject(new Error("Pick an image file (JPG, PNG, WebP)."));
      const img = new Image();
      const url = URL.createObjectURL(file);
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("That image couldn't be opened. Try a JPG or PNG.")); };
      img.onload = () => {
        URL.revokeObjectURL(url);
        const s = Math.min(1, 800 / Math.max(img.width, img.height));
        const c = document.createElement("canvas");
        c.width = Math.round(img.width * s); c.height = Math.round(img.height * s);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        c.toBlob(b => b ? resolve(b) : reject(new Error("Couldn't process that image.")), "image/jpeg", 0.88);
      };
      img.src = url;
    });
  }
  let coverCallback = null;
  $("#coverFile").addEventListener("change", async e => {
    const f = e.target.files[0]; e.target.value = "";
    if (!f || !coverCallback) return;
    try { coverCallback(await shrinkImage(f)); } catch (err) { toast(err.message); }
  });
  function pickCover(cb) { coverCallback = cb; $("#coverFile").click(); }

  // ---------- add album ----------
  const closeOverlay = () => document.querySelector(".overlay")?.remove();
  function openAddAlbum() {
    closeOverlay();
    let cover = null;
    const ov = document.createElement("div"); ov.className = "overlay";
    ov.innerHTML = `<form class="sheet small" role="dialog" aria-modal="true" aria-labelledby="addH">
      <div class="sheet-body">
        <h3 id="addH">New album</h3>
        <div class="field"><label for="newTitle">Album</label><input class="text" id="newTitle" autocomplete="off" placeholder="e.g. Blonde"></div>
        <div class="field"><label for="newArtist">Artist</label><input class="text" id="newArtist" list="artistList" autocomplete="off" placeholder="e.g. Frank Ocean">
          <datalist id="artistList">${groups().map(g => `<option value="${esc(g.name)}">`).join("")}</datalist></div>
        <div class="field"><label>Cover (optional)</label>
          <div class="cover-pick"><div class="sleeve" id="newCoverPrev"><div class="ph">?</div></div>
            <button class="btn" type="button" id="newCoverBtn">Choose image</button></div></div>
        <p class="err" id="newErr" hidden></p>
        <div class="row"><button class="btn" type="button" id="newCancel">Cancel</button><button class="btn primary" type="submit" id="newSubmit">Add album</button></div>
      </div></form>`;
    document.body.appendChild(ov);
    ov.addEventListener("mousedown", e => { if (e.target === ov) closeOverlay(); });
    ov.addEventListener("keydown", e => { if (e.key === "Escape") closeOverlay(); });
    $("#newCancel").onclick = closeOverlay;
    $("#newCoverBtn").onclick = () => pickCover(blob => {
      cover = blob;
      $("#newCoverPrev").innerHTML = `<img src="${URL.createObjectURL(blob)}" alt="Selected cover">`;
      $("#newCoverBtn").textContent = "Change image";
    });
    ov.querySelector("form").addEventListener("submit", async e => {
      e.preventDefault();
      const title = $("#newTitle").value.trim(), artist = $("#newArtist").value.trim();
      if (!title || !artist) { const er = $("#newErr"); er.textContent = "Add both the album name and the artist."; er.hidden = false; return; }
      const fd = new FormData();
      fd.append("title", title); fd.append("artist", artist);
      if (cover) fd.append("cover", cover, "cover.jpg");
      $("#newSubmit").disabled = true;
      try {
        const album = await api("POST", "/api/albums", fd);
        state.albums.set(album.id, album); syncArtist(album);
        renderDashboard(); openAlbum(album.id, true);
      } catch (_) { $("#newSubmit").disabled = false; }
    });
    setTimeout(() => $("#newTitle").focus(), 0);
  }

  // ---------- album sheet ----------
  const songTimers = {};
  function saveSongSoon(songId, fields) {
    clearTimeout(songTimers[songId]?.t);
    const merged = { ...(songTimers[songId]?.fields || {}), ...fields };
    songTimers[songId] = { fields: merged, t: setTimeout(() => { delete songTimers[songId]; api("PATCH", `/api/songs/${songId}`, merged).catch(() => { }); }, 400) };
  }
  function flushSongs() {
    for (const [id, p] of Object.entries(songTimers)) { clearTimeout(p.t); api("PATCH", `/api/songs/${id}`, p.fields).catch(() => { }); delete songTimers[id]; }
  }
  let titleTimer = null;

  function openAlbum(id, focusFirst) {
    closeOverlay();
    const a = state.albums.get(id); if (!a) return;
    state.openId = id;
    const ov = document.createElement("div"); ov.className = "overlay";
    ov.innerHTML = `<div class="sheet" role="dialog" aria-modal="true" aria-label="${esc(a.title)}">
      <button class="icon-btn close-x" type="button" id="closeAlbum" aria-label="Close">✕</button>
      <div class="album-head">
        <button class="cover-btn" type="button" id="coverBtn" aria-label="Change cover">
          <div class="sleeve" id="albumSleeve">${sleeveHtml(a)}</div><span class="hint">${a.cover_url ? "Change cover" : "Add cover"}</span></button>
        <div class="album-meta">
          <input class="bare t-title" id="albTitle" value="${esc(a.title)}" aria-label="Album name">
          <input class="bare t-artist" id="albArtist" value="${esc(a.artist)}" aria-label="Artist" list="artistList2">
          <datalist id="artistList2">${groups().map(g => `<option value="${esc(g.name)}">`).join("")}</datalist>
          <div class="stats" id="albStats"></div>
        </div>
      </div>
      <div class="tracks" id="tracks"></div>
      <button class="add-song" type="button" id="addSong"><b>+</b> Add song</button>
      <p class="tip">'Enter' key moves from the song name to its score, then to a new song. Scores run 0.5 to 10, halves allowed.</p>
      <div class="sheet-foot" id="foot">
        <button class="btn danger" type="button" id="delAlbum">Delete album</button>
        <button class="btn primary" type="button" id="doneAlbum">Done</button>
      </div></div>`;
    document.body.appendChild(ov);
    renderTracks(); renderStats();

    const close = () => { flushSongs(); state.openId = null; closeOverlay(); renderDashboard(); };
    ov.addEventListener("mousedown", e => { if (e.target === ov) close(); });
    $("#closeAlbum").onclick = close; $("#doneAlbum").onclick = close;
    ov.addEventListener("keydown", e => { if (e.key === "Escape") close(); });

    $("#coverBtn").onclick = () => pickCover(async blob => {
      const fd = new FormData(); fd.append("cover", blob, "cover.jpg");
      try {
        const updated = await api("POST", `/api/albums/${id}/cover`, fd);
        a.cover_url = updated.cover_url; $("#albumSleeve").innerHTML = sleeveHtml(a);
      } catch (_) { }
    });
    $("#albTitle").addEventListener("input", e => {
      a.title = e.target.value;
      clearTimeout(titleTimer);
      titleTimer = setTimeout(() => api("PATCH", `/api/albums/${id}`, { title: a.title }).catch(() => { }), 400);
    });
    $("#albTitle").addEventListener("blur", e => { if (!a.title.trim()) { a.title = "Untitled"; e.target.value = a.title; api("PATCH", `/api/albums/${id}`, { title: a.title }).catch(() => { }); } });
    $("#albArtist").addEventListener("change", async e => {
      try {
        const updated = await api("PATCH", `/api/albums/${id}`, { artist: e.target.value });
        Object.assign(a, updated); e.target.value = a.artist; syncArtist(a);
      } catch (_) { e.target.value = a.artist; }
    });
    $("#addSong").onclick = () => addSong(true);
    $("#delAlbum").onclick = () => {
      const n = a.songs.filter(s => s.name).length;
      $("#foot").innerHTML = `<div class="confirm"><span>Delete “${esc(a.title)}”${n ? ` and its ${plural(n, "song")}` : ""}?</span>
        <button class="btn danger solid" type="button" id="yesDel">Delete</button><button class="btn" type="button" id="noDel">Keep it</button></div>`;
      $("#noDel").onclick = () => openAlbum(id);
      $("#yesDel").onclick = async () => {
        try {
          await api("DELETE", `/api/albums/${id}`);
          state.albums.delete(id); pruneArtists();
          state.openId = null; closeOverlay(); renderDashboard(); toast("Album deleted");
        } catch (_) { }
      };
    };
    if (focusFirst) setTimeout(() => document.querySelector(".track .name")?.focus(), 0);
  }

  function renderStats() {
    const a = state.albums.get(state.openId); if (!a) return;
    const avg = albumAvg(a);
    const rated = a.songs.filter(s => s.rating != null);
    const best = rated.length ? rated.reduce((x, y) => (y.rating > x.rating ? y : x)) : null;
    $("#albStats").innerHTML = `
      <div class="stat"><div class="k">Average</div><div class="v">${avg != null ? `<span class="big-avg" style="background:${ratingColor(avg)}">${fmt(avg)}</span>` : "—"}</div></div>
      <div class="stat"><div class="k">Rated</div><div class="v">${rated.length}/${a.songs.filter(s => s.name || s.rating != null).length}</div></div>
      ${best && best.name ? `<div class="stat" style="min-width:0"><div class="k">Top track</div><div style="font-weight:600;overflow-wrap:anywhere">${esc(best.name)}</div></div>` : ""}`;
  }
  function trackHtml(s, i) {
    const filled = s.rating != null;
    return `<div class="track" data-sid="${s.id}">
      <span class="no">${i + 1}</span>
      <input class="name" id="n-${s.id}" value="${esc(s.name)}" placeholder="Song name" aria-label="Song ${i + 1} name" autocomplete="off">
      <input class="rate${filled ? " filled" : ""}" id="r-${s.id}" value="${filled ? fmt(s.rating) : ""}" placeholder="–/10" inputmode="decimal"
        aria-label="Score out of 10" autocomplete="off" style="${filled ? `background:${ratingColor(s.rating)}` : ""}">
      <button class="del" type="button" data-del="${s.id}" aria-label="Remove song">×</button>
    </div>`;
  }
  function renderTracks() {
    const a = state.albums.get(state.openId); if (!a) return;
    $("#tracks").innerHTML = a.songs.map((s, i) => trackHtml(s, i)).join("");
  }
  async function addSong(focus) {
    const id = state.openId; const a = state.albums.get(id);
    try {
      const s = await api("POST", `/api/albums/${id}/songs`, { name: "" });
      if (state.openId !== id) return;
      a.songs.push(s);
      $("#tracks").insertAdjacentHTML("beforeend", trackHtml(s, a.songs.length - 1));
      if (focus) document.getElementById("n-" + s.id).focus();
    } catch (_) { }
  }
  function parseRating(v) {
    const t = String(v).trim().replace(",", ".");
    if (!t) return null;
    const n = Number(t);
    if (!isFinite(n)) return undefined;
    return Math.round(Math.min(10, Math.max(0.5, n)) * 2) / 2;
  }
  const songFromEvent = e => {
    const a = state.albums.get(state.openId); const row = e.target.closest?.(".track");
    if (!a || !row) return null;
    return { a, s: a.songs.find(x => x.id === Number(row.dataset.sid)) };
  };
  document.addEventListener("input", e => {
    const hit = songFromEvent(e); if (!hit || !hit.s) return;
    const { s } = hit;
    if (e.target.classList.contains("name")) { s.name = e.target.value; saveSongSoon(s.id, { name: s.name }); renderStats(); }
    if (e.target.classList.contains("rate")) {
      const r = parseRating(e.target.value);
      if (r === undefined) return;
      s.rating = r;
      e.target.classList.toggle("filled", r != null);
      e.target.style.background = r != null ? ratingColor(r) : "";
      saveSongSoon(s.id, { rating: r }); renderStats();
    }
  });
  document.addEventListener("focusout", e => {
    if (!e.target.classList?.contains("rate")) return;
    const hit = songFromEvent(e); if (!hit || !hit.s) return;
    e.target.value = hit.s.rating != null ? fmt(hit.s.rating) : "";
  });
  document.addEventListener("keydown", e => {
    const hit = songFromEvent(e); if (!hit || !hit.s) return;
    const { a, s } = hit;
    if (e.target.classList.contains("name") && e.key === "Enter") { e.preventDefault(); document.getElementById("r-" + s.id).select(); }
    if (!e.target.classList.contains("rate")) return;
    if (e.key === "Enter") {
      e.preventDefault();
      const next = a.songs[a.songs.indexOf(s) + 1];
      if (next) document.getElementById("n-" + next.id).focus(); else addSong(true);
    }
    if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      const up = e.key === "ArrowUp";
      const cur = s.rating ?? (up ? 0 : 11);
      const step = up ? Math.floor(cur) + 1 : Math.ceil(cur) - 1;   // whole numbers, with 0.5 at the bottom
      e.target.value = Math.min(10, Math.max(0.5, step));
      e.target.dispatchEvent(new Event("input", { bubbles: true }));
    }
  });
  document.addEventListener("click", async e => {
    const b = e.target.closest("[data-del]"); if (!b || !state.openId) return;
    const a = state.albums.get(state.openId); const sid = Number(b.dataset.del);
    try {
      clearTimeout(songTimers[sid]?.t); delete songTimers[sid];
      await api("DELETE", `/api/songs/${sid}`);
      a.songs = a.songs.filter(x => x.id !== sid);
      renderTracks(); renderStats();
    } catch (_) { }
  });
  window.addEventListener("beforeunload", flushSongs);

  load().catch(() => setStatus("Couldn't load your albums. Make sure app.py is running, then reload."));
})();
