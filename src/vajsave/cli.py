import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .backend import MountedVolumeBackend
from .models import ScanResult, VolumeInfo
from .scanner import scan
from .volume import MountedVolumeProvider, watch_volumes


def _format_human_scan(result: ScanResult) -> str:
    lines: List[str] = []
    lines.append(f"📁 Root: {result.root_path}")
    lines.append(f"🎮 Platform: {result.platform.upper()}")

    if result.sources:
        lines.append("📦 Sources:")
        for s in result.sources:
            lines.append(f"   • [{s.source_id}] {s.description} ({s.root_path})")

    if result.saves:
        lines.append(f"💾 Found {len(result.saves)} save(s):")
        for save in result.saves:
            title_part = f" [{save.title_id}]" if save.title_id else ""
            slot_part = f" (slot: {save.slot})" if save.slot else ""
            user_part = f" (user: {save.user})" if save.user else ""
            lines.append(f"   • {save.display_name}{title_part}{slot_part}{user_part}")
            lines.append(f"     Path: {save.path}")
    else:
        lines.append("💾 Saves: (none)")

    if result.warnings:
        lines.append("⚠️ Warnings:")
        for w in result.warnings:
            lines.append(f"   • {w}")

    return "\n".join(lines)


def handle_scan(args: argparse.Namespace) -> int:
    results: List[ScanResult] = []

    if args.paths:
        target_paths = [Path(p) for p in args.paths]
    else:
        backend = MountedVolumeBackend()
        target_paths = list(backend.iter_roots())
        if not target_paths and not args.json:
            print("No mounted external volumes found.", file=sys.stderr)

    for path in target_paths:
        res = scan(path)
        results.append(res)
        if res.warnings:
            for w in res.warnings:
                print(f"Warning [{path}]: {w}", file=sys.stderr)

    if args.json:
        data = [r.to_dict() for r in results]
        print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        for i, res in enumerate(results):
            if i > 0:
                print("\n" + "=" * 50 + "\n")
            print(_format_human_scan(res))

    return 0


def handle_watch(args: argparse.Namespace) -> int:
    provider = MountedVolumeProvider()

    def on_volume_event(event_type: str, volume: VolumeInfo):
        if args.json:
            event_data = {
                "event": event_type,
                "volume": volume.to_dict(),
            }
            if event_type == "appeared":
                scan_res = scan(volume.mount_point)
                event_data["scan"] = scan_res.to_dict()
            print(json.dumps(event_data, indent=2, sort_keys=True, ensure_ascii=False), flush=True)
        else:
            if event_type == "appeared":
                print(f"\n[+] Volume Appeared: {volume.name} ({volume.mount_point})")
                res = scan(volume.mount_point)
                print(_format_human_scan(res), flush=True)
            elif event_type == "disappeared":
                print(f"\n[-] Volume Disappeared: {volume.name} ({volume.mount_point})", flush=True)

    if not args.json:
        print(f"Watching for volume changes (polling every {args.interval}s, press Ctrl+C to stop)...")

    try:
        watch_volumes(provider, interval=args.interval, callback=on_volume_event)
    except KeyboardInterrupt:
        if not args.json:
            print("\nStopped volume watcher.")
    return 0


def handle_app(args: argparse.Namespace) -> int:
    from .app import main as app_main
    return app_main()


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vajsave",
        description="Read-only handheld console save manager and volume scanner.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # scan subcommand
    scan_parser = subparsers.add_parser("scan", help="Scan mounted volumes or specific paths for saves.")
    scan_parser.add_argument(
        "paths",
        nargs="*",
        help="Specific path(s) to scan. If omitted, scans discovered volumes.",
    )
    scan_parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as structured JSON.",
    )

    # watch subcommand
    watch_parser = subparsers.add_parser("watch", help="Watch for mounted volume changes and scan new volumes.")
    watch_parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds (default: 1.0).",
    )
    watch_parser.add_argument(
        "--json",
        action="store_true",
        help="Output volume events as JSON lines.",
    )

    # app subcommand
    subparsers.add_parser("app", help="Launch the graphical desktop application.")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = create_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2

    if args.command == "scan":
        return handle_scan(args)
    elif args.command == "watch":
        return handle_watch(args)
    elif args.command == "app":
        return handle_app(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
