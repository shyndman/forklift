from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from forklift.cli_runtime import build_container_env, resolve_chown_target
from forklift.opencode_env import OpenCodeEnv


class CliRuntimeHelperTests(unittest.TestCase):
    def test_resolve_chown_target_defaults_gid_when_omitted(self) -> None:
        with patch("forklift.cli_runtime.default_host_ids", return_value=(123, 456)):
            uid, gid = resolve_chown_target("42")
        self.assertEqual(uid, 42)
        self.assertEqual(gid, 456)

    def test_build_container_env_includes_required_keys(self) -> None:
        env = OpenCodeEnv(
            api_key="abc",
            model="model-x",
            variant="default",
            agent="worker",
            server_password="pw",
            server_port=4096,
        )
        with patch.dict(os.environ, {"TZ": "America/Vancouver"}, clear=False):
            container_env = build_container_env(
                env,
                "main",
                "run-123",
                forward_tz=True,
            )

        self.assertEqual(container_env["FORKLIFT_MAIN_BRANCH"], "main")
        self.assertEqual(container_env["FORKLIFT_RUN_ID"], "run-123")
        self.assertEqual(container_env["TZ"], "America/Vancouver")


if __name__ == "__main__":
    _ = unittest.main()
