"""Serializable sklearn estimator factory; import does not train or write files."""
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from ml.features import InputValidator, FeatureBuilder, NUM_FEATURES, CAT_FEATURES

def create_pipeline(seed=42, C=1.0):
    numeric = Pipeline([("imputer", SimpleImputer(strategy="median")),
                        ("scaler", StandardScaler())])
    categories = Pipeline([("imputer", SimpleImputer(strategy="most_frequent")),
                           ("encoder", OneHotEncoder(handle_unknown="ignore", sparse_output=False))])
    return Pipeline([
        ("validation", InputValidator()), ("features", FeatureBuilder()),
        ("preprocessing", ColumnTransformer([("num", numeric, NUM_FEATURES),
                                              ("cat", categories, CAT_FEATURES)],
                                             verbose_feature_names_out=True)),
        ("classifier", LogisticRegression(C=C, random_state=seed, max_iter=2000,
                                           solver="lbfgs", class_weight=None)),
    ])
