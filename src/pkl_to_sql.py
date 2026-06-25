#!/usr/bin/env python
"""
View a pandas .pkl DataFrame as SQL.

Converts a pickled DataFrame into a SQLite database table so you can browse /
query it with any SQL tool (DB Browser for SQLite, DBeaver, sqlite3 CLI, etc.).

Usage:
    python pkl_to_sql.py [PKL_PATH] [SQLITE_PATH] [TABLE_NAME]

Defaults:
    PKL_PATH     ResourceFiles/optimization_dataset.pkl
    SQLITE_PATH  same name as the pkl but with .db extension
    TABLE_NAME   "data"

After it runs you can either:
    sqlite3 optimization_dataset.db        # then: .tables / SELECT * FROM data LIMIT 10;
or open the .db file in DB Browser for SQLite (GUI).
"""
import os
import sqlite3
import sys

import pandas as pd


def main():
    pkl_path = sys.argv[1] if len(sys.argv) > 1 else "ResourceFiles/optimization_dataset.pkl"
    db_path = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(pkl_path)[0] + ".db"
    table = sys.argv[3] if len(sys.argv) > 3 else "data"

    df = pd.read_pickle(pkl_path)
    print(f"Loaded {pkl_path}: {df.shape[0]} rows x {df.shape[1]} cols")

    with sqlite3.connect(db_path) as conn:
        df.to_sql(table, conn, if_exists="replace", index=False)

    print(f"Wrote table '{table}' to {db_path}")
    print("\nOpen it with:")
    print(f"    sqlite3 {db_path}")
    print(f'    SELECT * FROM {table} LIMIT 10;')
    print("Or open the .db file in DB Browser for SQLite (GUI).")


if __name__ == "__main__":
    main()
