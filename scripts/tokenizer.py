# -*- coding: utf-8 -*-
"""
Created on Mon Sep 16 16:35:05 2024

@author: lucp8733
"""

import spacy
from tqdm import tqdm

def extraxt_lemmas(line, 
                   nlp_model):
    line = line.replace("\n", " ")
    if len(line) > 1000000:
        line = line[:1000000]
    nlp_line = nlp_model(line)
    lemma_line = [token.lemma_ for token in nlp_line if not token.is_stop]
    return " ".join(lemma_line)

def preprocess(dataset, tokenizer):
    tqdm.pandas(desc="!Tokenizing progress")
    
    nlp = spacy.load("nl_core_news_lg", disable = ['parser','ner'])
    
    prep_data = dataset.assign(
        lemma_text = dataset['long_text'].progress_apply(lambda x: extraxt_lemmas(x, nlp))
       )
    
    prep_data = tokenizer.transform(prep_data['lemma_text'].values)
    
    return prep_data