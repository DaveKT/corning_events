# Corning NY Event Aggregator: Source Specification

Version 1.0. Compiled 2026-07-24. All sources verified on that date unless a different date is stated.

This document is a handoff artifact. It specifies which sources to ingest, what each one exposes, how to reach it, and what is known to be broken about it. It does not prescribe an architecture.

---

## 1. Purpose and scope

Build a service that monitors multiple sources for public leisure events in and around Corning, New York, and surfaces them in one place.

**In scope:** festivals, concerts, live music, theatre, comedy, film screenings, museum programming, gallery openings, lectures and author talks, farmers markets, races and running events, craft fairs, family and children's programming, trivia and game nights, fundraisers open to the public, seasonal and holiday events.

**Out of scope:** municipal board and committee meetings, school district athletic schedules, private ticketed conferences, professional development and continuing education courses, and business networking unless open to the general public.

**Geographic anchor:** 42.1481, -77.0569 (Corning, NY).

**Deliverable.** The service publishes a public iCal feed that family members subscribe to on their phones. This is not an internal database with a UI. The output format is a hard constraint on the design. See section 9.

**Radius tiers.** Distances below are median great-circle miles from the anchor, measured from the geocoded records in the FLXcalendar capture rather than estimated.

| Ring | Radius | Places, with measured median distance |
|---|---|---|
| Core | 0 to 10 miles | Corning (0.6), Painted Post, Riverside, Gang Mills, Erwin, Big Flats (9.4), Horseheads (9.4) |
| Near | 10 to 25 miles | Elmira (13.2), Bath (18.7), Watkins Glen (18.8), Addison |
| Regional | 25 to 50 miles | Hammondsport (25.3), Seneca Lake (27.4), Hector (27.6), **Ithaca (34.8)**, Penn Yan (35.5), Trumansburg, Owego, Montour Falls, Hornell, Geneva (49.8) |
| Out | over 50 miles | Exclude by default |

**Ithaca is in scope.** It is the second-largest contributor to FLXcalendar (268 records tagged Ithaca, 316 tagged Tompkins County, against 253 tagged Corning) and it falls inside the 50 mile band, not outside it. Treat the Regional ring as included rather than opt-in. Geneva at 49.8 miles sits on the boundary and is the natural place to cut if volume becomes a problem.

---

## 2. How to read the source registry

Sources are ranked in four tiers by ingest cost, not by importance:

- **Tier A** returns machine-readable data. Parse it.
- **Tier B** requires HTML parsing but the markup is server-rendered and stable.
- **Tier C** is reachable but degraded: client-side rendering, stale pages, or no calendar at all.
- **Tier D** is news monitoring, not calendar ingest.
- **Tier E** has active programming and no scrapeable surface.

Every URL below was fetched or its documentation read on 2026-07-24. Where a platform is named, it was identified from page markup or URL structure, not inferred from appearance.

---

## 3. Tier A: structured feeds

### 3.1 FLXcalendar

The highest-value source in the registry. Fully specified in section 4.

| Field | Value |
|---|---|
| Export endpoint | `https://timelyapp.time.ly/api/calendars/48240494/export?format=xml` |
| Public calendar | `https://calendar.time.ly/o9b0uzpj` |
| Human-facing site | `https://www.flxcalendar.com/` |
| Newsletter | `flxcalendar.beehiiv.com` (weekly) |
| Platform | Timely (time.ly), xCal export via kigkonsult iCalcreator 2.26.9 |
| Format | RFC 6321 xCal, namespace `urn:ietf:params:xml:ns:icalendar-2.0` |
| Record count | 1,464 VEVENT records, 1,807 occurrences after RDATE expansion |
| Robots | **`timelyapp.time.ly` disallows automated access.** `calendar.time.ly` does not. See section 11 |

### 3.2 Southeast Steuben County Library

| Field | Value |
|---|---|
| Feed | `https://ssclibrary.org/?post_type=tribe_events&ical=1&eventDisplay=list` |
| Human page | `https://ssclibrary.org/calendar/` |
| Platform | WordPress with The Events Calendar (Modern Tribe) |
| Verified | Page modified 2026-07-21, 12 events in the following week |
| Notes | Confirmed from page markup (`tec-api-version` meta), not inferred. The library also programs offsite: Movie Night in Centerway Square, Storytime in the Park. Those are genuine public events and should not be filtered out as "library programming". The legacy path `/activities/events-search/` redirects |

