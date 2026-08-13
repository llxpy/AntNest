# -*- coding: utf-8 -*-
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _mock_models_open(*_args, **_kwargs):
    import json

    body = json.dumps({"data": [{"id": "deepseek-v4-flash", "context_length": 128000}]}).encode()

    class _Resp:
        status = 200

        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    return _Resp()


class BridgeSettingsTest(unittest.TestCase):
    def test_ensure_loaded_uses_stored_ui_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "config.json")
            ui_cfg = os.path.join(tmp, "ui_config.json")
            Path(cfg).write_text(
                '{"api":{"base_url":"https://example.com/v1","model_name":"deepseek-v4-flash","api_key":"k"},'
                '"agent":{"max_depth":2,"max_clones":{"0":10}}}',
                encoding="utf-8",
            )
            Path(ui_cfg).write_text(
                '{"mcp_enabled":"true","mcp_config":"missing-mcp.json","skills_enabled":"true","skills_dir":""}',
                encoding="utf-8",
            )
            os.environ["ANT_CONFIG_PATH"] = cfg
            os.environ["ANT_UI_CONFIG_PATH"] = ui_cfg
            os.environ.setdefault("ANT_API_KEY", "k")
            os.environ.setdefault("ANT_MODEL_NAME", "deepseek-v4-flash")

            import antnest_bridge as bridge_mod

            importlib.reload(bridge_mod)
            core = bridge_mod.AntNestCore()
            core.set_ui_settings(
                {
                    "llm_base_url": "https://example.com/v1",
                    "llm_model": "deepseek-v4-flash",
                    "llm_api_key": "k",
                    "max_depth": "2",
                    "max_clones": "{0:10}",
                    "mcp_enabled": "true",
                    "mcp_config": "missing-mcp.json",
                    "skills_enabled": "true",
                    "skills_dir": "",
                }
            )
            with mock.patch("urllib.request.urlopen", _mock_models_open):
                import AntNest  # noqa: F401

                ok, _err = core.ensure_loaded()
            self.assertTrue(ok)
            self.assertTrue(core._ui_settings.get("mcp_enabled"))
            for k in ("ANT_CONFIG_PATH", "ANT_UI_CONFIG_PATH"):
                os.environ.pop(k, None)


class FetchModelsTest(unittest.TestCase):
    def test_fetch_models_list_success(self):
        import antnest_bridge as bridge_mod

        with mock.patch("urllib.request.urlopen", _mock_models_open):
            result = bridge_mod.fetch_models_list("https://example.com/v1", "test-key")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["models"]), 1)
        self.assertEqual(result["models"][0]["id"], "deepseek-v4-flash")


if __name__ == "__main__":
    unittest.main()
