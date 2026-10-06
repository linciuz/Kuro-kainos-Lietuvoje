# Fuelis search visibility audit — 2026-10-06

Baseline source commit: `5d9996493449e334ead9aeb1af07dbb39eaa800f`.
The sanitized public HTTP snapshot is `live-baseline.json`, completed at
**2026-10-06T16:34:05Z**. It stores response hashes and indexing metadata;
no raw HTML, scripts, cookies or verification tokens are retained.

## Verified public baseline

| Check | Observed result |
|---|---|
| Sitemap | 64 unique URLs: homepage, directory, open-data page, privacy page and 60 municipality pages |
| Live GET of every sitemap URL | **64/64 HTTP200**, without login |
| Canonical and indexing directives | 64/64 have one self-canonical; no meta `noindex` or `X-Robots-Tag` observed |
| Basic HTML metadata | One title, H1 and description per URL; zero JSON-LD parsing errors |
| Discovery through static `<a href>` links | **64/64 reachable** from the homepage, including through the municipality directory |
| [robots.txt](https://fuelis.lt/robots.txt) | HTTP200, `User-agent: *` allows `/`, declares the sitemap |
| Host consolidation | HTTP, `www` and the old GitHub Pages host each return one HTTP301 to `https://fuelis.lt/`, then HTTP200 |
| Other URL behavior | `/index.html` returns200 with root canonical; a deliberately missing page returns404 |
| Public DNS verification | Google domain-verification TXT is present; its value was not retained |

These public checks found **no blanket technical crawl/indexing blocker**. They
do not establish how an authenticated Google crawler sees the site, whether
Google has indexed a URL, which canonical Google selected, or why a query ranks
poorly. Syntax-valid JSON-LD also does not establish rich-result eligibility.

## Google visibility and Search Console limits

The parent agent checked actual Google result pages in the in-app browser,
signed out, with Lithuania/Vilnius context at approximately16:32UTC. Fuelis was
absent from the observed first result pages for `kuro kainos` and
`kuro kainu paieska`. A `site:fuelis.lt` result page did not expose a Fuelis result
either. This is a dated, limited visibility observation, **not proof that the
site is absent from Google's index** or a complete rank measurement.

At the16:34UTC baseline, Search Console index status, query impressions/clicks, selected canonical,
last crawl, manual actions, security issues and sitemap processing remain
**unverified**. Public DNS verification does not prove that this session has
access to the property. No Search Console connector or callable authenticated
Search Console client was found in the repository/tool inventory; the parent
opened the Search Console reports URL in the browser, which redirected to the
signed-out `/about` page. The initial in-app browser session therefore had no
verified authenticated Search Console property access. No credentials were read or provider settings
changed. No Google indexing request or submission is claimed.

Subsequently, the authorized Chrome session verified an indexed homepage, the
correct canonical, and no manual actions or security issues. Detailed Search
Console telemetry is retained only locally, outside the public repository.

`scripts/ping_indexnow.py` records an **August5 historical** observation of five
indexed URLs and nine excluded URLs, including a Kaunas page without a referring
sitemap/page. The baseline `scripts/build_opendata.py` also asserted zero inbound
links in that old observation; the unsupported claim has been removed from its
opening documentation in this change. Neither supplies a current export or
proves today's cause.
IndexNow is a separate search-engine submission flow, not a Search Console client
or Google's general indexing mechanism. The claim that this is currently a
crawl-budget problem is not verified.

## Reviewed implementation in the SEO worktree

The homepage now has a visible H1, concise search explanation and permanent
Lithuanian guide with direct links to six city price pages and the municipality
directory. Its dated national price block remains visible after JavaScript
rendering, independently of interactive filters. These additions are marked
`lang="lt"`. Titles/descriptions describe fuel prices and station search;
unused meta keywords were removed. Existing crawlable footer links, canonical
URLs, robots and host redirects did not require changes.

The national directory and municipality generators now provide distinct
petrol95, diesel and LPG comparisons. Each available fuel has its own cheapest
three records, source/time context and link to the corresponding interactive
filter. This includes stations that publish only LPG. District pages describe
the whole district rather than only its administrative centre; missing quotes
are excluded from arithmetic and identified explicitly. Nearby comparisons
label their distance as an approximate straight line between dataset station
centres, not a driving distance or distance from the reader.

Source explanations distinguish the LEA price date, individual row timestamps,
marked newer operator quotes and API generation time. The API documentation's
sample cheapest record comes from the actual input data. Display corrections
require their attribution fields; source addresses, municipality keys and price
values are preserved. Documentation offers Fuelis's compiled dataset without
claiming to relicense underlying LEA/operator data.

The baseline station schema incorrectly put a district label such as
`Kauno rajonas` in `PostalAddress.addressLocality` for premises in Ramučiai or
Garliava. The generator now emits the attributed municipality as `addressRegion`
and omits the unsupported locality. Fuel-specific ItemLists describe the
visible records. No fabricated reviews, offers or rich-result eligibility are
claimed. The service-worker cache version is bumped to deliver the frontend
changes.

Independent offline review ran the nine `tests/test_seo_pages.py` cases against
the recorded real 2026-10-06 fixture: all passed. They cover per-fuel minima,
counts, source/timestamp presentation, region scope, schema locality, map links,
homepage markers, real API examples and preservation of prices/raw identity.
RSS omits optional publication/build dates when their actual times are unknown.
The review caught and resolved a build-clock-only `lastBuildDate` that would
defeat the existing commit-noise gate: the final renderer emits neither that
field nor an assumed item `pubDate`. The updated regression passes and verifies
identical feed XML when only the wall clock advances by15minutes.

This is a content, usability and metadata improvement, not a diagnosed Google
penalty or proof of improved ranking. The unchanged live baseline above is
separate from the worktree implementation. Publication, current indexing and
subsequent query visibility require later evidence.

## Official Google references checked

- [Technical requirements](https://developers.google.com/search/docs/essentials/technical): public access, successful response and indexable content establish eligibility; indexing is not guaranteed.
- [URL Inspection](https://support.google.com/webmasters/answer/9012289): indexed data establishes current index status and Google-selected canonical; the live test cannot predict canonical selection or guarantee indexing.
- [Sitemap guidance](https://developers.google.com/search/docs/crawling-indexing/sitemaps/overview) and [crawlable links](https://developers.google.com/search/docs/crawling-indexing/links-crawlable): discovery signals are useful but do not guarantee crawling/indexing.
- [Title guidance](https://developers.google.com/search/docs/appearance/title-link) and [SEO starter guide](https://developers.google.com/search/docs/fundamentals/seo-starter-guide): clear descriptive text helps; Google ignores meta keywords.
- [JavaScript SEO](https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics) and [structured-data policies](https://developers.google.com/search/docs/appearance/structured-data/sd-policies): inspect rendered content and describe real, relevant information accurately.
- [Spam policies](https://developers.google.com/search/docs/essentials/spam-policies): accessibility-only screen-reader text is not inherently hidden-text abuse; fabricated or repetitive ranking content is inappropriate.
- [Crawl-budget guidance](https://developers.google.com/crawling/docs/crawl-budget): advanced guidance primarily concerns large/rapidly changing sites or confirmed discovery backlogs; this64URL public audit does not diagnose such a backlog.
- [Indexing API scope](https://developers.google.com/search/apis/indexing-api/v3/quickstart): restricted to eligible job-posting/livestream pages; ordinary fuel-price pages are outside that scope.

## Verification before publication

The complete offline suite passed **48 tests**, including nine real-data SEO
regressions. JavaScript syntax and diff whitespace checks passed. The regenerated
HTML passed **64/64** self-canonical/title/H1/description/JSON-LD checks and all
64 pages remain reachable through static links. Details and page hashes are in
`prepublish-validation.json`.

The source station document is unchanged from the baseline commit: **809 registry
rows and 757 priced API rows**. API station identities, prices, timestamps,
national statistics, cheapest records and historical price values also match
the baseline. Only explanatory API metadata changed.

Browser checks covered the homepage at 360px, 390px and desktop width, the
Klaipėda comparison page on mobile, the EMSI address search, fuel list/map views,
EV results, language switching and keyboard skip-link focus. No page-wide
horizontal overflow was observed at the tested mobile widths. The dated
national snapshot persists across app renders. A local preview CSP blocked
external counter/report writes during these tests.

RSS omits optional publication/build clocks that the price snapshots cannot
establish. A regression checks identical RSS bytes when the wall clock changes
but the source data does not; this preserves the existing suppression of
unnecessary data commits and Pages builds. Google indexing and query rank remain
unverified until authenticated Search Console evidence is available.

This audit author edited only the audit documentation and sanitized baseline
artifact. Other contributors implemented the reviewed app/generator changes.
Final build, browser verification and publication evidence is appended by the
parent agent after completion.