### 3.3 Clemens Center (Elmira)

| Field | Value |
|---|---|
| Feed | `https://clemenscenter.org/?post_type=tribe_events&ical=1&eventDisplay=list` |
| Human page | `https://clemenscenter.org/events-calendar/` |
| Platform | WordPress with The Events Calendar |
| Verified | 35 upcoming events, 2026-27 Broadway season published |
| Notes | The region's main touring venue. Distance from Corning is roughly 20 miles, so it lands in the Near ring |

### 3.4 Ticketmaster Discovery API

| Field | Value |
|---|---|
| Endpoint | `https://app.ticketmaster.com/discovery/v2/events.json` |
| Query | `?latlong=42.1481,-77.0569&radius=25&unit=miles&apikey=KEY` |
| Auth | Free key issued immediately on registration at `developer.ticketmaster.com` |
| Rate limit | 5,000 requests per day |
| Notes | The only source with documented geo-radius search. Radius defaults to 25 miles if omitted. Covers ticketed concerts and touring shows only. Expect low volume for this market but high data quality |

### 3.5 Burbio

| Field | Value |
|---|---|
| URL | `https://www.burbio.com/states/New-York/Corning` |
| Format | iCal and Google Calendar subscription |
| Notes | Aggregates school district, library and community calendars. Overlaps the SSCL feed. Chiefly valuable for school district events, which no other source in this registry covers |

---

## 4. FLXcalendar detailed specification

A full export was captured on 2026-07-24 at 23:05:51Z (9.3 MB) and analyzed. Findings below are measured, not estimated.

### 4.1 Identity

Calendar-level properties confirm provenance:

```
X-WR-CALNAME   FLXcalendar
X-FROM-URL     https://calendar.time.ly/o9b0uzpj
X-WR-TIMEZONE  America/New_York
PRODID         -////NONSGML kigkonsult.se iCalcreator 2.26.9//
METHOD         PUBLISH
```

The calendar is human-curated by a single editor and funded by the Community Foundation of Elmira-Corning and the Finger Lakes. This has two consequences: coverage reflects editorial judgment rather than automated ingest, and polling behaviour should be considerate of a small operation. See section 11.

### 4.2 Property inventory

Counts are occurrences across 1,464 VEVENT records.

| Property | Count | Coverage | Notes |
|---|---|---|---|
| `uid` | 1464 | 100% | Format: UUID suffixed `-time.ly`. Stable primary key |
| `dtstamp` | 1464 | 100% | Identical across all records, equal to export time |
| `summary` | 1464 | 100% | 52 records contain the literal string `None` |
| `description` | 1464 | 100% | HTML, frequently pasted from Facebook with inline styles |
| `dtstart` / `dtend` | 1464 | 100% | `TZID=America/New_York` parameter present |
| `sequence` | 1464 | 100% | |
| `status` | 1464 | 100% | Always `CONFIRMED`. No cancellations represented |
| `url` | 1464 | 100% | Timely permalink |
| `x-original-url` | 1464 | 100% | Organizer's own URL. Use for provenance and dedupe |
| `x-cost-type` | 1464 | 100% | |
| `categories` | 1462 | 99.9% | Topical taxonomy |
| `x-tags` | 1455 | 99.4% | Geographic taxonomy. **Primary filter field** |
| `x-wp-images-url` | 1454 | 99.3% | |
| `location` | 790 | 54% | Format: `Venue Name @ Street Address` |
| `x-location` | 790 | 54% | |
| `geo` | 712 | 48.6% | Latitude and longitude child elements |
| `contact` | 631 | 43% | |
| `rdate` | 343 | n/a | Recurrence dates, not expanded into separate records |
| `x-featured-event` | 311 | 21% | Editorial promotion flag |
| `x-instant-event` | 216 | 15% | |
| `x-cost` | 85 | 5.8% | |
| `x-tickets-url` | 85 | 5.8% | |
| `rrule` | 20 | 1.4% | |
| `exdate` | 1 | 0.1% | |

### 4.3 Temporal coverage

Date span: 2026-04-01 to 2026-11-12.

| Month | Occurrences (DTSTART plus RDATE) |
|---|---|
| 2026-04 | 331 |
| 2026-05 | 359 |
| 2026-06 | 419 |
| 2026-07 | 386 |
| 2026-08 | 235 |
| 2026-09 | 43 |
| 2026-10 | 14 |
| 2026-11 | 9 |
| 2026-12 | 11 |

