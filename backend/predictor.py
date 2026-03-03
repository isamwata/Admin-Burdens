import json
import os
import pickle

import numpy as np
import pandas as pd
import tensorflow as tf
import tensorflow.keras.backend as K
from keras.models import model_from_json


def f1_loss(y_true, y_pred):
    tp = K.sum(K.cast(y_true * y_pred, "float"), axis=0)
    fp = 0.1 * K.sum(K.cast((1 - y_true) * y_pred, "float"), axis=0)
    fn = 100 * K.sum(K.cast(y_true * (1 - y_pred), "float"), axis=0)
    p = tp / (tp + fp + K.epsilon())
    r = tp / (tp + fn + K.epsilon())
    f1 = 2 * p * r / (p + r + K.epsilon())
    f1 = tf.where(tf.math.is_nan(f1), tf.zeros_like(f1), f1)
    return 1 - K.mean(f1)


def f1(y_true, y_pred):
    y_pred = K.round(y_pred)
    tp = K.sum(K.cast(y_true * y_pred, "float"), axis=0)
    fp = K.sum(K.cast((1 - y_true) * y_pred, "float"), axis=0)
    fn = K.sum(K.cast(y_true * (1 - y_pred), "float"), axis=0)
    p = tp / (tp + fp + K.epsilon())
    r = tp / (tp + fn + K.epsilon())
    f1 = 2 * p * r / (p + r + K.epsilon())
    f1 = tf.where(tf.math.is_nan(f1), tf.zeros_like(f1), f1)
    return K.mean(f1)


def _find_latest(folder, suffix):
    files = [f for f in os.listdir(folder) if f.endswith(suffix)]
    if not files:
        raise FileNotFoundError(f"No file ending with '{suffix}' found in {folder}")
    return os.path.join(folder, sorted(files)[-1])


def predict_documents(dataset: pd.DataFrame, config: dict) -> pd.DataFrame:
    model_location = config["model_location"]
    mode = config["embedder_or_tokenizer"]

    if mode == "embedder":
        from sentence_transformers import SentenceTransformer
        from scripts.embedder import preprocess as embed_preprocess

        model = SentenceTransformer(config["sentence_embedder"])
        preprocessed = embed_preprocess(dataset, model)
        preprocessed = np.array(list(preprocessed["padded_embedding"]))
        model_json_path = _find_latest(model_location + "/lstm", "__model.json")
        weights_path = _find_latest(model_location + "/lstm", "__model.h5")

    else:  # tokenizer
        from scripts.tokenizer import preprocess as token_preprocess

        with open(_find_latest(model_location, "__tokenizer.pickle"), "rb") as f:
            tokenizer = pickle.load(f)
        with open(_find_latest(model_location, "__normmeans.pickle"), "rb") as f:
            norm_means = pickle.load(f)
        with open(_find_latest(model_location, "__normstds.pickle"), "rb") as f:
            norm_stds = pickle.load(f)
        with open(_find_latest(model_location, "__pca.pickle"), "rb") as f:
            pca_model = pickle.load(f)

        preprocessed = token_preprocess(dataset, tokenizer)
        preprocessed = (preprocessed - norm_means) / norm_stds
        if pca_model is not None:
            preprocessed = pca_model.transform(np.array(preprocessed))

        model_json_path = _find_latest(model_location + "/nn", "__model.json")
        weights_path = _find_latest(model_location + "/nn", ".weights.h5")

    with open(model_json_path) as f:
        model_json = json.load(f)

    trained_model = model_from_json(
        json.dumps(model_json),
        custom_objects={"f1_loss": f1_loss, "f1": f1},
    )
    trained_model.load_weights(weights_path)

    predictions = trained_model.predict(preprocessed)
    result = dataset.assign(
        prediction=np.round(predictions, 0),
        certainty=2 * (np.round(predictions, 2) - 0.5),
    )
    return result
