"""Shared gateway voice-mode and TTS behavior for generic transports."""
import json
import os
import queue
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform
from gateway.platform_registry import PlatformEntry, platform_registry
from gateway.platforms.base import BasePlatformAdapter, SessionSource
from gateway.platforms.event import MessageEvent, MessageType
from gateway.run import GatewayRunner


@pytest.fixture
def runner(tmp_path):
    platform_registry.register(PlatformEntry(name='voiceprobe', label='Voice probe',
        adapter_factory=lambda cfg: None, check_fn=lambda: True))
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._voice_mode = {}
    runner._VOICE_MODE_PATH = tmp_path / 'gateway_voice_mode.json'
    runner._session_db = None
    runner.session_store = MagicMock()
    runner._is_user_authorized = lambda source: True
    yield runner
    platform_registry.unregister('voiceprobe')


def event(text='', message_type=MessageType.TEXT):
    source = SessionSource(platform=Platform('voiceprobe'), chat_id='123', user_id='user1', chat_type='dm')
    return MessageEvent(text=text, message_type=message_type, source=source, message_id='msg42')


@pytest.mark.asyncio
async def test_voice_mode_toggle_and_persistence(runner):
    await runner._handle_voice_command(event('/voice on'))
    assert json.loads(runner._VOICE_MODE_PATH.read_text())['voiceprobe:123'] == 'voice_only'
    await runner._handle_voice_command(event('/voice'))
    assert runner._voice_mode['voiceprobe:123'] == 'off'


@pytest.mark.asyncio
async def test_voice_mode_isolated_by_platform(runner):
    first = event('/voice on')
    second = event('/voice off')
    second.source.platform = Platform.WEBHOOK
    await runner._handle_voice_command(first)
    await runner._handle_voice_command(second)
    assert runner._voice_mode['voiceprobe:123'] == 'voice_only'
    assert runner._voice_mode['webhook:123'] == 'off'


def test_legacy_unprefixed_voice_mode_warns_and_is_skipped(runner, caplog):
    runner._VOICE_MODE_PATH.write_text(json.dumps({'123': 'all', 'voiceprobe:123': 'voice_only'}))
    assert runner._load_voice_modes() == {'voiceprobe:123': 'voice_only'}
    assert 'legacy unprefixed voice mode key' in caplog.text


def test_sync_voice_modes_and_default(runner, monkeypatch):
    monkeypatch.setattr('hermes_cli.config.load_config', lambda: {'voice': {'auto_tts': True}})
    runner._voice_mode = {'voiceprobe:off': 'off', 'voiceprobe:on': 'voice_only',
                          'voiceprobe:all': 'all', 'webhook:other': 'voice_only'}
    adapter = SimpleNamespace(platform=Platform('voiceprobe'), _auto_tts_default=False,
                              _auto_tts_disabled_chats=set(), _auto_tts_enabled_chats=set())
    runner._sync_voice_mode_state_to_adapter(adapter)
    assert adapter._auto_tts_default is True
    assert adapter._auto_tts_disabled_chats == {'off'}
    assert adapter._auto_tts_enabled_chats == {'on', 'all'}


def test_auto_voice_reply_dedup_and_error(runner):
    voice = event(message_type=MessageType.VOICE)
    text = event(message_type=MessageType.TEXT)
    runner._voice_mode['voiceprobe:123'] = 'voice_only'
    assert runner._should_send_voice_reply(voice, 'hello', []) is False
    assert runner._should_send_voice_reply(text, 'hello', []) is False
    runner._voice_mode['voiceprobe:123'] = 'all'
    assert runner._should_send_voice_reply(voice, 'hello', []) is False
    assert runner._should_send_voice_reply(voice, 'hello', [], already_sent=True) is True
    assert runner._should_send_voice_reply(text, 'hello', []) is True
    assert runner._should_send_voice_reply(text, 'Error: boom', []) is False
    calls = [{'role': 'assistant', 'tool_calls': [{'function': {'name': 'text_to_speech'}}]}]
    assert runner._should_send_voice_reply(voice, 'hello', calls, already_sent=True) is False
    runner._voice_mode['voiceprobe:123'] = 'off'
    assert runner._should_send_voice_reply(text, 'hello', []) is False