1,202 of 1,464 records have a start date in the past. Only 262 are future-dated.

**Interpretation:** the export is a rolling window of roughly four months backward and six weeks forward. Listings arrive close to the event date. Two design consequences follow. First, the feed is unsuitable as a source of truth for anything beyond about six weeks out, so long-lead events such as festivals and race weekends must come from Tier B venue sites. Second, most new information appears in the near-term window, so polling cadence matters more than backfill depth.

### 4.4 Geographic distribution

Of the 712 records carrying GEO coordinates, measured against the Corning anchor:

| Ring | Count |
|---|---|
| Within 10 miles | 116 |
| 10 to 25 miles | 129 |
| 25 to 50 miles | 54 |
| Over 50 miles | 52 |

Cumulative: 245 within 25 miles, 299 within 50.

Geographic `x-tags` across all records:

| Tag | Count |
|---|---|
| Steuben County | 396 |
| Tompkins County | 316 |
| Schuyler County | 279 |
| Ithaca | 268 |
| Chemung County | 254 |
| Corning | 253 |
| Elmira | 179 |
| Hector | 98 |
| Watkins Glen | 60 |
| Hammondsport | 54 |
| Geneva | 52 |
| Horseheads | 43 |
| Seneca County | 40 |
| Tioga County | 39 |
| Yates County | 38 |
| Penn Yan | 27 |
| Seneca Lake | 25 |
| Big Flats | 25 |
| Bath | 25 |
| Trumansburg | 22 |
| Owego | 22 |
| Montour Falls | 15 |

Records carry both a city tag and a county tag. As of the capture, 44 future-dated records were tagged Corning and 73 tagged Steuben County.

**Critical filtering note:** because GEO is present on only 48.6% of records, radius filtering alone silently discards more than half the calendar. Filter on `x-tags`, present on 99.4%, and use GEO only to refine.

### 4.5 Topical categories

| Category | Count | | Category | Count |
|---|---|---|---|---|
| Music | 780 | | History and Heritage | 62 |
| Performing Arts | 496 | | Games | 54 |
| Community | 263 | | Comedy | 52 |
| Beer | 241 | | Film | 51 |
| Family Fun | 215 | | Glass | 46 |
| Food | 163 | | Crafts | 45 |
| Connection | 125 | | Dancing | 37 |
| Art | 117 | | Competitions | 34 |
| Causes | 106 | | Sports | 33 |
| Wine | 89 | | LGBTQ+ | 30 |
| Festivals | 88 | | Sober-friendly | 29 |
| New to the Area | 79 | | Other | 16 |
| Nature | 75 | | Volunteering | 15 |
| Literature | 71 | | Farmers Market | 14 |
| Education | 71 | | Running | 14 |

`Sober-friendly`, `New to the Area` and `LGBTQ+` are editorial accessibility tags and are worth preserving as first-class filters rather than flattening into a generic tag bag.

### 4.6 Parsing notes

The xCal export parses with `xml.etree.ElementTree` from the standard library. No third-party dependency is required. This is a meaningful advantage over the `.ics` variant, which would need an external iCalendar parser.

Namespace: `urn:ietf:params:xml:ns:icalendar-2.0`. Property values are wrapped in a type element, so `dtstart` contains a `date-time` child, `summary` contains a `text` child, `geo` contains `latitude` and `longitude` children. `X-` properties wrap their value in an `unknown` element.

Use `iterparse` with `el.clear()`. The file is 9.3 MB and grows.

**Recurrence must be expanded.** 123 records are recurring, encoded as a single VEVENT carrying multiple `rdate` properties rather than as expanded instances. Ignoring RDATE loses 343 occurrences, roughly 19% of the calendar. Twenty records additionally carry `rrule`, and one carries `exdate`.

**Known data quality issues:**

1. 52 records have the summary `None`. Filter or flag them.
2. Descriptions contain raw HTML with Facebook-generated class names and inline styles. Strip to text before storage.
3. 46% of records lack LOCATION. Where present the format is `Venue Name @ Street Address` and splits cleanly on ` @ `.
4. `status` is `CONFIRMED` on every record. Cancelled events are presumed to be deleted rather than marked, so cancellation detection requires diffing consecutive pulls by UID.

