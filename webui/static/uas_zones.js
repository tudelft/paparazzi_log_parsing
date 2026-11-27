// Drone (UAS) zones (uas_zones.py) on a Leaflet map: a layer below everything else with an on/off switch in the map's
// corner (remembered per browser), and a click on the map lists the zones at that point. Used by Locations and Plan flight.
const UasZones = (() => {
  const LABELS = {PROHIBITED: 'Prohibited', REQ_AUTHORISATION: 'Requires authorisation', CONDITIONAL: 'Conditional', NO_RESTRICTION: 'No restriction'};
  const COLORS = {PROHIBITED: '#cf222e', REQ_AUTHORISATION: '#bc4c00', CONDITIONAL: '#8250df', NO_RESTRICTION: '#1a7f37'};
  const KEY = 'uasZonesHidden';
  const escape = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  let data = null;
  const load = force => {
    if (force) data = null;
    return data = data || fetch('/api/uas-zones').then(r => r.ok ? r.json() : {info: null, zones: []}).catch(() => ({info: null, zones: []}));
  };
  const label = r => LABELS[r] || (r ? r.replace(/_/g, ' ').toLowerCase() : 'Unknown restriction');
  const color = r => COLORS[r] || '#57606a';
  const limits = z => z.upper_m == null ? '' : `${z.lower_m || 0}–${z.upper_m} m${z.upper_ref ? ' ' + z.upper_ref : ''}`;
  const hidden = () => { try { return localStorage.getItem(KEY) === '1'; } catch (e) { return false; } };
  const remember = off => { try { off ? localStorage.setItem(KEY, '1') : localStorage.removeItem(KEY); } catch (e) { /* private window */ } };
  function inside(lat, lon, points) {
    let hit = false;
    for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
      const [yi, xi] = points[i], [yj, xj] = points[j];
      if ((yi > lat) !== (yj > lat) && lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) hit = !hit;
    }
    return hit;
  }
  const at = (zones, ll) => zones.filter(z => z.shapes.some(s => s.type === 'circle'
    ? ll.distanceTo(s.center) <= s.radius_m : inside(ll.lat, ll.lng, s.points)));
  const zoneHTML = z => `<div class="uas-zone" title="${escape(z.message)}"><b style="color:${color(z.restriction)}">${escape(label(z.restriction))}</b>
    ${limits(z) ? `<span class="uas-lim">${escape(limits(z))}</span>` : ''}<br>${escape(z.name)}</div>`;
  // Add the zones to map m (once). Returns a promise of the data ({info, zones}).
  function attach(m) {
    if (m._uasZones) return m._uasZones;
    // a map gets its view later (Plan flight fits it to the area): draw only once it has one
    return m._uasZones = load().then(d => new Promise(ready => m.whenReady(() => ready(d)))).then(d => {
      if (!d.zones.length) return d;
      if (!m.getPane('uasZones')) m.createPane('uasZones').style.zIndex = 350;  // under boundaries, areas and markers
      const renderer = L.canvas({pane: 'uasZones'});
      const group = L.layerGroup();
      for (const z of d.zones) {
        const style = {renderer, pane: 'uasZones', interactive: false, color: color(z.restriction), weight: 1, opacity: .7,
                       fillOpacity: z.restriction === 'PROHIBITED' ? .18 : .07};
        for (const s of z.shapes)
          (s.type === 'circle' ? L.circle(s.center, {...style, radius: s.radius_m}) : L.polygon(s.points, style)).addTo(group);
      }
      if (!hidden()) group.addTo(m);
      const swatch = r => `<i style="display:inline-block;width:9px;height:9px;margin:0 2px 0 6px;border:1px solid ${color(r)};background:${color(r)}33"></i>`;
      const name = `Drone zones <span style="font-size:11px" title="${escape(d.info.title)}">${swatch('PROHIBITED')}prohibited${swatch('REQ_AUTHORISATION')}authorisation${swatch('CONDITIONAL')}conditional</span>`;
      L.control.layers(null, {[name]: group}, {collapsed: false, position: 'topright'}).addTo(m);
      m.on('overlayadd overlayremove', e => { if (e.layer === group) remember(e.type === 'overlayremove'); });
      m.on('click', e => {
        if (!m.hasLayer(group)) return;
        const here = at(d.zones, e.latlng);
        L.popup({maxWidth: 320, className: 'uas-popup'}).setLatLng(e.latlng).setContent(here.length
          ? here.map(zoneHTML).join('') + `<div class="uas-src">${escape(d.info.title)}</div>`
          : `No drone zone here. <div class="uas-src">${escape(d.info.title)}</div>`).openOn(m);
      });
      return d;
    });
  }
  return {load, attach, label, color, limits, escape};
})();
