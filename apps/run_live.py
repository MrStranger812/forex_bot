from __future__ import annotations

import argparse

from bot.config import endpoint_manifest_from_env, load_config, require_live_interlock


def run(config_path: str) -> None:
    config = load_config(config_path)
    manifest = endpoint_manifest_from_env(
        verified=bool(config.get("ourbit", {}).get("endpoint_manifest_verified"))
    )
    require_live_interlock(config, manifest)
    raise RuntimeError(
        "Interlocks passed, but live order transport is intentionally not promoted. "
        "Complete read-only payload fixtures, reconciliation integration, shadow, and paper gates."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail-closed Ourbit live runner")
    parser.add_argument("--config", default="configs/live.yaml")
    args = parser.parse_args()
    run(args.config)


if __name__ == "__main__":
    main()
