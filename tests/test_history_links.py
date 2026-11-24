"""Exercise history hyperlink wiring without a desktop or browser."""
from html import escape
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import urlsplit
import unittest
from unittest.mock import Mock

from test_playlists import method_from_source

SOURCE = 'app/ui/history_dialog.py'


class FakeUrl:
    ParsingMode = SimpleNamespace(StrictMode='strict')

    def __init__(self, value, mode):
        self.value = value
        self.mode = mode
        self.parsed = urlsplit(value)

    def isValid(self):
        return ' ' not in self.value

    def scheme(self):
        return self.parsed.scheme

    def host(self):
        return self.parsed.hostname or ''

    def userInfo(self):
        return self.parsed.netloc.rsplit('@', 1)[0] if '@' in self.parsed.netloc else ''


class FakeLabel:
    def __init__(self):
        self.linkActivated = Mock()

    def __getattr__(self, key):
        if key.startswith('set'):
            return lambda value: setattr(self, key[3:], value)
        raise AttributeError(key)


class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.hrefs.append(dict(attrs)['href'])

    def handle_data(self, data):
        self.text.append(data)


class HistoryLinkTests(unittest.TestCase):
    def setUp(self):
        qt = SimpleNamespace(
            TextFormat=SimpleNamespace(PlainText='plain', RichText='rich'),
            TextInteractionFlag=SimpleNamespace(TextSelectableByMouse='select', TextBrowserInteraction='browser'),
            FocusPolicy=SimpleNamespace(StrongFocus='strong'),
        )
        self.browser = Mock()
        self.browser.openUrl.return_value = True
        self.critical = Mock()
        class Dialog:
            _browser_url = staticmethod(method_from_source(SOURCE, 'HistoryDialog', '_browser_url', QUrl=FakeUrl))
            _make_url_label = method_from_source(SOURCE, 'HistoryDialog', '_make_url_label',
                QLabel=FakeLabel, Qt=qt, escape=escape, tr=lambda k:k)
            _open_history_url = method_from_source(SOURCE, 'HistoryDialog', '_open_history_url',
                QDesktopServices=self.browser, critical=self.critical, tr=lambda k:k)
        self.dialog = Dialog()

    def test_web_url_with_query_is_displayed_and_opened_intact(self):
        url = 'https://www.youtube.com/watch?v=fixture&list=PL_fixture&index=3#t=10'
        label = self.dialog._make_url_label(url)
        parser = AnchorParser()
        parser.feed(label.Text)
        self.assertEqual(parser.hrefs, [url])
        self.assertEqual(''.join(parser.text), url)
        self.assertIn('&amp;', label.Text)
        self.assertEqual(label.TextFormat, 'rich')
        self.assertEqual(label.TextInteractionFlags, 'browser')
        self.assertEqual(label.FocusPolicy, 'strong')
        self.assertFalse(label.OpenExternalLinks)
        self.browser.openUrl.assert_not_called()
        handler = label.linkActivated.connect.call_args.args[0]
        handler(url)
        target = self.browser.openUrl.call_args.args[0]
        self.assertEqual(target.value, url)
        self.assertEqual(target.mode, 'strict')

    def test_each_label_uses_its_own_original_url(self):
        urls = ['https://x.com/user/status/123/photo/1', 'https://www.facebook.com/photo?fbid=123&set=pcb.456']
        labels = [self.dialog._make_url_label(url) for url in urls]
        for label in labels:
            label.linkActivated.connect.call_args.args[0]('ignored signal value')
        self.assertEqual([call.args[0].value for call in self.browser.openUrl.call_args_list], urls)

    def test_unsafe_entries_are_plain_text_and_cannot_open(self):
        for url in ('file:///tmp/file', 'javascript:alert(1)', 'mailto:a@example.com',
                    'https://user:pass@example.com/', 'https:///missing-host',
                    '<b>not a url</b>', 'https://example.com/\nother', ''):
            with self.subTest(url=url):
                label = self.dialog._make_url_label(url)
                self.assertEqual(label.TextFormat, 'plain')
                self.assertEqual(label.Text, url)
                label.linkActivated.connect.assert_not_called()
                self.dialog._open_history_url(url)
        self.browser.openUrl.assert_not_called()

    def test_legacy_http_link_can_open(self):
        self.dialog._open_history_url('http://example.com/old')
        self.assertEqual(self.browser.openUrl.call_args.args[0].value, 'http://example.com/old')

    def test_browser_failure_reports_error(self):
        self.browser.openUrl.return_value = False
        self.dialog._open_history_url('https://youtu.be/fixture')
        self.critical.assert_called_once_with(self.dialog, 'history.title', 'history.open_failed')

    def test_title_and_copy_controls_use_original_values(self):
        # Execute the actual row builder with minimal Qt widget doubles.
        class Widget:
            def __init__(self, *args):
                self.children = []
                self.clicked = Mock()
            def __getattr__(self, key):
                if key.startswith('set'):
                    return lambda *args: setattr(self, key[3:], args)
                raise AttributeError(key)
            def addWidget(self, widget):
                self.children.append(widget)
            def addLayout(self, layout, *args):
                self.children.append(layout)
        layouts = []
        def horizontal_layout(*args):
            layout = Widget(*args)
            layouts.append(layout)
            return layout
        clipboard = Mock()
        self.dialog.rows = Widget()
        self.dialog._format_timestamp = lambda timestamp: timestamp
        add_row = method_from_source(SOURCE, 'HistoryDialog', '_add_row', QWidget=Widget,
            QHBoxLayout=horizontal_layout, QVBoxLayout=Widget, QLabel=Widget, QPushButton=Widget,
            QGuiApplication=SimpleNamespace(clipboard=lambda: clipboard),
            Qt=SimpleNamespace(TextFormat=SimpleNamespace(PlainText='plain')), tr=lambda k:k)
        url = 'https://www.instagram.com/p/fixture/?img_index=1'
        add_row(self.dialog, {'url': url, 'title': '<b>Title</b>', 'timestamp': 'date'})
        row = self.dialog.rows.children[0]
        self.assertEqual(row.ObjectName, ('historyRow',))
        text_box, copy_button = layouts[0].children
        metadata = text_box.children[1]
        self.assertEqual(metadata.TextFormat, ('plain',))
        self.assertEqual(metadata.Text, ('<b>Title</b> - date',))
        copy_button.clicked.connect.call_args.args[0]()
        clipboard.setText.assert_called_once_with(url)
        self.browser.openUrl.assert_not_called()
