from datetime import datetime, timedelta, timezone
from contextlib import redirect_stderr
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_blog.py"
spec = importlib.util.spec_from_file_location("update_blog", SCRIPT)
blog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(blog)
NOW = datetime(2026, 11, 16, 10, tzinfo=timezone.utc)


def feed(items=(), channel_link=blog.BLOG_URL):
    return ('<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" '
            'xmlns:media="http://search.yahoo.com/mrss/"><channel>'
            '<link>' + escape(channel_link) + '</link>' + ''.join(items) + '</channel></rss>').encode()


def item(number=1, title="まちの駅のお知らせ", date="Mon, 16 Nov 2026 10:00:00 +0900", description="", url=None):
    return ('<item><title>' + escape(title) + '</title><link>'
            + escape(url or f"{blog.BLOG_URL}entry-{number}.html")
            + '</link><pubDate>' + escape(date) + '</pubDate><description>'
            + escape(description) + '</description></item>')


class BlogTests(unittest.TestCase):
    def test_empty_blog_is_valid(self):
        self.assertEqual(blog.parse_feed(feed()), [])
        self.assertEqual(blog.render_posts([]), "")

    def test_latest_three_are_sorted_and_deduplicated(self):
        data = feed([
            item(1, date="Thu, 12 Nov 2026 10:00:00 +0900"),
            item(3, date="Sat, 14 Nov 2026 10:00:00 +0900"),
            item(4, date="Sun, 15 Nov 2026 10:00:00 +0900"),
            item(2, date="Fri, 13 Nov 2026 10:00:00 +0900"),
            item(4),
        ])
        self.assertEqual([p['url'] for p in blog.parse_feed(data)],
                         [f"{blog.BLOG_URL}entry-{i}.html" for i in [4, 3, 2]])

    def test_one_or_two_posts_render_without_placeholders(self):
        for count in [1, 2]:
            rendered = blog.render_posts(blog.parse_feed(feed([item(i) for i in range(count)])))
            self.assertEqual(rendered.count('class="home-blog-article"'), count)

    def test_images_are_extracted_and_tracking_is_ignored(self):
        markup = '<img src="https://tracker.example/pixel.png"><img width="1" height="1" src="https://stat.ameba.jp/user_images/pixel.png"><img data-src="http://stat.ameba.jp/user_images/photo.jpg?a=1&amp;b=2">'
        post = blog.parse_feed(feed([item(description=markup)]))[0]
        self.assertEqual(post['image'], "https://stat.ameba.jp/user_images/photo.jpg?a=1&b=2")

    def test_content_encoded_and_media_thumbnail(self):
        for contents in [
            '<content:encoded><![CDATA[<img src="https://stat100.ameba.jp/user_images/photo.jpg">]]></content:encoded>',
            '<media:thumbnail url="https://stat100.ameba.jp/user_images/photo.jpg"/>',
        ]:
            data = item().replace('</item>', contents + '</item>')
            self.assertEqual(blog.parse_feed(feed([data]))[0]['image'],
                             "https://stat100.ameba.jp/user_images/photo.jpg")

    def test_no_image_uses_local_logo_and_broken_image_has_fallback(self):
        rendered = blog.render_posts(blog.parse_feed(feed([item()])))
        self.assertIn('src="assets/logo-mark.png"', rendered)
        self.assertIn('this.onerror=null', rendered)

    def test_title_and_image_attributes_are_escaped(self):
        rendered = blog.render_posts(blog.parse_feed(feed([item(
            title='<script>alert("x")</script> & お知らせ',
            description='<img src="https://stat.ameba.jp/user_images/image.jpg?a=1&amp;b=2">',
        )])))
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertIn('?a=1&amp;b=2', rendered)

    def test_untrusted_urls_are_rejected(self):
        for url in ['javascript:alert(1)', 'https://ameblo.jp.evil.test/machinoeki-now/entry-1.html',
                    'https://ameblo.jp/other/entry-1.html', 'https://user@ameblo.jp/machinoeki-now/entry-1.html',
                    'https://ameblo.jp:999/machinoeki-now/entry-1.html']:
            self.assertIsNone(blog.safe_url(url))
        self.assertIsNone(blog.safe_url('https://stat.ameba.jp.evil.test/photo.jpg', image=True))

    def test_invalid_items_do_not_clear_previous_posts(self):
        with self.assertRaises(ValueError):
            blog.parse_feed(feed([item(url='https://other.example/entry-1.html')]))

    def test_html_wrong_blog_malformed_or_entities_fail(self):
        for raw in [b'<html>error</html>', b'<rss><channel>', feed(channel_link='https://ameblo.jp/other/'),
                    b'<!DOCTYPE rss [<!ENTITY x "test">]><rss><channel/></rss>', b'x' * (blog.MAX_FEED_BYTES + 1)]:
            with self.assertRaises((ValueError, blog.ET.ParseError)):
                blog.parse_feed(raw)

    def make_root(self, directory):
        root = Path(directory)
        template = 'HEADER\n' + blog.START + '\n' + blog.END + '\nFOOTER'
        for name in ['index.html', 'index.dc.html']:
            (root / name).write_text(template, encoding='utf-8')
        return root

    def test_render_is_idempotent_and_weekly_check_keeps_schedule_active(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            _, changed = blog.update_site(root, feed([item()]), NOW)
            self.assertEqual(len(changed), 3)
            self.assertEqual(blog.update_site(root, feed([item()]), NOW + timedelta(days=1))[1], [])
            self.assertEqual(blog.update_site(root, feed([item()]), NOW + timedelta(days=7))[1], ['data/blog.json'])
            self.assertTrue((root / 'index.html').read_text().startswith('HEADER\n'))
            self.assertTrue((root / 'index.html').read_text().endswith('\nFOOTER'))

    def test_missing_markers_or_bad_feed_leave_both_pages_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            blog.update_site(root, feed([item()]), NOW)
            for raw in [b'not xml', feed(channel_link='https://wrong.example/')]:
                before = {name: (root / name).read_bytes() for name in ['index.html', 'index.dc.html', 'data/blog.json']}
                with self.assertRaises((ValueError, blog.ET.ParseError)):
                    blog.update_site(root, raw, NOW)
                self.assertEqual(before, {name: (root / name).read_bytes() for name in before})
            (root / 'index.dc.html').write_text('no markers', encoding='utf-8')
            before = (root / 'index.html').read_bytes()
            with self.assertRaises(ValueError):
                blog.update_site(root, feed([item(2)]), NOW)
            self.assertEqual((root / 'index.html').read_bytes(), before)

    def test_fetch_failure_keep_on_error_leaves_site_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            before = (root / 'index.html').read_bytes()
            errors = io.StringIO()
            with patch('sys.argv', ['update_blog.py', '--root', directory, '--keep-on-error']), \
                 patch.object(blog, 'fetch_feed', side_effect=TimeoutError), redirect_stderr(errors):
                blog.main()
            self.assertEqual((root / 'index.html').read_bytes(), before)
            self.assertIn('keeping the published posts', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
