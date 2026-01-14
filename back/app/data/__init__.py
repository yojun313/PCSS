from app.db import name_col
import os
import pandas as pd

name_dict = {
    doc["name"]: doc["score"]
    for doc in name_col.find({}, {"_id": 0, "name": 1, "score": 1})
}

conf_df = pd.read_csv(os.path.join(os.path.dirname(__file__), 'conf.csv'))
conf_param_list = conf_df['param'].tolist()
conf_param_dict = conf_df.set_index('conference')['param'].to_dict()
