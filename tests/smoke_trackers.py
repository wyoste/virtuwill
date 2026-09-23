"""Optional browser check with user-supplied HTML; inputs never enter Git.

pip install playwright
python tests/smoke_trackers.py --finance /private/Yoste-Finance.html \
    --health /private/Yoste-Health.html --chromium /path/to/chromium
"""
import argparse
import json
import logging
from pathlib import Path
import secrets
import sys
import tempfile
import threading

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app
import config
from trackers import TrackerStore

parser = argparse.ArgumentParser()
parser.add_argument('--finance', required=True, type=Path)
parser.add_argument('--health', required=True, type=Path)
parser.add_argument('--chromium')
args = parser.parse_args()
logging.getLogger('werkzeug').setLevel(logging.ERROR)

with tempfile.TemporaryDirectory() as folder, sync_playwright() as p:
    app.config.update(SECRET_KEY=secrets.token_hex(32), TRACKER_AUTH_CONFIGURED=True, TRACKER_DATA_DIR=folder)
    store = TrackerStore(folder)
    store.install('finance', args.finance.read_text())
    store.install('health', args.health.read_text())
    server = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    browser = p.chromium.launch(executable_path=args.chromium, args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    context.request.post(base + '/api/admin/login', data={'username': config.ADMIN_USER, 'password': config.ADMIN_PASSWORD})
    context.request.post(base + '/api/journal/unlock', data={'password': config.JOURNAL_PASSWORD})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(base, wait_until='domcontentloaded')
    page.evaluate("go('admin'); VW.Admin.showSection('finance')")
    page.wait_for_function("document.querySelector('#adm-tracker-finance .tracker-status').textContent.includes('Saved to server')")
    finance = page.frame_locator('#adm-tracker-finance iframe')
    for name in ['Spending', 'Groceries', 'Income & schedules', 'Goals', 'Retirement', 'Data & settings', 'Summary']:
        finance.locator('nav button').filter(has_text=name).click()
    with page.expect_download() as download:
        finance.locator('#savehtml').click()
    exported = Path(folder) / 'export.html'
    download.value.save_as(exported)
    assert 'id="vw-tracker-bridge"' not in exported.read_text()
    assert 'await trackerConfirm(' not in exported.read_text()
    standalone = context.new_page()
    standalone.goto(exported.as_uri(), wait_until='domcontentloaded')
    assert standalone.locator('h1').inner_text() == 'Summary'
    standalone.close()
    page.evaluate("VW.Admin.showSection('health')")
    page.wait_for_function("document.querySelector('#adm-tracker-health .tracker-status').textContent.includes('Saved to server')")
    health = page.frame_locator('#adm-tracker-health iframe')
    for name in ['Meals', 'Dinner planner', 'Groceries', 'Settings', 'Movement']:
        health.locator('nav button').filter(has_text=name).click()
    health.locator('#worknote').fill('Browser persistence check')
    with page.expect_response(lambda r: r.url.endswith('/api/admin/trackers/health') and r.request.method == 'PUT') as saved:
        health.get_by_role('button', name='Save session', exact=True).click()
    assert saved.value.status == 200
    page.wait_for_function("!VW.Trackers.isDirty()")
    assert json.loads(store.get('health')['state'])['workouts'][-1]['note'] == 'Browser persistence check'
    health.locator('nav button').filter(has_text='Overview').click()
    health.locator('#wv').fill('185.2')
    with page.expect_response(lambda r: r.url.endswith('/api/admin/trackers/health') and r.request.method == 'PUT') as saved:
        health.get_by_role('button', name='Save weight', exact=True).click()
    assert saved.value.status == 200
    page.wait_for_function("!VW.Trackers.isDirty()")
    assert json.loads(store.get('health')['state'])['weights'][-1]['value'] == 185.2
    # Restore a backup through the actual sandbox-compatible confirmation dialog.
    health.locator('nav button').filter(has_text='Settings').click()
    backup = Path(folder) / 'health.json'
    backup.write_text(store.get('health')['state'])
    health.locator('input[type=file]').set_input_files(backup)
    health.get_by_role('button', name='Restore backup', exact=True).click()
    page.wait_for_function("!VW.Trackers.isDirty()")
    # A second device sees the saved state without generating a new revision.
    revision = store.get('health')['revision']
    second = context.new_page()
    second.goto(base, wait_until='domcontentloaded')
    second.evaluate("go('admin'); VW.Admin.showSection('health')")
    second.wait_for_function("document.querySelector('#adm-tracker-health .tracker-status').textContent.includes('Connected')")
    assert store.get('health')['revision'] == revision
    h2 = second.frame_locator('#adm-tracker-health iframe')
    h2.locator('nav button').filter(has_text='Movement').click()
    assert h2.get_by_text('Browser persistence check', exact=True).count() == 1
    # First tab advances the revision, then the stale second tab must conflict.
    health.locator('nav button').filter(has_text='Movement').click()
    with page.expect_response(lambda r: r.url.endswith('/api/admin/trackers/health') and r.request.method == 'PUT') as saved:
        health.get_by_role('button', name='Save session', exact=True).click()
    assert saved.value.status == 200
    page.wait_for_function("!VW.Trackers.isDirty()")
    h2.get_by_role('button', name='Save session', exact=True).click()
    second.wait_for_function("document.querySelector('#adm-tracker-health .tracker-status').textContent.includes('Another tab')")
    second.on('dialog', lambda dialog: dialog.accept())
    second.close(run_before_unload=False)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.evaluate("VW.Admin.showSection('health')")
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(Path(folder) / 'mobile.png'))
    page.evaluate("VW.Auth.logout()")
    page.wait_for_function("document.querySelectorAll('.tracker-frame').length === 0")
    assert context.request.get(base + '/admin/trackers/health/frame').status == 401
    assert not errors, errors
    browser.close()
    server.shutdown()
    print('PASS: tracker tabs, edits, persistence, backup restore, portable HTML export, cross-tab conflict, mobile width, logout; no page errors.')
