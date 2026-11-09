# Inputs

Two de-identified files from one driver of a small carrier in California, September 2026, produced by
`scripts/prepare_inputs.py` from the raw exports (which are not in this repository).

| File | What it is | Size |
|---|---|---|
| `driver_output_file.csv` | The driver's ELD output file (49 CFR 395 Appendix A, 4.8.2.1) generated on 2026-10-01 for the preceding month: 7 header lines, 2 users, 1 CMV, 424 driver events, 16 certification records, 2 malfunction/diagnostic records, 39 login/logout records, 226 engine power-up/shut-down records, 83 unidentified-driver records, file data check. Carriage-return line delimiter as in the original, no terminator after the last line. | 800 data lines |
| `driver_events_export.csv` | The same month as shown by the carrier portal's event list (the "eRODS" view): date, time, state, odometer, engine hours, event label, origin, sequence number, VIN, power unit. | 790 rows |

## De-identification

Same keyed pseudonyms (HMAC-SHA256, private key) as the `telematics-data-foundation` tables, so the
carrier and the truck join to `dim_org` / `dim_vehicle` there:

| Field | Published form |
|---|---|
| Carrier name | `ORG-` + 10 hex (the foundation's organization key) |
| USDOT number | 7-digit number derived from the key (not the real number) |
| VIN | `VIN` + 14 hex (17 characters; the first 10 hex digits equal the foundation's `VIN-` key) |
| CMV power unit number | `PU` + 6 hex |
| Driver / co-driver / listed users | last name `DRV` + 8 hex, first name `DRIVER` |
| ELD username | `drv_` + 10 hex |
| Driver's license number | `DL` + 8 hex (state kept) |
| ELD registration id / identifier / authentication value | `DEID` / `DEID01` / 32 hex derived from the key |
| Coordinates | rounded to a 0.05 degree grid, two decimals |
| Trailer numbers, shipping document, annotations, location text | removed; the events export keeps only the state |

Dates, times, odometer, engine hours, event types and codes, sequence numbers, record status and
origin are unchanged.

## Data check values

The rewrite changes fields that the data checks cover, so **every line data check, event data check
and the file data check were recomputed** with the algorithms in `eldfile/checks.py`. The original
file verified completely before the rewrite (800/800 line checks, 424/424 driver event checks, file
check), which is how the implementation was validated against a real device. The username of the
unidentified-driver profile is not part of the file; its 83 event data checks are recomputed with the
placeholder username `UNIDENTIFIED` (in the original they verify against one constant username
contribution, see the report).

The output file comment field reads `test` in the original and is kept.
