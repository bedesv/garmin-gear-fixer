const $ = id => document.getElementById(id);
let activities = [], maps = [], applying = false, loadedSource = null, reviewId = null, locationEnabled = false, loadedRadius = 50;
let pickerMap = null, pickerMarker = null;

async function post(path, data) {
  const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-App-Token': APP_TOKEN}, body: JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'Request failed');
  return result;
}
function text(tag, value) { const element = document.createElement(tag); element.textContent = value; return element; }
function selected() { return activities.filter(a => a.checkbox.checked && !a.completed); }
function filter() {
  const name = $('name').value, from = $('timeFrom').value, to = $('timeTo').value, near = $('near').value;
  let visible = 0;
  for (const a of activities) {
    const t = a.time;
    let timeOk = true;
    if ((from || to) && !t) timeOk = false;
    else if (from && to && from > to) timeOk = t >= from || t <= to;
    else timeOk = (!from || t >= from) && (!to || t <= to);
    const n = a.near === null ? 'unknown' : String(a.near);
    const show = (!name || name === a.name) && timeOk && (!near || near === n);
    a.element.hidden = !show;
    if (show) visible++;
  }
  $('count').textContent = `${visible} shown · ${activities.length} loaded · ${selected().length} selected`;
  $('apply').disabled = applying || !selected().length || !$('replacement').value || $('replacement').value === loadedSource;
  for (const m of maps) if (!m.el.closest('.activity').hidden) m.map.invalidateSize();
}
function setReplacementOptions() {
  for (const option of $('replacement').options) option.disabled = !!option.value && option.value === loadedSource;
  if ($('replacement').value === loadedSource) $('replacement').value = '';
}
function render(data) {
  maps.forEach(m => m.map.remove()); maps = []; activities = data;
  $('results').replaceChildren(); $('name').replaceChildren(new Option('All names', ''));
  for (const n of [...new Set(data.map(a => a.name))].sort()) $('name').add(new Option(n, n));
  for (const a of data) {
    const card = document.createElement('article'); card.className = 'activity'; a.element = card;
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.className = 'activity-check';
    checkbox.setAttribute('aria-label', `Select ${a.name}, ${a.date || 'date unknown'} ${a.time || ''}`);
    checkbox.addEventListener('change', filter); a.checkbox = checkbox; a.completed = false;
    const info = document.createElement('div');
    info.append(text('h3', a.name), text('p', `Start: ${a.date || 'Date unknown'} ${a.time || 'Time unknown'}`));
    if (locationEnabled) info.append(text('p', `Within ${loadedRadius} m: ${a.near === null ? 'Unknown' : String(a.near)}`));
    a.message = text('p', a.route_error ? 'Route unavailable: ' + a.route_error : ''); a.message.className = 'message'; info.append(a.message);
    const mapEl = document.createElement('div'); mapEl.className = 'map'; card.append(checkbox, info, mapEl); $('results').append(card);
    if (a.route.length) {
      const map = L.map(mapEl, {zoomControl: false, attributionControl: true, scrollWheelZoom: false, dragging: false, doubleClickZoom: false, touchZoom: false});
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '© OpenStreetMap'}).addTo(map);
      const route = L.polyline(a.route, {color: '#087650', weight: 3}).addTo(map);
      map.fitBounds(route.getBounds(), {padding: [8, 8], maxZoom: 16}); maps.push({map, el: mapEl});
    } else { mapEl.textContent = 'No route available'; mapEl.style.display = 'grid'; mapEl.style.placeItems = 'center'; }
  }
  setReplacementOptions(); filter();
}
async function loadActivities(refresh = false) {
  if (selected().length && !confirm('Loading another selection will discard checked activities. Continue?')) return;
  try {
    $('load').disabled = true; $('refresh').disabled = true;
    await post('/load', {source: $('source').value, start: $('start').value, end: $('end').value, latitude: $('latitude').value, longitude: $('longitude').value, radius: $('radius').value, refresh});
    render([]); $('status').textContent = 'Loading walks from Garmin or local cache…';
    const poll = async () => {
      try {
        const s = await (await fetch('/status?wait=1')).json();
        $('status').textContent = s.error ? 'Error: ' + s.error : s.message;
        if (s.running) { poll(); return; }
        $('load').disabled = false; $('refresh').disabled = false;
        if (!s.error) { loadedSource = s.source; reviewId = s.review_id; locationEnabled = s.location_enabled; loadedRadius = s.radius; $('near').value = ''; $('nearLabel').hidden = !locationEnabled; $('nearTitle').textContent = `Within ${loadedRadius} m`; render(s.activities); }
      } catch (e) { $('status').textContent = 'Error: ' + e.message; $('load').disabled = false; $('refresh').disabled = false; }
    };
    poll();
  } catch (e) { $('status').textContent = e.message; $('load').disabled = false; $('refresh').disabled = false; }
}
$('load').onclick = () => loadActivities(false);
$('refresh').onclick = () => loadActivities(true);
$('apply').onclick = async () => {
  const chosen = selected(), replacement = $('replacement').value;
  if (!chosen.length || !replacement || replacement === loadedSource) return;
  const hidden = chosen.filter(a => a.element.hidden).length;
  const gearName = $('replacement').selectedOptions[0].textContent;
  if (!confirm(`Change ${chosen.length} selected activities${hidden ? ` (${hidden} hidden by filters)` : ''} to ${gearName}?`)) return;
  const changes = chosen.map(a => ({id: a.id, gear: replacement}));
  applying = true; $('apply').disabled = true; $('load').disabled = true; $('refresh').disabled = true; $('replacement').disabled = true;
  activities.forEach(a => a.checkbox.disabled = true);
  try {
    const result = await post('/apply', {changes, review_id: reviewId});
    for (const r of result.outcomes) {
      const a = activities.find(x => String(x.id) === String(r.id));
      a.message.textContent = r.message; a.message.className = r.ok ? 'message ok' : 'message';
      if (r.ok) { a.checkbox.checked = false; a.completed = true; }
    }
    $('status').textContent = `Finished: ${result.outcomes.filter(x => x.ok).length} changed, ${result.outcomes.filter(x => !x.ok).length} need attention.` + (result.cache_error ? ' ' + result.cache_error : '');
  } catch (e) { $('status').textContent = 'Error: ' + e.message; }
  finally {
    applying = false; $('load').disabled = false; $('refresh').disabled = false; $('replacement').disabled = false;
    activities.forEach(a => a.checkbox.disabled = a.completed); filter();
  }
};
for (const id of ['name', 'timeFrom', 'timeTo', 'near', 'replacement']) $(id).addEventListener('change', filter);

