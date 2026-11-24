"""Stage-1 regression tests, without requiring a graphical Qt display."""
from __future__ import annotations

import ast
import tempfile
import types
import unittest
from pathlib import Path

from app.downloader import GleanDownloader
from app.platforms import classify_url, is_youtube_playlist, youtube_single_video_url

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = "https://www.youtube.com/watch?v=TestVideo01&list=PL_TEST_FIXTURE&index=3"
SINGLE = "https://www.youtube.com/watch?v=TestVideo01"


def method_from_source(path: str, class_name: str, method_name: str, **dependencies):
    """Run the *real* UI method in isolation when PySide6 is not available."""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    clazz = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in clazz.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name)
    method.decorator_list = []
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = dict(dependencies)
    exec(compile(module, path, "exec"), namespace)
    return namespace[method_name]


class PlaylistChoiceTests(unittest.TestCase):
    def test_video_playlist_reduces_to_single_video(self):
        self.assertEqual(youtube_single_video_url(SAMPLE), SINGLE)
        self.assertTrue(is_youtube_playlist(SAMPLE))
        self.assertFalse(is_youtube_playlist(SINGLE))

    def test_query_order_and_tracking_are_ignored(self):
        self.assertEqual(
            youtube_single_video_url("https://m.youtube.com/watch?list=PL123&index=5&v=abc123&t=36s"),
            "https://m.youtube.com/watch?v=abc123",
        )
        self.assertEqual(
            youtube_single_video_url("https://youtu.be/abc123?list=PL123&index=5"),
            "https://youtu.be/abc123",
        )
        self.assertEqual(
            youtube_single_video_url("https://www.youtube.com/shorts/abc123?list=PL123&feature=share"),
            "https://www.youtube.com/shorts/abc123",
        )

    def test_dedicated_playlist_has_no_single_video(self):
        self.assertIsNone(youtube_single_video_url("https://www.youtube.com/playlist?list=PL123"))
        self.assertIsNone(youtube_single_video_url("https://www.youtube.com/watch?v=abc123"))
        self.assertIsNone(youtube_single_video_url("https://example.com/watch?v=abc123&list=PL123"))

    def test_downloader_receives_correct_playlist_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            downloader = GleanDownloader(tmp, "ffmpeg.exe")
            playlist_opts = downloader._build_options(SAMPLE, "video", "Alta", False)
            self.assertFalse(playlist_opts["noplaylist"])
            self.assertEqual(Path(playlist_opts["outtmpl"]).parent.parent, Path(tmp))
            self.assertIn("%(playlist_title,playlist|Playlist)", Path(playlist_opts["outtmpl"]).parent.name)
            self.assertIn("%(id)s", playlist_opts["outtmpl"])
            single_opts = downloader._build_options(SINGLE, "video", "Alta", False)
            self.assertTrue(single_opts["noplaylist"])
            self.assertNotIn("%(playlist_title)s", single_opts["outtmpl"])
            self.assertEqual(classify_url(SINGLE), "yt")


class PopupScopeTests(unittest.TestCase):
    def test_popup_accept_returns_selected_url_for_both_paths(self):
        accept = method_from_source("app/ui/clipboard_popup.py", "ClipboardDownloadPopup", "_accept")
        class Radio:
            def __init__(self, checked): self.checked = checked
            def isChecked(self): return self.checked
        class Signal:
            def __init__(self): self.calls = []
            def emit(self, *args): self.calls.append(args)
        class Popup:
            def __init__(self, choose_single):
                self._url = SAMPLE
                self._single_video_url = SINGLE
                self._allow_audio = True
                self.single_video_radio = Radio(choose_single)
                self.audio_radio = Radio(True)
                self.download_requested = Signal()
                self.hide_count = 0
            def hide(self): self.hide_count += 1
        popup = Popup(True)
        accept(popup)
        self.assertEqual(popup.download_requested.calls, [(SINGLE, "audio")])
        self.assertEqual(popup.hide_count, 1)
        popup = Popup(False)
        accept(popup)
        self.assertEqual(popup.download_requested.calls, [(SAMPLE, "audio")])
        popup._single_video_url = None
        accept(popup)
        self.assertEqual(popup.download_requested.calls[-1], (SAMPLE, "audio"))

    def test_popup_cancel_does_not_emit_download(self):
        cancel = method_from_source("app/ui/clipboard_popup.py", "ClipboardDownloadPopup", "_cancel")
        class Signal:
            def __init__(self): self.calls = 0
            def emit(self): self.calls += 1
        class Popup:
            def __init__(self):
                self.cancelled = Signal()
                self.download_requested = Signal()
            def hide(self): pass
        popup = Popup()
        cancel(popup)
        self.assertEqual(popup.cancelled.calls, 1)
        self.assertEqual(popup.download_requested.calls, 0)


