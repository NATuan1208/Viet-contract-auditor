"""Apply or dry-run LightRAG incremental KG update manifests."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from pipeline.kg_update import apply_manifest_items_sync, latest_manifest_items, load_manifest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "data" / "lakehouse" / "gold" / "kg_update_manifest.jsonl"
DEFAULT_STATE = ROOT / "data" / "pipeline_state" / "kg_applied_manifests.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LightRAG incremental KG updater")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--state-path", default=str(DEFAULT_STATE))
    parser.add_argument("--crawl-run-id", help="Apply only manifests from one crawl_run_id")
    parser.add_argument(
        "--force-reapply",
        action="store_true",
        help="Ignore applied state and delete each doc_id before inserting it again.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate actions without changing LightRAG")
    parser.add_argument("--apply", action="store_true", help="Apply updates to LightRAG")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args()
    if args.apply and args.dry_run:
        raise SystemExit("Use either --apply or --dry-run, not both.")
    dry_run = not args.apply
    raw_items = load_manifest(args.manifest)
    if args.crawl_run_id:
        raw_items = [item for item in raw_items if item.crawl_run_id == args.crawl_run_id]
    applied = _load_applied(args.state_path)
    pending_raw = raw_items if args.force_reapply else [item for item in raw_items if item.manifest_id not in applied]
    items = latest_manifest_items(pending_raw)
    results = apply_manifest_items_sync(
        items,
        dry_run=dry_run,
        force_replace=args.force_reapply and args.apply,
    )
    if args.apply:
        applied.update(item.manifest_id for item in pending_raw)
        _save_applied(args.state_path, applied)
    print(
        json.dumps(
            {
                "manifest": args.manifest,
                "raw_count": len(raw_items),
                "pending_raw": len(pending_raw),
                "apply_count": len(items),
                "compacted_duplicates": len(pending_raw) - len(items),
                "crawl_run_id": args.crawl_run_id,
                "force_reapply": args.force_reapply,
                "state_path": args.state_path,
                "results": results,
            },
            indent=2,
        )
    )


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
