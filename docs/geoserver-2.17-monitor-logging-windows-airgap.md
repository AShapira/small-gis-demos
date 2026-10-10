# GeoServer 2.17 Monitor logging on an air-gapped Windows workstation

Prepared: 4 October 2026

## Purpose and scope

This guide explains how to record GeoServer requests for later investigation, identify slow and failed requests, and measure overall usage. It targets GeoServer 2.17 running from the command line on Windows with its bundled Jetty server and the Monitor extension already installed.

The recommended approach is to record all relevant service requests in Monitor audit files, preserve the GeoServer application logs alongside them, and analyze copies locally. No Internet connection, external database, reverse proxy, or additional GeoServer extension is required for this approach.

Configuration and implementation details were checked against the upstream **2.17.0** documentation and source. The exact 2.17 patch release, Java version, Jetty version, and configuration of the target workstation have not been inspected. The configuration and template examples below must pass the acceptance checks on that workstation before operational use. This guide does not claim that logging has been installed or tested there.

## 1. What the Monitor extension does

Monitor observes requests inside GeoServer and collects information about their execution: HTTP method and URL, OGC service and operation, requested resources, duration, client identity where available, response metadata, and errors. For POST and PUT requests, it can capture a bounded amount of the request body.

There are three distinct concepts:

| Concept | Meaning | Recommended setting |
|---|---|---|
| Monitoring mode | When request information is updated | `mode=history` for completed-request analysis |
| Monitor storage | Where the Monitor Query API obtains its records | `storage=memory` for a lightweight installation |
| Audit logging | A separate listener that writes request records to disk | `audit.enabled=true` with a persistent local path |

