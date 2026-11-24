import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from app.image_conversion import IMAGE_FORMATS, convert_image
from app.platforms import media_kind, classify_url
from app.downloader import GleanDownloader, DownloadCancelled
from test_playlists import method_from_source

PHOTO = 'https://www.facebook.com/photo?fbid=1000000000000002&set=pcb.1000000000000003'
REEL = 'https://www.facebook.com/reel/1000000000000001'
IG = 'https://www.instagram.com/p/TestPost001/?img_index=1'
X = 'https://x.com/fixture_user/status/1234567890123456789/photo/1'

class MediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'sample.png'
        Image.new('RGBA', (37,23), (25,80,120,100)).save(self.path)
        self.engine = GleanDownloader(self.tmp.name,'ffmpeg.exe')
    def test_url_types(self):
        for u in (PHOTO, IG, X): self.assertEqual(media_kind(u),'image')
        for u in (REEL,'https://youtu.be/id','https://www.tiktok.com/@u/video/1'): self.assertEqual(media_kind(u),'video')
        self.assertEqual(media_kind('https://www.instagram.com/p/abc/'),'unknown')
        self.assertEqual(media_kind('https://x.com/u/status/1'),'unknown')
    def test_original_is_unchanged(self):
        before = self.path.read_bytes()
        self.assertEqual(convert_image(self.path,'original'), self.path)
        self.assertEqual(self.path.read_bytes(), before)
    def test_formats_resolution_and_alpha(self):
        for fmt in IMAGE_FORMATS[1:]:
            result = convert_image(self.path,fmt)
            with Image.open(result) as out:
                self.assertEqual(out.size,(37,23))
                if fmt in ('png','webp','tiff'):
                    self.assertEqual(out.convert('RGBA').getpixel((0,0)),(25,80,120,100))
                else: self.assertEqual(out.mode,'RGB')
    def test_collision_never_overwrites(self):
        dest=self.path.with_suffix('.jpg'); dest.write_bytes(b'existing')
        result=convert_image(self.path,'jpg')
        self.assertNotEqual(dest,result); self.assertEqual(dest.read_bytes(),b'existing')
    def test_multi_frame_not_silently_flattened(self):
        gif=self.path.with_suffix('.gif')
        Image.new('RGB',(2,2),'red').save(gif,save_all=True,append_images=[Image.new('RGB',(2,2),'blue')])
        with self.assertRaises(ValueError): convert_image(gif,'jpg')
        self.assertEqual(convert_image(gif,'original'),gif)
    def test_image_download_converts_only_returned_paths(self):
        def gallery(*a,**kw):
            source = Path(self.engine.download_dir) / self.path.name
            source.write_bytes(self.path.read_bytes())
            self.engine._gallery_paths=[str(source)]; return 1,0
        with patch.object(self.engine,'_download_social_gallery',side_effect=gallery) as g, patch.object(self.engine,'_run_ytdlp') as y:
            self.engine.download(PHOTO,'image','webp',True)
            g.assert_called_once_with(PHOTO,media_mode='images'); y.assert_not_called()
        self.assertTrue(self.path.with_suffix('.webp').is_file())
    def test_video_only_never_downloads_gallery(self):
        with patch.object(self.engine,'_download_social_gallery') as g, patch.object(self.engine,'_run_ytdlp',return_value=1) as y:
            self.engine.download(REEL,'video_only','Alta',True)
            g.assert_not_called(); y.assert_called_once_with(REEL,'video','Alta',True,emit_final=False)
    def test_image_cancel(self):
        def gallery(*a,**kw):
            source = Path(self.engine.download_dir) / self.path.name
            source.write_bytes(self.path.read_bytes())
            self.engine._gallery_paths=[str(source)]; self.engine.cancel_event.set(); return 1,0
        with patch.object(self.engine,'_download_social_gallery',side_effect=gallery):
            with self.assertRaises(DownloadCancelled): self.engine.download(PHOTO,'image','jpg',False)
        self.assertFalse(self.path.with_suffix('.jpg').exists())
    def test_invalid_selection(self):
        for url,fmt in [(REEL,'png'),(PHOTO,'exe')]:
            with self.assertRaises(ValueError): self.engine.download(url,'image',fmt,False)
    def test_queue_retains_format_and_disables_thumb(self):
        job=method_from_source('app/ui/main_window.py','MainWindow','_clipboard_job',classify_url=classify_url,media_kind=media_kind,IMAGE_FORMATS=IMAGE_FORMATS)
        flow=SimpleNamespace(config=SimpleNamespace(audio_quality='Alta',video_quality='Alta',download_thumbnails=True))
        self.assertEqual(job(flow,PHOTO,'image:tiff'),{'url':PHOTO,'media_type':'image','quality':'tiff','thumbnails':False})
        self.assertEqual(job(flow,REEL,'audio')['media_type'],'audio')
        self.assertIsNone(job(flow,PHOTO,'audio'))
    def test_probe_without_library_falls_back(self):
        from app.downloader import probe_media_kind
        with patch.dict('sys.modules',{'gallery_dl':None}):
            self.assertEqual(probe_media_kind('https://x.com/u/status/1'),'unknown')
            self.assertEqual(probe_media_kind(PHOTO),'image')
    def test_png_conversion_from_tiff_and_cmyk(self):
        tiff=self.path.with_suffix('.tiff')
        Image.new('CMYK',(8,9),(0,120,200,0)).save(tiff)
        converted=convert_image(tiff,'png')
        with Image.open(converted) as out:
            self.assertEqual(out.mode,'RGB'); self.assertEqual(out.size,(8,9))
    def test_probe_metadata_images_videos_and_mixed(self):
        from app.downloader import probe_media_kind
        from types import ModuleType
        gallery=ModuleType('gallery_dl'); ext=ModuleType('gallery_dl.extractor'); msg=ModuleType('gallery_dl.extractor.message')
        msg.Message=SimpleNamespace(Url=3,Queue=6)
        gallery.config=SimpleNamespace(set=lambda *args: None); gallery.extractor=ext
        url='https://x.com/u/status/1'
        with patch.dict('sys.modules',{'gallery_dl':gallery,'gallery_dl.extractor':ext,'gallery_dl.extractor.message':msg}):
            for suffixes,expected in [(['jpg'],'image'),(['mp4'],'video'),(['jpg','mp4'],'mixed'),([], 'unknown')]:
                ext.find=lambda _: [(3,'https://example.com/a.'+s,{'extension':s}) for s in suffixes]
                self.assertEqual(probe_media_kind(url),expected)
    def test_popup_emits_image_format(self):
        accept=method_from_source('app/ui/clipboard_popup.py','ClipboardDownloadPopup','_accept')
        emitted=[]
        popup=SimpleNamespace(_url=PHOTO,_single_video_url=None,_allow_audio=False,image_radio=SimpleNamespace(isChecked=lambda:True),image_format=SimpleNamespace(currentData=lambda:'webp'),hide=lambda:None,download_requested=SimpleNamespace(emit=lambda *args:emitted.append(args)))
        accept(popup)
        self.assertEqual(emitted,[(PHOTO,'image:webp')])
