#!/usr/bin/env python3
import json
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


DB_PATH = Path("/opt/turny-site/instance/trmt.db")


COLORS = ["blue", "teal", "amber", "purple", "coral", "gray", "green"]


def clean(value):
    return str(value or "").strip()


def short_name(name):
    return clean(name)[:12]


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: import_trmt1_vmaster.py /path/to/vmaster_vessels.json")

    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    rows = payload.get("vessels", payload if isinstance(payload, list) else [])
    vessels = [
        r for r in rows
        if clean(r.get("team")).upper() == "TRMT1" and clean(r.get("name"))
    ]
    if not vessels:
        raise SystemExit("no TRMT1 vessels found")

    # Deterministic order by supervisor then vessel name keeps tabs stable.
    vessels.sort(key=lambda r: (clean(r.get("owner_supervisor")), clean(r.get("name"))))

    backup = DB_PATH.with_suffix(DB_PATH.suffix + f".bak-vmaster-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(DB_PATH, backup)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            existing_order = {
                r["name"]: r["display_order"]
                for r in conn.execute("SELECT name, display_order FROM supervisors")
            }
            max_order = conn.execute(
                "SELECT COALESCE(MAX(display_order), 0) FROM supervisors"
            ).fetchone()[0]
            supervisor_ids = {}

            supervisors = []
            for r in vessels:
                name = clean(r.get("owner_supervisor"))
                if name and name not in supervisors:
                    supervisors.append(name)

            for idx, name in enumerate(supervisors):
                email = clean(next((r.get("supervisor_email") for r in vessels
                                    if clean(r.get("owner_supervisor")) == name
                                    and clean(r.get("supervisor_email"))), ""))
                row = conn.execute("SELECT id FROM supervisors WHERE name=?", (name,)).fetchone()
                if row:
                    sid = row["id"]
                    conn.execute(
                        """
                        UPDATE supervisors
                           SET email=COALESCE(NULLIF(?, ''), email),
                               active=1,
                               updated_at=datetime('now','localtime')
                         WHERE id=?
                        """,
                        (email, sid),
                    )
                else:
                    max_order += 1
                    sid = conn.execute(
                        """
                        INSERT INTO supervisors
                            (name, color, display_order, email, active)
                        VALUES (?, ?, ?, ?, 1)
                        """,
                        (name, COLORS[idx % len(COLORS)], max_order, email),
                    ).lastrowid
                supervisor_ids[name] = sid

            imported_ids = []
            for r in vessels:
                name = clean(r.get("name"))
                supervisor = clean(r.get("owner_supervisor"))
                sid = supervisor_ids.get(supervisor)
                values = {
                    "short_name": short_name(name),
                    "vessel_type": clean(r.get("vessel_type") or r.get("kind")),
                    "imo": clean(r.get("imo")),
                    "flag": clean(r.get("flag")),
                    "class_society": clean(r.get("class_society")),
                    "manager": clean(r.get("management_company")),
                    "vsl_cd": clean(r.get("code")),
                    "active": 1,
                }
                cur = conn.execute("SELECT id FROM vessels WHERE name=?", (name,)).fetchone()
                if cur:
                    vid = cur["id"]
                    conn.execute(
                        """
                        UPDATE vessels
                           SET short_name=?,
                               vessel_type=?,
                               imo=?,
                               flag=?,
                               class_society=?,
                               manager=?,
                               vsl_cd=?,
                               active=1,
                               updated_at=datetime('now','localtime')
                         WHERE id=?
                        """,
                        (
                            values["short_name"],
                            values["vessel_type"],
                            values["imo"],
                            values["flag"],
                            values["class_society"],
                            values["manager"],
                            values["vsl_cd"],
                            vid,
                        ),
                    )
                else:
                    vid = conn.execute(
                        """
                        INSERT INTO vessels
                            (name, short_name, vessel_type, imo, flag, class_society,
                             manager, vsl_cd, active)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            name,
                            values["short_name"],
                            values["vessel_type"],
                            values["imo"],
                            values["flag"],
                            values["class_society"],
                            values["manager"],
                            values["vsl_cd"],
                        ),
                    ).lastrowid
                imported_ids.append(vid)
                conn.execute("DELETE FROM supervisor_vessels WHERE vessel_id=?", (vid,))
                if sid:
                    conn.execute(
                        "INSERT OR IGNORE INTO supervisor_vessels (vessel_id, supervisor_id) VALUES (?, ?)",
                        (vid, sid),
                    )

            # 1팀 사이트는 vmaster TRMT1 로스터가 권위다. 기존 테스트/수기 선박 중
            # 이번 로스터에 없는 것은 숨김 처리하되 이력 데이터는 보존한다.
            placeholders = ",".join("?" for _ in imported_ids)
            conn.execute(
                f"""
                UPDATE vessels
                   SET active=0, updated_at=datetime('now','localtime')
                 WHERE id NOT IN ({placeholders})
                """,
                imported_ids,
            )

        counts = Counter(clean(r.get("owner_supervisor")) for r in vessels)
        print(f"backup={backup}")
        print(f"imported={len(vessels)}")
        for name, count in sorted(counts.items()):
            print(f"supervisor={name} vessels={count}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