**`mode=history` alone does not create a durable history.** With the default memory storage, the queryable history contains only the latest 100 requests and is lost on restart. Audit logging provides the disk record independently. The Monitor Query API does not read the archived audit files back into that memory store. [Configuration](https://github.com/geoserver/geoserver/blob/2.17.0/doc/en/user/source/extensions/monitoring/configuration.rst)

In the 2.17.0 implementation, the audit listener receives records after post-processing and queues them for a background writer. Writing is asynchronous, so file appearance may lag request completion. This is operational request logging, not a guarantee of crash-proof, lossless capture. [Audit implementation](https://github.com/geoserver/geoserver/blob/2.17.0/src/extension/monitor/core/src/main/java/org/geoserver/monitor/auditlog/AuditLogger.java)

## 2. Recommended arrangement

```text
LAN clients
    |
Bundled Jetty -> GeoServer services -> Monitor
                                      |
                                      +-> recent in-memory records / Monitor API
                                      +-> local audit files

GeoServer application logging ---------> geoserver.log and rotated logs

Closed audit files + application logs -> local archive -> offline analysis
```

Use three separate locations, adjusted to the workstation's actual drives:

```text
C:\GeoServerLogs\audit       Active and recently closed Monitor files
C:\GeoServerLogs\archive     Copies of closed files, optionally compressed
C:\GeoServerLogs\reports     Derived CSV, HTML, or analysis database
```

Use an existing suitable local disk with free space. A different directory on the same disk provides organization, not independent backup. A slow or unavailable network share is a poor choice for the active audit destination.

Keep all service requests in the raw archive. Select slow and failed requests during analysis. Logging only errors would remove the denominator needed to calculate failure rates; logging only slow requests would prevent reliable usage and latency distributions.

## 3. Inventory and backup before configuration

Record the following in a small deployment note:

1. Exact GeoServer and Monitor JAR versions. Extension builds should match GeoServer's exact release.
2. The actual Java executable used by the startup script and its version. `java -version` in another shell may refer to a different installation.
3. The GeoServer installation path and **active data directory**. Confirm the latter in GeoServer's Server Status page and the startup configuration; do not assume it is the installation's default `data_dir`.
4. The startup batch file or Java command, listening port, and Windows account that launches it.
5. Existing `monitoring` files, custom templates, application log path, logging profile, and rotation settings.
6. Free disk capacity and the Windows/JVM timezone and default character encoding.

Back up the complete `monitoring` directory and any startup file you will edit. Preserve existing site-specific filters and custom templates for review. Coordinate a normal shutdown/restart window; closing a command window or forcibly killing Java can leave incomplete audit files and lose queued records.

No system-wide environment changes are needed for the baseline setup. This guide provides manual instructions; it does not modify the workstation.

## 4. Enable persistent logging

The configuration file is:

```text
<ACTIVE_DATA_DIRECTORY>\monitoring\monitor.properties
```

Merge these entries into the existing file, leaving only one effective value for each key:

```properties
# Recent records remain available through the Monitor API.
storage=memory
mode=history

# Durable request audit files.
audit.enabled=true
audit.path=C:/GeoServerLogs/audit
audit.roll_limit=10000

# Capture up to 64 KiB of each request body.
maxBodySize=65536

# Avoid reverse DNS and GeoIP enrichment in this environment.
# Keep layerNameNormalizer enabled.
ignorePostProcessors=reverseDNS,geoIp

# Conservative bounding-box extraction.
bboxMode=no_wfs
bboxCrs=EPSG:4326
```

The placeholder in the path above represents the active data directory; it is not a variable to create. Use forward slashes in the actual `audit.path` value, or escape Windows backslashes correctly. Do not wrap the property value in quotation marks. Keep properties files ASCII-compatible; save these examples without a byte-order mark.

Create the audit directory and grant write access to the Windows account running GeoServer. Restrict read access to the people who need the captured requests. Start GeoServer normally after editing, send one ordinary service request, and inspect both the audit directory and `geoserver.log`.

| Property | Effect and practical interpretation |
|---|---|
| `audit.enabled` | Correct spelling is **enabled**. An old documentation paragraph contains `audit.enable`; use the configuration key shown here. |
| `audit.path` | Local destination for audit files. A JVM `GEOSERVER_AUDIT_PATH` system property can override it; check existing startup options if files appear elsewhere. |
| `audit.roll_limit` | Request-count threshold for rolling, not a byte limit or number of retained files. 10,000 is the 2.17.0 implementation default and a reasonable initial value. |
| `maxBodySize` | Maximum captured body bytes. `0` disables body capture; `-1` removes the bound. It is not an HTTP upload limit. |
| `ignorePostProcessors` | Comma-separated names without spaces. Disabling DNS/GeoIP retains client IP capture but omits those enrichments. |
| `bboxMode=no_wfs` | Avoids heuristic WFS bbox extraction. Preserve the original URL/body for complex spatial filters. |
| `bboxCrs` | Fallback for bbox metadata; it does not reproject requests or replace their CRS. |

Files are named like `geoserver_audit_20261004_12.log`. In the 2.17.0 implementation, daily rolling and filename dates use GMT/UTC and rolling is evaluated as batches are written. A file may exceed the request-count threshold by a batch; an idle server does not necessarily create a new file exactly at midnight. File dates can therefore differ from the local Windows calendar day. [Audit documentation](https://github.com/geoserver/geoserver/blob/2.17.0/doc/en/user/source/extensions/monitoring/audit.rst), [implementation](https://github.com/geoserver/geoserver/blob/2.17.0/src/extension/monitor/core/src/main/java/org/geoserver/monitor/auditlog/AuditLogger.java)

Do not assume a UI reload applies every change. For the initial rollout, use a controlled restart and verify the resulting files. Later configuration changes should follow the same validation procedure.

## 5. Choose which requests to capture

The file `<data directory>\monitoring\filter.properties` contains **exclusion** patterns. A starting point is:

```properties
# Exclude Monitor's own queries.
/rest/monitor/**

# Exclude the administration UI.
/web
/web/**
```

Patterns match the path after `/geoserver`, without the query string. For example, `/geoserver/workspace/wms?...` is matched as `/workspace/wms`.

Do not add `/wms`, `/wfs`, `/ows`, or equivalent workspace endpoints if their usage is needed. The upstream sample includes a WCS exclusion as an example; copying it would hide WCS traffic. Review existing exclusions instead of blindly replacing the file. [Filter rules](https://github.com/geoserver/geoserver/blob/2.17.0/doc/en/user/source/extensions/monitoring/configuration.rst)

Test global endpoints, workspace-specific endpoints, and any GeoWebCache/WMTS routes actually used. A request that never reaches the Monitor filter cannot be counted there. Administrative and REST traffic should be explicitly included or excluded according to the report's stated scope.

## 6. What to retain in each record

The default XML template already provides most investigation fields:

| Fields | Use |
|---|---|
| Request ID, start/end times | Find the event and correlate it with other logs |
| Service, operation, version, resources | Separate WMS maps, WFS queries, exports and other activity |
| HTTP method, path, query string, body | Understand the actual request and reproduce suitable read-only requests |
| Total time | Server-observed duration in **milliseconds** |
| Client address, user, host | Attribute requests where identities are available |
| Response status, content type, length | Understand outcomes and response volume |
| Failed flag, error message | Detect application exceptions as well as HTTP errors |
| Cache result/miss reason | Explain cache behavior when these fields are populated |
| Resource rendering and label times | Investigate WMS rendering cost |

The Monitor data model contains more fields than the default audit template emits. To include lifecycle status, User-Agent, request content type, and capture-size information, customize the template as described next. [Field reference](https://github.com/geoserver/geoserver/blob/2.17.0/doc/en/user/source/extensions/monitoring/reference.rst)

## 7. Optional template enrichment

First prove that the unmodified default audit output works. Then obtain the **matching installed Monitor JAR's** default templates, or the templates from the exact matching distribution, and copy them into the `monitoring` directory for customization. Do not start with an empty `content.ftl`, because that would remove the existing record fields.

The three template names are:

| File | Purpose |
|---|---|
| `header.ftl` | Written once when a file opens; normally declares XML and opens `<Requests>` |
| `content.ftl` | Written for each request |
| `footer.ftl` | Written when the file closes; normally `</Requests>` |

Inside the existing `<Request>` element and its XML escaping block, add the following FreeMarker fragment. It supplements the existing fields, including the existing body element:

```ftl
<Status>${status!""}</Status>
<Category>${category!""}</Category>
<UserAgent>${remoteUserAgent!""}</UserAgent>
<BodyContentType>${bodyContentType!""}</BodyContentType>
<BodyContentLength>${bodyContentLength?c}</BodyContentLength>
<#if body??>
<CapturedBodyBytes>${body?size?c}</CapturedBodyBytes>
<#else>
<CapturedBodyBytes>0</CapturedBodyBytes>
</#if>
```

The fields are present in 2.17.0's `RequestData`. Keep the existing `<#escape x as x?xml>` protection, or equivalent valid escaping, around text fields. Use `?c` for machine-readable numbers so locale formatting does not introduce grouping separators. Validate this fragment with the installed FreeMarker/Monitor build before relying on it. [Request data source](https://github.com/geoserver/geoserver/blob/2.17.0/src/extension/monitor/core/src/main/java/org/geoserver/monitor/RequestData.java)

An original body length larger than captured bytes indicates incomplete capture when the original length is known. A missing/unknown original length does not prove capture was complete. Body capture can also depend on how the application reads the request stream. Test POST forms as well as XML if both are used.

### Windows encoding caveat

The 2.17.0 source uses the JVM default charset both for the audit `FileWriter` and for `bodyAsString` conversion from bytes. The default template declares UTF-8, which does not itself force the writer to use UTF-8. This matters for Hebrew and other non-ASCII text on older Windows Java installations. [Writer source](https://github.com/geoserver/geoserver/blob/2.17.0/src/extension/monitor/core/src/main/java/org/geoserver/monitor/auditlog/AuditLogger.java), [body conversion](https://github.com/geoserver/geoserver/blob/2.17.0/src/extension/monitor/core/src/main/java/org/geoserver/monitor/RequestData.java)

Check the actual launch JVM's encoding and test a harmless UTF-8 request containing Hebrew text. If appropriate for the installed Java and application, evaluate `-Dfile.encoding=UTF-8` in that GeoServer process's Java startup options, before `-jar`. This affects the whole JVM's default encoding, so test other integrations as well. It does not guarantee correct decoding of requests sent in a different encoding. Do not change workstation-wide Java settings just to enable auditing.

Treat XML body text as an investigation aid, not guaranteed byte-for-byte evidence. Exact binary replay requires a separate byte-preserving capture mechanism.

## 8. Define slow and failed requests

### Slow requests

Use an initial rule such as `TotalTime >= 5000` for interactive map requests. Five seconds is an operational starting point, not a GeoServer default or universal service-level objective. Set different thresholds for large WFS exports, WCS downloads, and other long-running operations.

For each slow request retain the original record and extract: timestamp, duration, operation, layers, client, HTTP outcome, path, query string, body, response size, cache result and error message. Compare like operations and similar output sizes. Separate successful and failed latency distributions because fast failures can make overall averages look deceptively good.

Monitor duration is server-observed time. It is not pure database execution time, pure rendering time, or complete client-perceived latency. Use application/database logs and machine measurements to investigate the cause.

### Failed or abnormal requests

Keep separate indicators rather than collapsing all outcomes into one number:

| Indicator | Classification |
|---|---|
| `Failed=true` or an error message/exception | Application failure, regardless of HTTP status |
| HTTP 500–599 | HTTP server error |
| HTTP 400–499 | Client/authentication/request error; report separately |
| Status `FAILED`, `CANCELLED`, `INTERRUPTED`, or `CANCELLING` | Abnormal lifecycle state requiring context |
| Missing outcome fields | Unknown/incomplete evidence; do not assume success |

Count the union once when reporting “requests with any failure indicator”; one event can trigger several indicators. Do not classify solely by HTTP status: test how actual OGC exception responses are represented by this installation. An invalid layer request is a useful controlled test. Cancellations and client disconnects may reflect user behavior rather than a server defect.

## 9. Measure overall usage correctly

Produce daily and weekly reports with a stated timezone, time window, included endpoints, and completeness status:

| Metric | Recommended breakdown |
|---|---|
| Request count | Hour/day, service, operation, layer or resource set |
| Clients | IP and authenticated username separately; identify anonymous requests |
| Latency | Count, median, p95, p99, maximum; per operation and success/failure |
| Slow requests | Count and percentage using each operation's threshold |
| Failures | Application, 4xx, 5xx, cancellation and unknown outcome |
| Response volume | Sum of recorded response lengths, with unknown values excluded |
| Cache behavior | HIT/MISS among populated cache results; keep unknown separately |
| Logging health | Files ingested, parse failures, missing periods, truncated bodies |

Record percentile conventions, for example nearest-rank on sorted observations, and show sample counts. Small samples make p99 unstable. A request for multiple layers is one HTTP request but can contribute to multiple layer-usage counts; do not sum those layer counts as if they were unique requests.

Do not equate unique IPs with people. Shared clients, NAT, scripts, health checks and tile traffic affect the totals. Exclude synthetic validation traffic from business summaries but preserve it in raw evidence. Per-layer rendering times can overlap because GeoServer uses concurrent work; their sum is not a reliable wall-clock request duration. [Timing and cache field semantics](https://github.com/geoserver/geoserver/blob/2.17.0/doc/en/user/source/extensions/monitoring/reference.rst)

Use UTC for storage and comparison, then convert to the selected local reporting timezone with daylight-saving rules. The default audit timestamp format includes UTC (`Z`). A late-finishing request may be written to a file dated after its start time; filter records by timestamps rather than filename date alone.

## 10. Offline analysis workflow

Windows PowerShell 5.1 and .NET can parse XML and create CSV/HTML without downloading packages. For larger histories, an approved offline Python/SQLite toolchain is another option. Keep analysis outside the GeoServer JVM so reporting does not compete with request processing more than necessary.

Recommended processing steps:

1. Discover audit files and identify files that have closed. The active default XML file lacks its final `</Requests>` until closure. Old date or stable size alone is insufficient evidence of closure.
2. Copy closed files to a staging/archive location. Leave originals untouched while GeoServer runs.
3. Validate and parse the copies. Disable DTD processing and external entity resolution in XML readers. Report malformed files explicitly; do not silently discard them.
4. Record source filename, a SHA-256 file hash, and record ordinal with each event. Use these for repeatable imports; request ID alone should not be treated as globally unique across restarts/instances.
5. Normalize timestamps and numeric fields, preserve missing values, and derive the indicators in section 8.
6. Produce a request index, slow-request report, failure report, usage summary and import-health report.
7. Keep raw files and their hashes so every reported row can be traced to its source.

For an initial small deployment, rebuild reports from archived files on each run. For incremental ingestion, maintain a manifest of completed file hashes and skip previously ingested files. Do not append the same files on every daily run.

Use a real CSV writer for quoted delimiters/newlines. Request bodies and URLs are untrusted text: HTML-escape them in HTML reports and guard against spreadsheet formula interpretation in CSV exports. Keep bodies in the raw archive or separate detail files rather than inflating every summary row.

The Monitor API is useful for spot checks and recent events:

```text
http://localhost:8080/geoserver/rest/monitor/requests.html
http://localhost:8080/geoserver/rest/monitor/requests.csv?count=100
http://localhost:8080/geoserver/rest/monitor/requests.zip?count=100
http://localhost:8080/geoserver/rest/monitor/requests.html?order=totalTime;DESC
```

Adjust the port/context path and use the deployment's authorized authentication method. Do not embed passwords in saved commands. With memory storage, these endpoints only search the small retained window; a daily API export can miss almost all of a busy day's requests. ZIP exports include body/error detail for the records still available. [Query API](https://github.com/geoserver/geoserver/blob/2.17.0/doc/en/user/source/extensions/monitoring/query.rst)

This document defines the analysis workflow; it does not include a tested, production-ready report generator.

## 11. Rotation, retention and capacity

Rotation creates additional files; it does not set a retention period. Manage archive retention separately and never truncate, rename, compress in place, or delete the active audit file.

A reasonable initial policy is 30 days of raw audit and corresponding application logs, adjusted to investigation needs and measured capacity. Retain incident-related files longer when required. Compress closed copies and verify their contents before any approved cleanup. Scheduled cleanup should initially run in report-only mode.

Estimate capacity from measured data:

```text
daily bytes ≈ requests/day × average serialized bytes/request
retention bytes ≈ daily bytes × retention days + incident/headroom allowance
```

For illustration, 100,000 requests/day at 2 KiB each is about 195 MiB/day or 5.7 GiB over 30 days before compression. If each record instead averages 64 KiB, the same volume is about 6.1 GiB/day before XML overhead. The configured maximum body size is not the average record size.

Large bodies also occupy memory while queued. The 2.17.0 audit writer has a queue of 10,000 records and waits when it fills. Sustained disk slowness can therefore cause monitoring backpressure; watch for the “Auditing subsystem overload” message. Unlimited body capture is a poor starting point. [Queue implementation](https://github.com/geoserver/geoserver/blob/2.17.0/src/extension/monitor/core/src/main/java/org/geoserver/monitor/auditlog/AuditLogger.java)

Measure free space, daily log growth, audit delay and normal request latency after enabling capture. Review access permissions because filters, queries, usernames, coordinates and tokens embedded in URLs may be present. Raw request logs should remain within the air-gapped environment unless an approved transfer is specifically needed.

## 12. Hangs, crashes and complementary evidence

Completed-request logging cannot prove which request caused a JVM crash or identify every request stuck indefinitely. Changing to `mode=live` does not turn the audit writer into a durable request-start journal: in 2.17.0 it writes from the post-processed request callback.

For these incidents, collect:

- `geoserver.log`, rotated application logs, and relevant startup/console output.
- Windows CPU, available memory, Java process memory, disk latency and free-space measurements.
- Java thread dumps during the hang, using tooling compatible with the running JVM and the correct process identity.
- Database-side timing/connection evidence if requests use a database.
- Jetty request logs when separately configured and validated against the installed Jetty release.

Jetty access logging is complementary HTTP evidence, not a replacement for Monitor's OGC fields. Ordinary completion access logs also do not guarantee a record of a request that never completes. Exact Jetty XML/startup changes depend on the bundled Jetty version, so generic Tomcat or modern Jetty instructions should not be pasted into this installation.

Keep the normal application logging profile initially. Broad DEBUG logging can produce large volumes and change performance; use narrowly scoped, temporary diagnostics when an investigation warrants them.

## 13. Acceptance test before relying on the records

Use a test layer and controlled traffic during the agreed maintenance window:

| Test | Expected evidence |
|---|---|
| Valid WMS GET | Correct operation, layer, URL parameters, duration and response outcome |
| Valid WFS POST larger than 1 KiB | Captured body contains the expected filter beyond byte 1,024 |
| Request larger than configured body cap | Truncation is detectable; no claim of complete replay |
| UTF-8 POST containing Hebrew text | Readable body after XML parsing; declared/output encoding agree |
| Invalid layer or invalid parameter | Useful error evidence; verify HTTP status and Monitor error fields independently |
| Each workspace/OWS/tile route used | Records appear despite differences in routing and cache behavior |
| Authentication failure, if permitted | Document whether it is observed by Monitor or handled earlier |
| Controlled slow read-only request | Recorded duration and analysis threshold flag agree |
| Low roll limit in an isolated test | Closed XML files parse and subsequent files continue recording |
| Normal restart | Old files remain and new requests continue into valid files |
| Repeat analysis of the same archive | Counts stay unchanged; no duplicate ingestion |

Do not create artificial production overload to test the slow flag. Validate the report logic with synthetic records if needed. After initial setup, compare performance using a small representative workload and the same conditions. Preserve a few known-good sample records and document any routes or outcomes that remain outside coverage.

## 14. Troubleshooting and rollback

| Symptom | Checks |
|---|---|
| No audit file | Correct active data directory; exact `audit.enabled` spelling; writable destination; eligible request sent; startup override; application log errors |
| Records visible in API but not files | Audit configuration, queue/writer errors, template failures, disk space and path permissions |
| Only latest requests visible in API | Expected with memory storage; analyze audit files for history |
| Some services absent | Exclusion patterns, workspace paths, routing, authentication filter ordering and cache path |
| XML parser rejects newest file | It may still be open; parse closed copies |
| XML parser rejects an old file | Possible abrupt shutdown, encoding mismatch or bad template; retain original and label any recovered copy |
| POST filter is incomplete | Body cap, original/captured length, stream consumption and encoding |
| Logging stopped after customization | Restore known-good templates; check `Request Dumper` warnings and restart normally |
| Unexpected disk growth | Request rate, body size, missing retention policy and repeated archiving |
| Unexpected slowdown | Disk contention, queue saturation, DNS enrichment, body capture volume or verbose application logging |

For rollback, preserve the generated logs, restore the backed-up monitoring/startup configuration and templates, and restart normally in the agreed window. To stop audit capture while retaining Monitor itself, set `audit.enabled=false` and verify after the controlled restart. Do not delete the historical audit directory as part of rollback.

## 15. Deployment checklist

- [ ] Exact versions, startup command and active data directory recorded.
- [ ] Existing monitoring configuration and templates backed up.
- [ ] Writable local audit path and restricted access verified.
- [ ] All relevant service routes included.
- [ ] Bounded POST capture configured and tested with representative requests.
- [ ] Windows encoding checked with non-ASCII input.
- [ ] Slow/failure classifications validated independently of HTTP status alone.
- [ ] Closed-file parsing and duplicate prevention verified.
- [ ] Application logs retained for the same investigation period.
- [ ] Disk budget, archive policy and logging-health checks assigned.
- [ ] Normal restart and rollback procedure verified.
- [ ] Report timezone, coverage and known limitations documented.

## Source references

These public sources were used to prepare the guide; no network access is needed to follow the included configuration steps. They are pinned to 2.17.0 to avoid accidentally applying settings from a newer release.

1. [Monitor configuration](https://github.com/geoserver/geoserver/blob/2.17.0/doc/en/user/source/extensions/monitoring/configuration.rst)
2. [Audit logging and templates](https://github.com/geoserver/geoserver/blob/2.17.0/doc/en/user/source/extensions/monitoring/audit.rst)
3. [Captured data reference](https://github.com/geoserver/geoserver/blob/2.17.0/doc/en/user/source/extensions/monitoring/reference.rst)
4. [Monitor Query API](https://github.com/geoserver/geoserver/blob/2.17.0/doc/en/user/source/extensions/monitoring/query.rst)
5. [AuditLogger implementation](https://github.com/geoserver/geoserver/blob/2.17.0/src/extension/monitor/core/src/main/java/org/geoserver/monitor/auditlog/AuditLogger.java)
6. [RequestData implementation](https://github.com/geoserver/geoserver/blob/2.17.0/src/extension/monitor/core/src/main/java/org/geoserver/monitor/RequestData.java)
7. [MonitorConfig implementation](https://github.com/geoserver/geoserver/blob/2.17.0/src/extension/monitor/core/src/main/java/org/geoserver/monitor/MonitorConfig.java)