---

## 5. Tier B: high-yield HTML sources

| Source | URL | Platform | Verified state | Ingest notes |
|---|---|---|---|---|
| Corning Area Chamber of Commerce | `https://www.corningny.com/events` | ChamberMaster / GrowthZone | 49 upcoming events | Accepts `?from=MM/DD/YYYY&to=MM/DD/YYYY&o=alpha` and `?rendermode=print`. The print mode is the cleaner parse target. Broadest single Corning source; republishes ARTS Council, CMoG, Rockwell and Farmers Market items |
| Corning Museum of Glass | `https://whatson.cmog.org/events-programs` | Drupal 10 | Events through 2026-09-07 | Highest event volume in the city. Also `/daily-schedule` and `/seasonal`. Related subdomains: `visit.cmog.org`, `glassmaking.cmog.org` |
| The Rockwell Museum | `https://rockwellmuseum.org/community-education/events/` | WordPress, custom post type | Modified 2026-07-09, events through 2026-11-07 | **Not** The Events Calendar, so no `ical=1` endpoint. Scrape `/events/{slug}` detail pages. Also monitor `/community-education/community-wide-events/` for citywide partner events |
| Corning's Gaffer District | `https://www.gafferdistrict.com/events/` | Simpleview CMS | Footer 2026 | Crawl the `/events/` subtree; the landing page is thin. Known child paths: `/events/glassfest/`, `/events/annual-events-festivals/harvest/`, `/events/summer-in-downtown-corning/`, `/shopping/farmers-market/`. Also maintains a business directory under `/listing/` |
| Explore Steuben (Steuben County CVB) | `https://exploresteuben.com/events/` | WordPress / Divi | Active | Canonical CVB domain. Publishes an e-newsletter usable as an email-ingest alternative |
| Finger Lakes Tourism Alliance | `https://www.fingerlakes.org/events` | Custom | Current | Filter to Corning and Steuben. Its partner directory is useful for discovering additional venue sites |
| Elmira Downtown Development | `https://www.elmiradowntown.com/events` | Static PHP | Programming published through 2026-09-11 | Alive After 5 concert series, Wisner Market Wednesdays, Street Painting Festival |
| Watkins Glen International | `https://www.theglen.com/calendar/` | NASCAR Digital Media | Updated within days of check | Carries non-race events: Bike Night, Motors and Music, car shows. `/events/` is an archive, not the live calendar |
| Tag's Summer Stage (Big Flats) | `https://tagstickets.com/events` | Custom ticketing | Active | The region's principal outdoor touring-act venue, roughly 12 miles out, operating over 30 years. `tagstickets.com` is the only official ticketing domain; ignore reseller listings. **Not present in FLXcalendar** |
| Moe-Town Music Venue (Addison) | `https://moe-town.com/` | Static PHP | 2026 shows confirmed | Event detail pages follow `post.php?pid=N`. Tribute bands, camping |
| Wineglass Race Series | `https://www.wineglassmarathon.com/` | WordPress | Expo 2026-10-02, 5K 10-03, Marathon and Half 10-04 | Long-lead event. Expo at CMoG, finish on Market Street. Drives ancillary downtown activity |
| Orchestra of the Southern Finger Lakes | `https://www.osfl.org/calendar` | Squarespace | 2026-27 season published | Performs at Clemens Center and CMoG. Library "Stories with Music" series in summer |
| Chemung County Historical Society | `https://chemungvalleymuseum.org/events/` | WordPress with an events calendar | Month views present | Probe for a Tribe iCal endpoint before writing a parser |
| Tanglewood Nature Center | `https://tanglewoodnaturecenter.wildapricot.org/` | Wild Apricot | Active | Event records live on the Wild Apricot instance, not the marketing site |
| Patch Corning NY | `https://patch.com/new-york/corning-ny/calendar` | Patch | Active | Submitted events with venue and time metadata |
| AllEvents.in Corning | `https://allevents.in/corning-ny/all` | AllEvents | Events into 2026-08 | Republishes a subset of Facebook events, partially covering the Facebook gap. Noisy; dedupe aggressively |
| Eventbrite Corning | `https://www.eventbrite.com/d/ny--corning/events/` | Eventbrite | Active | The public discovery API is org-scoped only. Scrape the location page |
| Bandsintown city pages | `https://bandsintown.com/c/corning-ny`, `https://bandsintown.com/c/elmira-ny` | Bandsintown | Active | Public pages, no API key needed. Chiefly valuable for **venue discovery**: these pages surfaced Tag's Summer Stage, Radisson Hotel Corning and Jr's Log Cabin as active venues |

