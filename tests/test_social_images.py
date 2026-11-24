import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import patch, Mock
from app import downloader as d
from app.platforms import classify_url, social_image_target
from app.url_list import extract_urls

IG = 'https://www.instagram.com/p/TestPost001/?img_index=1'
X = 'https://x.com/fixture_user/status/1234567890123456789/photo/1'

class ImageTests(unittest.TestCase):
    def test_supported_urls(self):
        self.assertEqual(classify_url(IG), 'ig')
        self.assertEqual(classify_url(X), 'x')
        self.assertEqual(extract_urls(IG+'\n'+X), [IG,X])
    def test_targets(self):
        self.assertEqual(social_image_target(IG), (IG.split('?')[0], '1'))
        self.assertEqual(social_image_target(X), (X.rsplit('/photo/',1)[0], '1'))
        self.assertIsNone(social_image_target(X.rsplit('/photo/',1)[0]))
    def test_reject_other_paths(self):
        for suffix in ('/photo/0','/photo/abc','/photo/1/extra','/video/1'):
            self.assertIsNone(classify_url(X.rsplit('/photo/',1)[0]+suffix))
    def test_indexed_download_never_calls_video(self):
        for url in (IG,X):
            engine=d.GleanDownloader('.', 'ffmpeg.exe')
            with patch.object(engine,'_download_social_gallery',return_value=(1,0)) as gallery, patch.object(engine,'_run_ytdlp') as video:
                engine.download(url,'video','Alta',True)
                gallery.assert_called_once_with(url,media_mode='images')
                video.assert_not_called()
    def test_empty_indexed_download_fails(self):
        engine=d.GleanDownloader('.', 'ffmpeg.exe')
        with patch.object(engine,'_download_social_gallery',return_value=(0,0)), patch.object(engine,'_run_ytdlp') as video:
            with self.assertRaises(RuntimeError): engine.download(X,'video','Alta',False)
            video.assert_not_called()
    def test_gallery_receives_canonical_url_and_index(self):
        engine=d.GleanDownloader('.', 'ffmpeg.exe')
        job=Mock(); job.run.return_value=0; job._glean_stats={'images':1}
        factory=Mock(return_value=job)
        with patch.object(d,'_get_gallery_runtime',return_value=(Mock(),SimpleNamespace(TerminateExtraction=RuntimeError),factory)):
            self.assertEqual(engine._download_social_gallery(X),(1,0))
        self.assertEqual(factory.call_args.args[0],X.rsplit('/photo/',1)[0])
        self.assertEqual(factory.call_args.kwargs['image_index'],'1')
    def test_real_wrapper_filters_position_and_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved=[]
            class Base:
                def __init__(self,url,parent): self.pathfmt=None
                def handle_url(self,url,kwdict):
                    saved.append(url); path=Path(tmp)/'image.jpg'; path.write_bytes(b'test'); self.pathfmt=SimpleNamespace(path=path)
            package=ModuleType('gallery_dl'); package.config=Mock(); package.exception=SimpleNamespace(TerminateExtraction=RuntimeError)
            jobs=ModuleType('gallery_dl.job'); jobs.DownloadJob=Base
            with patch.dict('sys.modules',{'gallery_dl':package,'gallery_dl.job':jobs}), patch.multiple(d,gallery_config=None,gallery_exception=None,_GalleryMediaJob=None):
                _,_,cls=d._get_gallery_runtime()
                job=cls(X,image_index='2')
                job.handle_url('a.jpg',{'num':1,'extension':'jpg'})
                job.handle_url('b.mp4',{'num':2,'extension':'mp4'})
                job.handle_url('c.jpg',{'num':2,'extension':'jpg'})
                self.assertEqual(saved,['c.jpg'])
                self.assertEqual(job._glean_stats['images'],1)
                single=cls(IG,image_index='1')
                single.handle_url('single.jpg',{'count':1,'extension':'jpg'})
                self.assertEqual(saved[-1],'single.jpg')
    def test_cancel_propagates(self):
        engine=d.GleanDownloader('.', 'ffmpeg.exe')
        with patch.object(engine,'_download_social_gallery',side_effect=d.DownloadCancelled()), patch.object(engine,'_run_ytdlp') as video:
            with self.assertRaises(d.DownloadCancelled): engine.download(X,'video','Alta',False)
            video.assert_not_called()
