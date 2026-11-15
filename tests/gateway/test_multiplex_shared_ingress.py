"""Shared ingress publishes profile-scoped plugin routes without binding a listener."""
from types import SimpleNamespace

import pytest

pytest.importorskip('aiohttp')
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from gateway.config import Platform
from gateway.platforms.shared_ingress import bind_listener, dispatch_profile_ingress


@pytest.mark.asyncio
async def test_shared_ingress_routes_to_named_profile_without_binding(monkeypatch):
    from contextlib import contextmanager
    from agent.secret_scope import get_secret_str, reset_secret_scope, set_secret_scope

    @contextmanager
    def profile_scope(path):
        token = set_secret_scope({'PROBE_SECRET': 'secret-' + path.name})
        try:
            yield
        finally:
            reset_secret_scope(token)

    monkeypatch.setattr('gateway.run._profile_runtime_scope', profile_scope)
    monkeypatch.setattr('hermes_cli.profiles.get_profile_dir', lambda profile: __import__('pathlib').Path(profile))
    runner = SimpleNamespace(_profile_adapters={}, adapters={Platform.API_SERVER: SimpleNamespace(_host='0.0.0.0', _port=8642)})
    seen = []

    async def handler(request):
        if request.headers.get('X-Secret') != get_secret_str('PROBE_SECRET'):
            raise web.HTTPUnauthorized()
        seen.append(get_secret_str('PROBE_SECRET'))
        return web.Response(text='ok')

    for profile in ('coder', 'ops'):
        adapter = SimpleNamespace(_shared_listener_profile=profile, gateway_runner=runner,
                                  platform=SimpleNamespace(value='probe'))
        app = web.Application(client_max_size=1024)
        app.router.add_post('/probe/webhook', handler)
        assert await bind_listener(adapter, app, '127.0.0.1', 1, '/probe/webhook') is None
        assert adapter._shared_ingress_url == f'http://127.0.0.1:8642/p/{profile}/probe/webhook'
        runner._profile_adapters[profile] = {'probe': adapter}

    request = make_mocked_request('POST', '/p/coder/probe/webhook', headers={'X-Secret': 'secret-coder'})
    response = await dispatch_profile_ingress(runner, 'coder', 'probe/webhook', request)
    assert response.status == 200 and seen == ['secret-coder']
    wrong = make_mocked_request('POST', '/p/coder/probe/webhook', headers={'X-Secret': 'secret-ops'})
    with pytest.raises(web.HTTPUnauthorized):
        await dispatch_profile_ingress(runner, 'coder', 'probe/webhook', wrong)
    assert seen == ['secret-coder']
    with pytest.raises(web.HTTPNotFound):
        await dispatch_profile_ingress(runner, 'missing', 'probe/webhook', request)
    runner._profile_adapters['ops'] = {}
    with pytest.raises(web.HTTPNotFound):
        await dispatch_profile_ingress(runner, 'ops', 'probe/webhook', request)