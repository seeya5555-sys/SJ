#!/usr/bin/env python3
"""Sync SVMS noon report status into the 1팀 Fleet Map cache.

Read-only SVMS calls only:
- PKG_OP_REPORT.SP_GET_REPORT for recent noon reports.

The script stores the latest valid SVMS position in vessel_positions and writes
instance/svms_dnr.json for audit/debug. Credentials stay outside the repo.
"""
import datetime as dt
import http.cookiejar
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


BASE = "https://svms.sinokor.co.kr"
APP_DIR = Path(__file__).resolve().parents[1]
INSTANCE_DIR = Path(os.environ.get("TURNY_INSTANCE_DIR", APP_DIR / "instance"))
DB_PATH = Path(os.environ.get("TURNY_DB", INSTANCE_DIR / "trmt.db"))
CRED_FILE = Path(os.environ.get("SVMS_CRED_FILE", INSTANCE_DIR / "svms-cred"))
USER_ID = os.environ.get("SVMS_USER_ID", "SS0094")
LOOKBACK_DAYS = int(os.environ.get("SVMS_DNR_LOOKBACK_DAYS", "21"))
MISS_THRESHOLD_DAYS = int(os.environ.get("SVMS_DNR_MISS_DAYS", "2"))
SVMS_VSL_CD = os.environ.get("SVMS_REPORT_VSL_CD", "")


def normalize_name(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def norm_imo(value):
    return str(value or "").strip().lstrip("0")


def cursor(result):
    if isinstance(result, dict):
        for value in result.values():
            if isinstance(value, list):
                return value
    return []


class Svms:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def post(self, path, payload, timeout=70):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            BASE + path,
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with self.opener.open(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def login(self):
        password = CRED_FILE.read_text(encoding="utf-8").strip()
        last = None
        for attempt in range(5):
            try:
                result = self.post("/api/login", {"p": f"{USER_ID}=:=", "c": password, "lang": "ko_KR"})
                if result.get("authenticated"):
                    return
                last = result
            except Exception as exc:
                last = exc
            time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"SVMS login failed: {last}")

    def select(self, package, procedure, param):
        last = None
        body = {
            "DATASET": [{
                "PACKAGE": package,
                "PROCEDURE": procedure,
                "PARAM": param,
            }]
        }
        for attempt in range(4):
            try:
                result = self.post("/api/vms/comm/selectVmsCommonProcedure", body)
                return result.get("data", {}).get(procedure, {})
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in (500, 502, 503, 504):
                    raise
            except Exception as exc:
                last = exc
            time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"SVMS select failed {package}.{procedure}: {last}")


def dir_sign(code, axis):
    value = str(code or "").upper()
    if value in ("S", "W"):
        return -1
    if value in ("N", "E"):
        return 1
    if value.startswith("DIR"):
        try:
            bearing = ((int(value[3:]) - 1) % 16) * 22.5
        except ValueError:
            return 1
        if axis == "lat":
            return -1 if 90 < bearing < 270 else 1
        return -1 if 180 < bearing < 360 else 1
    return 1


def decode_pos(deg, minute, direction, axis):
    if deg in (None, ""):
        return None
    try:
        value = float(deg) + float(minute or 0) / 60.0
    except (TypeError, ValueError):
        return None
    return round(value * dir_sign(direction, axis), 4)


def valid_position(row):
    lat = decode_pos(row.get("LAT_DEG"), row.get("LAT_MIN"), row.get("LAT_D"), "lat")
    lng = decode_pos(row.get("LON_DEG"), row.get("LON_MIN"), row.get("LON_D"), "lon")
    if lat is None or lng is None:
        return None
    if abs(lat) < 0.01 and abs(lng) < 0.01:
        return None
    return lat, lng


def report_key(row):
    return str(row.get("RPT_DI") or row.get("RPT_DT") or "")


def report_date(row):
    value = str(row.get("RPT_DT") or "")[:8]
    if len(value) == 8 and value.isdigit():
        return value
    value = "".join(ch for ch in str(row.get("RPT_DI") or "") if ch.isdigit())[:8]
    return value if len(value) == 8 else ""


def load_vessels(conn):
    rows = conn.execute(
        """
        SELECT v.id, v.name, v.imo, v.vsl_cd, v.vessel_type, v.class_society,
               GROUP_CONCAT(s.name, ', ') AS supervisor_names
          FROM vessels v
          JOIN supervisor_vessels sv ON sv.vessel_id = v.id
          JOIN supervisors s ON s.id = sv.supervisor_id AND s.active = 1
         WHERE v.active = 1
         GROUP BY v.id
         ORDER BY v.name
        """
    ).fetchall()
    return [dict(row) for row in rows]


