# -*- coding: utf-8 -*-
"""
Created on Wed Jul 17 15:19:45 2024

@author: lucp8733
"""
import pandas as pd
import numpy as np

import os
import json
import pickle
import datetime

import tensorflow.keras.backend as K
from keras.models import load_model, model_from_json
import tensorflow as tf
from sentence_transformers import SentenceTransformer


from scripts.embedder import preprocess as embed_preprocess
from scripts.tokenizer import preprocess as token_preprocess

with open('config.json') as cfile:
    config_data = json.load(cfile)
    
def find_latest_model(model_folder, file_suffix = "__model.json"):
    saved_models = [file for file in os.listdir(model_folder) if file.endswith(file_suffix)]
    last_model_json = sorted(saved_models)[-1]
        
    return model_folder + "/" + last_model_json


data_files = [file for file in os.listdir(config_data['predictions']['input_location']) if file.endswith(".xlsx") and not file.startswith("~$")]
    
datasets = [pd.read_excel(config_data['predictions']['input_location'] + "/" + df, index_col=0) for df in data_files]
dataset = pd.concat(datasets, axis = 0, ignore_index=True)

prepocessed_data = None
model_json = None

if(config_data['predictions']['embedder_or_tokenizer'] == "embedder"):
    model = SentenceTransformer(
        config_data['predictions']['sentence_embedder'])
    prepocessed_data = embed_preprocess(dataset, model)
    prepocessed_data = np.array(list(prepocessed_data["padded_embedding"]))
    
    model_json = find_latest_model(
        config_data['predictions']['model_location'] + '/lstm',
        file_suffix = "__model.json"
        )
    
    trained_weights_file = find_latest_model(
        config_data['predictions']['model_location']+ '/lstm', 
        file_suffix = "__model.h5")


if(config_data['predictions']['embedder_or_tokenizer'] == "tokenizer"):
    tokenizer = find_latest_model(
        config_data['predictions']['model_location'],
        file_suffix = "__tokenizer.pickle"
        )
    
    with open(tokenizer, "rb") as f:
        tokenizer = pickle.load(f)
    
    norm_means = find_latest_model(
        config_data['predictions']['model_location'],
        file_suffix = "__normmeans.pickle"
        )
    
    with open(norm_means, "rb") as f:
        norm_means = pickle.load(f)
        
    norm_stds = find_latest_model(
        config_data['predictions']['model_location'],
        file_suffix = "__normstds.pickle"
        )
    
    with open(norm_stds, "rb") as f:
        norm_stds = pickle.load(f)
        
    pca_model = find_latest_model(
        config_data['predictions']['model_location'],
        file_suffix = "__pca.pickle"
        )
    
    with open(pca_model, "rb") as f:
        pca_model = pickle.load(f)
        
    prepocessed_data = token_preprocess(dataset, tokenizer)
    prepocessed_data = (prepocessed_data - norm_means) / norm_stds 
    
    if pca_model is not None:
        prepocessed_data = pca_model.transform(np.array(prepocessed_data))

    # model_full = find_latest_model(
    #     config_data['predictions']['model_location'] + '/nn',
    #     file_suffix = "__model.keras"
    #     )
    
    model_json = find_latest_model(
        config_data['predictions']['model_location'] + '/nn',
        file_suffix = "__model.json"
        )
        
    trained_weights_file = find_latest_model(
        config_data['predictions']['model_location']+ '/nn', 
        file_suffix = ".weights.h5")


with open(model_json) as f:
    model_json = json.load(f)



def f1_loss(y_true, y_pred):
    
    tp = K.sum(K.cast(y_true*y_pred, 'float'), axis=0)
    fp = 0.1*K.sum(K.cast((1-y_true)*y_pred, 'float'), axis=0)
    fn = 100*K.sum(K.cast(y_true*(1-y_pred), 'float'), axis=0)

    p = tp / (tp + fp + K.epsilon())
    r = tp / (tp + fn + K.epsilon())

    f1 = 2*p*r / (p+r+K.epsilon())
    f1 = tf.where(tf.math.is_nan(f1), tf.zeros_like(f1), f1)
    return 1 - K.mean(f1)

def f1(y_true, y_pred):
    y_pred = K.round(y_pred)
    tp = K.sum(K.cast(y_true*y_pred, 'float'), axis=0)
    fp = K.sum(K.cast((1-y_true)*y_pred, 'float'), axis=0)
    fn = K.sum(K.cast(y_true*(1-y_pred), 'float'), axis=0)

    p = tp / (tp + fp + K.epsilon())
    r = tp / (tp + fn + K.epsilon())

    f1 = 2*p*r / (p+r+K.epsilon())
    f1 = tf.where(tf.math.is_nan(f1), tf.zeros_like(f1), f1)
    return K.mean(f1)

trained_model = model_from_json(json.dumps(model_json),
                           custom_objects={'f1_loss': f1_loss,
                                           'f1':f1})

# trained_model = load_model(model_full,
#                            custom_objects={'f1_loss': f1_loss,
#                                            'f1':f1})

trained_model.load_weights(trained_weights_file)

predictions = trained_model.predict(prepocessed_data)

result = dataset.assign(prediction = np.round(predictions,0))
result = result.assign(certainty = 2*(np.round(predictions,2)-0.5))

ts = datetime.datetime.timestamp(datetime.datetime.now())
ts = str(ts).replace(".", "_")

result.to_excel(
    config_data['predictions']['output_location'] + "/" + ts + "__results.xlsx"
)