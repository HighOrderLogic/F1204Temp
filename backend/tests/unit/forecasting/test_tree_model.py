from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))  # for the top-level ml package

from ml.forecasting import MODEL_FILE, WINDOW, make_features  # noqa: E402
from ml.tree_model import MODEL_JSON, TreeModel  # noqa: E402

MODELS_DIR = REPO_ROOT / "ml" / "models"


def one_tree_model(objective: str = "reg:absoluteerror") -> dict:
    """base_score 0.5 and one tree: x0 < 10 (or missing) -> -1, otherwise +3."""
    tree = {
        "left_children": [1, -1, -1],
        "right_children": [2, -1, -1],
        "split_indices": [0, 0, 0],
        "split_conditions": [10.0, -1.0, 3.0],
        "default_left": [1, 0, 0],
        "split_type": [0, 0, 0],
    }
    return {
        "learner": {
            "objective": {"name": objective},
            "learner_model_param": {"base_score": "5E-1"},
            "gradient_booster": {"name": "gbtree", "model": {"trees": [tree]}},
        }
    }


@pytest.mark.unit
def test_walks_the_trees_like_xgboost():
    pred = TreeModel(one_tree_model()).inplace_predict(np.array([[9.0], [10.0], [np.nan]]))
    np.testing.assert_allclose(pred, [-0.5, 3.5, -0.5])  # strict "<", and NaN follows default_left


@pytest.mark.unit
def test_rejects_models_it_cannot_run():
    with pytest.raises(ValueError, match="binary:logistic"):
        TreeModel(one_tree_model("binary:logistic"))


@pytest.mark.unit
def test_committed_json_model_matches_the_xgboost_model():
    """ml/models/xgb_weight.json must be the .ubj model: export it again after every retrain."""
    meta = json.loads((MODELS_DIR / "model_meta.json").read_text())
    model = TreeModel.load(MODELS_DIR / MODEL_JSON)
    assert len(model.roots) == meta["n_estimators"]
    assert model.depth == meta["params"]["max_depth"]

    rng = np.random.default_rng(0)
    windows = 300 + np.cumsum(rng.normal(1.0, 2.0, (500, WINDOW)), axis=1)
    windows[rng.random(windows.shape) < 0.3] = np.nan  # gaps, as in short histories
    windows[:, -1] = rng.normal(400, 80, 500)
    x = make_features(windows, np.full(len(windows), 1.0))

    preds = model.inplace_predict(x)
    assert preds.shape == (500,)
    assert np.all(np.isfinite(preds))
    np.testing.assert_allclose(
        preds[:5],
        [6.4808059, -0.4613999, 4.9852924, 5.9592481, 0.0682795],
        atol=1e-4,
    )

    if importlib.util.find_spec("xgboost") is not None:
        import xgboost as xgb

        booster = xgb.Booster()
        booster.load_model(MODELS_DIR / MODEL_FILE)
        np.testing.assert_allclose(preds, booster.inplace_predict(x), atol=1e-4)
