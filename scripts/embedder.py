# -*- coding: utf-8 -*-
"""
Created on Wed Jul 17 15:20:21 2024

@author: lucp8733
"""

# -*- coding: utf-8 -*-
"""
Created on Thu Jan 18 16:40:48 2024

@author: lucp8733
"""
import numpy as np

from tqdm import tqdm

def create_embedding(text, model):
    lines = text.splitlines()
    sent_embeddings = [
        model.encode(line) for line in lines
        ]
    return sent_embeddings

def pad_embedding(embedding, target_size):
    embedding = np.array(embedding)
    target_diff = target_size - embedding.shape[0]
    if target_diff > 0:
        padded_embedding = np.pad(embedding,[(target_diff,0),(0,0)])
    else:
        padded_embedding = embedding[-target_size:]
    return padded_embedding

def preprocess(dataset, model):
    tqdm.pandas(desc="!Embedding progress")
    
    prep_data = dataset.assign(
        embedding = dataset['long_text'].progress_apply(lambda x: create_embedding(x, model))
        )
    
    prep_data = prep_data.assign(
        nr_lines = prep_data["embedding"].apply(len)
        )
    
    prep_data = prep_data.assign(
        padded_embedding = prep_data["embedding"].apply(lambda x: pad_embedding(x,100))
        )
    
    return prep_data