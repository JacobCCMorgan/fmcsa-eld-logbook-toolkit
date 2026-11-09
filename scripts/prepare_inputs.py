"""Publish de-identified inputs from one driver's ELD output file and the matching event export.

Run once, privately, with the raw files and the de-identification key of the foundation project:

    TDF_SRC=/path/to/telematics-data-foundation/src \\
    TDF_DEID_KEY_FILE=/path/to/deid_key.txt \\
    python scripts/prepare_inputs.py --output-file raw/output_file.csv --events raw/events.csv

The raw files never enter the repository. What is published:

* ``data/input/driver_output_file.csv`` - the ELD output file with every identifier replaced by a keyed
  pseudonym (same key and namespaces as the foundation tables, so the carrier and the truck join to
  ``dim_org`` and ``dim_vehicle``), coordinates coarsened to a 0.05 degree grid, and **all data check
  values recomputed** after the rewrite so that the published file still verifies. The unidentified
  driver profile's username is not part of the file; its event data checks are recomputed with the
  placeholder username ``UNIDENTIFIED``.
* ``data/input/driver_events_export.csv`` - the driver's event export from the carrier portal
  (the "eRODS" view), with the location text reduced to the state and the same pseudonyms.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("TDF_SRC", ""))
from tdf.deid import Pseudonymizer, coarsen_coordinate, normalize_org_name  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from eldfile import checks, parse  # noqa: E402

UD_USERNAME = "UNIDENTIFIED"
DIRECTIONS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}


def state_of(location: str) -> str:
    """Location text reads like ``2mi SW CA Los Angeles``: the state is the two-letter token that is not a compass direction."""
    for tok in (location or "").split():
        if re.fullmatch(r"[A-Z]{2}", tok) and tok not in DIRECTIONS:
            return tok
    return ""


class Names:
    """Keyed pseudonyms shaped to the output file's field constraints (same key and namespaces as the
    foundation project; the foundation keeps ten hex digits, the longer fields here keep more of the
    same digest, so prefixes match)."""

    def __init__(self, p: Pseudonymizer):
        self.p = p

    def digest(self, namespace: str, value: str) -> str:
        return hmac.new(self.p.key, f"{namespace}|{value}".encode(), hashlib.sha256).hexdigest()

    def vin(self, vin: str) -> str:
        """17 alphanumeric characters; the first ten hex digits equal the foundation's VIN- key."""
        if not vin or vin.strip() == "":
            return vin
        return "VIN" + self.digest("vin", vin.strip().upper())[:14].upper()

    def power_unit(self, unit: str) -> str:
        return "PU" + self.digest("power_unit", unit.strip())[:6].upper() if unit.strip() else unit

    def username(self, user: str) -> str:
        return "drv_" + self.digest("eld_user", user.strip().lower())[:10] if user.strip() else user

    def person(self, last: str, first: str) -> tuple[str, str]:
        if not (last.strip() or first.strip()):
            return last, first
        return "DRV" + self.digest("driver_name", f"{last.strip().upper()},{first.strip().upper()}")[:8].upper(), "DRIVER"

    def license(self, number: str) -> str:
        return "DL" + self.digest("license", number.strip().upper())[:8].upper() if number.strip() else number

    def carrier(self, name: str) -> str:
        return self.p.one("org", "ORG-", normalize_org_name(name)) or "ORG-UNKNOWN"

    def usdot(self, dot: str) -> str:
        return str(1_000_000 + int(self.digest("dot", dot.strip())[:8], 16) % 8_999_999)

    def eld_auth(self, value: str) -> str:
        return self.digest("eld_auth", value.strip())[:32].upper()


def coord(value: str) -> str:
    if value in ("X", "M", "E", ""):
        return value
    c = coarsen_coordinate(float(value), 0.05)
    return f"{c:.2f}"


