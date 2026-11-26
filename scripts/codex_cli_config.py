"""Read the current CC Switch Codex provider without changing personal settings."""
from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tomllib
from urllib.parse import urlsplit

from app.model_runtime import ModelRuntimeError

API_KEY_ENV = "WORKBENCH_CODEX_API_KEY"
RUNTIME_PROVIDER = "workbench_ccswitch"


def config_error():
    return ModelRuntimeError("MODEL_CONFIG_INVALID", "CC Switch 的 Codex 配置不可用，请确认当前服务商、模型和凭据后重启服务", True)


def toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if isinstance(value, dict):
        return "{" + ", ".join(json.dumps(k) + " = " + toml_value(v) for k, v in value.items()) + "}"
    raise config_error()


@dataclass
class CliConfiguration:
    arguments: list[str]
    environment: dict = field(repr=False)
    public: dict
    isolated: bool = False
    provider_configuration: dict = field(default_factory=dict, repr=False)

    def session_environment(self, workdir):
        environment = dict(self.environment)
        if self.isolated:
            cli_home = Path(workdir) / "cli-home"
            cli_home.mkdir(exist_ok=True)
            environment["CODEX_HOME"] = str(cli_home)
        return environment

    def verify(self, configuration):
        if self.public["source"] != "cc-switch":
            return
        expected_provider = RUNTIME_PROVIDER if self.isolated else "openai"
        actual_provider = configuration.get("model_providers", {}).get(expected_provider, {})
        route_matches = all(actual_provider.get(key) == self.provider_configuration.get(key)
                            for key in ("base_url", "wire_api", "env_key", "requires_openai_auth")) if self.isolated else True
        if (configuration.get("model_provider") != expected_provider
                or configuration.get("model") != self.public["configured_model"]
                or not route_matches
                or (self.public.get("reasoning_effort")
                    and configuration.get("model_reasoning_effort") != self.public["reasoning_effort"])):
            raise ModelRuntimeError("MODEL_CONFIG_MISMATCH", "CLI 生效配置与 CC Switch 当前选择不一致，请重启模型服务", True)


def load_cli_configuration(*, home=None, environment=None):
    environment = dict(os.environ if environment is None else environment)
    home = Path(home) if home is not None else Path.home()
    explicit_root = environment.get("ONTODATAWORKS_CC_SWITCH_HOME")
    root = Path(explicit_root) if explicit_root else home / ".cc-switch"
    database = root / "cc-switch.db"
    loaded_at = datetime.now(timezone.utc).isoformat()
    if not database.exists():
        if explicit_root:
            raise config_error()
        return CliConfiguration([], environment, {"source": "codex-user-config", "loaded_at": loaded_at})
    try:
        settings_path = root / "settings.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8-sig")) if settings_path.exists() else {}
        selected = settings.get("currentProviderCodex")
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            if selected:
                rows = db.execute("SELECT id,name,settings_config FROM providers WHERE app_type='codex' AND id=?", (selected,)).fetchall()
            else:
                rows = db.execute("SELECT id,name,settings_config FROM providers WHERE app_type='codex' AND is_current=1").fetchall()
            if len(rows) != 1:
                raise config_error()
            provider_id, name, raw = rows[0]
            proxy = db.execute("SELECT live_takeover_active,listen_address,listen_port FROM proxy_config WHERE app_type='codex'").fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='proxy_config'").fetchone() else None
        payload = json.loads(raw)
        configuration = tomllib.loads(payload["config"])
        model = configuration.get("model")
        provider_key = configuration.get("model_provider", "openai")
        effort = configuration.get("model_reasoning_effort")
        if not isinstance(model, str) or not model.strip() or len(model) > 160:
            raise config_error()
        if effort is not None and effort not in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
            raise config_error()
        provider = dict(configuration.get("model_providers", {}).get(provider_key, {}))
        if proxy and proxy[0]:
            # Takeover owns the live local route. Never bypass an active proxy.
            source_home = Path(environment.get("CODEX_HOME") or home / ".codex")
            live = tomllib.loads((source_home / "config.toml").read_text(encoding="utf-8-sig"))
            live_provider = live.get("model_providers", {}).get(live.get("model_provider"), {})
            route = urlsplit(live_provider.get("base_url", ""))
            if route.hostname not in {"127.0.0.1", "localhost", "::1"} or route.port != proxy[2]:
                raise config_error()
            provider["base_url"] = live_provider["base_url"]
        credential = (payload.get("auth") or {}).get("OPENAI_API_KEY") or provider.get("experimental_bearer_token")
        if not credential and provider.get("env_key"):
            credential = environment.get(provider["env_key"])
        public = {"source": "cc-switch", "provider_id": provider_id, "provider_name": str(name)[:120],
                  "configured_model": model, "reasoning_effort": effort, "loaded_at": loaded_at,
                  "proxy_takeover": bool(proxy and proxy[0])}
        arguments = ["-c", "model=" + toml_value(model)]
        if effort:
            arguments.extend(["-c", "model_reasoning_effort=" + toml_value(effort)])
        if provider_key == "openai" and not credential and not public["proxy_takeover"]:
            # The CLI continues to use its existing official login; no auth file is read or copied here.
            arguments.extend(["-c", 'model_provider="openai"'])
            return CliConfiguration(arguments, environment, public)
        if not isinstance(credential, str) or not credential.strip():
            raise config_error()
        if provider_key == "openai" and not provider.get("base_url"):
            provider["base_url"] = "https://api.openai.com/v1"
        base_url = provider.get("base_url")
        route = urlsplit(base_url or "")
        if route.scheme not in {"http", "https"} or not route.hostname or route.username or route.password or route.query or route.fragment:
            raise config_error()
        runtime_provider = {"name": str(name)[:120], "base_url": base_url,
                            "wire_api": provider.get("wire_api", "responses"),
                            "env_key": API_KEY_ENV, "requires_openai_auth": False}
        for key in ("request_max_retries", "stream_max_retries", "stream_idle_timeout_ms", "supports_websockets"):
            if key in provider:
                runtime_provider[key] = provider[key]
        headers = dict(provider.get("env_http_headers") or {})
        for index, (header, value) in enumerate((provider.get("http_headers") or {}).items()):
            variable = "WORKBENCH_CODEX_HEADER_" + str(index)
            headers[header] = variable
            environment[variable] = str(value)
        if headers:
            if any(not environment.get(variable) for variable in headers.values()):
                raise config_error()
            runtime_provider["env_http_headers"] = headers
        for variable in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "CODEX_API_KEY"):
            environment.pop(variable, None)
        environment[API_KEY_ENV] = credential
        arguments.extend(["-c", "model_provider=" + toml_value(RUNTIME_PROVIDER),
                          "-c", "model_providers." + RUNTIME_PROVIDER + "=" + toml_value(runtime_provider)])
        return CliConfiguration(arguments, environment, public, isolated=True, provider_configuration=runtime_provider)
    except ModelRuntimeError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
        raise config_error() from None
