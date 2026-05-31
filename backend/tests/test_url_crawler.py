"""Tests for the recursive/sitemap crawl helpers (URLExtractor)."""

from app.services.processing.extractors import URLExtractor


def test_parse_sitemap_locs():
    xml = (
        "<urlset><url><loc>https://a.com/1</loc></url>"
        "<url><loc>  https://a.com/2  </loc></url></urlset>"
    )
    assert URLExtractor._parse_sitemap_locs(xml) == ["https://a.com/1", "https://a.com/2"]


def test_parse_sitemap_empty():
    assert URLExtractor._parse_sitemap_locs("<urlset></urlset>") == []


def test_same_domain_links_resolves_and_filters():
    links = [
        "/page2",                      # relative -> same domain
        "https://a.com/page3",         # absolute same domain
        "https://evil.com/x",          # other domain -> excluded
        "mailto:x@y.com",              # excluded
        "javascript:void(0)",          # excluded
        "#frag",                       # excluded
        "page4.html",                  # relative to current dir
        None,                          # excluded
    ]
    out = URLExtractor._same_domain_links("https://a.com/dir/", links, "a.com")

    assert "https://a.com/page2" in out
    assert "https://a.com/page3" in out
    assert "https://a.com/dir/page4.html" in out
    assert all("evil.com" not in u for u in out)
    assert all(u.startswith("http") for u in out)


def test_same_domain_links_strips_fragments():
    out = URLExtractor._same_domain_links("https://a.com/", ["https://a.com/p#section"], "a.com")
    assert out == ["https://a.com/p"]