@pytest.mark.asyncio
async def test_voice_reply_uses_thread_metadata_and_notification(runner, tmp_path):
    adapter = AsyncMock()
    runner.adapters[Platform('voiceprobe')] = adapter
    msg = event()
    msg.source.thread_id = 'thread'
    msg.message_id = 'anchor'
    audio = tmp_path / 'speech.mp3'
    audio.write_bytes(b'audio')
    with patch('tools.tts_tool.text_to_speech_tool', return_value=json.dumps({'success': True, 'file_path': str(audio)})) as tts, \
         patch('tools.tts_text_normalize._strip_markdown_for_tts', side_effect=lambda value: value):
        await runner._send_voice_reply(msg, 'hello')
    tts.assert_called_once()
    adapter.send_voice.assert_awaited_once()
    sent = adapter.send_voice.call_args.kwargs
    assert sent['chat_id'] == '123' and sent['reply_to'] == 'anchor'
    assert sent['metadata']['thread_id'] == 'thread' and sent['metadata']['notify'] is True
    assert not audio.exists()


def test_auto_tts_chat_override():
    adapter = SimpleNamespace(_auto_tts_default=False, _auto_tts_enabled_chats=set(), _auto_tts_disabled_chats=set())
    assert BasePlatformAdapter._should_auto_tts_for_chat(adapter, 'chat') is False
    adapter._auto_tts_enabled_chats.add('chat')
    assert BasePlatformAdapter._should_auto_tts_for_chat(adapter, 'chat') is True


def test_stream_tts_stop_and_error_set_done(monkeypatch):
    from tools.tts_tool_speaker import stream_tts_to_speaker
    monkeypatch.setattr('tools.tts_streaming.resolve_streaming_provider', lambda *a, **k: None)
    texts, stop, done = queue.Queue(), threading.Event(), threading.Event()
    stop.set()
    texts.put('Must not speak.')
    texts.put(None)
    spoken = []
    stream_tts_to_speaker(texts, stop, done, display_callback=spoken.append)
    assert done.is_set() and not spoken
    texts, stop, done = queue.Queue(), threading.Event(), threading.Event()
    texts.put(12345)
    texts.put(None)
    stream_tts_to_speaker(texts, stop, done)
    assert done.is_set()


def test_stream_tts_tempfile_closed_before_playback(monkeypatch):
    import wave
    import tools.tts_tool as tts_mod
    import tools.voice_mode as vm
    from tools.tts_tool_speaker import stream_tts_to_speaker

    class Streamer:
        sample_rate = 24000
        channels = 1

        def stream(self, text):
            yield b'\x00\x00' * 240

    monkeypatch.setattr('tools.tts_streaming.resolve_streaming_provider', lambda *a, **k: Streamer())
    def no_sounddevice():
        raise ImportError('disabled')
    monkeypatch.setattr(tts_mod, '_import_sounddevice', no_sounddevice)
    events = []
    real_ntf = tts_mod.tempfile.NamedTemporaryFile
    def spy(*args, **kwargs):
        handle = real_ntf(*args, **kwargs)
        close = handle.close
        def tracked_close():
            events.append(('close', handle.name))
            return close()
        handle.close = tracked_close
        return handle
    monkeypatch.setattr(tts_mod.tempfile, 'NamedTemporaryFile', spy)
    def play(path):
        events.append(('play', path))
        with wave.open(path, 'rb') as wav:
            assert wav.getnframes() > 0
    monkeypatch.setattr(vm, 'play_audio_file', play)
    texts, stop, done = queue.Queue(), threading.Event(), threading.Event()
    texts.put('This is a spoken sentence for the fallback. ')
    texts.put(None)
    stream_tts_to_speaker(texts, stop, done)
    play_index = next(i for i, entry in enumerate(events) if entry[0] == 'play')
    assert done.is_set() and any(entry[0] == 'close' for entry in events[:play_index])
    assert not os.path.exists(events[play_index][1])