"""Run a trained XGBoost model with numpy alone, so the API can forecast without installing xgboost.

xgboost needs scipy, and together they would push the Vercel function past its 250 MB size limit.
The FinalPweight model is small (a few hundred depth-4 trees), so walking the trees in numpy is
quick: every tree, for every animal, one level at a time.

Reads xgboost's JSON model format (`booster.save_model("model.json")`). Supports what
ml/train_xgb_recursive.py trains: a gbtree booster with numerical splits and an objective whose
prediction is the plain sum of the trees. Predictions match xgboost's to float32 precision;
backend/tests/unit/forecasting/test_tree_model.py checks the committed JSON against the .ubj model.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

MODEL_JSON = "xgb_weight.json"  # ml/models/xgb_weight.ubj in xgboost's JSON format
SUM_OBJECTIVES = {"reg:absoluteerror", "reg:squarederror"}  # prediction = base_score + sum of leaves


class TreeModel:
    """Stands in for xgboost.Booster: `inplace_predict` gives the same predictions."""

    def __init__(self, model: dict):
        learner = model["learner"]
        objective = learner["objective"]["name"]
        booster = learner["gradient_booster"]
        if booster["name"] != "gbtree" or objective not in SUM_OBJECTIVES:
            raise ValueError(f"Unsupported model: {booster['name']} booster with objective {objective}")
        trees = booster["model"]["trees"]
        if any(any(t["split_type"]) for t in trees):
            raise ValueError("Unsupported model: it has categorical splits")

        # "[1.6E-1]" in newer xgboost versions, "1.6E-1" in older ones
        self.base_score = np.float32(learner["learner_model_param"]["base_score"].strip("[]"))
        size = max(len(t["left_children"]) for t in trees)

        def stack(key: str, dtype, fill) -> np.ndarray:
            """One flat array for all trees: tree i's node j is at i * size + j."""
            out = np.full((len(trees), size), fill, dtype=dtype)
            for i, t in enumerate(trees):
                out[i, : len(t[key])] = t[key]
            return out.ravel()

        self.roots = np.arange(len(trees)) * size
        offsets = np.repeat(self.roots, size)
        left = stack("left_children", np.int64, -1)  # -1 on leaves
        right = stack("right_children", np.int64, -1)
        self.left = np.where(left == -1, -1, left + offsets)
        self.right = np.where(right == -1, -1, right + offsets)
        self.feature = stack("split_indices", np.int64, 0)
        self.value = stack("split_conditions", np.float32, 0)  # split threshold, or the output on a leaf
        self.default_left = stack("default_left", bool, False)  # where a missing (NaN) value goes
        self.depth = max(_depth(t) for t in trees)

    @classmethod
    def load(cls, path: Path) -> TreeModel:
        return cls(json.loads(Path(path).read_text()))

    def inplace_predict(self, data) -> np.ndarray:
        """One prediction per row of `data` (n, n_features), NaN = missing, like xgboost's."""
        x = np.asarray(data, dtype=np.float32)
        node = np.tile(self.roots, (len(x), 1))  # (rows, trees): every row starts at every root
        for _ in range(self.depth):
            value = np.take_along_axis(x, self.feature[node], axis=1)
            go_left = np.where(np.isnan(value), self.default_left[node], value < self.value[node])
            child = np.where(go_left, self.left[node], self.right[node])
            node = np.where(child == -1, node, child)  # rows already on a leaf stay there
        return self.base_score + self.value[node].sum(axis=1)


def _depth(tree: dict) -> int:
    """Deepest level of a tree. xgboost numbers every child after its parent."""
    depth = [0] * len(tree["left_children"])
    for node, (left, right) in enumerate(zip(tree["left_children"], tree["right_children"])):
        if left != -1:
            depth[left] = depth[right] = depth[node] + 1
    return max(depth)