function pickerLocation() {
  const latText = $('latitude').value, lonText = $('longitude').value;
  if (!latText || !lonText) return null;
  const lat = Number(latText), lon = Number(lonText);
  return Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? [lat, lon] : null;
}
function updatePickerMarker() {
  if (!pickerMap) return;
  const position = pickerLocation();
  if (pickerMarker) { pickerMap.removeLayer(pickerMarker); pickerMarker = null; }
  if (position) {
    pickerMarker = L.circleMarker(position, {radius: 7, color: '#087650', fillOpacity: 0.8}).addTo(pickerMap);
  }
}
$('mapPicker').addEventListener('toggle', () => {
  if (!$('mapPicker').open) return;
  if (!pickerMap) {
    pickerMap = L.map('pickerMap');
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '© OpenStreetMap contributors'}).addTo(pickerMap);
    const location = pickerLocation(); pickerMap.setView(location || [0, 0], location ? 14 : 2);
    updatePickerMarker();
    pickerMap.on('click', event => {
      const point = event.latlng.wrap();
      $('latitude').value = point.lat.toFixed(6);
      $('longitude').value = point.lng.toFixed(6);
      updatePickerMarker();
    });
  }
  setTimeout(() => pickerMap.invalidateSize(), 0);
});
for (const id of ['latitude', 'longitude']) $(id).addEventListener('change', () => {
  updatePickerMarker();
  const position = pickerLocation();
  if (pickerMap && position) pickerMap.setView(position, 14);
});
