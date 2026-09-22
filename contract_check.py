"""Assert every endpoint returns exactly the keys the Swift wire types decode.

Swift's synthesized Decodable throws on a missing key even when the property has
a default, so an omitted field is a hard failure in the app. This walks every
response shape and reports anything the client would choke on.
"""
import inspect
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path('/Users/prat14k/Office/VoxCPM')
PORT = 8822
BASE = f'http://127.0.0.1:{PORT}'

sys.path.insert(0, str(REPO))
import providers

# Keyed by the Swift struct that decodes each response.
EXPECTED = {
    '/health': ['ready', 'loading', 'error', 'warm', 'dry_run', 'model', 'device',
                'sample_rate', 'output_dir', 'themes', 'aspects', 'theme_labels',
                'theme_palettes', 'visual_kinds', 'claude_available'],
    '/settings': ['provider', 'defaults', 'presets', 'claude_available', 'output_dir'],
    '/voices': ['voices'],
    '/library': ['demos'],
    '/providers/detect': ['found'],
}
EXPECTED['provider'] = ['kind', 'preset', 'base_url', 'api_key', 'model', 'temperature',
                        'max_tokens', 'timeout', 'digest_budget', 'claude_model',
                        'api_key_set']
EXPECTED['defaults'] = ['theme', 'aspect', 'voice_id', 'sfx', 'scenes', 'angle']
EXPECTED['preset'] = ['label', 'base_url', 'local', 'key_required', 'note']
EXPECTED['voice'] = ['id', 'name', 'kind', 'description', 'enrolled']
EXPECTED['demo'] = ['id', 'name', 'subtitle', 'path', 'project', 'bytes', 'scenes',
                    'duration', 'voice', 'theme', 'aspect', 'has_hook', 'has_close',
                    'created']
EXPECTED['detected'] = ['port', 'hint', 'base_url', 'models', 'ok', 'error', 'api_key']
EXPECTED['models'] = ['ok', 'models', 'error', 'provider']
EXPECTED['test'] = ['ok', 'models', 'reply', 'error', 'latency_ms']
EXPECTED['theme_palette'] = ['bg', 'bg2', 'accent', 'accent2', 'text', 'dark']
EXPECTED['analyze'] = ['title', 'subtitle', 'hook', 'logo', 'scenes', 'provider', 'cost_usd']
EXPECTED['analyze_scene'] = ['heading', 'role', 'media', 'text', 'visual',
                             'visual_ref', 'visual_note']
EXPECTED['demo_result'] = ['path', 'project', 'scenes', 'duration', 'voice', 'title',
                           'theme', 'aspect', 'has_hook', 'has_close']

problems = []


def check(label, got, key):
    missing = [k for k in EXPECTED[key] if k not in got]
    extra = [k for k in got if k not in EXPECTED[key]]
    status = 'OK ' if not missing else 'MISSING'
    print(f'  [{status}] {label}')
    if missing:
        print(f'          missing: {missing}')
        problems.append(f'{label}: missing {missing}')
    if extra:
        print(f'          extra (ignored by the client): {extra}')


def call(path, body=None, method=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 method=method or ('POST' if data else 'GET'))
    if data:
        req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


