import itertools
import os
import sys

import lightgbm as lgb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from sklearn.metrics import roc_auc_score
from tqdm import tqdm

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import generate_submit, load_dataset, send_line_notification
from category_encoders import TargetEncoder
from config import *
from utils import timer

sns.set_style('darkgrid')

NAME = Path(__file__).stem
print(NAME)

with timer('load datasets'):
    feats = ['main_numeric', 'main_days_to_years', 'main_days_pairwise', 'main_target_enc',
             'main_ext_source_pairwise', 'bureau', 'prev', 'pos', 'credit', 'inst', 'pos_latest', 'credit_latest']
    X_train, y_train, X_test, cv = load_dataset(feats)

with timer('generate money pairwise features'):
    money_cols = X_train.filter(regex='AMT_(?!REQ)(?!.*_min)').columns
    print(money_cols)
    l = len(list(itertools.combinations(money_cols, 2)))
    for i, j in tqdm(itertools.combinations(money_cols, 2), total=l):
        X_train[f'{i}_minus_{j}'] = X_train[i] - X_train[j]
        X_test[f'{i}_minus_{j}'] = X_test[i] - X_test[j]

print('train:', X_train.shape)
print('test :', X_test.shape)
# print('feats: ', X_train.columns.tolist())

lgb_params = {
    'n_estimators': 4000,
    'learning_rate': 0.1,
    'num_leaves': 31,
    'colsample_bytree': 0.8,
    'subsample': 0.8,
    'reg_alpha': 0.1,
    'reg_lambda': 0.1,
    'min_split_gain': 0.01,
    'min_child_weight': 2,
    'random_state': 77,
    'silent': True,
    'n_jobs': -1,
}
fit_params = {
    'eval_metric': 'auc',
    'early_stopping_rounds': 150,
    'verbose': 100
}

with timer('training'):
    cv_results = []
    val_series = y_train.copy()
    test_df = pd.DataFrame()
    feat_df = pd.DataFrame(index=X_train.columns)
    for i, (trn_idx, val_idx) in enumerate(cv.split(X_train, y_train)):
        X_trn = X_train.iloc[trn_idx].copy()
        y_trn = y_train[trn_idx].copy()
        X_val = X_train.iloc[val_idx].copy()
        y_val = y_train[val_idx].copy()
        print('=' * 30, f'FOLD {i+1}/{cv.get_n_splits()}', '=' * 30)
        with timer('target encoding'):
            cat_cols = [f for f in X_trn.columns if X_trn[f].dtype=='object']
            te = TargetEncoder(cols=cat_cols)
            X_trn.loc[:, cat_cols] = te.fit_transform(X_trn.loc[:, cat_cols], y_trn).values
            X_val.loc[:, cat_cols] = te.transform(X_val.loc[:, cat_cols]).values
            X_test_ = X_test.copy()
            X_test_.loc[:, cat_cols] = te.transform(X_test.loc[:, cat_cols]).values
            X_trn.fillna(-9999)
            X_val.fillna(-9999)
            X_test_.fillna(-9999)
        
        with timer('fit'):
            model = lgb.LGBMClassifier(**lgb_params)
            model.fit(X_trn, y_trn, eval_set=[(X_trn, y_trn), (X_val, y_val)], **fit_params)
        
        p = model.predict_proba(X_val)[:, 1]
        val_series.iloc[val_idx] = p
        cv_results.append(roc_auc_score(y_val, p))
        test_df[i] = model.predict_proba(X_test_)[:, 1]
        feat_df[i] = model.feature_importances_

val_df = pd.DataFrame({'TARGET': y_train, 'p': val_series}).to_csv(OUTPUT / f'{NAME}_cv_pred.csv', index=False)
valid_score = np.mean(cv_results)

message = f"""cv: {valid_score: .5f}
feats: {feats}
model_params: {lgb_params}
fit_params: {fit_params}"""
send_line_notification(message)
print('=' * 60)
print(message)
print('=' * 60)

pred = test_df.mean(axis=1).values.ravel()
generate_submit(pred, f'{NAME}_{valid_score:.4f}')

print('output feature importances')
feat_df.mean(axis=1).sort_values(ascending=False).to_csv(OUTPUT / f'{NAME}_feat.csv')
imp = feat_df.mean(axis=1).sort_values(ascending=False)[:50]
imp[::-1].plot.barh(figsize=(20, 15))
plt.savefig(str(DROPBOX / f'{NAME}_feature_importances.pdf'), bbox_inches='tight')
