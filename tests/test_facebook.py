import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from app import downloader as d
from app.platforms import classify_url, facebook_media_kind, facebook_photo_url
from app.url_list import extract_urls

REEL = 'https://www.facebook.com/reel/1000000000000001'
PHOTO = 'https://www.facebook.com/photo?fbid=1000000000000002&set=pcb.1000000000000003'

class FacebookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = d.GleanDownloader(self.tmp.name, 'ffmpeg.exe')

    def test_supported_urls_and_txt(self):
        for url, kind in [(REEL,'video'), (PHOTO,'image')]:
            self.assertEqual(classify_url(url), 'fb')
            self.assertEqual(facebook_media_kind(url), kind)
        self.assertEqual(extract_urls(REEL+'\n'+PHOTO), [REEL, PHOTO])

    def test_other_individual_paths(self):
        for path in ('watch/?v=123', 'video.php?v=123', 'video/video.php?v=123', 'page/videos/123/'):
            self.assertEqual(facebook_media_kind('https://www.facebook.com/'+path), 'video')
        self.assertEqual(facebook_media_kind('https://m.facebook.com/photo.php?fbid=123'), 'image')

    def test_reject_non_media(self):
        for url in ('https://facebook.com/page', 'https://facebook.com/page/videos', 'https://facebook.com/media/set/?set=123', 'https://facebook.com/photo?set=123', 'https://facebook.com/photo?fbid=abc', 'https://facebook.com/photo?fbid=1&fbid=2', 'https://facebook.com/reel/abc', 'https://facebook.com/reel/123/extra', 'https://facebook.com.evil.test/reel/123', 'http://facebook.com/reel/123'):
            self.assertIsNone(classify_url(url), url)

    def test_photo_canonicalization(self):
        self.assertEqual(facebook_photo_url(PHOTO), 'https://www.facebook.com/photo/?fbid=1000000000000002')
        with self.assertRaises(ValueError): facebook_photo_url(REEL)

    def test_photo_dispatch(self):
        with patch.object(self.engine, '_download_social_gallery', return_value=(1,0)) as gallery, patch.object(self.engine, '_run_ytdlp') as video:
            self.engine.download(PHOTO,'video','Alta',True)
            gallery.assert_called_once_with(PHOTO,media_mode='images')
            video.assert_not_called()

    def test_reel_dispatch(self):
        with patch.object(self.engine, '_download_social_gallery') as gallery, patch.object(self.engine, '_run_ytdlp',return_value=1) as video:
            self.engine.download(REEL,'video','Alta',True)
            video.assert_called_once_with(REEL,'video','Alta',True,emit_final=False)
            gallery.assert_not_called()

    def test_empty_results_raise(self):
        for url in (PHOTO,REEL):
            with patch.object(self.engine,'_download_social_gallery',return_value=(0,0)), patch.object(self.engine,'_run_ytdlp',return_value=0):
                with self.assertRaises(RuntimeError): self.engine.download(url,'video','Alta',False)

    def test_gallery_config_and_url(self):
        job=Mock(); job.run.return_value=0; job._glean_stats={'images':1}
        factory=Mock(return_value=job); config=Mock()
        with patch.object(d,'_get_gallery_runtime',return_value=(config,SimpleNamespace(TerminateExtraction=RuntimeError),factory)):
            self.assertEqual(self.engine._download_social_gallery(PHOTO),(1,0))
        self.assertEqual(factory.call_args.args[0],facebook_photo_url(PHOTO))
        config.set.assert_any_call(('extractor','facebook'),'author-followups',False)
        config.set.assert_any_call(('extractor','facebook'),'videos',False)

    def test_video_options(self):
        opts=self.engine._build_options(REEL,'video','Alta',True)
        self.assertTrue(opts['noplaylist']); self.assertTrue(opts['writethumbnail'])
        self.assertFalse(opts.get('ignoreerrors',False))

    def test_cancel_and_restore_directory(self):
        with patch.object(self.engine,'_download_social_gallery',side_effect=d.DownloadCancelled()):
            with self.assertRaises(d.DownloadCancelled): self.engine.download_video(PHOTO,'Alta',False,str(Path(self.tmp.name)/'other'))
        self.assertEqual(self.engine.download_dir,self.tmp.name)

    def test_photo_title_does_not_call_ytdlp(self):
        with patch.object(d,'_get_ytdlp') as runtime:
            self.assertEqual(d.fetch_url_title(PHOTO),'')
            runtime.assert_not_called()
        self.assertEqual(self.engine.history_title(PHOTO),'Facebook photo')

    def test_audio_policy_for_images_and_video(self):
        with self.assertRaises(ValueError): self.engine.download(PHOTO,'audio','Alta',False)
        with patch.object(self.engine, "_run_ytdlp", return_value=1) as run:
            self.engine.download(REEL, "audio", "Alta", False)
            run.assert_called_once()