srv = subprocess.Popen([str(REPO / '.venv/bin/python'), 'server.py', '--dry-run'],
                       cwd=REPO, env={**os.environ, 'VOXDEMO_PORT': str(PORT)},
                       stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
try:
    for _ in range(60):
        try:
            call('/health', timeout=3)
            break
        except Exception:
            time.sleep(0.5)

    h = call('/health')
    check('/health', h, '/health')
    if h.get('theme_palettes'):
        check('theme_palettes[midnight]', h['theme_palettes'][list(h['theme_palettes'])[0]],
              'theme_palette')

    s = call('/settings')
    check('/settings', s, '/settings')
    check('settings.provider', s['provider'], 'provider')
    check('settings.defaults', s['defaults'], 'defaults')
    if s.get('presets'):
        check('settings.presets[omlx]', s['presets'][list(s['presets'])[0]], 'preset')

    v = call('/voices')
    check('/voices', v, '/voices')
    if v['voices']:
        check('voices[0]', v['voices'][0], 'voice')

    check('/library', call('/library'), '/library')
    lib = call('/library')['demos']
    if lib:
        check('library.demos[0]', lib[0], 'demo')
    else:
        print('  [skip] library.demos[0] — nothing rendered yet')

    check('/providers/detect', call('/providers/detect'), '/providers/detect')
    found = call('/providers/detect')['found']
    if found:
        check('detect.found[0]', found[0], 'detected')

    check('POST /providers/models', call('/providers/models', {'preset': 'omlx'}), 'models')
    # A provider pointing at an unreachable host must surface a meaningful error,
    # not a silent ok=false. The Settings inline error renders this string — if it
    # ever becomes empty, the user gets a generic modal with nothing useful in it.
    bad = call('/providers/models', {'preset': 'custom',
                                     'base_url': 'http://127.0.0.1:9999/v1',
                                     'api_key': '0000', 'model': 'x'})
    if bad.get('error', '').strip():
        print(f'  [OK ] /providers/models unreachable host returns a non-empty error '
              f'({len(bad["error"])} chars)')
    else:
        problems.append('/providers/models unreachable host: inline message would be empty')

    check('POST /providers/test', call('/providers/test', {'preset': 'omlx'}), 'test')

    jid = call('/analyze', {'repo_path': str(REPO), 'scenes': 4})['job_id']
    j = None
    for _ in range(120):
        j = call(f'/jobs/{jid}')
        if j['state'] in ('done', 'error'):
            break
        time.sleep(2)
    if j and j['state'] == 'done':
        r = j['result']
        check('analyze result', r, 'analyze')
        if r['scenes']:
            check('analyze scene[0]', r['scenes'][0], 'analyze_scene')
    else:
        print(f'  [FAIL] analyze did not finish: {(j or {}).get("error", "")[:200]}')
        problems.append('analyze did not finish')

    # --- timeout budget ----------------------------------------------------
    # Each Settings button gives its call a client-side deadline in Swift. Python's
    # own budgets must stay under it: if the server is still thinking when the client
    # gives up, the reply is "the engine isn't answering on 127.0.0.1:<port>" — which
    # sends you to restart the engine for a problem that is upstream of it. Raise a
    # python timeout past its swift budget and this fails instead of the user.
    swift_budget = {'/providers/models': 60, '/providers/test': 240,
                    '/providers/detect': 180}

    def default_of(fn, name):
        return inspect.signature(fn).parameters[name].default

    models_to = default_of(providers.list_models, 'timeout')
    detect_to = default_of(providers.probe_local, 'budget')
    src = inspect.getsource(providers.test)
    m = re.search(r'list_models\(p,\s*timeout=(\d+)\)', src)
    c = re.search(r'timeout=min\(p\.timeout,\s*(\d+)\)', src)
    if not (m and c):
        problems.append('could not read the timeout literals out of providers.test()')
        test_to = 0
    else:
        test_to = int(m.group(1)) + int(c.group(1))

    for endpoint, py_worst in (('/providers/models', models_to),
                               ('/providers/test', test_to),
                               ('/providers/detect', detect_to)):
        sw = swift_budget[endpoint]
        if py_worst >= sw:
            problems.append(
                f'{endpoint}: python budget {py_worst}s is not under the swift client '
                f'budget {sw}s — the client gives up first and reports the engine as '
                f'unresponsive instead of surfacing the real reason')
        else:
            print(f'  [OK ] {endpoint} python {py_worst}s < swift {sw}s')

    print()
    if problems:
        print(f'{len(problems)} contract problem(s):')
        for p in problems:
            print('  -', p)
        sys.exit(1)
    print('All endpoint payloads match the Swift wire types.')
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except subprocess.TimeoutExpired:
        srv.kill()