---

## 6. Tier C: degraded or caveated

| Source | URL | Problem | Handling |
|---|---|---|---|
| ARTS Council of the Southern Finger Lakes | `https://www.earts.org/regional-arts-calendar/` | Events render client-side. A plain GET returns filter controls and no event data | Find the underlying JSON call before reaching for a headless browser. Worth the effort: best regional coverage of small venues, churches and galleries. Organization confirmed active |
| WENY Community Calendar | `https://www.weny.com/featured/community-calendar/` | Same. Site is live but listings load from an embedded widget | Inspect the widget's own endpoint |
| SUNY Corning Community College | `https://banner.corning-cc.edu/SelfServiceBannerGeneralEventManagement/ssb/events#!/eventList` | Banner Self Service single-page app. `/calendar/index.php` is an empty shell | Most campus content is out of scope anyway. The public-interest venues have their own pages: Digital Dome planetarium, Collins Observatory, Spencer Crest Nature Center, athletics at `redbaronsathletics.com` |
| Corning's Palace Theatre | `https://corningpalacetheatre.com/` | Showtimes on `index.php` lagged roughly three weeks at check. `/events.php` is an empty stub containing only two headings | Consider the ticketing system instead: `internet-ticketing.com/websales/sales/PALCOR` |
| 171 Cedar Arts Center | `https://171cedararts.org/` | No calendar exists. `/event-directory/` is a single event page despite the name | Watch the three Events nav pages plus the Mailchimp newsletter. Class registration is on `imperisoft.com`; the ceramics calendar is at `calabunga.com/public/4198` |
| Heritage Village of the Southern Finger Lakes | `https://heritagevillagesfl.org/events-at-heritage-village-in-corning/` | Page modified 2026-07-21 but the events body was empty | Low yield. Re-check seasonally. Returns zero records in FLXcalendar as well |
| City of Corning | `https://cityofcorningny.gov/calendar` | The legacy `cityofcorning.com` domain returns a robots.txt disallow | Use the `.gov` domain. Mostly meetings; the in-scope content is Parks and Recreation and Senior Center programming |
| Town of Corning | `https://townofcorningny.gov/` | Very low volume | Notices and board meetings, occasional community event |

---

## 7. Tier D and E

**Tier D, news monitoring.** These carry announcements that never reach a calendar. Treat as text feeds for keyword extraction, not as structured event sources.

| Source | URL |
|---|---|
| WETM MyTwinTiers | `https://www.mytwintiers.com/community/events/` |
| WYDC-TV | `https://www.wydc-tv.com/news/things_to_do/` |

**Tier E, Facebook-primary venues.** Active programming, no scrapeable calendar. Facebook has no public Events API and scraping violates its terms. Practical options are ingesting email notifications from followed pages, or relying on AllEvents.in and FLXcalendar, both of which pick up some of this material.

| Venue | Page |
|---|---|
| Iron Flamingo Brewery and Barrel House | `facebook.com/IronFlamingoBrewery` |
| Liquid Shoes Brewing | `facebook.com/liquidshoesbrewing`. The brewery's own `/events` page states that its calendar lives on Facebook |

---

## 8. Canonical data model

Recommended normalized record. Field availability varies widely by source, so most fields must be nullable.

| Field | Type | Notes |
|---|---|---|
| `event_id` | text | Internal surrogate key |
| `source_id` | text | Registry key, for example `flxcalendar`, `ssclibrary`, `chamber` |
| `source_uid` | text | Native identifier where the source provides one. FLXcalendar and both Tribe feeds do |
| `title` | text | Reject or flag the literal string `None` |
| `description` | text | Strip HTML before storage |
| `start` | timestamptz | |
| `end` | timestamptz | Nullable |
| `all_day` | bool | |
| `venue_name` | text | From FLXcalendar, split LOCATION on ` @ ` |
| `address` | text | |
| `lat`, `lon` | float | Present on under half of FLXcalendar records |
| `city_tag`, `county_tag` | text | FLXcalendar `x-tags`. The most reliable geographic signal available |
| `categories` | text[] | |
| `cost` | text | Free text. Do not attempt to parse to numeric |
| `ticket_url` | text | |
| `source_url` | text | Link back to the aggregator page |
| `original_url` | text | Organizer's own URL. FLXcalendar `x-original-url`. Best dedupe signal |
| `first_seen`, `last_seen` | timestamptz | Drives cancellation detection |
| `recurrence_parent_id` | text | For expanded RDATE and RRULE instances |

