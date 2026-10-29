import getpass
import hashlib
import json
import math
import os
import threading
import uuid
import webbrowser
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path
from flask import Flask, jsonify, render_template, request, abort
from garminconnect import Garmin

ROOT = Path(__file__).resolve().parent
TOKEN_DIR = ROOT / '.state'
CACHE_FILE = TOKEN_DIR / 'activities.json'
def load_settings():
    # Existing process environment takes precedence over the optional .env file.
    values = {}
    env_path = ROOT / '.env'
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            name, value = line.split('=', 1)
            values[name.strip()] = value.strip().strip('"\'')
    def setting(name, default=''):
        return os.environ.get(name, values.get(name, default)).strip()
    return {
        'gear': setting('DEFAULT_GEAR_ID'),
        'latitude': setting('LOCATION_LATITUDE'),
        'longitude': setting('LOCATION_LONGITUDE'),
        'radius': setting('LOCATION_RADIUS_METERS', '50') or '50',
    }

SETTINGS = load_settings()
app = Flask(__name__)
api = None
gear = []
job = {'running': False, 'message': '', 'activities': [], 'error': None}
lock = threading.Lock()
operation_lock = threading.Lock()
load_finished = threading.Event()
load_finished.set()
nonce = uuid.uuid4().hex


def uid(item):
    raw = item.get('uuid') or item.get('gearUuid') or item.get('gearUUID')
    try:
        return str(uuid.UUID(str(raw)))
    except (ValueError, AttributeError):
        return ''


def gear_ids(items):
    if not isinstance(items, list):
        raise ValueError('Unexpected Garmin gear response; no changes made.')
    return {uid(x) for x in items if isinstance(x, dict) and uid(x)}


def route_from_gpx(data):
    root = ET.fromstring(data)
    segments = []
    for seg in root.findall('.//{*}trkseg'):
        points = []
        for p in seg.findall('{*}trkpt'):
            try:
                lat, lon = float(p.attrib['lat']), float(p.attrib['lon'])
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    points.append([lat, lon])
            except (ValueError, KeyError):
                continue
        if points:
            segments.append(points)
    return segments


def distance_to_route(segments, target):
    """Shortest distance to recorded points or the minor great-circle arcs between them."""
    def vector(point):
        lat, lon = map(math.radians, point)
        return (math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat))

    def dot(a, b):
        return sum(x*y for x, y in zip(a, b))

    def cross(a, b):
        return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])

    def norm(a):
        return math.sqrt(dot(a, a))

    def angle(a, b):
        return math.atan2(norm(cross(a, b)), dot(a, b))

    target_vector = vector(target)
    best = float('inf')
    for segment in segments:
        points = [vector(point) for point in segment]
        for point in points:
            best = min(best, angle(target_vector, point))
        for start, end in zip(points, points[1:]):
            normal = cross(start, end)
            length = norm(normal)
            if length < 1e-15:
                # Coincident or antipodal endpoints do not define a unique arc.
                continue
            normal = tuple(v / length for v in normal)
            offset = dot(target_vector, normal)
            projection = tuple(t - offset*n for t, n in zip(target_vector, normal))
            length = norm(projection)
            if length < 1e-15:
                continue
            closest = tuple(v / length for v in projection)
            arc = angle(start, end)
            if angle(start, closest) + angle(closest, end) <= arc + 1e-12:
                best = min(best, angle(target_vector, closest))
    return None if best == float('inf') else best * 6371000


def read_gear():
    result=[]
    for status in ('ACTIVE','RETIRED'):
        start=0
        while True:
            page=api.get_gear_list(start=start, limit=100, gear_statuses=[status])
            if not isinstance(page, list):
                raise ValueError('Unexpected gear list response from Garmin.')
            result.extend(page)
            if len(page)<100: break
            start += len(page)
    unique={}
    for x in result:
        key=uid(x)
        if key:
            name=x.get('displayName') or x.get('customMakeModel') or x.get('name') or ' '.join(filter(None, [x.get('makeName'), x.get('modelName')])) or key
            unique[key]={'id':key,'name':name,'status':x.get('gearStatusName') or x.get('status') or ''}
    return sorted(unique.values(), key=lambda x: x['name'].casefold())


def cache_read():
    try:
        data = json.loads(CACHE_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def cache_key(source, start, end):
    # Never share data across Garmin accounts. If identity is unavailable,
    # skip the cache instead of risking reuse for another user.
    identity = getattr(api, 'display_name', None)
    if not identity:
        return None
    account = hashlib.sha256(identity.encode()).hexdigest()
    return json.dumps([account, source, start, end])


def cache_write(data):
    TOKEN_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = CACHE_FILE.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f)
    os.replace(temp, CACHE_FILE)


