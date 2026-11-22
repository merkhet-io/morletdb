#!/usr/bin/env python3
"""Generate crawl files and validate metadata for the public HTML pages (stdlib only)."""
import argparse
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import sys
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'docs'
ORIGIN = 'https://morletdb.com'


class Metadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonicals = []
        self.descriptions = []
        self.social_urls = []
        self.titles = []
        self.in_title = False
        self.in_head = False

    def handle_starttag(self, tag, attrs):
        if tag == 'head':
            self.in_head = True
        if not self.in_head:
            return
        attrs = dict(attrs)
        if tag == 'title':
            self.in_title = True
            self.titles.append('')
        if tag == 'link' and 'canonical' in attrs.get('rel', '').split():
            self.canonicals.append(attrs.get('href', ''))
        if tag == 'meta' and attrs.get('name') == 'description':
            self.descriptions.append(attrs.get('content', ''))
        if tag == 'meta' and attrs.get('property') == 'og:url':
            self.social_urls.append(attrs.get('content', ''))

    def handle_endtag(self, tag):
        if tag == 'head':
            self.in_head = False
        if tag == 'title':
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.titles[-1] += data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    errors, titles, descriptions, urls = [], [], [], []
    for path in sorted(SITE.rglob('*.html')):
        rel = path.relative_to(SITE).as_posix()
        url = ORIGIN + '/' + (rel[:-10] if rel.endswith('index.html') else rel)
        metadata = Metadata()
        metadata.feed(path.read_text())
        if metadata.canonicals != [url]:
            errors.append(f'{rel}: expected one canonical URL: {url}')
        if metadata.social_urls != [url]:
            errors.append(f'{rel}: expected one og:url: {url}')
        for name, values, collected in [('title', metadata.titles, titles),
                                         ('description', metadata.descriptions, descriptions)]:
            if len(values) != 1 or not values[0].strip():
                errors.append(f'{rel}: expected one nonempty {name}')
            else:
                collected.append(values[0].strip())
        urls.append(url)
    for name, values in [('title', titles), ('description', descriptions)]:
        for value, count in Counter(values).items():
            if count > 1:
                errors.append(f'duplicate {name}: {value}')
    if (SITE / 'CNAME').read_text().strip() != 'morletdb.com':
        errors.append('CNAME does not match SEO origin')
    if not urls:
        errors.append('no public HTML pages found')
    tree = ET.Element('urlset', xmlns='http://www.sitemaps.org/schemas/sitemap/0.9')
    for url in urls:
        ET.SubElement(ET.SubElement(tree, 'url'), 'loc').text = url
    ET.indent(tree, space='  ')
    outputs = {
        'sitemap.xml': '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tree, encoding='unicode') + '\n',
        'robots.txt': f'User-agent: *\nAllow: /\n\nSitemap: {ORIGIN}/sitemap.xml\n',
    }
    if not errors:
        for name, expected in outputs.items():
            path = SITE / name
            if args.check:
                if not path.exists() or path.read_text() != expected:
                    errors.append(f'{name} is stale: run python3 tools/site_seo.py')
            else:
                path.write_text(expected)
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print(f'SEO: {len(urls)} pages, canonical/social URLs, unique metadata and crawl files OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
