"""Print the activation funnel for the database in DATABASE_URL.

This is a local operator script. It does not open a network service, send
email, or embed credentials. Point DATABASE_URL at a database you are allowed
to read.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    sql = (Path(__file__).with_name("activation_funnel.sql")).read_text(encoding="utf-8")
    engine = create_engine(database_url, future=True)
    with engine.connect() as connection:
        rows = connection.execute(text(sql)).mappings().all()
    if not rows:
        print("no funnel rows")
        return 0
    for row in rows:
        scope = row["scope_trip_id"] or "ALL"
        audience = row["audience"] or "-"
        print(
            f"{row['metric']}\tscope={scope}\taudience={audience}\t"
            f"{row['numerator']}/{row['denominator']}\trate={row['rate']}\t"
            f"{row['sample_flag']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