---

## 9. Output: public iCal feed

The service's product is a subscribable `.ics` file, or a small set of them, served over HTTPS and refreshed on a schedule. Family members subscribe once and the calendar appears alongside their personal calendars. Everything upstream exists to serve this.

### 9.1 Consequences for the pipeline

**Deduplication becomes user-visible.** A duplicate in a database is a data quality issue. A duplicate in a subscribed phone calendar is an annoyance that appears on four devices. The match cascade in section 10 is load-bearing, not optional.

**Volume control is mandatory.** The FLXcalendar capture alone holds 1,807 occurrences. A subscribed calendar carrying even the future portion of that across all sources renders a phone calendar unusable. Filter hard before emitting.

**Recommended feed variants** rather than one firehose. Each is a separate URL that a family member can subscribe to independently:

| Feed | Contents |
|---|---|
| `corning-core.ics` | Core and Near rings, all categories. The default subscription |
| `corning-family.ics` | Core and Near, filtered to Family Fun, Film, Festivals, Farmers Market, Nature |
| `flx-music.ics` | All rings, filtered to Music, Performing Arts, Comedy, Dancing |
| `flx-all.ics` | Everything inside 50 miles. For whoever wants the firehose |

Category filters map cleanly onto the FLXcalendar taxonomy in section 4.5 and can be approximated for other sources.

### 9.2 iCal generation requirements

**UID stability is the single most important detail.** Derive UIDs deterministically, for example a hash of `source_id` plus `source_uid`, and never regenerate them per run. A UID that changes between refreshes causes clients to treat every event as new: duplicates accumulate and alarms re-fire. Where a source supplies its own UID, as FLXcalendar and both Tribe feeds do, incorporate it rather than inventing one.

**SEQUENCE must increment** whenever any field of an already-published event changes. Clients ignore modifications that arrive without a SEQUENCE bump.

**Cancellations.** Do not silently drop events. When a UID disappears from an upstream feed while its start time is still in the future, emit the event with `STATUS:CANCELLED` and an incremented SEQUENCE, retain it for roughly 30 days, then remove it. Simple removal does propagate in most clients but leaves no trace for a subscriber who had already planned around it.

**Required calendar-level properties:**

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//<your identifier>//Corning Events Aggregator//EN
CALSCALE:GREGORIAN
METHOD:PUBLISH
X-WR-CALNAME:Corning Area Events
X-WR-TIMEZONE:America/New_York
REFRESH-INTERVAL;VALUE=DURATION:PT12H
X-PUBLISHED-TTL:PT12H
```

`REFRESH-INTERVAL` is honoured by Apple Calendar. `X-PUBLISHED-TTL` is the older Microsoft equivalent. Emit both; treat neither as reliable.

**Timezone handling.** Emit timed events as UTC with a trailing `Z` to avoid authoring a VTIMEZONE component, or emit `TZID=America/New_York` with a full VTIMEZONE block. Do not emit TZID without VTIMEZONE. All-day events must use `VALUE=DATE` with no time component, and DTEND is exclusive, meaning a single all-day event on the 5th has DTEND of the 6th.

**Per-event properties:** UID, DTSTAMP, DTSTART, DTEND, SUMMARY, DESCRIPTION, LOCATION, URL, GEO where available, CATEGORIES, SEQUENCE, STATUS.

**RFC 5545 formatting gotchas.** The standard library has no iCalendar writer, so these must be handled by hand:

- Lines fold at 75 octets, with continuation lines beginning with a single space. Long DESCRIPTION values will exceed this. Fold on octets, not characters, or multi-byte UTF-8 will break.
- In TEXT values, escape backslash, semicolon and comma with a backslash, and encode newlines as `\n`.
- Line endings are CRLF.
- Encoding is UTF-8.

Emitting invalid iCal tends to fail silently: the subscription appears to work and simply shows nothing. Validate output before publishing.

### 9.3 Client refresh behaviour

Refresh cadence is controlled by the client, not by the publisher.

- **Apple Calendar** exposes a user-selectable refresh interval per subscription and broadly respects `REFRESH-INTERVAL`. Subscribing via a `webcal://` URL gives one-tap subscription on iOS, so publish both `https://` and `webcal://` forms of each feed URL.
- **Google Calendar** refreshes external `.ics` subscriptions on its own schedule, commonly measured in many hours, and provides no way to force it. Family members on Android should expect lag. If someone needs prompt updates, subscribing in Apple Calendar or a third-party client is the practical answer.

