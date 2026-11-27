// Flight area editor (Plan flight and Locations): a window-filling map to draw a polygon on, next to its corners as
// "lat, lon" lines that can be typed or pasted; both stay in step (drag a corner on the map, or edit a line).
// Needs Leaflet and Leaflet.draw. openAreaEditor({title, points, boundary, onSave(points)}): points is the current
// area ([[lat, lon], ...] or null), boundary the location's shape ({type: 'circle', center, radius_m} or {points}).
(function () {
  const STYLE = `
  dialog.area-ed { width:100vw; height:100vh; max-width:none; max-height:none; margin:0; padding:0; border:0;
    background:var(--card); color:var(--txt); }
  dialog.area-ed[open] { display:flex; flex-direction:column; }
  dialog.area-ed::backdrop { background:rgba(0,0,0,.45); }
  .area-ed-bar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; padding:10px 16px; border-bottom:1px solid var(--line); }
  .area-ed-bar h2 { font-size:16px; margin:0 auto 0 0; }
  .area-ed-body { flex:1; display:grid; grid-template-columns:minmax(0,1fr) 280px; min-height:0; }
  .area-ed-map { min-height:0; }
  .area-ed-side { display:flex; flex-direction:column; gap:8px; padding:10px 12px; border-left:1px solid var(--line); min-height:0; }
  .area-ed-side textarea { flex:1; min-height:120px; width:100%; font:12px ui-monospace, SFMono-Regular, Menlo, monospace; resize:none; }
  .area-ed-side p { margin:0; font-size:12px; }
  .area-ed-err { color:var(--err); }
  @media (max-width:700px) { .area-ed-body { grid-template-columns:1fr; grid-template-rows:minmax(0,1fr) 190px; }
    .area-ed-side { border-left:0; border-top:1px solid var(--line); } }`;
  let dlg = null, map = null, ctx = null, poly = null, drawer = null, opts = null;
  const $q = sel => dlg.querySelector(sel);
  const round = p => [+p.lat.toFixed(7), +p.lng.toFixed(7)];

  function build() {
    const style = document.createElement('style');
    style.textContent = STYLE;
    document.head.append(style);
    dlg = document.createElement('dialog');
    dlg.className = 'area-ed';
    dlg.setAttribute('aria-label', 'Flight area');
    dlg.innerHTML = `<div class="area-ed-bar"><h2></h2>
        <button type="button" class="sec" data-a="draw">Draw new area</button>
        <button type="button" class="primary" data-a="save">Save</button>
        <button type="button" class="sec" data-a="cancel">Cancel</button></div>
      <div class="area-ed-body"><div class="area-ed-map"></div>
        <div class="area-ed-side"><p class="mute" data-r="hint"></p>
          <label class="mute" style="font-size:12px" for="areaEdCorners">Corners: latitude, longitude (decimal degrees), one per line</label>
          <textarea id="areaEdCorners" spellcheck="false" placeholder="52.1680, 4.4170&#10;52.1690, 4.4210&#10;52.1660, 4.4220"></textarea>
          <p class="area-ed-err" data-r="err" role="alert"></p></div></div>`;
    document.body.append(dlg);
    dlg.addEventListener('cancel', e => { e.preventDefault(); if (drawer) stopDraw(); else close(); });  // Escape
    $q('[data-a=draw]').onclick = startDraw;
    $q('[data-a=cancel]').onclick = close;
    $q('[data-a=save]').onclick = save;
    $q('textarea').oninput = fromText;
    map = L.map($q('.area-ed-map'), {attributionControl: false}).setView([52.0, 4.4], 8);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19}).addTo(map);
    ctx = L.layerGroup().addTo(map);
    map.on(L.Draw.Event.CREATED, e => { drawer = null; setPoints(e.layer.getLatLngs()[0].map(round), true); hint(); });
    map.on(L.Draw.Event.DRAWSTOP, () => { drawer = null; hint(); });
  }
  // Lines of "lat, lon" (comma, semicolon, space or tab between them) -> points, or an error message.
  function parse(text) {
    const out = [];
    for (const [i, line] of text.split(/\n/).entries()) {
      if (!line.trim()) continue;
      const m = line.trim().match(/^(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)$/);
      if (!m) return `Line ${i + 1}: write a latitude and a longitude, for example 52.1680, 4.4170.`;
      const lat = +m[1], lon = +m[2];
      if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return `Line ${i + 1}: latitude is at most 90, longitude at most 180.`;
      out.push([lat, lon]);
    }
    if (out.length > 1 && out[0][0] === out.at(-1)[0] && out[0][1] === out.at(-1)[1]) out.pop();  // a closed ring
    return out;
  }
  function err(text) { $q('[data-r=err]').textContent = text || ''; }
  function hint() {
    $q('[data-r=hint]').textContent = drawer ? 'Click the corners on the map; click the first corner to finish. Escape stops.'
      : poly ? 'Drag the corners on the map, or edit the lines below.' : 'Draw the area on the map, or type its corners below.';
  }
  function text(points) { $q('textarea').value = points.map(p => p.join(', ')).join('\n'); }
  function setPoints(points, writeText) {
    if (poly) { poly.editing.disable(); map.removeLayer(poly); poly = null; }
    if (writeText) text(points);
    if (points.length < 3) return;
    poly = L.polygon(points, {color: '#1a7f37', weight: 2, fillOpacity: .25}).addTo(map);
    poly.editing.enable();
    poly.on('edit', () => { text(poly.getLatLngs()[0].map(round)); err(''); });
  }
  function fromText() {
    const points = parse($q('textarea').value);
    if (typeof points === 'string') { err(points); return; }
    err(points.length && points.length < 3 ? 'An area needs at least three corners.' : '');
    setPoints(points, false);
  }
  function startDraw() {
    stopDraw();
    drawer = new L.Draw.Polygon(map, {allowIntersection: false, shapeOptions: {color: '#1a7f37'}});
    drawer.enable();
    hint();
  }
  function stopDraw() { if (drawer) { const d = drawer; drawer = null; d.disable(); } hint(); }
  function close() { stopDraw(); dlg.close(); }
  function save() {
    const points = parse($q('textarea').value);
    if (typeof points === 'string') { err(points); return; }
    if (points.length < 3) { err('An area needs at least three corners.'); return; }
    close();
    opts.onSave(points);
  }
  window.openAreaEditor = function (o) {
    if (!dlg) build();
    opts = o;
    $q('h2').textContent = o.title || 'Flight area';
    err('');
    ctx.clearLayers();
    const b = o.boundary;
    const boundary = b ? (b.type === 'circle' ? L.circle(b.center, {radius: b.radius_m}) : L.polygon(b.points)) : null;
    if (boundary) boundary.setStyle({color: '#57606a', weight: 2, fill: false, dashArray: '2 4', interactive: false}).addTo(ctx);
    dlg.showModal();
    map.invalidateSize();
    setPoints(o.points || [], true);
    const bounds = poly ? poly.getBounds() : null;
    if (boundary) map.fitBounds(boundary.getBounds().extend(bounds || boundary.getBounds()), {padding: [30, 30]});
    else if (bounds) map.fitBounds(bounds, {padding: [30, 30]});
    if (!poly) startDraw(); else hint();
  };
})();
