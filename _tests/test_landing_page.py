"""Structural checks for the static site. Standard library only.

Run from anywhere:  python3 _tests/test_landing_page.py
"""

import hashlib
import re
import struct
import unittest
import xml.etree.ElementTree as ET
from fnmatch import fnmatch
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://shreyas0786.github.io/tambola-adda-web/"
PAGES = ("index.html", "privacy/index.html", "support/index.html")

# Anchors other pages, the app and support replies may link to.
PRIVACY_ANCHORS = {
    "who", "stored", "speech", "network", "backups", "deleting", "support",
    "website", "sharing", "children", "changes", "contact",
}
SUPPORT_ANCHORS = {"faq", "privacy"}


class Page(HTMLParser):
    def __init__(self, rel_path):
        super().__init__(convert_charrefs=True)
        self.rel_path = rel_path
        self.ids = set()
        self.links = []  # (tag, attr, value)
        self.meta = {}
        self.canonical = None
        self.imgs = []
        self.h1_count = 0
        self.title = ""
        self._in_title = False
        self.tags = []  # (tag, attrs) in document order
        self.feed((ROOT / rel_path).read_text(encoding="utf-8"))

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a))
        if "id" in a:
            self.ids.add(a["id"])
        for attr in ("href", "src"):
            if a.get(attr) is not None:
                self.links.append((tag, attr, a[attr]))
        if tag == "meta":
            key = a.get("property") or a.get("name")
            if key:
                self.meta[key] = a.get("content", "")
        if tag == "link" and a.get("rel") == "canonical":
            self.canonical = a.get("href")
        if tag == "img":
            self.imgs.append(a)
        if tag == "h1":
            self.h1_count += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def page_url(rel_path):
    """Public URL of a local page file under the project base."""
    return urljoin(BASE, rel_path[: -len("index.html")] if rel_path.endswith("index.html") else rel_path)


def local_file_for(url):
    """Map a URL under the project base to a local file, or None if outside it."""
    if not url.startswith(BASE):
        return None
    path = urlsplit(url).path[len(urlsplit(BASE).path):]
    target = ROOT / path
    if path == "" or path.endswith("/"):
        target = target / "index.html"
    return target