Because clients poll on their own terms, regenerating the feed more than once or twice a day yields no benefit to subscribers.

### 9.4 Publication and access

The feed URL is public. An unlisted URL is obscurity, not access control: assume anything in the feed can be read by anyone who obtains the link. Nothing private should appear in DESCRIPTION or LOCATION fields.

Serve as a static file regenerated by a scheduled job. There is no need for a dynamic endpoint at this scale.

**Attribution.** Because the output redistributes other organizations' listings, include the originating source name and a link back in each event's DESCRIPTION, and populate URL with `original_url` where available. This is both courteous and practically useful: a subscriber who wants ticket details needs the source link on their phone.

---

## 10. Deduplication

**This is the primary engineering problem, not collection.**

A single CMoG glassblowing demonstration will arrive from the museum site, the Chamber calendar, Explore Steuben, Finger Lakes Tourism Alliance, FLXcalendar and AllEvents.in. Adding FLXcalendar increases the dedupe burden substantially because it overlaps the museum, library and Chamber feeds heavily while also contributing unique records.

Suggested match cascade, strongest signal first:

1. Exact match on `original_url` where both records have one.
2. Same normalized title, same start date, same venue.
3. Same start datetime, same venue, title similarity above a threshold. Titles vary in prefixes and suffixes across aggregators, so normalize by lowercasing, stripping punctuation, and removing leading venue names and trailing `@ Venue` fragments.
4. Same start datetime, coordinates within roughly 100 metres, title similarity above a threshold.

Recommended source priority for resolving conflicting field values, highest trust first: the venue's own site, then the Chamber, then FLXcalendar, then the tourism boards, then AllEvents.in and Eventbrite. Venue sites are authoritative on times and cancellations; aggregators lag.

---

## 11. Polling, change detection and etiquette

**Cadence.** Daily is sufficient for every source in this registry. Nothing in this market changes hourly. FLXcalendar's short forward horizon argues for daily rather than weekly, since new listings appear close to the event date.

**Change detection.** No source in the registry publishes cancellations. FLXcalendar sets `status` to `CONFIRMED` on all 1,464 records with no `CANCELLED` entries, which implies removal rather than marking. Detect cancellation by diffing consecutive pulls: a UID that disappears from the feed while its start date is still in the future is presumed cancelled. Do not delete such records outright; mark them and surface the ambiguity.

**Robots and terms.** Two hosts in this registry disallow automated access:

- `cityofcorning.com`, the legacy city domain. Use `cityofcorningny.gov` instead, which serves normally.
- `timelyapp.time.ly`, the Timely export host serving FLXcalendar. The public calendar host `calendar.time.ly` serves normally.

On the FLXcalendar question specifically: robots.txt is advisory metadata rather than access control, and a single daily GET against an iCal export is not crawling. The substantive considerations are Timely's platform terms of service, which govern regardless of robots.txt, and the fact that the underlying data is one curator's unpaid work funded by a community foundation. A further consideration applies here. The service output is a public iCal URL, so upstream listings are being republished rather than merely consumed. The audience is a handful of family members, which makes this small in practice, but an unlisted public URL is redistribution rather than private use, and that distinction is the one that matters when asking permission.

**Recommended action: use the contact form at `flxcalendar.com`, describe the project honestly as a small family calendar that republishes filtered listings, and ask.** A curated regional calendar funded to make events findable is unlikely to object, and may welcome it. An explicit yes removes the ambiguity entirely and may yield a supported feed. Per-event attribution and a link back, as specified in section 9.4, should be in place regardless of the answer. Until that answer arrives, the export captured on 2026-07-24 is sufficient for development.

**General politeness.** Identify the client with a descriptive User-Agent and a contact address. Back off on errors. Cache aggressively; most of these pages change once a day at most.

---

## 12. Known gaps and coverage risks