def main():
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    vessels = load_vessels(con)
    if not vessels:
        raise SystemExit("no active vessels")

    svms = Svms()
    svms.login()
    today = dt.date.today()
    start = (today - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    reports = cursor(svms.select(
        "PKG_OP_REPORT",
        "SP_GET_REPORT",
        {"SM_COMP_ID": "", "VSL_CD": SVMS_VSL_CD, "FM_DT": start, "TO_DT": end},
    ))

    by_code = {}
    by_imo = {}
    by_name = {}
    for row in reports:
        code = str(row.get("VSL_CD") or "").strip().upper()
        imo = norm_imo(row.get("IMO_NO"))
        name = normalize_name(row.get("VSL_NM_ENG") or row.get("VSL_NM"))
        if code:
            by_code.setdefault(code, []).append(row)
        if imo:
            by_imo.setdefault(imo, []).append(row)
        if name:
            by_name.setdefault(name, []).append(row)

    synced = 0
    dnr = []
    now_text = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with con:
        for vessel in vessels:
            rows = (
                by_code.get(str(vessel.get("vsl_cd") or "").strip().upper())
                or by_imo.get(norm_imo(vessel.get("imo")))
                or by_name.get(normalize_name(vessel.get("name")))
                or []
            )
            rows = sorted(rows, key=report_key)
            latest = rows[-1] if rows else None
            latest_rpt = report_date(latest or {})
            last_position_row = None
            for row in reversed(rows):
                if valid_position(row):
                    last_position_row = row
                    break

            if last_position_row:
                lat, lng = valid_position(last_position_row)
                raw = {
                    "source": "SVMS noon",
                    "rpt_dt": report_date(latest or last_position_row),
                    "rpt_di": latest.get("RPT_DI") if latest else last_position_row.get("RPT_DI"),
                    "bl": (latest or last_position_row).get("BL"),
                    "status": (latest or last_position_row).get("RPT_TP_GRP_NM"),
                    "dest_port": (latest or last_position_row).get("PORT_NM"),
                    "eta": (latest or last_position_row).get("ETA_DT"),
                    "course": (latest or last_position_row).get("COURSE"),
                    "speed": (latest or last_position_row).get("SPD_AVG"),
                }
                con.execute(
                    """
                    INSERT INTO vessel_positions
                        (vessel_id, vt_vessel_id, lat, lng, course, speed, source,
                         last_seen, destination, raw_json, updated_at)
                    VALUES (?, NULL, ?, ?, ?, ?, 'SVMS noon', ?, ?, ?, ?)
                    ON CONFLICT(vessel_id) DO UPDATE SET
                        lat = excluded.lat,
                        lng = excluded.lng,
                        course = COALESCE(excluded.course, vessel_positions.course),
                        speed = COALESCE(excluded.speed, vessel_positions.speed),
                        source = excluded.source,
                        last_seen = excluded.last_seen,
                        destination = excluded.destination,
                        raw_json = excluded.raw_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        vessel["id"], lat, lng, raw.get("course"), raw.get("speed"),
                        raw.get("rpt_di") or raw.get("rpt_dt"), raw.get("dest_port"),
                        json.dumps(raw, ensure_ascii=False), now_text,
                    ),
                )
                synced += 1

            missing = True
            miss_days = None
            if latest_rpt:
                try:
                    miss_days = (today - dt.datetime.strptime(latest_rpt, "%Y%m%d").date()).days
                    missing = miss_days >= MISS_THRESHOLD_DAYS
                except ValueError:
                    pass
            dnr.append({
                "vessel_id": vessel["id"],
                "name": vessel["name"],
                "vsl_cd": vessel.get("vsl_cd"),
                "supervisor": (vessel.get("supervisor_names") or "").split(", ")[0],
                "last_rpt": latest_rpt,
                "miss_days": miss_days,
                "missing": missing,
            })

    payload = {
        "generated_at": now_text,
        "window": {"from": start, "to": end},
        "active_vessels": len(vessels),
        "svms_report_rows": len(reports),
        "position_synced": synced,
        "dnr_missing": [row for row in dnr if row["missing"]],
        "dnr_all": dnr,
    }
    (INSTANCE_DIR / "svms_dnr.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "active_vessels": len(vessels),
        "position_synced": synced,
        "dnr_missing": len(payload["dnr_missing"]),
        "generated_at": now_text,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