def image_size(path):
    """Intrinsic (width, height) of a PNG, WebP or SVG file."""
    data = path.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8 ":
            w, h = struct.unpack("<HH", data[26:30])
            return w & 0x3FFF, h & 0x3FFF
        if chunk == b"VP8L":
            bits = int.from_bytes(data[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b"VP8X":
            return (int.from_bytes(data[24:27], "little") + 1,
                    int.from_bytes(data[27:30], "little") + 1)
    if path.suffix == ".svg":
        root = ET.fromstring(data)
        return float(root.get("width")), float(root.get("height"))
    raise ValueError(f"unsupported image: {path}")


class LinksAndFragments(unittest.TestCase):
    def test_local_links_images_and_fragments_resolve(self):
        pages = {p: Page(p) for p in PAGES}
        for rel, page in pages.items():
            for tag, attr, value in page.links:
                with self.subTest(page=rel, ref=value):
                    if value.startswith(("mailto:",)):
                        continue
                    self.assertFalse(value.startswith("/"),
                                     "root-absolute paths break under the project base")
                    url = urljoin(page_url(rel), value)
                    if not url.startswith(BASE):
                        continue  # external link, e.g. GitHub's privacy statement
                    target = local_file_for(url.split("#")[0])
                    self.assertTrue(target.is_file(), f"{value} -> {target} missing")
                    fragment = urlsplit(url).fragment
                    if fragment:
                        rel_target = target.relative_to(ROOT).as_posix()
                        target_page = pages.get(rel_target) or Page(rel_target)
                        self.assertIn(fragment, target_page.ids)

    def test_homepage_loads_no_third_party_resources(self):
        for tag, attr, value in Page("index.html").links:
            if tag in ("img", "script") or (tag == "link" and attr == "href"):
                url = urljoin(BASE, value)
                with self.subTest(ref=value):
                    self.assertTrue(url.startswith(BASE), f"external resource: {value}")


class Metadata(unittest.TestCase):
    def setUp(self):
        self.page = Page("index.html")

    def test_canonical_and_social_urls_use_project_base(self):
        self.assertEqual(self.page.canonical, BASE)
        self.assertEqual(self.page.meta.get("og:url"), BASE)
        image = self.page.meta.get("og:image", "")
        self.assertTrue(image.startswith(BASE))
        self.assertTrue(local_file_for(image).is_file())

    def test_social_image_dimensions_match_file(self):
        w, h = image_size(local_file_for(self.page.meta["og:image"]))
        self.assertEqual(int(self.page.meta["og:image:width"]), w)
        self.assertEqual(int(self.page.meta["og:image:height"]), h)
        self.assertTrue(self.page.meta.get("og:image:alt"))

    def test_required_metadata_present(self):
        self.assertTrue(self.page.title.strip())
        for key in ("description", "og:title", "og:description", "og:type", "twitter:card"):
            with self.subTest(key=key):
                self.assertTrue(self.page.meta.get(key))
        self.assertEqual(self.page.h1_count, 1)


class Sitemap(unittest.TestCase):
    def test_sitemap_lists_the_three_pages(self):
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        root = ET.parse(ROOT / "sitemap.xml").getroot()
        locs = [el.text.strip() for el in root.findall("sm:url/sm:loc", ns)]
        expected = {page_url(p) for p in PAGES}
        self.assertEqual(sorted(locs), sorted(expected))
        for loc in locs:
            self.assertTrue(local_file_for(loc).is_file(), loc)


class Images(unittest.TestCase):
    def test_declared_dimensions_match_files(self):
        for img in Page("index.html").imgs:
            path = ROOT / img["src"]
            with self.subTest(src=img["src"]):
                self.assertIn("alt", img)
                w, h = image_size(path)
                dw, dh = int(img["width"]), int(img["height"])
                if img["src"].startswith("assets/screenshots/"):
                    self.assertEqual((dw, dh), (w, h))
                else:  # branding shown at display size; aspect must hold
                    self.assertAlmostEqual(dw / dh, w / h, delta=0.01 * (w / h))


class ProtectedPages(unittest.TestCase):
    def test_privacy_and_support_keep_their_anchors(self):
        self.assertLessEqual(PRIVACY_ANCHORS, Page("privacy/index.html").ids)
        self.assertLessEqual(SUPPORT_ANCHORS, Page("support/index.html").ids)

    def test_privacy_and_support_link_home_and_share_styles(self):
        for rel in ("privacy/index.html", "support/index.html"):
            values = {v for _, _, v in Page(rel).links}
            with self.subTest(page=rel):
                self.assertIn("../", values)
                self.assertIn("../styles.css", values)


def csp_metas(page):
    return [a for t, a in page.tags
            if t == "meta" and a.get("http-equiv", "").lower() == "content-security-policy"]


def parse_csp(policy):
    directives = {}
    for part in policy.split(";"):
        tokens = part.split()
        if tokens:
            directives[tokens[0].lower()] = tokens[1:]
    return directives


class SecurityMetadata(unittest.TestCase):
    RESOURCE_TAGS = {"link", "img", "script", "style", "iframe", "object", "embed", "video", "audio", "source"}

    def test_single_csp_meta_precedes_resource_loads(self):
        for rel in PAGES:
            page = Page(rel)
            with self.subTest(page=rel):
                metas = csp_metas(page)
                self.assertEqual(len(metas), 1)
                order = [t for t, _ in page.tags]
                csp_index = next(i for i, (t, a) in enumerate(page.tags) if a is metas[0])
                charset_index = next(i for i, (t, a) in enumerate(page.tags) if t == "meta" and "charset" in a)
                first_resource = min((i for i, t in enumerate(order) if t in self.RESOURCE_TAGS), default=len(order))
                self.assertLess(charset_index, csp_index)
                self.assertLess(csp_index, first_resource)
                self.assertLess(csp_index, order.index("body"))

    def test_csp_blocks_active_content_and_allows_local_assets(self):
        blocked = ("script-src", "connect-src", "object-src", "form-action", "frame-src", "base-uri")
        for rel in PAGES:
            page = Page(rel)
            policy = csp_metas(page)[0]["content"]
            d = parse_csp(policy)
            with self.subTest(page=rel):
                self.assertEqual(d.get("default-src"), ["'none'"])
                for name in blocked:
                    self.assertEqual(d.get(name), ["'none'"], name)
                self.assertEqual(d.get("style-src"), ["'self'"])
                self.assertEqual(d.get("img-src"), ["'self'"])
                for loose in ("'unsafe-inline'", "'unsafe-eval'", "*", "http:", "https:", "data:"):
                    self.assertNotIn(loose, policy.split())
                # Nothing on the page needs what the policy blocks.
                for tag, a in page.tags:
                    self.assertNotIn(tag, {"script", "style", "form", "iframe", "object", "embed"})
                    self.assertNotIn("style", a, f"inline style on <{tag}>")
                for tag, attr, value in page.links:
                    if tag in ("img", "link"):
                        self.assertTrue(urljoin(page_url(rel), value).startswith(BASE), value)

    def test_referrer_policy(self):
        for rel in PAGES:
            metas = [a for t, a in Page(rel).tags if t == "meta" and a.get("name") == "referrer"]
            with self.subTest(page=rel):
                self.assertEqual([m.get("content") for m in metas], ["no-referrer"])


APP_STORE_URL = "https://apps.apple.com/app/id6824725587"
BADGE_SRC = "assets/app-store/download-on-the-app-store.svg"
QR_SRC = "assets/app-store/app-store-qr.svg"
# Apple's official black "Download on the App Store" badge (US-UK), as served at
# https://developer.apple.com/assets/elements/badges/download-on-the-app-store.svg
BADGE_SHA256 = "a26fc5b38380272c92e9019a2eb8b45542a66814b3e2b203772db8904b9fb99f"


def visible_text(rel_path):
    """Whitespace-normalised text of a page, without tags."""
    class Text(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

    parser = Text()
    parser.feed((ROOT / rel_path).read_text(encoding="utf-8"))
    return " ".join(" ".join(parser.parts).split())


class AppStoreDownload(unittest.TestCase):
    def setUp(self):
        self.page = Page("index.html")
        self.tags = self.page.tags

    def test_every_store_link_uses_the_exact_url(self):
        hrefs = [a["href"] for t, a in self.tags if t == "a" and "apple.com" in a.get("href", "")]
        self.assertGreaterEqual(len(hrefs), 2, "badge and FAQ text link")
        for href in hrefs:
            self.assertEqual(href, APP_STORE_URL)

    def test_single_official_badge_is_the_first_action_before_the_screenshot(self):
        badges = [i for i, (t, a) in enumerate(self.tags) if t == "img" and a.get("src") == BADGE_SRC]
        self.assertEqual(len(badges), 1, "one App Store badge per layout")
        img = self.tags[badges[0]][1]
        self.assertEqual(img.get("alt"), "Download on the App Store")
        self.assertEqual(img.get("height"), "48")
        link_tag, link = self.tags[badges[0] - 1]
        self.assertEqual((link_tag, link.get("href")), ("a", APP_STORE_URL))
        self.assertNotIn("button", link.get("class", "").split(), "animated button class")
        secondary = next(i for i, (t, a) in enumerate(self.tags)
                         if t == "a" and a.get("href") == "#how" and "button" in a.get("class", "").split())
        screenshot = next(i for i, (t, a) in enumerate(self.tags)
                          if t == "img" and a.get("src") == "assets/screenshots/web/game-light.webp")
        self.assertLess(badges[0], secondary, "badge is the primary action")
        self.assertLess(secondary, screenshot, "actions precede the screenshot")

    def test_availability_copy_and_trademark_notice(self):
        text = visible_text("index.html")
        self.assertNotIn("coming soon", text.lower())
        self.assertIn("Now available on iPhone.", text)
        self.assertIn("Where can I get Tambola Adda?", text)
        self.assertEqual(text.count("Scan with your iPhone to download."), 1)
        notice = ("Apple, the Apple logo, iPhone and App Store are trademarks of Apple Inc., "
                  "registered in the U.S. and other countries.")
        self.assertEqual(text.count(notice), 1)

    def test_badge_file_is_apples_artwork_unmodified(self):
        digest = hashlib.sha256((ROOT / BADGE_SRC).read_bytes()).hexdigest()
        self.assertEqual(digest, BADGE_SHA256)

    def test_qr_is_static_local_and_keeps_its_quiet_zone(self):
        img = next(a for t, a in self.tags if t == "img" and a.get("src") == QR_SRC)
        self.assertTrue(img.get("alt"))
        self.assertEqual(img.get("width"), img.get("height"))
        data = (ROOT / QR_SRC).read_text(encoding="utf-8")
        for banned in ("href", "<script", "<image", "<style", "url(", "http://", "https://"):
            self.assertNotIn(banned, data.replace('xmlns="http://www.w3.org/2000/svg"', ""))
        root = ET.fromstring(data)
        svg = "{http://www.w3.org/2000/svg}"
        self.assertEqual(root.get("viewBox"), "0 0 37 37")
        self.assertEqual([el.tag for el in root], [svg + "rect", svg + "path"])
        rect, path = list(root)
        self.assertEqual((rect.get("width"), rect.get("height"), rect.get("fill")), ("37", "37", "#fff"))
        self.assertEqual(path.get("fill"), "#000")
        # Dark modules are 1x1 squares; none may enter the four-module quiet zone.
        modules = [tuple(map(int, m)) for m in re.findall(r"M(\d+) (\d+)h1v1h-1z", path.get("d"))]
        self.assertTrue(modules)
        self.assertEqual("".join(f"M{x} {y}h1v1h-1z" for x, y in modules), path.get("d"))
        for x, y in modules:
            self.assertTrue(4 <= x < 33 and 4 <= y < 33, (x, y))


def jekyll_excludes():
    """Read the flat `exclude:` list from _config.yml (the only key it may hold)."""
    keys, excludes, current = [], [], None
    for raw in (ROOT / "_config.yml").read_text(encoding="utf-8").splitlines():
        line = raw.split(" #")[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line:
            continue
        if not line.startswith((" ", "-")):
            current = line.rstrip(":").strip()
            keys.append(current)
        elif current == "exclude" and line.strip().startswith("- "):
            excludes.append(line.strip()[2:].strip().strip("\"'"))
    return keys, excludes


def published(rel_path, excludes):
    """Approximate Jekyll 3.10's EntryFilter: special leading characters,
    exact/glob match, or a path-prefix match against an exclude entry."""
    parts = rel_path.split("/")
    if any(p[:1] in (".", "_", "#", "~") or p.endswith("~") for p in parts):
        return False
    for i in range(1, len(parts) + 1):
        entry = "/".join(parts[:i])
        for exp in excludes:
            if entry == exp.rstrip("/") or entry.startswith(exp) or fnmatch(entry, exp):
                return False
    return True


class Publishing(unittest.TestCase):
    def test_exclusions_hide_internal_files_but_keep_site_assets(self):
        keys, excludes = jekyll_excludes()
        self.assertEqual(keys, ["exclude"], "no theme, plugins or other settings")
        # GitHub Pages defaults (Jekyll 3.10 list plus CNAME) must survive the override.
        for default in ("Gemfile", "Gemfile.lock", "node_modules", "vendor/bundle/",
                        "vendor/cache/", "vendor/gems/", "vendor/ruby/", "CNAME"):
            self.assertIn(default, excludes)
        hidden = ["AGENTS.md", "_tests/test_landing_page.py"] + [
            p.relative_to(ROOT).as_posix()
            for folder in ("1206x2622", "1320x2868")
            for p in (ROOT / "assets/screenshots" / folder).glob("*.png")]
        for rel in hidden:
            with self.subTest(hidden=rel):
                self.assertFalse(published(rel, excludes))
        required = {"sitemap.xml", "styles.css", "home.css"}
        for rel in PAGES:
            page = Page(rel)
            required.add(rel)
            for tag, attr, value in page.links:
                url = urljoin(page_url(rel), value.split("#")[0])
                target = local_file_for(url)
                if target is not None:
                    required.add(target.relative_to(ROOT).as_posix())
        self.assertTrue(any(r.startswith("assets/screenshots/web/") for r in required))
        for rel in sorted(required):
            with self.subTest(required=rel):
                self.assertTrue(published(rel, excludes))


if __name__ == "__main__":
    unittest.main(verbosity=2)