**Facebook remains the largest structural hole.** Several small Corning venues publish only there. FLXcalendar and AllEvents.in each recover part of this, but neither is complete.

**FLXcalendar does not cover everything.** Zero records for Heritage Village, Liquid Shoes Brewing, Four Fights Distilling, Market Street Brewing and Tag's Summer Stage. Only two records for Watkins Glen International, three for the Palace Theatre, six for Moe-Town. It complements the venue sites; it does not replace them.

**Long-lead events are underrepresented everywhere.** Festivals and race weekends are announced months ahead on venue and organizer sites but do not enter aggregator feeds until weeks before. Tier B sources carry this load.

**Venues surfaced by FLXcalendar that are absent from the rest of the registry**, and which may warrant their own monitoring if the feed becomes unavailable: Radisson Hotel Corning (30 records, regular live music), Brick at 1 West Pulteney Street (14), Carey's Brew House at 58 Bridge Street, VOLO at 74 East Market Street (salsa and bachata), Burgers and Beer (trivia), Corning Country Club, First Congregational UCC, The Office on Market Street, and the Science and Discovery Center at Nonnie Hood.

---

## 13. Explicitly excluded, with reasons

Do not re-add these without new evidence.

| Source | Reason |
|---|---|
| Market Street Brewing Co. | Closed February 2024 after 27 years. Sold to new owners February 2026 with a reopening announced but no date. Revisit if it reopens |
| National Soaring Museum | Site is live and the museum is active, but `soaringmuseum.org/events.php` still displays the 2025 calendar. Its Facebook carries current programming |
| Wings of Eagles Discovery Center | No public events calendar. Programming is STEM camps. Open Friday to Sunday only |
| Hands-on Glass Studio | Appointment-only sessions, no scheduled events. Site still references COVID protocols |
| The Cellar | Restaurant with no events calendar. Contact page mixes notices from 2022, 2025 and 2026 |
| Four Fights Distilling | Advertises event information but publishes no calendar |
| Community Foundation of Elmira-Corning and the Finger Lakes | Grantmaker, not an event publisher. Superseded by FLXcalendar, the calendar it funds |
| corningfingerlakes.com | Legacy domain for the same organization as Explore Steuben |
| Visit Finger Lakes (Ontario County) | Covers a region roughly 80 miles north |
| GST BOCES | Not the Corning-Painted Post school district. Burbio likely mirrors that district already |
| Steuben and Chemung county government calendars | Legislative and committee meetings only. Steuben reported no published events in the current month |
| Southern Tier Library System | Duplicates the SSCL feed and Burbio |
| The Leader | Paywalled, no structured event data |
| Songkick | Public API access restricted for years |
| Bandsintown API | Requires a partner key. The public city pages in Tier B are the accessible route |
| Facebook Events API, Instagram, Nextdoor | No public API. Scraping violates terms of service |
| Reddit | No dedicated Corning subreddit found. Regional subreddit activity could not be confirmed with the tools available. Worth a manual check of r/Elmira and r/FingerLakes before dismissing |

---

## 14. Implementation constraints

- Python. Standard library preferred where practical. The xCal export parses with `xml.etree.ElementTree`, requiring no third-party dependency; prefer `format=xml` over `.ics` for that reason.
- Tabular outputs as CSV, text outputs as markdown.
- No em-dashes and no emoji in generated text or documentation.

---

## 15. Open questions for the implementer

Two earlier questions are now settled and are reflected in the spec: Ithaca is in scope (section 1), and the deliverable is a public iCal feed for family subscription (section 9).

Remaining decisions, all of which the implementing model can reasonably make on its own:

1. **Backfill.** 1,202 of 1,464 FLXcalendar records are already past. Store them for history and pattern detection, or discard at ingest? Past events must not reach the published feed either way.
2. **Recurring series.** Store as one record plus a rule, or expand into individual occurrences at write time? Expansion is simpler to query, matches how the other feeds behave, and maps directly onto per-occurrence VEVENTs in the output.
3. **Feed granularity.** The four variants proposed in section 9.1 are a starting point, not a requirement. Fewer feeds with sharper filtering may serve better than more feeds.
4. **Alarms.** Whether to emit VALARM components. Default to omitting them: alarms on a shared subscribed calendar are intrusive, and subscribers can add their own.
5. **FLXcalendar permission.** Whether to contact the curator before production polling. See section 11.
