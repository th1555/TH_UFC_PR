"""
check_names.py — name-collision guard.

Flags any fighter carried under two spellings in the live ledger (same name
after stripping accents/case/punctuation, suffixes preserved). A hit is a
candidate mis-split; a clean run means one identity per fighter.

Run:
    python tests/check_names.py     # exits 1 if anything needs review
"""
from pathlib import Path
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ingest  # noqa: E402

ledger = pd.read_parquet(ROOT / 'data' / 'bout_ledger.parquet')
roster = set(ledger['fighter_1']) | set(ledger['fighter_2'])
collisions = ingest.find_collisions(roster)

if collisions:
    print(f"{len(collisions)} name collision(s) to review:")
    for _, spellings in collisions.items():
        print(f"   {spellings}")
    print("\nSame fighter?  add an alias to name_aliases.yaml.")
    print("Different people (e.g. father/son)?  no action needed.")
    sys.exit(1)

print(f"No name collisions across {len(roster):,} fighters. OK")
sys.exit(0)
