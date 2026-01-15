from app.db import name_col
import os
import pandas as pd

name_dict = {
    doc["name"]: doc["score"]
    for doc in name_col.find({}, {"_id": 0, "name": 1, "score": 1})
}

#name_dict = {}
conf_df = pd.read_csv(os.path.join(os.path.dirname(__file__), 'conf.csv'))
conf_param_list = conf_df['param'].tolist()
conf_param_dict = conf_df.set_index('conference')['param'].to_dict()

def get_conferences_for_ui():
    # script.js가 기대하는: [{kind, conference}, ...]
    cols = ["kind", "conference"]
    missing = [c for c in cols if c not in conf_df.columns]
    if missing:
        raise RuntimeError(f"conf.csv missing columns: {missing}")
    return conf_df[cols].to_dict(orient="records")