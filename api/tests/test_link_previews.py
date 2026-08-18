"""Link-preview (Open Graph) tags on a published portal.

A pasted GeoDeploy URL used to render as a bare card — title and domain, no image — because nothing
in the stack emitted og: tags at all. Crawlers do not run JavaScript, so the tags have to be in the
HTML as served; a Vue route or the portal runtime cannot supply them.

The absolute-URL problem is the interesting half: og:image and og:url MUST be absolute, and neither
a portal bundle nor the shipped UI knows the instance's domain when it is written (bundles are also
rebuilt by celery and by a restore, where there is no request to read a Host header from). So both
emit `__GEODEPLOY_ORIGIN__` and nginx rewrites it per request — see the `sub_filter` in
nginx/nginx.conf and ui/nginx.conf.
"""
import re

from geodeploy.services.portal_generator import _social_meta


def _tags(html: str) -> dict[str, str]:
    return dict(re.findall(r'<meta (?:property|name)="([^"]+)" content="([^"]*)">', html))


def test_the_card_is_addressed_to_this_portal():
    t = _tags(_social_meta("wetlands", "Nyala Wetlands", "Seasonal inundation, 2019-2024."))
    assert t["og:title"] == "Nyala Wetlands"
    assert t["og:description"] == "Seasonal inundation, 2019-2024."
    assert t["og:url"] == "__GEODEPLOY_ORIGIN__/portals/wetlands/"


def test_the_image_is_absolute_and_declared():
    """LinkedIn shows a large card only when it is told the image is large enough (>=1200x627), and
    it refuses a relative og:image outright."""
    t = _tags(_social_meta("s", "T", None))
    assert t["og:image"] == "__GEODEPLOY_ORIGIN__/og-image.png"
    assert (t["og:image:width"], t["og:image:height"]) == ("1200", "630")
    assert t["twitter:card"] == "summary_large_image"


def test_a_portal_with_no_description_still_says_something():
    assert _tags(_social_meta("s", "T", ""))["og:description"] == "A geoportal published with GeoDeploy."


def test_markdown_and_html_are_flattened_for_the_card():
    """The description is authored as markdown and rendered to HTML for the about page. A card is
    plain text: raw syntax there reads as a broken page, not as emphasis."""
    d = _social_meta("s", "T", "**Bogs** of the north.\n\nSee [the survey](https://x.org).")
    assert _tags(d)["og:description"] == "Bogs of the north. See the survey(https://x.org)."


def test_a_long_description_is_cut_to_a_card_length():
    t = _tags(_social_meta("s", "T", "word " * 200))
    assert len(t["og:description"]) <= 200


def test_a_quote_in_a_title_cannot_break_out_of_the_attribute():
    """Portal titles are user input and land inside content="…". Same reasoning as {{TITLE}}."""
    t = _tags(_social_meta("s", 'He said "go" <script>alert(1)</script>', None))
    assert "<script>" not in _social_meta("s", '<script>alert(1)</script>', None)
    assert t["og:title"].startswith("He said")


def test_the_tags_are_injected_into_a_head_that_carries_attributes():
    """Portals render through whichever layout.html their template ships, including one a user
    wrote — so the tags are injected after <head>, not substituted into a placeholder that only our
    own templates would contain."""
    html = '<!DOCTYPE html>\n<html>\n<HEAD lang="en">\n<title>x</title>\n</head>\n<body></body>'
    out = re.sub(r"<head\b[^>]*>", lambda m: m.group(0) + "\n" + _social_meta("s", "T", None),
                 html, count=1, flags=re.IGNORECASE)
    assert '<HEAD lang="en">' in out                     # the tag itself is preserved verbatim
    assert out.index("og:title") < out.index("<title>")  # ...and the card lands inside the head
