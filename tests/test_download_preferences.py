"""Persistent UI defaults, flat download paths, and PNG transparency."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from app.config import AppConfig, ConfigStore
from app.downloader import GleanDownloader
from app import downloader
from app.image_conversion import convert_image, IMAGE_FORMATS
from app.platforms import classify_url, is_youtube_playlist, media_kind, youtube_single_video_url
from test_playlists import method_from_source, SAMPLE, SINGLE
from test_media import PHOTO, IG, X, REEL


class Radio:
    def __init__(self, group):
        self.group = group
        self.checked = False
        self.visible = True
        self.enabled = True
        self.blocked = False
        group.append(self)

    def setChecked(self, value):
        if value:
            for item in self.group:
                item.checked = False
        self.checked = value

    def isChecked(self): return self.checked
    def setVisible(self, value): self.visible = value
    def setEnabled(self, value): self.enabled = value
    def blockSignals(self, value):
        previous = self.blocked
        self.blocked = value
        return previous


class Popup:
    show_for_url = method_from_source(
        'app/ui/clipboard_popup.py', 'ClipboardDownloadPopup', 'show_for_url',
        is_youtube_playlist=is_youtube_playlist, youtube_single_video_url=youtube_single_video_url,
        media_kind=media_kind)
    set_media_kind = method_from_source('app/ui/clipboard_popup.py', 'ClipboardDownloadPopup', 'set_media_kind')
    _accept = method_from_source('app/ui/clipboard_popup.py', 'ClipboardDownloadPopup', '_accept')
    _cancel = method_from_source('app/ui/clipboard_popup.py', 'ClipboardDownloadPopup', '_cancel')

    def __init__(self):
        group = []
        self.video_radio, self.audio_radio, self.image_radio = (Radio(group) for _ in range(3))
        scope = []
        self.single_video_radio, self.playlist_radio = (Radio(scope) for _ in range(2))
        self.playlist_scope_widget = Mock()
        self.playlist_single_url_label = Mock()
        self.url_label = Mock()
        self.image_format = Mock()
        self.image_format.findData.side_effect = lambda fmt: IMAGE_FORMATS.index(fmt) if fmt in IMAGE_FORMATS else -1
        self.index = 0
        self.image_format.setCurrentIndex.side_effect = lambda index: setattr(self, 'index', index)
        self.image_format.currentData.side_effect = lambda: IMAGE_FORMATS[self.index]
        self.download_requested = Mock()
        self.cancelled = Mock()
        self.retranslate_ui = self.adjustSize = self._move_to_bottom_right = self.show = self.raise_ = self.hide = Mock()

    @property
    def original_url(self): return self._url

    def open(self, url, config):
        self.show_for_url(url, allow_audio=media_kind(url) != 'image',
                          default_media_type=config.preferred_av,
                          default_image_format=config.preferred_image_format,
                          default_playlist_scope=config.preferred_playlist_scope,
                          default_mixed_media=config.preferred_mixed_media)


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = ConfigStore(self.root / 'config.ini')
        self.config = AppConfig(download_path=str(self.root / 'downloads'))
        self.popup = Popup()
        group = []
        self.window = SimpleNamespace(config=self.config, _clipboard_popup=self.popup,
                                      video_check=Radio(group), audio_check=Radio(group),
                                      _save_config=lambda: self.store.save(self.config))
        self.remember = method_from_source('app/ui/main_window.py', 'MainWindow', '_remember_popup_choice',
                                           youtube_single_video_url=youtube_single_video_url)

    def confirm(self):
        self.popup._accept()
        self.remember(self.window, *self.popup.download_requested.emit.call_args.args)

    def test_confirm_png_reopen_and_restart(self):
        self.popup.open(PHOTO, self.config)
        self.popup.image_format.setCurrentIndex(IMAGE_FORMATS.index('png'))
        self.confirm()
        self.assertEqual(self.store.load().preferred_image_format, 'png')
        for url in (IG, X, PHOTO):
            popup = Popup()
            popup.open(url, self.store.load())
            self.assertTrue(popup.image_radio.isChecked())
            self.assertEqual(popup.image_format.currentData(), 'png')

    def test_audio_and_image_preferences_independent(self):
        self.popup.open(REEL, self.config)
        self.popup.audio_radio.setChecked(True)
        self.confirm()
        self.popup.open(PHOTO, self.config)
        self.popup.image_format.setCurrentIndex(IMAGE_FORMATS.index('tiff'))
        self.confirm()
        config = self.store.load()
        self.assertEqual((config.preferred_av, config.preferred_image_format), ('audio', 'tiff'))
        self.popup.open(SINGLE, config)
        self.assertTrue(self.popup.audio_radio.isChecked())
        self.popup.open('https://www.instagram.com/p/FixturePost/', config)
        self.assertTrue(self.popup.image_radio.isChecked())
        self.popup.set_media_kind(self.popup.original_url, 'video')
        self.assertTrue(self.popup.audio_radio.isChecked())

    def test_cancel_keeps_last_confirmed_format(self):
        self.config.remember_download_choice('image:png')
        self.store.save(self.config)
        self.popup.open(PHOTO, self.config)
        self.popup.image_format.setCurrentIndex(IMAGE_FORMATS.index('jpg'))
        self.popup._cancel()
        self.popup.download_requested.emit.assert_not_called()
        self.assertEqual(self.store.load().preferred_image_format, 'png')

    def test_playlist_scope_remembered_only_when_choice_exists(self):
        self.popup.open(SAMPLE, self.config)
        self.assertTrue(self.popup.single_video_radio.isChecked())
        self.popup.playlist_radio.setChecked(True)
        self.confirm()
        self.popup.open(SINGLE, self.config)
        self.confirm()
        self.popup.open(SAMPLE, self.store.load())
        self.assertTrue(self.popup.playlist_radio.isChecked())
        self.popup.single_video_radio.setChecked(True)
        self.confirm()
        self.assertEqual(self.store.load().preferred_playlist_scope, 'single')

    def test_old_settings_and_invalid_preferences(self):
        self.store.save(self.config)
        original = self.store.path.read_text(encoding='utf-8')
        old = '\n'.join(line for line in original.splitlines() if not line.startswith('preferred_'))
        self.store.path.write_text(old, encoding='utf-8')
        config = self.store.load()
        self.assertEqual((config.preferred_av, config.preferred_image_format, config.preferred_playlist_scope),
                         ('video', 'original', 'single'))
        self.store.path.write_text(old + '\npreferred_av = invalid\npreferred_image_format = exe\npreferred_playlist_scope = invalid\npreferred_mixed_media = invalid\n', encoding='utf-8')
        config = self.store.load()
        self.assertEqual((config.preferred_av, config.preferred_image_format, config.preferred_playlist_scope, config.preferred_mixed_media),
                         ('video', 'original', 'single', 'video'))
        self.assertEqual(config.download_path, self.config.download_path)

    def test_queue_start_does_not_override_latest_preference(self):
        start = method_from_source('app/ui/main_window.py', 'MainWindow', '_start_clipboard_job', tr=lambda key: key)
        self.config.remember_download_choice('audio')
        self.window.audio_check.setChecked(True)
        self.window._pending_urls = []
        self.window._loaded_list_path = None
        self.window.file_name_label = Mock()
        self.window.url_entry = Mock()
        self.window._update_download_type_label = Mock()
        self.window._start_download = Mock()
        start(self.window, {'url': PHOTO, 'media_type': 'image', 'quality': 'png', 'thumbnails': False})
        self.assertTrue(self.window.audio_check.isChecked())
        self.assertEqual(self.config.preferred_av, 'audio')
        self.window._start_download.assert_called_once_with([PHOTO], 'image', 'png', False)

    def test_main_window_passes_saved_preferences_to_popup(self):
        show = method_from_source('app/ui/main_window.py', 'MainWindow', '_show_clipboard_download_prompt',
                                  classify_url=classify_url, media_kind=media_kind)
        self.config.remember_download_choice('audio', 'playlist')
        self.config.remember_download_choice('image:png')
        self.window._clipboard_popup = Mock()
        self.window._clipboard_title_cache = {}
        self.window._clipboard_fallback_title = Mock(return_value='Media')
        self.window._request_clipboard_title = Mock()
        self.window._is_downloading = False
        self.window._clipboard_download_queue = []
        show(self.window, PHOTO)
        kwargs = self.window._clipboard_popup.show_for_url.call_args.kwargs
        self.assertEqual(kwargs['default_image_format'], 'png')
        self.assertEqual(kwargs['default_playlist_scope'], 'playlist')
        self.assertEqual(kwargs['default_mixed_media'], 'image')
        show(self.window, SAMPLE)
        self.assertEqual(self.window._clipboard_popup.show_for_url.call_args.kwargs['default_media_type'], 'audio')


class FlatPathTests(unittest.TestCase):
    def test_ytdlp_templates_are_flat_except_youtube_playlists(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = GleanDownloader(tmp, 'ffmpeg.exe')
            for url in (SAMPLE, SINGLE, REEL, 'https://www.instagram.com/p/FixturePost/',
                        'https://x.com/fixture/status/123', 'https://www.tiktok.com/@fixture/video/123'):
                for kind in ('video', 'audio'):
                    with self.subTest(url=url, kind=kind):
                        opts = engine._build_options(url, kind, 'Alta', True)
                        self.assertTrue(opts['writethumbnail'])
                        if url == SAMPLE:
                            self.assertEqual(Path(opts['outtmpl']).parent.parent, Path(tmp))
                            self.assertIn('%(playlist_id|unknown)s', Path(opts['outtmpl']).parent.name)
                            self.assertIn('%(id)s', opts['outtmpl'])
                            self.assertFalse(opts['noplaylist'])
                        else:
                            self.assertEqual(Path(opts['outtmpl']).parent, Path(tmp))

    def test_gallery_directory_overrides_for_every_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = GleanDownloader(tmp, 'ffmpeg.exe')
            for url in (PHOTO, IG, X, 'https://www.tiktok.com/@fixture/video/123'):
                config = Mock()
                job = Mock()
                job.run.return_value = 0
                job._glean_stats = {'images': 1}
                with patch.object(downloader, '_get_gallery_runtime', return_value=(config, SimpleNamespace(TerminateExtraction=RuntimeError), Mock(return_value=job))):
                    self.assertEqual(engine._download_social_gallery(url), (1, 0))
                for path in [('extractor',)] + [('extractor', name) for name in ('instagram', 'twitter', 'tiktok', 'facebook')]:
                    config.set.assert_any_call(path, 'base-directory', tmp)
                    config.set.assert_any_call(path, 'directory', [])


class PngAlphaTests(unittest.TestCase):
    def test_real_conversion_preserves_full_and_partial_alpha(self):
        with tempfile.TemporaryDirectory() as tmp:
            expected = [(10, 20, 30, 0), (25, 80, 120, 100), (40, 50, 60, 255)]
            image = Image.new('RGBA', (3, 1))
            image.putdata(expected)
            for fmt in ('WEBP', 'TIFF'):
                with self.subTest(format=fmt):
                    source = Path(tmp) / ('alpha.' + fmt.lower())
                    image.save(source, format=fmt, **({'lossless': True, 'exact': True} if fmt == 'WEBP' else {}))
                    before = source.read_bytes()
                    result = convert_image(source, 'png')
                    self.assertEqual(result.parent, Path(tmp))
                    with Image.open(source) as original, Image.open(result) as converted:
                        self.assertEqual(list(converted.convert('RGBA').getdata()), list(original.convert('RGBA').getdata()))
                        self.assertEqual([p[3] for p in converted.convert('RGBA').getdata()], [0, 100, 255])
                    self.assertEqual(source.read_bytes(), before)

    def test_palette_transparency_and_opaque_jpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'palette.gif'
            img = Image.new('P', (2, 1))
            img.putpalette([255, 0, 0, 0, 255, 0] + [0] * 762)
            img.putdata([0, 1])
            img.save(source, transparency=0)
            with Image.open(convert_image(source, 'png')) as converted:
                self.assertEqual(list(converted.convert('RGBA').getdata()), [(255, 0, 0, 0), (0, 255, 0, 255)])
            jpg = Path(tmp) / 'opaque.jpg'
            Image.new('RGB', (2, 2), (10, 20, 30)).save(jpg)
            with Image.open(convert_image(jpg, 'png')) as converted:
                self.assertEqual(converted.convert('RGBA').getchannel('A').getextrema(), (255, 255))
