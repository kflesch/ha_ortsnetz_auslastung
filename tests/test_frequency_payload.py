import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import aiohttp
from aiohttp import web
import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parents[1] / 'custom_components/ortsnetz_auslastung'
SOURCE = Path(os.environ.get('ORTSNETZ_TEST_SOURCE', ROOT / '__init__.py'))
spec = importlib.util.spec_from_file_location('ortsnetz_under_test', SOURCE, submodule_search_locations=[str(ROOT)])
integration = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = integration
spec.loader.exec_module(integration)


@pytest_asyncio.fixture
async def receiver(monkeypatch, socket_enabled):
    payloads = []

    async def receive(request):
        payloads.append(await request.json())
        return web.json_response({})

    app = web.Application()
    app.router.add_post('/v1/measurements', receive)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as session:
            monkeypatch.setattr(integration, 'async_get_clientsession', lambda hass: session)
            yield f'http://127.0.0.1:{port}', payloads
    finally:
        await runner.cleanup()


async def send(receiver, frequency):
    url, payloads = receiver
    states = {f'sensor.l{i}': SimpleNamespace(state=str(230 + i)) for i in (1, 2, 3)}
    states['sensor.forecast'] = SimpleNamespace(state='20.5')
    if frequency is not None:
        states['sensor.frequency'] = SimpleNamespace(state=frequency)
    hass = SimpleNamespace(states=SimpleNamespace(get=states.get))
    entry = SimpleNamespace(data={
        'api_url': url, 'latitude': 0, 'longitude': 0,
        'l1_entity': 'sensor.l1', 'l2_entity': 'sensor.l2', 'l3_entity': 'sensor.l3',
        'grid_frequency_entity': 'sensor.frequency', 'pv_forecast_entity': 'sensor.forecast',
    }, options={})
    await integration._send(hass, entry)
    assert len(payloads) == 1
    payload = payloads[0]
    assert [payload[key] for key in ('l1_v', 'l2_v', 'l3_v')] == [231, 232, 233]
    assert payload['pv_forecast_kwh'] == 20.5
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize('frequency', ['0', '-1', '44.99', '55.01', 'nan', 'inf', '-inf', 'unknown', 'unavailable', None])
async def test_invalid_frequency_does_not_block_voltage_submission(receiver, frequency):
    payload = await send(receiver, frequency)
    assert 'grid_frequency_hz' not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize('frequency', ['45', '49.89', '50.06', '55'])
async def test_valid_frequency_is_submitted(receiver, frequency):
    payload = await send(receiver, frequency)
    assert payload['grid_frequency_hz'] == float(frequency)
