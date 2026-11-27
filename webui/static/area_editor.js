// Area editor (Plan flight and Locations): a window-filling map next to the coordinates, which can be typed or pasted;
// both stay in step (drag a corner or the circle on the map, or edit the fields). Needs Leaflet and Leaflet.draw.
// openAreaEditor({title, boundary, points, editBoundary, editArea = true, areaRequired = true, onSave(area, boundary)}):
// boundary is the location's shape ({type: 'circle', center, radius_m} or {type: 'polygon', points}), points the flight
// area ([[lat, lon], ...] or null). With editBoundary the boundary can be changed too (polygon corners, or a circle's
// centre and radius); without editArea only the boundary is edited. onSave gets the area (null when left empty and
// not required) and the boundary.
(function () {
  const STYLE = `
  dialog.area-ed { width:100vw; height:100vh; max-width:none; max-height:none; margin:0; padding:0; border:0;
    background:var(--card); color:var(--txt); }
  dialog.area-ed[open] { display:flex; flex-direction:column; }
  dialog.area-ed::backdrop { background:rgba(0,0,0,.45); }
  .area-ed-bar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; padding:10px 16px; border-bottom:1px solid var(--line); }
  .area-ed-bar h2 { font-size:16px; margin:0 auto 0 0; }
  .area-ed-body { flex:1; display:grid; grid-template-columns:minmax(0,1fr) 300px; min-height:0; }
  .area-ed-map { min-height:0; }
  .area-ed-side { display:flex; flex-direction:column; gap:10px; padding:10px 12px; border-left:1px solid var(--line);
    min-height:0; overflow:auto; }
  .area-ed-sec { display:flex; flex-direction:column; gap:6px; flex:1; min-height:150px; }
  .area-ed-sec h3 { font-size:13px; margin:0; display:flex; align-items:center; gap:8px; }
  .area-ed-sec h3 i { display:inline-block; width:14px; height:8px; border:2px solid; }
  .area-ed-sec h3 select { margin-left:auto; font-size:12px; }
  .area-ed-sec textarea { flex:1; min-height:90px; width:100%; font:12px ui-monospace, SFMono-Regular, Menlo, monospace; resize:none; }
  .area-ed-circle { display:grid; grid-template-columns:1fr 1fr; gap:6px; font-size:12px; }
  .area-ed-circle label { display:flex; flex-direction:column; gap:2px; color:var(--mute); }
  .area-ed-circle label:last-child { grid-column:1 / -1; }
  .area-ed-circle input { width:100%; min-width:0; }
  .area-ed-side p { margin:0; font-size:12px; }
  dialog.area-ed [hidden] { display:none !important; }
  .area-ed-err { color:var(--err); }
  @media (max-width:700px) { .area-ed-body { grid-template-columns:1fr; grid-template-rows:minmax(0,1fr) 230px; }
    .area-ed-side { border-left:0; border-top:1px solid var(--line); } }`;
  const AREA = {color: '#1a7f37', weight: 2, fillOpacity: .25};
  const BOUNDARY = {color: '#57606a', weight: 2, fillOpacity: .05, dashArray: '2 4'};
  let dlg = null, map = null, opts = null, drawer = null, drawing = null;
  let area = null, boundary = null;  // the editable layers
  const $q = sel => dlg.querySelector(sel);
  const round = p => [+p.lat.toFixed(7), +p.lng.toFixed(7)];

  function build() {
    const style = document.createElement('style');
    style.textContent = STYLE;
    document.head.append(style);
    dlg = document.createElement('dialog');
    dlg.className = 'area-ed';
    dlg.setAttribute('aria-labelledby', 'areaEdTitle');
    dlg.innerHTML = `<div class="area-ed-bar"><h2 id="areaEdTitle"></h2>
        <button type="button" class="sec" data-a="drawBoundary">Draw new boundary</button>
        <button type="button" class="sec" data-a="drawArea">Draw new flight area</button>
        <button type="button" class="primary" data-a="save">Save</button>
        <button type="button" class="sec" data-a="cancel">Cancel</button></div>
      <div class="area-ed-body"><div class="area-ed-map"></div>
        <div class="area-ed-side"><p class="mute" data-r="hint"></p>
          <div class="area-ed-sec" data-r="boundarySec"><h3><i style="border-color:#57606a;border-style:dotted"></i>Location boundary
              <select data-r="kind" aria-label="Boundary shape"><option value="polygon">Corners</option><option value="circle">Circle</option></select></h3>
            <label class="mute" style="font-size:12px" for="areaEdBoundary" data-r="boundaryLabel">Corners: latitude, longitude (decimal degrees), one per line</label>
            <textarea id="areaEdBoundary" spellcheck="false" data-r="boundaryText"></textarea>
            <div class="area-ed-circle" data-r="circle"><label>Centre latitude<input type="number" step="any" data-r="lat"></label>
              <label>Centre longitude<input type="number" step="any" data-r="lon"></label>
              <label>Radius (m)<input type="number" step="any" min="1" data-r="radius"></label></div></div>
          <div class="area-ed-sec" data-r="areaSec"><h3><i style="border-color:#1a7f37;background:#1a7f3740"></i>Flight area</h3>
            <label class="mute" style="font-size:12px" for="areaEdCorners">Corners: latitude, longitude (decimal degrees), one per line</label>
            <textarea id="areaEdCorners" spellcheck="false" data-r="areaText" placeholder="52.1680, 4.4170&#10;52.1690, 4.4210&#10;52.1660, 4.4220"></textarea></div>
          <p class="area-ed-err" data-r="err" role="alert"></p></div></div>`;
    document.body.append(dlg);
    dlg.addEventListener('cancel', e => { e.preventDefault(); if (drawer) stopDraw(); else close(); });  // Escape
    $q('[data-a=drawArea]').onclick = () => startDraw('area');
    $q('[data-a=drawBoundary]').onclick = () => startDraw('boundary');
    $q('[data-a=cancel]').onclick = close;
    $q('[data-a=save]').onclick = save;
    $q('[data-r=areaText]').oninput = () => fromText('area');
    $q('[data-r=boundaryText]').oninput = () => fromText('boundary');
    for (const k of ['lat', 'lon', 'radius']) $q(`[data-r=${k}]`).oninput = fromCircleFields;
    $q('[data-r=kind]').onchange = () => { setBoundary(null); paintKind(); startDraw('boundary'); };
    map = L.map($q('.area-ed-map'), {attributionControl: false}).setView([52.0, 4.4], 8);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19}).addTo(map);
    map.on(L.Draw.Event.CREATED, e => {
      const target = drawing;
      drawer = drawing = null;
      if (target === 'area') setArea(e.layer.getLatLngs()[0].map(round), true);
      else setBoundary(e.layer instanceof L.Circle ? circleShape(e.layer) : {type: 'polygon', points: e.layer.getLatLngs()[0].map(round)}, true);
      hint();
    });
    map.on(L.Draw.Event.DRAWSTOP, () => { drawer = drawing = null; hint(); });
  }
  const circleShape = c => ({type: 'circle', center: round(c.getLatLng()), radius_m: Math.round(c.getRadius() * 10) / 10});
  // Lines of "lat, lon" (comma, semicolon, space or tab between them) -> points, or an error message.
  function parse(text, what) {
    const out = [];
    for (const [i, line] of text.split(/\n/).entries()) {
      if (!line.trim()) continue;
      const m = line.trim().match(/^(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)$/);
      if (!m) return `${what}, line ${i + 1}: write a latitude and a longitude, for example 52.1680, 4.4170.`;
      const lat = +m[1], lon = +m[2];
      if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return `${what}, line ${i + 1}: latitude is at most 90, longitude at most 180.`;
      out.push([lat, lon]);
    }
    if (out.length > 1 && out[0][0] === out.at(-1)[0] && out[0][1] === out.at(-1)[1]) out.pop();  // a closed ring
    return out;
  }
  function err(text) { $q('[data-r=err]').textContent = text || ''; }
  function hint() {
    const what = drawing === 'boundary' ? (kind() === 'circle' ? 'Drag from the centre to draw the circle.' : 'Click the corners of the boundary; click the first corner to finish.')
      : drawing === 'area' ? 'Click the corners of the flight area; click the first corner to finish.' : null;
    $q('[data-r=hint]').textContent = what ? what + ' Escape stops.'
      : 'Drag the corners (or the circle) on the map, or edit the coordinates below.';
  }
  const kind = () => $q('[data-r=kind]').value;
  function paintKind() {
    const circle = kind() === 'circle';
    $q('[data-r=boundaryText]').hidden = $q('[data-r=boundaryLabel]').hidden = circle;
    $q('[data-r=circle]').hidden = !circle;
  }
  const lines = points => points.map(p => p.join(', ')).join('\n');
  function writeBoundaryFields(shape) {
    if (shape && shape.type === 'circle') {
      $q('[data-r=lat]').value = shape.center[0]; $q('[data-r=lon]').value = shape.center[1]; $q('[data-r=radius]').value = shape.radius_m;
    } else $q('[data-r=boundaryText]').value = shape ? lines(shape.points) : '';
  }
  function setArea(points, write) {
    if (area) { area.editing.disable(); map.removeLayer(area); area = null; }
    if (write) $q('[data-r=areaText]').value = lines(points);
    if (points.length < 3) return;
    area = L.polygon(points, AREA).addTo(map);
    area.editing.enable();
    area.on('edit', () => { $q('[data-r=areaText]').value = lines(area.getLatLngs()[0].map(round)); err(''); });
  }
  function setBoundary(shape, write) {
    if (boundary) { if (boundary.editing) boundary.editing.disable(); map.removeLayer(boundary); boundary = null; }
    if (write) writeBoundaryFields(shape);
    if (!shape || (shape.type !== 'circle' && shape.points.length < 3)) return;
    boundary = (shape.type === 'circle' ? L.circle(shape.center, {radius: shape.radius_m, ...BOUNDARY}) : L.polygon(shape.points, BOUNDARY)).addTo(map);
    if (!opts.editBoundary) { boundary.options.interactive = false; return; }
    boundary.editing.enable();
    boundary.on('edit', () => {
      writeBoundaryFields(boundary instanceof L.Circle ? circleShape(boundary) : {type: 'polygon', points: boundary.getLatLngs()[0].map(round)});
      err('');
    });
  }
  function fromText(which) {
    const points = parse($q(which === 'area' ? '[data-r=areaText]' : '[data-r=boundaryText]').value, which === 'area' ? 'Flight area' : 'Boundary');
    if (typeof points === 'string') { err(points); return; }
    err(points.length && points.length < 3 ? 'An area needs at least three corners.' : '');
    if (which === 'area') setArea(points, false); else setBoundary({type: 'polygon', points}, false);
  }
  function circleFields() {
    const [lat, lon, r] = ['lat', 'lon', 'radius'].map(k => $q(`[data-r=${k}]`).value.trim());
    if (!lat || !lon || !r) return 'The circle needs a centre latitude, longitude and a radius.';
    if (Math.abs(+lat) > 90 || Math.abs(+lon) > 180 || !(+r > 0)) return 'Latitude is at most 90, longitude at most 180, and the radius more than 0 m.';
    return {type: 'circle', center: [+lat, +lon], radius_m: +r};
  }
  function fromCircleFields() {
    const shape = circleFields();
    if (typeof shape === 'string') { err(shape); return; }
    err('');
    setBoundary(shape, false);
  }
  function startDraw(target) {
    stopDraw();
    drawing = target;
    drawer = target === 'boundary' && kind() === 'circle' ? new L.Draw.Circle(map, {shapeOptions: BOUNDARY})
      : new L.Draw.Polygon(map, {allowIntersection: false, shapeOptions: target === 'area' ? AREA : BOUNDARY});
    drawer.enable();
    hint();
  }
  function stopDraw() { if (drawer) { const d = drawer; drawer = drawing = null; d.disable(); } hint(); }
  function close() { stopDraw(); dlg.close(); }
  function save() {
    let shape = opts.boundary || null;
    if (opts.editBoundary) {
      if (kind() === 'circle') shape = circleFields();
      else {
        const points = parse($q('[data-r=boundaryText]').value, 'Boundary');
        shape = typeof points === 'string' ? points : points.length < 3 ? 'The boundary needs at least three corners.' : {type: 'polygon', points};
      }
      if (typeof shape === 'string') { err(shape); return; }
    }
    let points = null;
    if (opts.editArea) {
      points = parse($q('[data-r=areaText]').value, 'Flight area');
      if (typeof points === 'string') { err(points); return; }
      if (points.length && points.length < 3) { err('The flight area needs at least three corners.'); return; }
      if (!points.length) {
        if (opts.areaRequired) { err('The flight area needs at least three corners.'); return; }
        points = null;
      }
    }
    close();
    opts.onSave(points, shape);
  }
  window.openAreaEditor = function (o) {
    if (!dlg) build();
    opts = {editArea: true, areaRequired: true, ...o};
    $q('#areaEdTitle').textContent = o.title || 'Flight area';
    err('');
    $q('[data-r=boundarySec]').hidden = $q('[data-a=drawBoundary]').hidden = !opts.editBoundary;
    $q('[data-r=areaSec]').hidden = $q('[data-a=drawArea]').hidden = !opts.editArea;
    $q('[data-r=areaText]').placeholder = opts.areaRequired ? '52.1680, 4.4170\n52.1690, 4.4210\n52.1660, 4.4220' : 'Empty: no flight area';
    $q('[data-r=kind]').value = o.boundary && o.boundary.type === 'circle' ? 'circle' : 'polygon';
    $q('[data-r=lat]').value = $q('[data-r=lon]').value = $q('[data-r=radius]').value = $q('[data-r=boundaryText]').value = '';
    paintKind();
    dlg.showModal();
    map.invalidateSize();
    setBoundary(o.boundary || null, true);
    setArea(opts.editArea ? o.points || [] : [], true);
    const bounds = [boundary, area].filter(Boolean).map(l => l.getBounds());
    if (bounds.length) map.fitBounds(bounds.reduce((a, b) => a.extend(b), L.latLngBounds(bounds[0].getSouthWest(), bounds[0].getNorthEast())), {padding: [30, 30]});
    if (opts.editArea && !area && !(opts.editBoundary && !boundary)) startDraw('area');
    else if (opts.editBoundary && !boundary) startDraw('boundary');
    else hint();
  };
})();
