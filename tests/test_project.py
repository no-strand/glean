import json
import tempfile
import unittest
from pathlib import Path

from app.config import AppConfig, ConfigStore
from app.downloader import GleanDownloader
from app.history import HistoryStore
from app.i18n import DEFAULT_LOCALE
from app.platforms import classify_url, is_youtube_playlist
from app.url_list import extract_urls, load_url_file
from app.updater import ReleaseAsset, ReleaseInfo, is_newer_version, select_update_asset, versions_equal
from app.version import APP_AUTHOR, APP_NAME, APP_VERSION


ROOT = Path(__file__).resolve().parents[1]


class BackendValidationTests(unittest.TestCase):
    def setUp(self):
        self.downloader = GleanDownloader(".", "ffmpeg.exe")

    def test_supported_urls_are_individual_social_posts_or_youtube(self):
        cases = {
            "https://www.youtube.com/watch?v=abc": "yt",
            "https://www.youtube.com/watch?v=abc&list=PL123": "yt",
            "https://www.youtube.com/playlist?list=PL123": "yt",
            "https://youtu.be/abc": "yt",
            "https://www.youtube.com/shorts/abc": "yt",
            "https://www.youtube.com/live/abc": "yt",
            "https://www.instagram.com/p/AbC_123-/": "ig",
            "https://www.tiktok.com/@user/video/123456789": "tt",
            "https://x.com/user_name/status/123456789": "x",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(classify_url(url), expected)
                self.assertEqual(self.downloader.validate_url(url), expected)

    def test_social_collections_and_non_requested_variants_are_rejected(self):
        invalid = (
            "https://www.instagram.com/example/",
            "https://www.instagram.com/reel/abc/",
            "https://instagram.com/p/abc/",
            "https://x.com/example",
            "https://x.com/example/media",
            "https://twitter.com/example/status/123",
            "https://www.x.com/example/status/123",
            "https://www.tiktok.com/@example",
            "https://vm.tiktok.com/abc/",
            "https://vt.tiktok.com/abc/",
            "https://www.youtube.com/@channel",
            "https://www.youtube.com/channel/UC123",
            "http://www.instagram.com/p/abc/",
            "https://example.com/test",
        )
        for url in invalid:
            with self.subTest(url=url):
                self.assertIsNone(classify_url(url))
                with self.assertRaises(ValueError):
                    self.downloader.validate_url(url)

    def test_youtube_playlist_detection_is_preserved(self):
        self.assertTrue(self.downloader.is_playlist("https://www.youtube.com/playlist?list=PL123"))
        self.assertTrue(self.downloader.is_playlist("https://www.youtube.com/watch?v=x&list=PL123"))
        self.assertFalse(self.downloader.is_playlist("https://www.youtube.com/watch?v=x"))
        self.assertTrue(is_youtube_playlist("https://www.youtube.com/playlist?list=PL123"))
        self.assertTrue(is_youtube_playlist("https://www.youtube.com/watch?v=x&list=PL123"))
        self.assertFalse(is_youtube_playlist("https://www.youtube.com/watch?v=x"))


    def test_audio_is_youtube_only(self):
        for url in (
            "https://www.instagram.com/p/test/",
            "https://www.tiktok.com/@u/video/1",
            "https://x.com/u/status/1",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.downloader._build_options(url, "audio", "Alta", False)

    def test_single_social_post_keeps_multi_item_extraction(self):
        opts = self.downloader._build_options("https://x.com/u/status/1", "video", "Alta", False)
        self.assertFalse(opts["noplaylist"])
        self.assertTrue(opts["ignoreerrors"])

    def test_no_authentication_options_are_added(self):
        opts = self.downloader._build_options("https://www.instagram.com/p/abc/", "video", "Alta", False)
        for key in ("cookiesfrombrowser", "username", "password"):
            self.assertNotIn(key, opts)

    def test_title_tracking_for_history(self):
        self.downloader._playlist_mode = False
        self.downloader._announce_info({"title": "  Example   video  "})
        self.assertEqual(self.downloader.history_title("https://youtu.be/abc"), "Example video")
        self.downloader.last_title = ""
        self.assertEqual(
            self.downloader.history_title("https://x.com/user/status/123"),
            "X post 123",
        )


class UrlListTests(unittest.TestCase):
    def test_extracts_only_strictly_valid_urls(self):
        source = (
            "https://youtu.be/one\n"
            "https://www.youtube.com/watch?v=two "
            "https://www.instagram.com/p/three/|"
            "https://www.instagram.com/reel/rejected/;"
            "https://www.tiktok.com/@user/video/4,"
            "https://vm.tiktok.com/rejected/ "
            "https://x.com/user/status/5,"
            "https://twitter.com/user/status/6 "
            "https://youtu.be/six"
        )
        self.assertEqual(
            extract_urls(source),
            [
                "https://youtu.be/one",
                "https://www.youtube.com/watch?v=two",
                "https://www.instagram.com/p/three/",
                "https://www.tiktok.com/@user/video/4",
                "https://x.com/user/status/5",
                "https://youtu.be/six",
            ],
        )

    def test_txt_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "list.csv"
            bad.write_text("https://youtu.be/a", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_url_file(bad)
            good = Path(tmp) / "list.txt"
            good.write_text("https://youtu.be/a,https://x.com/a/status/1", encoding="utf-8")
            self.assertEqual(len(load_url_file(good)), 2)


class ConfigTests(unittest.TestCase):
    def test_save_and_load_without_login_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            store = ConfigStore(path)
            source = AppConfig("Média", "Baixa", str(Path(tmp) / "downloads"), True, "pt_BR")
            store.save(source)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("Instagram_Browser", text)
            self.assertNotIn("X_Browser", text)
            loaded = store.load()
            self.assertEqual(loaded.video_quality, "Média")
            self.assertEqual(loaded.audio_quality, "Baixa")
            self.assertTrue(loaded.download_thumbnails)
            self.assertEqual(loaded.language, "pt_BR")
            self.assertFalse(loaded.start_with_windows)


    def test_default_download_folder_is_glean(self):
        default_path = Path(ConfigStore.default_download_path())
        self.assertEqual(default_path.name, "Glean")
        self.assertEqual(default_path.parent.name, "Downloads")

    def test_pre_schema_config_resets_stale_default_path_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(
                "[Settings]\n"
                "Video_Quality = Média\n"
                "Audio_Quality = Baixa\n"
                "DownloadPath = C:/Users/Test/Downloads/PreviousAppFolder\n"
                "Download_Thumbnails = true\n"
                "Language = pt_BR\n",
                encoding="utf-8",
            )
            store = ConfigStore(path)
            loaded = store.load()
            self.assertEqual(Path(loaded.download_path), Path(store.default_download_path()))
            text = path.read_text(encoding="utf-8")
            self.assertIn("configschema = 1", text.lower())
            self.assertIn("Glean", text)
            self.assertEqual(loaded.video_quality, "Média")
            self.assertEqual(loaded.audio_quality, "Baixa")
            self.assertTrue(loaded.download_thumbnails)
            self.assertEqual(loaded.language, "pt_BR")

    def test_current_schema_keeps_user_selected_download_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            selected = Path(tmp) / "My Downloads"
            store = ConfigStore(path)
            store.save(AppConfig(download_path=str(selected)))
            loaded = store.load()
            self.assertEqual(Path(loaded.download_path), selected.resolve())

    def test_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = ConfigStore(Path(tmp) / "config.ini").load()
            self.assertFalse(loaded.download_thumbnails)
            self.assertFalse(loaded.start_with_windows)
            self.assertEqual(loaded.language, DEFAULT_LOCALE)


class HistoryTests(unittest.TestCase):
    def test_history_persists_title_url_and_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = HistoryStore(Path(tmp) / "history.json")
            store.add("https://x.com/a/status/1", "First title")
            store.add("https://www.instagram.com/p/abc/", "Instagram title")
            store.add("https://x.com/a/status/1", "Updated title")
            entries = store.entries()
            self.assertEqual(entries[0]["url"], "https://x.com/a/status/1")
            self.assertEqual(entries[0]["title"], "Updated title")
            self.assertTrue(entries[0]["timestamp"])
            store.save()
            loaded = HistoryStore(Path(tmp) / "history.json").entries()
            self.assertEqual(loaded[0]["title"], "Updated title")

    def test_old_history_without_title_remains_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            path.write_text('[{"url":"https://youtu.be/x","timestamp":"2026-01-01T00:00:00+00:00"}]', encoding="utf-8")
            entry = HistoryStore(path).entries()[0]
            self.assertEqual(entry["title"], "")


class UpdaterTests(unittest.TestCase):
    def test_version_comparison_accepts_v_prefix(self):
        self.assertTrue(is_newer_version("v2.0.1", "2.0.0"))
        self.assertFalse(is_newer_version("v2.0.0", "2.0.0"))
        self.assertTrue(versions_equal("v2.0.0", "2.0.0"))

    def test_setup_exe_is_preferred_for_automatic_update(self):
        release = ReleaseInfo(
            version="2.1.0",
            tag_name="v2.1.0",
            title="Glean 2.1.0",
            html_url="https://github.com/no-strand/glean/releases/tag/v2.1.0",
            notes="",
            assets=(
                ReleaseAsset("Glean_2.1.0.zip", "https://example.invalid/a.zip"),
                ReleaseAsset("Glean_2.1.0_Setup.exe", "https://example.invalid/setup.exe"),
            ),
        )
        selected = select_update_asset(release)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.name, "Glean_2.1.0_Setup.exe")


class LocalizationTests(unittest.TestCase):
    def test_locales_have_no_login_strings_and_describe_current_scope(self):
        for path in (ROOT / "locales").glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(any(key == "action.login" or key.startswith("login.") for key in data))
            self.assertIn("Instagram", data["about.body"])
            self.assertIn("TikTok", data["about.body"])
            self.assertIn(" X", data["about.body"])
            self.assertIn("{author}", data["about.body"])
            self.assertIn("history.title", data)
            self.assertIn("error.invalid_url", data)
            self.assertNotIn("https://", data["error.invalid_url"])
            for key in (
                "download.type_label",
                "download.type.list",
                "download.type.youtube",
                "download.type.youtube_playlist",
                "download.type.instagram",
                "download.type.tiktok",
                "download.type.x",
                "clipboard.prompt",
                "clipboard.queue_prompt",
                "clipboard.yes",
                "clipboard.cancel",
                "clipboard.media_type",
                "clipboard.audio_youtube_only",
                "media.video",
                "media.audio",
                "action.start_with_windows",
                "startup.installed_only",
                "startup.enabled",
                "startup.disabled",
                "startup.change_failed",
                "queue.pending",
                "action.check_updates",
                "update.title",
                "update.current_version",
                "update.latest_version",
                "update.up_to_date",
                "update.download_and_install",
                "update.download_cancelled",
            ):
                self.assertIn(key, data)


class UiSourceTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "app" / "ui" / "main_window.py").read_text(encoding="utf-8")

    def test_login_ui_and_modules_are_removed(self):
        self.assertNotIn("open_login_dialog", self.source)
        self.assertNotIn("login_menu", self.source)
        self.assertNotIn("build_auth_sessions", self.source)
        self.assertFalse((ROOT / "app" / "auth.py").exists())
        self.assertFalse((ROOT / "app" / "ui" / "login_dialog.py").exists())

    def test_header_buttons_use_requested_icons_without_text(self):
        folder = self.source.index("self.header_folder_button = QPushButton()")
        history = self.source.index("self.history_button = QPushButton()")
        title = self.source.index("self.title_label = QLabel()")
        self.assertLess(folder, history)
        self.assertLess(history, title)
        self.assertIn("header_actions_row.addStretch(1)", self.source)
        self.assertIn("card_layout.addWidget(self.title_label)", self.source)
        self.assertIn('"folder_open_24.png"', self.source)
        self.assertIn('"history_24.png"', self.source)
        self.assertIn('self.header_folder_button.setText("")', self.source)
        self.assertIn('self.history_button.setText("")', self.source)
        self.assertTrue((ROOT / "resources" / "folder_open_24.png").is_file())
        self.assertTrue((ROOT / "resources" / "history_24.png").is_file())

    def test_download_type_label_is_above_download_information(self):
        type_label = self.source.index('self.download_type_label = QLabel("")')
        file_label = self.source.index('self.file_name_label = QLabel("")')
        status_label = self.source.index('self.status_label = QLabel("")')
        self.assertLess(type_label, file_label)
        self.assertLess(file_label, status_label)
        self.assertIn('download.type.youtube_playlist', self.source)
        self.assertIn('download.type.instagram', self.source)
        self.assertIn('download.type.tiktok', self.source)
        self.assertIn('download.type.x', self.source)
        style = (ROOT / "resources" / "style.qss").read_text(encoding="utf-8")
        self.assertIn('QLabel#downloadTypeLabel', style)
        self.assertIn('font-size: 9pt', style)

    def test_history_receives_title_from_worker(self):
        worker = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
        history_dialog = (ROOT / "app" / "ui" / "history_dialog.py").read_text(encoding="utf-8")
        self.assertIn("url_completed = Signal(str, str)", worker)
        self.assertIn("self.engine.history_title(url)", worker)
        self.assertIn('f"{title} - {formatted_date}"', history_dialog)

    def test_clipboard_monitor_prompts_only_for_supported_urls(self):
        popup = (ROOT / "app" / "ui" / "clipboard_popup.py").read_text(encoding="utf-8")
        self.assertIn("QTimer.singleShot(0, self._start_clipboard_monitoring)", self.source)
        self.assertIn("clipboard.dataChanged.connect(self._on_clipboard_changed)", self.source)
        self.assertIn("classify_url(text) is None", self.source)
        self.assertIn("self._show_clipboard_download_prompt(text)", self.source)
        self.assertIn("popup.download_requested.connect(self._download_clipboard_url)", self.source)
        self.assertNotIn("if self._is_downloading or classify_url(text) is None", self.source)
        self.assertIn("self._clipboard_download_queue.append(job)", self.source)
        self.assertIn("QTimer.singleShot(0, self._start_next_queued_download)", self.source)
        self.assertIn("class ClipboardDownloadPopup(QWidget)", popup)
        self.assertIn("download_requested = Signal(str, str)", popup)
        self.assertIn("queue_mode: bool = False", popup)
        self.assertIn("default_media_type: str = \"video\"", popup)
        self.assertIn("allow_audio: bool = False", popup)
        self.assertIn("self.video_radio = QRadioButton()", popup)
        self.assertIn("self.audio_radio = QRadioButton()", popup)
        self.assertIn("self.download_requested.emit(url, media_type)", popup)
        self.assertIn("allow_audio = platform_key == \"yt\"", self.source)
        self.assertIn("job = self._clipboard_job(url, media_type)", self.source)
        self.assertIn('tr("clipboard.queue_prompt")', popup)
        self.assertIn("Qt.WindowType.WindowStaysOnTopHint", popup)
        self.assertIn("screen.availableGeometry()", popup)
        style = (ROOT / "resources" / "style.qss").read_text(encoding="utf-8")
        self.assertIn("QFrame#clipboardPopup", style)
        self.assertIn("QPushButton#clipboardPopupAccept", style)
        self.assertIn("self.setFixedWidth(420)", popup)
        self.assertIn("option.setMinimumWidth(max(96, text_width + 52))", popup)
        self.assertIn("display_title: str = \"\"", popup)
        self.assertIn("self.url_label.setText(title)", popup)
        self.assertNotIn("shown = self._url", popup)
        self.assertIn("self._request_clipboard_title(url)", self.source)
        self.assertIn("fetch_url_title", (ROOT / "app" / "downloader.py").read_text(encoding="utf-8"))

    def test_clipboard_queue_is_visible_and_sequential(self):
        queue_label = self.source.index('self.queue_label = QLabel("")')
        type_label = self.source.index('self.download_type_label = QLabel("")')
        self.assertLess(queue_label, type_label)
        self.assertIn("self._active_download_urls = set(urls)", self.source)
        self.assertIn("url in self._active_download_urls or url in self._queued_urls()", self.source)
        self.assertIn("job = self._clipboard_download_queue.pop(0)", self.source)
        self.assertIn("if not self._cancel_all_requested and self._clipboard_download_queue", self.source)
        self.assertIn("self._clipboard_download_queue.clear()", self.source)
        style = (ROOT / "resources" / "style.qss").read_text(encoding="utf-8")
        self.assertIn("QLabel#queueLabel", style)

    def test_tray_menu_actions_are_initialized_with_visible_text(self):
        self.assertIn('menu.setObjectName("trayMenu")', self.source)
        self.assertIn('show_action = QAction(tr("tray.show"), self)', self.source)
        self.assertIn('exit_action = QAction(tr("action.exit"), self)', self.source)
        style = (ROOT / "resources" / "style.qss").read_text(encoding="utf-8")
        self.assertIn("QMenu {", style)
        self.assertIn("color: #e6e6ec;", style)
        self.assertIn("QMenu::item {", style)
        self.assertIn("background-color: transparent;", style)

    def test_about_uses_project_icon_instead_of_stock_information_icon(self):
        message_boxes = (ROOT / "app" / "message_boxes.py").read_text(encoding="utf-8")
        self.assertIn('icon_path=os.path.join(self.resource_dir, "icon.ico")', self.source)
        self.assertIn("box.setIconPixmap(icon.pixmap(64, 64))", message_boxes)
        self.assertIn("Criado por {author}", json.loads((ROOT / "locales" / "pt_BR.json").read_text(encoding="utf-8"))["about.body"])

    def test_help_menu_has_integrated_update_checker(self):
        updater = (ROOT / "app" / "updater.py").read_text(encoding="utf-8")
        dialog = (ROOT / "app" / "ui" / "update_dialog.py").read_text(encoding="utf-8")
        self.assertIn("self.check_updates_action = QAction(self)", self.source)
        self.assertIn("self.check_updates_action.triggered.connect(self.check_for_updates)", self.source)
        self.assertIn('tr("action.check_updates")', self.source)
        self.assertIn("https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest", updater)
        self.assertIn('GITHUB_REPOSITORY = "no-strand/glean"', updater)
        self.assertIn("class UpdateDialog(QDialog)", dialog)
        self.assertIn("QProgressBar", dialog)
        self.assertIn("self.cancel_button.clicked.connect(self._cancel_download)", dialog)
        self.assertIn("self.install_requested.emit(path)", dialog)

    def test_settings_and_help_remain(self):
        for name in (
            "start_with_windows_action",
            "thumbnails_action",
            "video_quality_action",
            "audio_quality_action",
            "change_directory_action",
            "open_folder_action",
            "check_updates_action",
            "language_action",
            "about_action",
        ):
            self.assertIn(name, self.source)

    def test_start_with_windows_precedes_thumbnails_and_uses_installed_build_helper(self):
        startup = (ROOT / "app" / "startup.py").read_text(encoding="utf-8")
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        start_action = self.source.index("self.start_with_windows_action = QAction(self)")
        thumbnails = self.source.index("self.thumbnails_action = QAction(self)")
        self.assertLess(start_action, thumbnails)
        self.assertIn("startup_available()", self.source)
        self.assertIn("set_startup_enabled(bool(checked))", self.source)
        self.assertIn('r"Software\\Microsoft\\Windows\\CurrentVersion\\Run"', startup)
        self.assertIn('app_dir.glob("unins*.exe")', startup)
        self.assertIn('subprocess.list2cmdline([executable, "--startup"])', startup)
        self.assertIn('if "--startup" not in sys.argv[1:]', main)
        iss = (ROOT / "Glean.iss").read_text(encoding="utf-8")
        self.assertIn('Software\\Microsoft\\Windows\\CurrentVersion\\Run', iss)

    def test_style_has_no_login_state_rules(self):
        style = (ROOT / "resources" / "style.qss").read_text(encoding="utf-8")
        self.assertNotIn("loginStatusLabel", style)
        self.assertNotIn("sessionState", style)
        self.assertIn("font-size: 10pt", style)
        self.assertIn("QLabel#titleLabel { font-size: 24pt;", style)


class PackagingTests(unittest.TestCase):
    def test_identity(self):
        self.assertEqual(APP_NAME, "Glean")
        self.assertEqual(APP_AUTHOR, "Nostrand")
        self.assertEqual(APP_VERSION, "2.0.0")


    def test_legacy_product_identity_is_fully_removed(self):
        legacy_tokens = ("YouTube" + " Downloader", "YouTube" + "Downloader", "Youtube" + " Downloader")
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix.lower() in {".exe", ".ico", ".png", ".pyc"}:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for token in legacy_tokens:
                self.assertNotIn(token, content, f"legacy identity found in {path}")

    def test_spec_includes_new_header_icons(self):
        spec = (ROOT / "Glean.spec").read_text(encoding="utf-8")
        self.assertIn('"folder_open_24.png"', spec)
        self.assertIn('"history_24.png"', spec)
        self.assertIn('collect_submodules("yt_dlp")', spec)
        self.assertIn('collect_submodules("gallery_dl")', spec)

    def test_installer_uses_dedicated_installer_icon(self):
        installer = ROOT / "resources" / "installer.ico"
        self.assertTrue(installer.is_file())
        self.assertGreater(installer.stat().st_size, 0)
        iss = (ROOT / "Glean.iss").read_text(encoding="utf-8")
        self.assertIn(r"SetupIconFile=resources\installer.ico", iss)

    def test_readme_documents_no_login_and_strict_social_urls(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("https://www.instagram.com/p/<post>/", readme)
        self.assertIn("https://x.com/<user>/status/<id>", readme)
        self.assertIn("https://www.tiktok.com/<user>/video/<id>", readme)
        self.assertIn("no account login", readme)


if __name__ == "__main__":
    unittest.main()
