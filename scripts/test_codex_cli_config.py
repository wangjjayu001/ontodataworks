"""CC Switch startup snapshots use the selected route without leaking credentials."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "scripts")]
from app.model_runtime import ModelRuntimeError
from codex_cli_config import API_KEY_ENV, RUNTIME_PROVIDER, load_cli_configuration
from codex_bridge import Bridge, RpcSession


class CliConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / ".cc-switch"
        self.root.mkdir()
        self.db = sqlite3.connect(self.root / "cc-switch.db")
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE providers(id TEXT, app_type TEXT, name TEXT, settings_config TEXT, is_current BOOLEAN)")
        self.settings = self.root / "settings.json"

    def provider(self, ident="current", *, model="gpt-test", effort="high", key="synthetic-test-key", current=True, official=False):
        config = f'model="{model}"\nmodel_reasoning_effort="{effort}"\n'
        if not official:
            config += 'model_provider="custom"\n[model_providers.custom]\nname="Synthetic service"\nbase_url="https://example.invalid/v1"\nwire_api="responses"\nrequires_openai_auth=true\n'
        payload = {"auth": {"OPENAI_API_KEY": key}, "config": config}
        self.db.execute("INSERT INTO providers VALUES(?, 'codex', ?, ?, ?)", (ident, ident, json.dumps(payload), current))
        self.db.commit()
        return payload

    def load(self, **kwargs):
        return load_cli_configuration(home=self.home, environment=kwargs.get("environment", {}))

    def test_current_provider_configures_child_environment_and_never_exposes_key(self):
        self.provider()
        inherited = {"OPENAI_API_KEY": "stale-personal-key", "OPENAI_BASE_URL": "https://old.invalid", "CODEX_HOME": "personal-home"}
        config = self.load(environment=inherited)
        workdir = self.home / "session"
        workdir.mkdir()
        env = config.session_environment(workdir)
        self.assertEqual(env[API_KEY_ENV], "synthetic-test-key")
        self.assertEqual(Path(env["CODEX_HOME"]), workdir / "cli-home")
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("OPENAI_BASE_URL", env)
        self.assertEqual(inherited["OPENAI_API_KEY"], "stale-personal-key")
        self.assertEqual(config.public["configured_model"], "gpt-test")
        exposed = " ".join(config.arguments) + json.dumps(config.public) + repr(config)
        self.assertNotIn("synthetic-test-key", exposed)
        self.assertNotIn("stale-personal-key", exposed)
        self.assertIn('"requires_openai_auth" = false', " ".join(config.arguments))

    def test_local_selection_is_respected_when_database_current_flag_is_stale(self):
        self.provider("old", model="old-model")
        self.provider("selected", model="current-model", current=False)
        self.settings.write_text(json.dumps({"currentProviderCodex": "selected"}), encoding="utf-8")
        self.assertEqual(self.load().public["configured_model"], "current-model")

    def test_restart_reads_new_provider_payload_and_old_snapshot_stays_fixed(self):
        self.provider()
        old = self.load()
        self.db.execute("DELETE FROM providers")
        self.provider(model="new-model", effort="medium", key="rotated-test-key")
        current = self.load()
        self.assertEqual(old.public["configured_model"], "gpt-test")
        self.assertEqual(current.public["configured_model"], "new-model")
        self.assertEqual(current.public["reasoning_effort"], "medium")
        self.assertEqual(current.environment[API_KEY_ENV], "rotated-test-key")

    def test_custom_provider_without_credentials_cannot_fall_back_to_official_login(self):
        self.provider(key=None)
        with self.assertRaises(ModelRuntimeError) as failure:
            self.load()
        self.assertEqual(failure.exception.code, "MODEL_CONFIG_INVALID")

    def test_missing_selected_provider_and_ambiguous_current_fail(self):
        self.provider("a")
        self.provider("b")
        with self.assertRaises(ModelRuntimeError):
            self.load()
        self.settings.write_text(json.dumps({"currentProviderCodex": "missing"}), encoding="utf-8")
        with self.assertRaises(ModelRuntimeError):
            self.load()

    def test_invalid_provider_configuration_is_a_redacted_error(self):
        self.provider()
        self.db.execute("UPDATE providers SET settings_config=?", ('{invalid-secret-value',))
        self.db.commit()
        with self.assertRaises(ModelRuntimeError) as failure:
            self.load()
        self.assertNotIn("invalid-secret-value", str(failure.exception))

    def test_official_provider_reuses_cli_login_without_reading_auth_file(self):
        self.provider(key=None, official=True)
        environment = {"CODEX_HOME": "original-cli-home"}
        config = self.load(environment=environment)
        self.assertFalse(config.isolated)
        self.assertIn('model_provider="openai"', config.arguments)
        self.assertEqual(config.session_environment(self.home), environment)

    def test_builtin_provider_with_api_key_uses_isolated_authentication(self):
        self.provider(official=True)
        config = self.load()
        self.assertTrue(config.isolated)
        self.assertIn("https://api.openai.com/v1", " ".join(config.arguments))

    def test_no_ccswitch_keeps_codex_configuration_and_explicit_missing_root_fails(self):
        elsewhere = self.home / "without-switch"
        environment = {"CODEX_HOME": "existing-cli-home"}
        config = load_cli_configuration(home=elsewhere, environment=environment)
        self.assertEqual(config.public["source"], "codex-user-config")
        self.assertEqual(config.environment, environment)
        with self.assertRaises(ModelRuntimeError):
            load_cli_configuration(home=elsewhere, environment={"ONTODATAWORKS_CC_SWITCH_HOME": str(elsewhere)})

    def test_active_proxy_uses_verified_live_local_route(self):
        self.provider()
        self.db.execute("CREATE TABLE proxy_config(app_type TEXT,live_takeover_active BOOLEAN,listen_address TEXT,listen_port INTEGER)")
        self.db.execute("INSERT INTO proxy_config VALUES('codex',1,'127.0.0.1',15721)")
        self.db.commit()
        live = self.home / ".codex"
        live.mkdir()
        path = live / "config.toml"
        path.write_text('model_provider="proxy"\n[model_providers.proxy]\nbase_url="http://127.0.0.1:15721/v1"', encoding="utf-8")
        config = self.load()
        self.assertIn("http://127.0.0.1:15721/v1", " ".join(config.arguments))
        path.write_text('model_provider="proxy"\n[model_providers.proxy]\nbase_url="https://unrelated.invalid/v1"', encoding="utf-8")
        with self.assertRaises(ModelRuntimeError):
            self.load()

    def test_actual_cli_configuration_must_match_selected_provider_model_and_effort(self):
        self.provider()
        config = self.load()
        correct = {"model_provider": RUNTIME_PROVIDER, "model": "gpt-test", "model_reasoning_effort": "high",
                   "model_providers": {RUNTIME_PROVIDER: config.provider_configuration}}
        config.verify(correct)
        for field in ("model_provider", "model", "model_reasoning_effort"):
            with self.subTest(field=field), self.assertRaises(ModelRuntimeError) as failure:
                config.verify({**correct, field: "unexpected"})
            self.assertEqual(failure.exception.code, "MODEL_CONFIG_MISMATCH")
        with self.assertRaises(ModelRuntimeError):
            config.verify({**correct, "model_providers": {RUNTIME_PROVIDER: {**config.provider_configuration, "base_url": "https://wrong.invalid"}}})

    def test_cli_process_receives_configuration_and_retains_tool_restrictions(self):
        self.provider()
        config = self.load()
        with patch("codex_bridge.subprocess.Popen") as popen, patch("codex_bridge.threading.Thread.start"):
            RpcSession("codex", self.home, configuration=config)
        args = popen.call_args.args[0]
        self.assertIn('approval_policy="never"', args)
        self.assertIn('sandbox_mode="read-only"', args)
        self.assertIn('model_provider="' + RUNTIME_PROVIDER + '"', args)
        self.assertNotIn("synthetic-test-key", str(args))
        self.assertEqual(popen.call_args.kwargs["env"][API_KEY_ENV], "synthetic-test-key")

    def test_model_catalog_default_uses_configured_model_and_effort(self):
        self.provider()
        bridge = object.__new__(Bridge)
        bridge.configuration = self.load()
        bridge.catalog = None
        bridge.catalog_lock = threading.Lock()
        bridge.cli_version = "synthetic-test"
        session = MagicMock()
        session.rpc.return_value = {"data": [
            {"id": "old-model", "isDefault": True},
            {"id": "gpt-test", "isDefault": False, "defaultReasoningEffort": "low",
             "supportedReasoningEfforts": [{"reasoningEffort": "high"}]}]}
        with patch.object(bridge, "session", return_value=(session, self.home / "unused")):
            catalog = bridge.models()
        selected = next(model for model in catalog["models"] if model["isDefault"])
        self.assertEqual(selected["id"], "gpt-test")
        self.assertEqual(selected["defaultReasoningEffort"], "high")
        self.assertNotIn("synthetic-test-key", json.dumps(catalog))


if __name__ == "__main__":
    unittest.main()
