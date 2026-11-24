"""Regression tests for download publication and clipboard notifications."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from app.downloader import GleanDownloader, DownloadCancelled
from app.platforms import classify_url
from test_playlists import method_from_source

PHOTO = 'https://www.facebook.com/photo?fbid=1000000000000002'
IG = 'https://www.instagram.com/p/FixturePost/?img_index=1'
X = 'https://x.com/fixture/status/123/photo/1'


class SingleFormatTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.engine = GleanDownloader(tmp.name, 'ffmpeg.exe')

    def gallery(self, source_format='WEBP', names=('image',), cancel=False, fail=False):
        def run(url, media_mode):
            paths = []
            for name in names:
                path = Path(self.engine.download_dir) / (name + '.' + source_format.lower())
                Image.new('RGBA', (5, 7), (11, 22, 33, 90)).save(path, format=source_format,
                    **({'lossless': True} if source_format == 'WEBP' else {}))
                paths.append(str(path))
            self.engine._gallery_paths = paths
            if cancel:
                self.engine.cancel_event.set()
            if fail:
                raise RuntimeError('extraction failed')
            return len(paths), 0
        return run

    def test_each_platform_only_publishes_selected_format(self):
        for i, url in enumerate((PHOTO, IG, X)):
            name = f'photo_{i}'
            with patch.object(self.engine, '_download_social_gallery', side_effect=self.gallery(names=(name,))):
                self.engine.download(url, 'image', 'png', False)
            with Image.open(self.root / (name + '.png')) as im:
                self.assertEqual(im.format, 'PNG')
                self.assertEqual(im.getpixel((0, 0)), (11, 22, 33, 90))
                self.assertEqual(im.size, (5, 7))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['photo_0.png', 'photo_1.png', 'photo_2.png'])

    def test_original_format_is_not_converted(self):
        with patch.object(self.engine, '_download_social_gallery', side_effect=self.gallery()):
            self.engine.download(PHOTO, 'image', 'original', False)
        self.assertEqual([p.name for p in self.root.iterdir()], ['image.webp'])

    def test_already_selected_format_is_published_once(self):
        with patch.object(self.engine, '_download_social_gallery', side_effect=self.gallery(source_format='PNG')):
            self.engine.download(PHOTO, 'image', 'png', False)
        self.assertEqual([p.name for p in self.root.iterdir()], ['image.png'])

    def test_existing_original_and_destination_are_untouched(self):
        old_source = self.root / 'image.webp'
        old_dest = self.root / 'image.png'
        old_source.write_bytes(b'old source')
        old_dest.write_bytes(b'old destination')
        with patch.object(self.engine, '_download_social_gallery', side_effect=self.gallery()):
            self.engine.download(PHOTO, 'image', 'png', False)
        self.assertEqual(old_source.read_bytes(), b'old source')
        self.assertEqual(old_dest.read_bytes(), b'old destination')
        self.assertTrue((self.root / 'image_1.png').is_file())
        self.assertEqual(len(list(self.root.iterdir())), 3)

    def test_multiple_images_and_all_conversion_formats(self):
        for fmt in ('jpg', 'png', 'webp', 'tiff', 'bmp'):
            names = (fmt + '_1', fmt + '_2')
            with patch.object(self.engine, '_download_social_gallery', side_effect=self.gallery(names=names)):
                self.engine.download(IG, 'image', fmt, False)
            for name in names:
                self.assertTrue((self.root / (name + '.' + fmt)).is_file())
        self.assertEqual(len(list(self.root.iterdir())), 10)
        self.assertTrue(all(p.is_file() for p in self.root.iterdir()))

    def test_conversion_failure_recovers_original(self):
        with patch.object(self.engine, '_download_social_gallery', side_effect=self.gallery()), \
                patch('app.downloader.convert_image', side_effect=ValueError('conversion failed')):
            with self.assertRaisesRegex(ValueError, 'conversion failed'):
                self.engine.download(PHOTO, 'image', 'png', False)
        self.assertEqual([p.name for p in self.root.iterdir()], ['image.webp'])
        self.assertEqual(Path(self.engine.download_dir), self.root)

    def test_partial_conversion_failure_does_not_restore_converted_sources(self):
        from app.image_conversion import convert_image
        def convert(path, fmt):
            if path.stem == 'second':
                raise ValueError('conversion failed')
            return convert_image(path, fmt)
        with patch.object(self.engine, '_download_social_gallery', side_effect=self.gallery(names=('first', 'second'))), \
                patch('app.downloader.convert_image', side_effect=convert):
            with self.assertRaises(ValueError):
                self.engine.download(PHOTO, 'image', 'png', False)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['first.png', 'second.webp'])

    def test_cancel_and_extractor_error_recover_completed_original(self):
        for cancel, fail, exc in ((True, False, DownloadCancelled), (False, True, RuntimeError)):
            self.engine.cancel_event.clear()
            name = 'cancelled' if cancel else 'failed'
            with patch.object(self.engine, '_download_social_gallery', side_effect=self.gallery(names=(name,), cancel=cancel, fail=fail)):
                with self.assertRaises(exc):
                    self.engine.download(PHOTO, 'image', 'png', False)
            self.assertTrue((self.root / (name + '.webp')).is_file())
        self.assertTrue(all(p.is_file() for p in self.root.iterdir()))

    def test_publish_failure_and_recovery_failure_leave_staging(self):
        with patch.object(self.engine, '_download_social_gallery', side_effect=self.gallery()), \
                patch('app.downloader.publish_image', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(RuntimeError, 'disk full'):
                self.engine.download(PHOTO, 'image', 'png', False)
        stages = list(self.root.glob('.glean-image-*'))
        self.assertEqual(len(stages), 1)
        self.assertTrue((stages[0] / 'image.webp').exists())


class ClipboardTests(unittest.TestCase):
    def setUp(self):
        self.changed = method_from_source('app/ui/main_window.py', 'MainWindow', '_on_clipboard_changed', classify_url=classify_url)
        self.clip = Mock()
        self.clip.text.return_value = PHOTO
        self.clip.mimeData.return_value = None
        self.popup = Mock(original_url=PHOTO)
        self.popup.isVisible.return_value = False
        self.window = SimpleNamespace(_clipboard=self.clip, _clipboard_popup=self.popup,
                                      _manual_download_plan=None, _show_clipboard_download_prompt=Mock())

    def test_repeated_copy_or_cut_event_can_reopen_same_link(self):
        self.changed(self.window)
        self.changed(self.window)
        self.assertEqual(self.window._show_clipboard_download_prompt.call_count, 2)
        self.window._show_clipboard_download_prompt.assert_called_with(PHOTO)

    def test_duplicate_event_does_not_reset_open_prompt(self):
        self.popup.isVisible.return_value = True
        self.changed(self.window)
        self.window._show_clipboard_download_prompt.assert_not_called()
        self.clip.text.return_value = IG
        self.changed(self.window)
        self.window._show_clipboard_download_prompt.assert_called_once_with(IG)

    def test_uri_list_only(self):
        self.clip.text.return_value = ''
        mime = Mock()
        mime.hasUrls.return_value = True
        mime.urls.return_value = [Mock(toString=Mock(return_value=X))]
        self.clip.mimeData.return_value = mime
        self.changed(self.window)
        self.window._show_clipboard_download_prompt.assert_called_once_with(X)

    def test_unsupported_or_empty_clipboard_is_not_prompted(self):
        for text in ('', 'https://example.org/', 'ordinary text'):
            self.clip.text.return_value = text
            self.changed(self.window)
        self.window._show_clipboard_download_prompt.assert_not_called()

    def test_manual_confirmation_is_not_replaced(self):
        self.window._manual_download_plan = {}
        self.changed(self.window)
        self.window._show_clipboard_download_prompt.assert_not_called()

    def test_startup_connects_without_opening_existing_link(self):
        app = Mock()
        app.clipboard.return_value = self.clip
        start = method_from_source('app/ui/main_window.py', 'MainWindow', '_start_clipboard_monitoring',
                                   QApplication=SimpleNamespace(instance=lambda: app))
        self.window._on_clipboard_changed = Mock()
        start(self.window)
        self.clip.dataChanged.connect.assert_called_once_with(self.window._on_clipboard_changed)
        self.window._on_clipboard_changed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