def cache_after_changes(source, outcomes):
    changed = {str(x['id']) for x in outcomes if x['ok']}
    had_failure = any(not x['ok'] for x in outcomes)
    if not changed and not had_failure:
        return
    data = cache_read()
    # A changed activity may now belong to another source gear's cached query.
    # Invalidate other source queries for this account to avoid missing it.
    identity = getattr(api, 'display_name', None)
    if not identity:
        return
    account = hashlib.sha256(identity.encode()).hexdigest()
    for key in list(data):
        try:
            owner, cached_source, _, _ = json.loads(key)
            if owner != account:
                continue
            if had_failure or cached_source != source:
                del data[key]
            else:
                data[key]['activities'] = [a for a in data[key]['activities'] if str(a['id']) not in changed]
        except (ValueError, KeyError, TypeError):
            del data[key]
    cache_write(data)


def apply_location(activities, target, radius):
    for item in activities:
        distance = distance_to_route(item.get('route', []), target) if target is not None else None
        item['near'] = None if distance is None else distance <= radius


def load_worker(source, start, end, refresh=False, target=None, radius=50):
    try:
        key = cache_key(source, start, end)
        cached = cache_read().get(key) if key and not refresh else None
        if isinstance(cached, dict) and isinstance(cached.get('activities'), list):
            results = cached['activities']
            if any('date' not in a for a in results):
                # Older caches contain the route and time but not the date.
                # Fetch the activity list once and keep the saved routes.
                job['message'] = 'Updating cached activity dates…'
                summaries = api.get_activities_by_date(start, end, activitytype='walking')
                dates = {str(a['activityId']): a.get('startTimeLocal', '')[:10] for a in summaries}
                for item in results:
                    item['date'] = dates.get(str(item['id']), '')
                try:
                    cache = cache_read()
                    cache[key] = cached
                    cache_write(cache)
                except OSError:
                    pass
            apply_location(results, target, radius)
            job.update(activities=results, message=f"Loaded {len(results)} matching activities from local cache (saved {cached.get('saved', 'unknown time')}).", error=None)
            return
        activities=api.get_activities_by_date(start,end,activitytype='walking')
        results=[]
        for i,a in enumerate(activities):
            job['message']=f'Checking activity {i+1} of {len(activities)}'
            aid=a['activityId']
            if source not in gear_ids(api.get_activity_gear_v2(aid)): continue
            stamp=a.get('startTimeLocal','')
            if not start <= stamp[:10] <= end: continue
            item={'id':aid,'name':a.get('activityName') or 'Untitled walk','date':stamp[:10], 'time':stamp[11:16], 'route':[], 'near':None, 'route_error':None}
            try:
                item['route']=route_from_gpx(api.download_activity(str(aid), api.ActivityDownloadFormat.GPX))
            except Exception as e:
                item['route_error']=str(e)
            results.append(item)
        apply_location(results, target, radius)
        if key:
            try:
                cache = cache_read()
                cache[key] = {'saved': datetime.now().strftime('%Y-%m-%d %H:%M'), 'activities': results}
                cache_write(cache)
            except OSError as e:
                job.update(activities=results, message=f'Loaded {len(results)} activities, but could not save the cache: {e}', error=None)
                return
        job.update(activities=results,message=f'Loaded {len(results)} matching activities from Garmin.',error=None)
    except Exception as e:
        job.update(error=str(e),message='Loading failed.',activities=[])
    finally:
        job['running']=False
        load_finished.set()
        operation_lock.release()


@app.before_request
def guard():
    if request.host not in ('127.0.0.1:8765', 'localhost:8765'):
        abort(403)
    if request.method == 'POST' and request.headers.get('X-App-Token') != nonce:
        abort(403)

@app.get('/')
def index():
    today=date.today()
    return render_template('index.html', gear=gear, token=nonce, default_gear=SETTINGS['gear'], settings=SETTINGS, start=f'{today.year}-01-01', end=f'{today.year}-12-31')

