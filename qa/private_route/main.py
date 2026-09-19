"""In-process QA runner: fixtures -> routing -> browser -> isolation -> scan.

Started by qa/validate-private-route.sh with the database, API and frontend
already up. The raw synthetic tokens are created here, live only in this
process's memory and are never passed on: not through a file, an environment
variable, argv, a screenshot, HAR, trace or report.

Exit: 0 all checks passed, 1 a check failed, 2 harness/setup error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from private_route import browser_checks, fixtures, isolation_checks, public_checks, ram_state, routing_checks
from private_route.common import Report, format_exception, install_excepthook, scan_tree


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend-url", required=True)
    parser.add_argument("--frontend-dir", required=True, type=Path)
    parser.add_argument("--api-origin", required=True)
    parser.add_argument("--tmp-dir", required=True, type=Path)
    parser.add_argument("--server-kind", required=True, choices=["builtin", "wrangler"])
    parser.add_argument("--wrangler-state", type=Path, default=None, help="RAM-backed Wrangler state dir (required with --server-kind wrangler)")
    args = parser.parse_args()

    install_excepthook()
    report = Report()
    if (args.server_kind == "wrangler") != (args.wrangler_state is not None):
        print("QA harness error: --wrangler-state is required with, and only with, --server-kind wrangler", file=sys.stderr)
        return 2
    try:
        if args.wrangler_state is not None:
            report.section("Wrangler state storage (records request paths, so it must be RAM-backed)")
            problems = ram_state.verify_state_dir(args.wrangler_state)
            if args.wrangler_state.resolve().is_relative_to(args.tmp_dir.resolve()):
                problems.append("state directory is inside the disk-backed run temp dir")
            report.group("state dir exists, is unique to this run, private, writable and on tmpfs/ramfs (never on disk)", problems)
        report.section("Fixtures (real certificate lifecycle, tokens kept in memory only)")
        fx = fixtures.build()
        report.note(
            "issued through activate/revoke_certificate: valid, revoked, unpublished-piece; plus a never-issued token"
        )
        fixtures.check_token_persistence(report, fx)
        routing_checks.run(report, fx, args.frontend_url, args.frontend_dir, args.server_kind)
        browser_checks.run(report, fx, args.frontend_url, args.api_origin, str(args.tmp_dir))
        public_checks.run(report, fx, args.frontend_url, args.api_origin)
        isolation_checks.run(report, fx, args.api_origin, args.frontend_dir)

        report.section("Token persistence (files written during this run)")
        scanned, problems = scan_tree(args.tmp_dir)
        report.group("no token or hash in any file under the run's temp dir (API and server logs included)", problems)
        report.note(f"{scanned} files scanned")
    except BaseException as exc:  # noqa: BLE001 - sanitized, never hidden
        print("\nQA harness error (secrets scrubbed):", file=sys.stderr)
        print(format_exception(exc), file=sys.stderr)
        return 2

    print(f"\nprivate-route QA [{args.server_kind}]: {report.passed} passed, {report.failed} failed", flush=True)
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
