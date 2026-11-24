"""History acceptance and persistence tests without a Qt display."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from app.history import HistoryStore
from app.platforms import classify_url, is_youtube_playlist
from test_playlists import method_from_source, SAMPLE, SINGLE

A = 'https://youtu.be/first'
B = 'https://www.instagram.com/p/abc/?img_index=1'
BAD = 'https://example.com/video'

def method(name, **deps):
    return method_from_source('app/ui/main_window.py', 'MainWindow', name, **deps)

class Flow:
    _record_download_attempts = method('_record_download_attempts', classify_url=classify_url)
    _record_download_url = method('_record_download_url')
    _download_clipboard_url = method('_download_clipboard_url')
    download_or_cancel = method('download_or_cancel', classify_url=classify_url, is_youtube_playlist=is_youtube_playlist, tr=lambda x: x)
    _prompt_next_media_job = method("_prompt_next_media_job")
    _resolve_media_job = method("_resolve_media_job", youtube_single_video_url=lambda url: None, tr=lambda x:x)
    def _show_clipboard_download_prompt(self, url, **kwargs):
        self._resolve_media_job(url, "video")
    def _start_next_queued_download(self):
        self._start_download([j["url"] for j in self._clipboard_download_queue])
    def __init__(self, path):
        self.history_store = HistoryStore(path)
        self._clipboard_title_cache = {A: 'Cached title'}
        self._manual_download_plan = None
        self._active_download_urls = set()
        self._clipboard_download_queue = []
        self._is_downloading = False
        self._pending_urls = []
        self.url_entry = SimpleNamespace(text=lambda: A, clear=lambda: None)
        self.video_check = SimpleNamespace(isChecked=lambda: True)
        self.audio_check = SimpleNamespace(isChecked=lambda: False)
        self.config = SimpleNamespace(video_quality='High', download_thumbnails=False)
        self.started = []
    def _flush_history(self): self.history_store.save()
    def _schedule_history_save(self): self._flush_history()
    def _clipboard_job(self, url, media_type): return {'url': url} if classify_url(url) else None
    def _remember_popup_choice(self, url, media_type): pass
    def _queued_urls(self): return {j['url'] for j in self._clipboard_download_queue}
    def _update_queue_label(self): pass
    def _start_clipboard_job(self, job): self.started.append(job['url'])
    def _start_download(self, urls, *args):
        # Verify persistence before the worker can run or fail.
        assert set(urls) <= {e['url'] for e in HistoryStore(self.history_store.path).entries()}
        self.started.extend(urls)
        raise RuntimeError('simulated startup/download failure')
    def _set_status(self, *args): pass
    def _request_cancel(self): self._clipboard_download_queue.clear()

class HistoryAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'history.json'
        self.flow = Flow(self.path)
    def entries(self): return HistoryStore(self.path).entries()
    def test_manual_failure_keeps_url(self):
        with self.assertRaises(RuntimeError): self.flow.download_or_cancel()
        self.assertEqual(self.entries()[0]['url'], A)
    def test_entire_batch_persisted_before_first_failure(self):
        self.flow._pending_urls = [A, B]
        with self.assertRaises(RuntimeError): self.flow.download_or_cancel()
        self.assertEqual({e['url'] for e in self.entries()}, {A, B})
    def test_queue_persists_even_if_cancelled_before_start(self):
        self.flow._is_downloading = True
        self.flow._download_clipboard_url(B, 'video')
        self.flow.download_or_cancel()
        self.assertEqual(self.flow.started, [])
        self.assertEqual(self.entries()[0]['url'], B)
    def test_clipboard_immediate(self):
        self.flow._download_clipboard_url(A, 'video')
        self.assertEqual(self.entries()[0]['title'], 'Cached title')
    def test_invalid_url_not_recorded(self):
        self.flow._pending_urls = [BAD]
        self.flow.download_or_cancel()
        self.flow._download_clipboard_url(BAD, 'video')
        self.flow._record_download_attempts([BAD])
        self.assertEqual(self.entries(), [])
    def test_title_update_preserves_order_and_time(self):
        self.flow._record_download_attempts([A, B])
        before = self.entries()
        self.flow._record_download_url(A, 'New title')
        after = self.entries()
        self.assertEqual([e['url'] for e in before], [e['url'] for e in after])
        self.assertEqual(before[1]['timestamp'], after[1]['timestamp'])
        self.assertEqual(after[1]['title'], 'New title')
    def test_cleared_history_not_resurrected_by_completion(self):
        self.flow._record_download_attempts([A])
        self.flow.history_store.clear()
        self.flow._record_download_url(A, 'Late title')
        self.assertEqual(self.entries(), [])
    def test_repeat_keeps_known_title_and_unique_url(self):
        self.flow.history_store.add(A, 'Known title')
        self.flow._clipboard_title_cache.clear()
        self.flow._record_download_attempts([A])
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(self.entries()[0]['title'], 'Known title')
    def test_selected_playlist_scope(self):
        self.flow._download_clipboard_url(SINGLE, 'video')
        self.assertEqual([e['url'] for e in self.entries()], [SINGLE])
        self.flow._download_clipboard_url(SAMPLE, 'video')
        self.assertEqual([e['url'] for e in self.entries()], [SAMPLE, SINGLE])