@app.post('/load')
def load():
    data=request.get_json()
    source=data.get('source','')
    try:
        start=date.fromisoformat(data.get('start','')).isoformat(); end=date.fromisoformat(data.get('end','')).isoformat()
        if start > end or source not in {g['id'] for g in gear}: raise ValueError()
    except (ValueError,TypeError):
        return jsonify(error='Choose valid gear and an inclusive date range.'),400
    lat = str(data.get('latitude', '')).strip()
    lon = str(data.get('longitude', '')).strip()
    try:
        radius = float(data.get('radius') or 50)
        if not math.isfinite(radius) or radius <= 0:
            raise ValueError()
        if bool(lat) != bool(lon):
            raise ValueError()
        target = (float(lat), float(lon)) if lat and lon else None
        if target and (not all(math.isfinite(v) for v in target) or not -90 <= target[0] <= 90 or not -180 <= target[1] <= 180):
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify(error='Enter both valid coordinates and a positive distance in metres, or leave both coordinates blank.'),400
    if not operation_lock.acquire(blocking=False): return jsonify(error='Another operation is in progress.'),409
    load_finished.clear()
    job.update(running=True,message='Loading walks from Garmin…',activities=[],error=None,source=source,review_id=uuid.uuid4().hex, location_enabled=target is not None, radius=radius)
    threading.Thread(target=load_worker,args=(source,start,end,bool(data.get('refresh')),target,radius),daemon=True).start()
    return jsonify(ok=True)

@app.get('/status')
def status():
    if request.args.get('wait') == '1' and job['running']:
        load_finished.wait(timeout=25)
    return jsonify(job)

@app.post('/apply')
def apply():
    if not operation_lock.acquire(blocking=False): return jsonify(error='Another operation is in progress.'),409
    try:
        return apply_changes()
    finally:
        operation_lock.release()

def apply_changes():
    data=request.get_json(); changes=data.get('changes',[])
    if not data.get('review_id') or data['review_id'] != job.get('review_id'):
        return jsonify(error='This review is out of date. Reload activities before applying changes.'),409
    allowed={str(a['id']) for a in job['activities']}
    allowed_gear={g['id'] for g in gear}
    source=job.get('source')
    if not isinstance(changes,list) or not changes: return jsonify(error='Choose at least one replacement.'),400
    if any(str(c.get('id')) not in allowed or c.get('gear') not in allowed_gear or c.get('gear') == source for c in changes):
        return jsonify(error='Invalid activity or replacement gear.'),400
    if len({str(c['id']) for c in changes}) != len(changes): return jsonify(error='Duplicate activity.'),400
    outcomes=[]
    for c in changes:
        aid=c['id']; replacement=c['gear']
        try:
            existing=gear_ids(api.get_activity_gear_v2(aid))
            if source not in existing and replacement in existing:
                outcomes.append({'id':aid,'ok':True,'message':'Already changed.'}); continue
            if source not in existing: raise ValueError('Original gear is no longer linked; skipped.')
            if replacement not in existing:
                api.add_gear_to_activity(replacement,aid)
            current=gear_ids(api.get_activity_gear_v2(aid))
            if replacement not in current: raise ValueError('Could not verify replacement; original gear kept.')
            api.remove_gear_from_activity(source,aid)
            final=gear_ids(api.get_activity_gear_v2(aid))
            if source in final or replacement not in final: raise ValueError('Could not verify final gear; check Garmin before retrying.')
            outcomes.append({'id':aid,'ok':True,'message':'Changed.'})
        except Exception as e:
            outcomes.append({'id':aid,'ok':False,'message':str(e)})
    try:
        cache_after_changes(source, outcomes)
    except OSError:
        # Changes may already be applied remotely. Tell the user to refresh.
        return jsonify(outcomes=outcomes, cache_error='Could not update the cache; use Refresh from Garmin next time.')
    return jsonify(outcomes=outcomes)


def main():
    global api, gear
    TOKEN_DIR.mkdir(mode=0o700,exist_ok=True)
    try:
        api=Garmin(); api.login(str(TOKEN_DIR))
        print('Signed in with saved Garmin tokens.')
    except Exception:
        print('Sign in to Garmin Connect. Your password is not saved by this app.')
        email=input('Email: ').strip(); password=getpass.getpass('Password: ')
        api=Garmin(email=email,password=password,prompt_mfa=lambda: input('MFA code: ').strip())
        api.login(str(TOKEN_DIR)); password=None
    gear=read_gear()
    print('Open http://127.0.0.1:8765 and press Ctrl+C here to stop.')
    webbrowser.open('http://127.0.0.1:8765')
    app.run(host='127.0.0.1',port=8765,debug=False,threaded=True)

if __name__ == '__main__': main()
