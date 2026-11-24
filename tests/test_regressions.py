import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from app.downloader import GleanDownloader
from app.platforms import classify_url
from test_playlists import method_from_source

ROOT = Path(__file__).resolve().parents[1]

class FinalRegressionTests(unittest.TestCase):
    def test_invalid_authority(self):
        for authority in ('www.youtube.com:bad', 'www.youtube.com:99999', 'www.youtube.com:8080', 'user@www.youtube.com', 'user:pass@www.youtube.com', '[invalid'):
            with self.subTest(authority=authority):
                self.assertIsNone(classify_url(f'https://{authority}/watch?v=abc'))
        self.assertEqual(classify_url('https://www.youtube.com:443/watch?v=abc'), 'yt')

    def test_empty_audio_playlist_is_not_success(self):
        self.assertEqual(GleanDownloader._count_audio_entries({'entries':[None, {'entries':[]}]}), 0)
        self.assertEqual(GleanDownloader._count_audio_entries({'entries':[{'acodec':'none'}, {'acodec':'opus'}, {'requested_downloads':[{'acodec':'aac'}]}]}), 2)

    def test_empty_extract_is_error_in_both_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = GleanDownloader(tmp, 'ffmpeg.exe')
            runtime = MagicMock()
            runtime.YoutubeDL.return_value.__enter__.return_value.extract_info.return_value = {'entries':[None]}
            with patch.object(engine, '_ensure_runtime', return_value=runtime):
                for mode in ('video','audio'):
                    with self.subTest(mode=mode), self.assertRaises(RuntimeError):
                        engine._run_ytdlp('https://youtu.be/abc', mode, 'Alta', False)

    def test_nonfinal_empty_extract_allows_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = GleanDownloader(tmp, 'ffmpeg.exe')
            runtime = MagicMock()
            runtime.YoutubeDL.return_value.__enter__.return_value.extract_info.return_value = None
            with patch.object(engine, '_ensure_runtime', return_value=runtime):
                self.assertEqual(engine._run_ytdlp('https://x.com/u/status/1', 'video', 'Alta', False, emit_final=False), 0)

    def test_queue_takes_precedence_over_new_manual_plan(self):
        run = method_from_source('app/ui/main_window.py','MainWindow','download_or_cancel')
        flow = SimpleNamespace(_is_downloading=False, _manual_download_plan=None, _clipboard_download_queue=[{}], _start_next_queued_download=MagicMock())
        run(flow)
        flow._start_next_queued_download.assert_called_once()

    def test_start_does_not_hide_unrelated_popup(self):
        import ast
        tree = ast.parse((ROOT/'app/ui/main_window.py').read_text())
        method = next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_start_download')
        self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='hide' for n in ast.walk(method)))

    def test_locale_key_parity(self):
        locales = [json.loads(p.read_text(encoding='utf-8')) for p in (ROOT/'locales').glob('*.json')]
        for locale in locales[1:]: self.assertEqual(set(locale),set(locales[0]))