class ManualFlowTests(unittest.TestCase):
    def _flow(self, urls):
        prompt_next = method_from_source(
            "app/ui/main_window.py", "MainWindow", "_prompt_next_manual_playlist",
            is_youtube_playlist=is_youtube_playlist, classify_url=classify_url,
        )
        resolve = method_from_source(
            "app/ui/main_window.py", "MainWindow", "_resolve_manual_playlist_choice",
            youtube_single_video_url=youtube_single_video_url, classify_url=classify_url,
        )
        class Flow:
            _prompt_next_manual_playlist = prompt_next
            _resolve_manual_playlist_choice = resolve
            def __init__(self):
                self._manual_download_plan = {
                    "urls": urls, "resolved": [], "next_index": 0,
                    "media_type": "video", "quality": "Alta", "thumbnails": True,
                }
                self.config = types.SimpleNamespace(video_quality="Alta", audio_quality="Média")
                self._pending_urls = []
                self.url_entry = types.SimpleNamespace(clear=lambda: self.cleared.append(True))
                self.cleared = []
                self.prompts = []
                self.downloads = []
                self.recorded = []
            def _show_clipboard_download_prompt(self, url, **kwargs):
                self.prompts.append((url, kwargs))
            def _record_download_attempts(self, urls): self.recorded.extend(urls)
            def _start_download(self, *args):
                self.downloads.append(args)
        return Flow()

    def test_one_manual_url_waits_for_choice_and_only_then_downloads(self):
        flow = self._flow([SAMPLE])
        flow._prompt_next_manual_playlist()
        self.assertEqual(len(flow.prompts), 1)
        self.assertEqual(flow.downloads, [])
        flow._resolve_manual_playlist_choice(SINGLE, "video")
        self.assertEqual(flow.downloads, [([SINGLE], "video", "Alta", True)])
        self.assertEqual(flow.recorded, [SINGLE])
        self.assertIsNone(flow._manual_download_plan)

    def test_manual_batch_confirms_each_playlist_without_losing_other_urls(self):
        second = "https://www.youtube.com/playlist?list=PL123"
        last = "https://www.instagram.com/p/abc/"
        flow = self._flow(["https://youtu.be/first", SAMPLE, second, last])
        flow._pending_urls = ["loaded-list"]
        flow._prompt_next_manual_playlist()
        self.assertEqual([x[0] for x in flow.prompts], [SAMPLE])
        self.assertFalse(flow.downloads)
        flow._resolve_manual_playlist_choice(SINGLE, "video")
        self.assertEqual([x[0] for x in flow.prompts], [SAMPLE, second])
        flow._resolve_manual_playlist_choice(second, "video")
        self.assertEqual(flow.downloads, [(["https://youtu.be/first", SINGLE, second, last], "video", "Alta", True)])

    def test_rejects_unrelated_url_in_manual_flow(self):
        flow = self._flow([SAMPLE])
        flow._prompt_next_manual_playlist()
        flow._resolve_manual_playlist_choice("https://youtu.be/unrelated", "video")
        self.assertIsNone(flow._manual_download_plan)
        self.assertEqual(flow.downloads, [])
        self.assertEqual(flow.recorded, [])


if __name__ == "__main__":
    unittest.main()
