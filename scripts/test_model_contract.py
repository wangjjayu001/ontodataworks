"""Cross-boundary regression tests for the Codex CLI model contract."""
import sys
from pathlib import Path
from unittest.mock import patch
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "scripts")]

from app.model_runtime import (  # noqa: E402
    MODEL_CONTRACT_VERSION, METADATA_CANDIDATE_SCHEMA, ModelRuntimeError,
    canonical_hash, derive_metadata_candidate, invoke, list_models, stage_schema_hashes,
)
from codex_bridge import Bridge  # noqa: E402


class ModelContractTests(unittest.TestCase):
    def test_model_directory_rejects_bridge_with_stale_stage_schemas(self):
        stale = {"model_contract_version": MODEL_CONTRACT_VERSION,
                 "stage_schema_hashes": {"intent": "old-hash"}, "models": []}
        with patch("app.model_runtime._bridge_call", return_value=stale):
            with self.assertRaises(ModelRuntimeError) as exc:
                list_models()
        self.assertEqual(exc.exception.code, "MODEL_CONTRACT_MISMATCH")
        self.assertTrue(exc.exception.retryable)

    def test_candidate_request_and_response_share_the_same_schema_hash(self):
        output = {"candidate_plan": None, "data_gaps": ["缺少费用字段"]}
        response = {"model_contract_version": MODEL_CONTRACT_VERSION,
            "output_schema_hash": canonical_hash(METADATA_CANDIDATE_SCHEMA), "output": output}
        with patch("app.model_runtime._bridge_call", return_value=response) as call:
            result = derive_metadata_candidate("统计订单量", {"metadata_candidates": []},
                {"model": "model-a"}, request_id="candidate-test")
        body = call.call_args.args[1]
        self.assertEqual(body["stage"], "metadata_candidate")
        self.assertEqual(body["model_contract_version"], MODEL_CONTRACT_VERSION)
        self.assertEqual(body["output_schema_hash"], canonical_hash(METADATA_CANDIDATE_SCHEMA))
        self.assertEqual(result["output"], output)

    def test_bridge_rejects_a_backend_request_with_the_wrong_stage_hash_before_spawning_cli(self):
        bridge = object.__new__(Bridge)
        body = {"request_id": "request-1", "stage": "metadata_candidate",
            "input": {"question": "统计订单量", "context": {}}, "model_selection": {},
            "data_classification": "synthetic", "model_contract_version": MODEL_CONTRACT_VERSION,
            "output_schema_hash": "0" * 64}
        with self.assertRaises(ModelRuntimeError) as exc:
            bridge.invoke(body)
        self.assertEqual(exc.exception.code, "MODEL_CONTRACT_MISMATCH")

    def test_unregistered_stage_is_rejected_by_the_backend(self):
        with self.assertRaises(ModelRuntimeError) as exc:
            invoke("metadata_sql", {}, model_selection={"model": "model-a"})
        self.assertEqual(exc.exception.code, "MODEL_STAGE_UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
