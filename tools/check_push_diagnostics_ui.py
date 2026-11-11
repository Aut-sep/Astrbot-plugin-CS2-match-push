"""Exercise the shared panel in isolated headless Chrome with local fixtures.

Uses aiohttp and Chrome's DevTools protocol; no remote APIs, live AstrBot, or QQ.
"""
import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from aiohttp import ClientSession, web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from test_push_diagnostics import main, match, NOW, diagnostics_module


async def check(output):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cs-diagnostics-ui-') as temporary:
        temp = Path(temporary)
        store = main.DataStore(str(temp / 'state.json'))
        store.set('push_groups', ['100001'])
        diagnostics = diagnostics_module.PushDiagnostics(temp / 'diag.sqlite3')
        diagnostics.last_audit = time.time()
        diagnostics.record('not_started_time_elapsed', match=match(scheduled_at='2026-11-11T04:15:00Z'),
                           evidence={'old_snapshot': {'sched': '2026-11-11T04:30:00Z'},
                                     'error': '<img src=x onerror="window.__unsafe=true">'})
        diagnostics._issue(1762451, '100001', 'missing_upcoming', {'reminder_active': False})
        diagnostics.record('send_failed', key='upcoming:1762451', group_id='100001', level='ERROR')
        recent = diagnostics.query(match_id=1762451, limit=1)['events'][0]
        assert recent['reason'] == 'send_failed'
        assert recent['match_teams'] == ['Vitality', 'Team Falcons']
        for index in range(25):
            diagnostics.record('schedule_received', match=match(), evidence={'test_index': index})
        diagnostics.record('reminder_scheduled', match=match(id=1762452))
        diagnostics.record('schedule_filtered', match=match(id=1762453))
        diagnostics.record('send_success', key='daily:100001:2026-11-11_12:00', group_id='100001')
        grouped = diagnostics.query(match_id=1762451, limit=1, view='detail')['event_groups']
        assert grouped['delivery'][0]['reason'] == 'send_failed'
        assert grouped['schedule'][0]['reason'] == 'not_started_time_elapsed'
        calls = []

        async def handler(request):
            calls.append((request.method, request.path, dict(request.query)))
            assert request.method == 'GET', 'Unexpected mutation from diagnostic UI'
            if request.path == '/api/config':
                return web.json_response({'ok': True, 'data': store.export_all(), 'token_configured': True,
                                          'team_logos': {}, 'official_platforms': []})
            if request.path == '/api/matches':
                return web.json_response({'ok': True, 'matches': [], 'schedule_status': 'ready'})
            if request.path == '/api/diagnostics':
                mid = request.query.get('match_id')
                return web.json_response({'ok': True, **diagnostics.query(match_id=int(mid) if mid else None,
                                                                         limit=int(request.query.get('limit', 100)),
                                                                         view=request.query.get('view'))})
            if request.path == '/api/logs':
                return web.json_response({'ok': True, 'logs': []})
            path = (ROOT / 'pages/management' / (request.path.lstrip('/') or 'index.html')).resolve()
            if path.parent != ROOT / 'pages/management' or not path.is_file():
                raise web.HTTPNotFound()
            return web.FileResponse(path)

        app = web.Application()
        app.router.add_route('*', '/{path:.*}', handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        chrome_override = os.environ.get('CS_DIAGNOSTICS_CHROME')
        if chrome_override:
            chrome = Path(chrome_override)
        else:
            cached = sorted((Path.home() / '.cache/ms-playwright').glob('chromium-*/chrome-linux64/chrome'))
            if not cached:
                raise RuntimeError('Set CS_DIAGNOSTICS_CHROME to a Chrome or Chromium executable')
            chrome = cached[-1]
        process = subprocess.Popen([str(chrome), '--headless', '--no-sandbox', '--disable-gpu',
                                    '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
                                    '--no-first-run', '--disable-background-networking',
                                    '--user-data-dir=' + str(temp / 'profile'), 'about:blank'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        errors, checks = [], []
        try:
            active_port = temp / 'profile/DevToolsActivePort'
            for _ in range(200):
                if active_port.exists():
                    break
                await asyncio.sleep(0.05)
            debug_port = active_port.read_text().splitlines()[0]
            async with ClientSession() as session:
                targets = []
                for _ in range(100):
                    async with session.get(f'http://127.0.0.1:{debug_port}/json/list') as response:
                        targets = [t for t in await response.json() if t.get('type') == 'page']
                    if targets:
                        break
                    await asyncio.sleep(0.05)
                assert targets, 'Chrome did not create a page target'
                async with session.ws_connect(targets[0]['webSocketDebuggerUrl']) as ws:
                    sequence = 0

                    async def call(method, params=None):
                        nonlocal sequence
                        sequence += 1
                        number = sequence
                        await ws.send_json({'id': number, 'method': method, 'params': params or {}})
                        while True:
                            item = await ws.receive_json(timeout=15)
                            if item.get('method') == 'Runtime.exceptionThrown':
                                errors.append(item['params'])
                            if item.get('id') == number:
                                assert 'error' not in item, item
                                return item.get('result', {})

                    async def evaluate(expression):
                        result = await call('Runtime.evaluate', {'expression': expression, 'returnByValue': True,
                                                                 'awaitPromise': True})
                        assert 'exceptionDetails' not in result, result
                        return result['result'].get('value')

                    async def until(expression):
                        for _ in range(100):
                            if await evaluate(expression):
                                return
                            await asyncio.sleep(0.05)
                        raise AssertionError(expression)

                    await call('Runtime.enable')
                    await call('Page.enable')
                    await call('Browser.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': str(temp / 'downloads')})
                    await call('Emulation.setDeviceMetricsOverride', {'width': 1280, 'height': 1000, 'deviceScaleFactor': 1, 'mobile': False})
                    await call('Page.navigate', {'url': f'http://127.0.0.1:{port}/'})
                    await until("document.querySelector('#app')?.getAttribute('aria-busy') === 'false'")
                    assert not await evaluate("!document.querySelector('#page-error').hidden")
                    await evaluate("document.querySelector('#tab-diagnostics').click()")
                    await until("document.querySelectorAll('#diagnostic-matches > details').length === 2")
                    assert await evaluate("document.querySelector('#diagnostic-matches > details').dataset.diagnosticMatchId === '1762451'")
                    assert await evaluate("document.querySelectorAll('#diagnostic-matches > details[open]').length === 0")
                    assert await evaluate("""
                        [...document.querySelectorAll('[data-diagnostic-match-id="1762451"] .diagnostic-team')]
                            .map((team) => team.textContent).join(' 对 ') === 'Vitality 对 Team Falcons'
                    """)
                    assert await evaluate("""
                        getComputedStyle(document.querySelector('.diagnostic-team')).color !==
                        getComputedStyle(document.querySelector('.diagnostic-versus')).color
                    """)
                    await evaluate("document.querySelector('#diagnostic-matches > details > summary').click()")
                    await until("document.querySelector('[data-diagnostic-key=issues] strong')?.textContent === '赛前提醒可能漏发'")
                    assert await evaluate("""
                        !document.querySelector('[data-diagnostic-key=updates]').open &&
                        document.querySelector('[data-diagnostic-key=delivery] strong').textContent === '发送时出现错误' &&
                        document.querySelector('[data-diagnostic-key=schedule] strong').textContent.includes('开赛时间已过去') &&
                        !document.querySelector('[data-diagnostic-key=delivery] article details').open
                    """)
                    assert not await evaluate('Boolean(window.__unsafe)')
                    await evaluate("document.querySelector('#diagnostic-general > summary').click()")
                    await until("document.querySelector('#diagnostic-general [data-diagnostic-key=delivery] h5')?.textContent === '赛程日报'")
                    checks.append('one card per match, issues first, highlighted teams, categorized detail, separate daily notices')
                    await evaluate("document.querySelector('#diagnostic-match').value='1762451';document.querySelector('#refresh-diagnostics').click()")
                    await until("!document.querySelector('#refresh-diagnostics').disabled")
                    assert any(q.get('match_id') == '1762451' for _, p, q in calls if p == '/api/diagnostics')
                    await evaluate("document.querySelector('#export-diagnostics').click()")
                    for _ in range(100):
                        if list((temp / 'downloads').glob('*.json')):
                            break
                        await asyncio.sleep(0.05)
                    exported = json.loads(next((temp / 'downloads').glob('*.json')).read_text())
                    assert all(e['match_id'] == 1762451 for e in exported['events'])
                    checks.append('match filter and downloadable diagnostic JSON')
                    await evaluate("document.querySelector('#diagnostic-match').value='bad';document.querySelector('#refresh-diagnostics').click()")
                    await until("document.querySelector('#toasts').textContent.includes('有效的比赛 ID')")
                    checks.append('invalid ID validation')
                    await evaluate("document.querySelector('#diagnostic-match').value='1762451';document.querySelector('#toasts').innerHTML='';document.querySelector('#diagnostics').scrollIntoView()")
                    shot = await call('Page.captureScreenshot', {'format': 'png'})
                    (output / 'desktop.png').write_bytes(base64.b64decode(shot['data']))
                    await call('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True})
                    await evaluate("document.documentElement.dataset.theme='dark';document.querySelector('#diagnostics').scrollIntoView()")
                    assert await evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    shot = await call('Page.captureScreenshot', {'format': 'png'})
                    (output / 'mobile-dark.png').write_bytes(base64.b64decode(shot['data']))
                    checks.append('390px mobile, dark theme and no horizontal overflow')
                    await call('Page.addScriptToEvaluateOnNewDocument', {'source': '''
                        window.AstrBotPluginView = {
                            ready: async () => ({isDark: false}),
                            onContext: () => () => {}, getContext: () => ({isDark: false}),
                            apiGet: async (_, query) => {
                                const url = new URL(query.path, location.origin);
                                Object.entries(query).forEach(([k,v]) => { if(k !== 'path') url.searchParams.set(k,v); });
                                return (await fetch(url)).json();
                            },
                            apiPost: async () => { throw new Error('Writes are disabled in offline preview'); },
                            download: async (endpoint, query, filename) => { window.__diagnosticDownload = {endpoint, query, filename}; }
                        };
                    '''})
                    await call('Emulation.setDeviceMetricsOverride', {'width': 1280, 'height': 1000, 'deviceScaleFactor': 1, 'mobile': False})
                    await call('Page.navigate', {'url': f'http://127.0.0.1:{port}/'})
                    await until("document.querySelector('#app')?.getAttribute('aria-busy') === 'false'")
                    await evaluate("document.querySelector('#tab-diagnostics').click()")
                    await until("document.querySelectorAll('#diagnostic-matches > details').length === 2")
                    await evaluate("document.querySelector('#diagnostic-match').value='1762451';document.querySelector('#export-diagnostics').click()")
                    await until('Boolean(window.__diagnosticDownload)')
                    exported = await evaluate('window.__diagnosticDownload')
                    assert exported['endpoint'] == 'diagnostics' and exported['query']['match_id'] == '1762451'
                    await evaluate("document.documentElement.dataset.theme='light';document.querySelector('#diagnostics').scrollIntoView()")
                    shot = await call('Page.captureScreenshot', {'format': 'png'})
                    (output / 'embedded-light.png').write_bytes(base64.b64decode(shot['data']))
                    checks.append('embedded API bridge, export bridge and light theme')
                    assert not errors, errors
                    assert all(method == 'GET' for method, _, _ in calls)
                    checks.append('no browser exceptions or write requests')
        finally:
            process.terminate()
            process.wait(timeout=5)
            await runner.cleanup()
            diagnostics.close()
        report = {'checks': checks, 'screenshots': ['desktop.png', 'mobile-dark.png', 'embedded-light.png']}
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(check(Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/tmp/cs-diagnostics-ui')))
