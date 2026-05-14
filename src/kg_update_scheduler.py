"""Scheduled KG update worker for manifest-driven LightRAG updates."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

from pipeline.kg_update import apply_manifest_items_sync, latest_manifest_items, load_manifest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "data" / "lakehouse" / "gold" / "kg_update_manifest.jsonl"
DEFAULT_STATE = ROOT / "data" / "pipeline_state" / "kg_applied_manifests.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run KG updates on an interval")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--state-path", default=str(DEFAULT_STATE))
    parser.add_argument("--interval-minutes", type=float, default=15.0)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args()
    apply_live = os.getenv("KG_UPDATE_APPLY", "false").lower() == "true"
    while True:
        applied = _load_applied(args.state_path)
        pending_raw = [item for item in load_manifest(args.manifest) if item.manifest_id not in applied]
        pending = latest_manifest_items(pending_raw)
        results = apply_manifest_items_sync(pending, dry_run=not apply_live)
        if apply_live:
            applied.update(item.manifest_id for item in pending_raw)
            _save_applied(args.state_path, applied)
        print(
            json.dumps(
                {
                    "apply": apply_live,
                    "pending_raw": len(pending_raw),
                    "pending_apply": len(pending),
                    "compacted_duplicates": len(pending_raw) - len(pending),
                    "results": results,
                },
                indent=2,
            )
        )
        if args.once:
            return
        time.sleep(max(args.interval_minutes, 1.0) * 60)


def _load_applied(path: str | Path) -> set[str]:
    state_path = Path(path)
    if not state_path.exists():
        return set()
    raw = json.loads(state_path.read_text(encoding="utf-8") or "[]")
    return {str(item) for item in raw}


def _save_applied(path: str | Path, applied: set[str]) -> None:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(sorted(applied), indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
