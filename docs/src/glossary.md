# Definitions

The terms the census uses, on the dashboard and in its data. The
[methodology](https://github.com/lhzn-io/csb-census/blob/main/docs/src/methodology.md)
gives the exact rules.

## What is counted

### Sounding

One depth measurement: a position, a depth and a time, logged by a vessel's
echosounder. It is one row of the archive.

### Platform

One `UNIQUE_ID` in the archive: the anonymous ID a Trusted Node issues to each
vessel it registers, shaped `<node prefix>-<UUID>`. It is the only vessel
identifier the archive carries.

A platform is usually one vessel, but not always. A vessel that changes logger
or Trusted Node can get a new ID, and the same soundings sometimes appear under
more than one ID (see cross-platform duplicate). The archive also has a
`PLATFORM_NAME` column, which the census neither uses nor publishes.

### File

One CSV file in the archive, from one submission, usually holding a single
platform's soundings. Its name starts with the time NCEI ingested it.

### Batch

NCEI publishes new files four times a day, at about 00, 06, 12 and 18 UTC. Each
release is a batch.

## How it is counted

### Unique sounding

The first published copy of a sounding. Two soundings are the same when their
longitude, latitude, depth and time match exactly, as DCDB writes them.

### Duplicate

Any later copy of a sounding already published. A **resend** carries the same
platform ID as the original. A **cross-platform** duplicate carries a different
one. The census reports both, and does not say why a duplicate exists: it can
come from a provider, an ingest retry or a data-center policy.

### Vessel-day

One platform collecting in one place on one UTC day. "One place" is an H3 cell
at resolution 8 (about 0.7 km²); on coarser maps, a vessel-day counts once per
cell however far the vessel moved inside it. Vessel-days measure how many boats
were somewhere, where sounding counts mostly measure how long loggers ran.

### Cell

A hexagon of Uber's [H3](https://h3geo.org/) grid. The maps switch resolution
with zoom:

| Resolution | Typical cell area | Used for |
| :--- | :--- | :--- |
| 4 | 1,770 km² | World view, and the landing map |
| 6 | 36 km² | Regional views |
| 8 | 0.7 km² | Harbors and coasts; the unit of a vessel-day |
| 9 | 0.1 km² | The closest zoom on the last 24 hours |

## The two clocks

### Collection time

When the vessel logged the sounding, from the `TIME` the vessel's own logger
recorded. It answers "where were boats, and when". Every map except the last 24
hours uses it.

### Publication time

When NCEI made the sounding public, taken from the ingest stamp at the start of
the file name. It answers "what came in today".

### Publication lag

Collection time to publication time, for a file's newest sounding. Weeks to
months is typical: data waits on the vessel, the logger's upload and the Trusted
Node before NCEI publishes it. Many boats have no internet at sea, so their data
leaves the logger only when someone copies it off in port, and a whole season can
arrive at once.

### Age at publication

How long before publication a cell's soundings were collected, averaged over
its soundings. On the last 24 hours map, older data is drawn fainter.

### Census latency

How far the census runs behind NCEI: from the newest file of a batch appearing
in the open archive to the census having counted it. The pages show the latest
value and the 30-day median.

## Who's who

### IHO

The [International Hydrographic Organization](https://iho.int/), the
intergovernmental body for hydrography and nautical charting. Its
[Crowdsourced Bathymetry](https://iho.int/en/crowdsourced-bathymetry) initiative
collects depth measurements from ordinary vessels.

### DCDB

The [IHO Data Centre for Digital Bathymetry](https://iho.int/en/data-centre-for-digital-bathymetry),
which holds the world's crowdsourced bathymetry. It is hosted by NOAA NCEI.

### NCEI

[NOAA's National Centers for Environmental Information](https://www.ncei.noaa.gov/).
NCEI publishes DCDB's crowdsourced soundings to the
[open archive](https://registry.opendata.aws/noaa-dcdb-bathymetry-pds/) on AWS,
which is the census's only source.

### Trusted Node

An organization that collects crowdsourced bathymetry from vessels, adds the
metadata DCDB requires (IHO publication B-12), checks it, and submits it to
DCDB. Trusted Nodes issue the platform IDs. They stand between the vessels and
the archive, so DCDB deals with a few organizations rather than thousands of
boats.

### Provider

The value in the archive's `PROVIDER` column: the organization the data came
through, usually a Trusted Node. Providers include companies, nonprofits,
research programs and fleets. Per-provider figures describe what NCEI published
under that label. They measure publication, not the provider's behavior.

### Seabed 2030

[The Nippon Foundation-GEBCO Seabed 2030 Project](https://seabed2030.org/), which
aims to map the whole ocean floor by 2030. Crowdsourced bathymetry is one of its
sources.

### Long Horizon Observatory

Independent research lab on the Long Island Sound that builds and runs this
census ([longhorizon.eco](https://longhorizon.eco/)). It works from NCEI's public
archive and is not part of the IHO, DCDB or NOAA. It also maps the seabed of the
Long Island Sound with a [WIBL](https://github.com/CCOMJHC/WIBL) logger. The lab
built this census to help: good crowdsourced bathymetry depends on both the data
and the operations behind it, from the logger to the archive, and it would
welcome the chance to work on both with the community.

## How data reaches the archive

A sounding passes through several hands before the census sees it:

1. **On the vessel**, the echosounder and GNSS receiver broadcast depth and
   position on the boat's instrument network (NMEA 2000 or NMEA 0183). A logger
   on the network records them.
2. **Off the vessel**, the logger's files go to a Trusted Node, uploaded
   directly where the boat has internet, or copied off in port by phone.
3. **At the Trusted Node**, the files are converted to B-12, given the vessel's
   metadata, checked and submitted to DCDB.
4. **At NCEI**, DCDB's data is published to the open archive in the next
   6-hourly batch. The census starts here.

The census sees only the last step. Each earlier step can delay data, repeat it
or keep it back, and the census cannot tell which happened.

### Logger

A small device that listens on a vessel's instrument network and records what
it hears. It does not ping the seafloor itself; it needs an echosounder and a
position source already on board. [WIBL](https://github.com/CCOMJHC/WIBL), the
Wireless Inexpensive Bathymetry Logger from UNH CCOM, is an open-source example
that costs a few tens of dollars to build.

### B-12

The IHO's guidance on crowdsourced bathymetry, including the data and metadata
each contribution should carry. Trusted Nodes usually write it as GeoJSON. The
only metadata strictly required is a unique identifier for the vessel's system;
anything more (vessel type, sensor offsets) is optional but makes the data more
useful. That is why `UNIQUE_ID` is often all the archive knows about a vessel.

### Valid

Data that passes a format check, such as the B-12 schema: the required fields
are present and correctly written. Valid is not the same as good. A well-formed
file can still hold a bad depth, a wrong position or a clock error. The census
counts what was published, not how accurate it is.
