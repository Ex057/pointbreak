import os
from pathlib import Path


TEST_DB = Path(__file__).resolve().parent / "tradeguard-test.sqlite"

os.environ.setdefault("DATA_SOURCE", "fixture")
os.environ["DB_URL"] = f"sqlite:///{TEST_DB}"

if TEST_DB.exists():
    TEST_DB.unlink()