def rewrite_output_file(text: str, n: Names) -> str:
    f = parse.read_text(text)
    h = dict(f.header)
    h["last_name"], h["first_name"] = n.person(h["last_name"], h["first_name"])
    h["username"] = n.username(h["username"])
    h["license_number"] = n.license(h["license_number"])
    h["codriver_last_name"], h["codriver_first_name"] = n.person(h["codriver_last_name"], h["codriver_first_name"])
    h["codriver_username"] = n.username(h["codriver_username"])
    h["power_unit"] = n.power_unit(h["power_unit"])
    h["vin"] = n.vin(h["vin"])
    h["trailers"] = ""
    h["carrier_name"] = n.carrier(h["carrier_name"])
    h["usdot"] = n.usdot(h["usdot"])
    h["shipping_document"] = ""
    h["current_lat"], h["current_lon"] = coord(h["current_lat"]), coord(h["current_lon"])
    h["eld_registration_id"], h["eld_identifier"] = "DEID", "DEID01"
    h["eld_authentication"] = n.eld_auth(h["eld_authentication"])
    header_lines = []
    for _name, names in parse.HEADER_LINES:
        body = ",".join(h[k] for k in names)
        header_lines.append(f"{body},{checks.line_check(body)}")
    unit_by_order = {}
    tables: dict[str, list[str]] = {}
    for key, names in parse.FIELDS.items():
        rows = []
        for _, r in f.table(key).iterrows():
            v = {k: str(r[k]) for k in names}
            if key == "users":
                v["last_name"], v["first_name"] = n.person(v["last_name"], v["first_name"])
            elif key == "cmvs":
                v["power_unit"], v["vin"] = n.power_unit(v["power_unit"]), n.vin(v["vin"])
                unit_by_order[v["cmv_order"]] = v["power_unit"]
            elif key in ("events", "unidentified"):
                v["lat"], v["lon"] = coord(v["lat"]), coord(v["lon"])
                user = h["username"] if key == "events" else UD_USERNAME
                v["event_check"] = checks.event_check(v["event_type"], v["event_code"], v["date"], v["time"], v["accum_miles"], v["elapsed_hours"], v["lat"], v["lon"], unit_by_order.get(v["cmv_order"], h["power_unit"]), user)
            elif key == "annotations":
                v["username"] = n.username(v["username"])
                v["comment"], v["location"] = "", ""
            elif key == "logins":
                v["username"] = n.username(v["username"])
            elif key == "power":
                v["lat"], v["lon"] = coord(v["lat"]), coord(v["lon"])
                v["power_unit"], v["vin"] = n.power_unit(v["power_unit"]), n.vin(v["vin"])
                v["trailers"], v["shipping_document"] = "", ""
            body = ",".join(v[k] for k in names)
            rows.append(f"{body},{checks.line_check(body)}")
        tables[key] = rows
    all_checks = [checks.split_check(line)[1] for line in header_lines] + [checks.split_check(line)[1] for key in parse.FIELDS for line in tables[key]]
    return parse.to_text(header_lines, tables, checks.file_check(all_checks), "\r", f.section_headers_seen)


def rewrite_events(path: Path, out: Path, n: Names) -> int:
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    keep = ["Date", "Time", "Location", "Odometer", "Engine Hours", "Event Type/Status", "Origin", "Sequence #", "VIN", "PowerUnit"]
    with open(out, "w", newline="", encoding="ascii") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "time", "state", "odometer", "engine_hours", "event_label", "origin", "seq", "vin", "power_unit"], lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({"date": r["Date"], "time": r["Time"], "state": state_of(r["Location"]), "odometer": (r["Odometer"] or "").replace(",", ""), "engine_hours": (r["Engine Hours"] or "").replace(",", ""),
                        "event_label": r["Event Type/Status"], "origin": r["Origin"], "seq": r["Sequence #"], "vin": n.vin(r["VIN"] or ""), "power_unit": n.power_unit(r["PowerUnit"] or "")})
    assert set(keep) <= set(rows[0].keys())
    return len(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output-file", required=True, type=Path)
    ap.add_argument("--events", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=Path("data/input"))
    a = ap.parse_args(argv)
    n = Names(Pseudonymizer.from_env())
    a.out.mkdir(parents=True, exist_ok=True)
    text = a.output_file.read_bytes().decode("ascii")
    published = rewrite_output_file(text, n)
    (a.out / "driver_output_file.csv").write_bytes(published.encode("ascii"))
    k = rewrite_events(a.events, a.out / "driver_events_export.csv", n)
    f = parse.read_text(published)
    ok = all(checks.verify_line(t) for _, _, t in f.data_lines)
    print(f"output file: {len(f.data_lines)} data lines, all line checks verify: {ok}, file check {f.file_check}")
    print(f"events export: {k} rows")


if __name__ == "__main__":
    main()
