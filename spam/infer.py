"""Comment-spam inference: e5-small embedding + logistic-regression head.

The deployed model is a frozen SentenceTransformer embedding plus a tiny logistic
head (see trains/spam/train_comment_head.py and plan 11.8). At runtime we only
need the embedding model + a numpy dot-product; no sklearn dependency.

The model tar extracts to ./model/ and contains the SentenceTransformer files
plus head.json: {coef, intercept, base_prefix, threshold, dim}.
"""
import json
import math

import numpy as np
from sentence_transformers import SentenceTransformer

CHECKPOINT = "./model/"
SEQUENCE_LENGTH = 512

_model = SentenceTransformer(CHECKPOINT)
_model.max_seq_length = SEQUENCE_LENGTH

with open(CHECKPOINT + "head.json") as _fh:
    _head = json.load(_fh)
_COEF = np.asarray(_head["coef"], dtype="float32")
_INTERCEPT = float(_head["intercept"])
_PREFIX = _head.get("base_prefix", "")

# recommended decision threshold; consumers (e.g. the coast-guard bot tiers) may
# override. Exposed for callers that want the model's own suggested cutoff.
THRESHOLD = float(_head.get("threshold", 0.5))


def _sigmoid(z):
    return 1.0 / (1.0 + math.exp(-z))


def infer(texts: list[str]):
    """Return P(spam) in [0,1] for each text (logreg on L2-normalized embedding)."""
    embs = _model.encode(
        [_PREFIX + (t or "") for t in texts],
        normalize_embeddings=True,
        batch_size=32,
    )
    logits = embs @ _COEF + _INTERCEPT
    return [_sigmoid(float(z)) for z in np.atleast_1d(logits)]
