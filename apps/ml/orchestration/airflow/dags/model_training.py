"""
Model Training DAG
Demonstrates MLflow integration for experiment tracking
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator

default_args = {
    'owner': 'ml-platform',
    'depends_on_past': False,
    'start_date': datetime(2024, 1, 1),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}

dag = DAG(
    'model_training',
    default_args=default_args,
    description='Model training pipeline with MLflow tracking',
    schedule_interval='@weekly',
    catchup=False,
    tags=['ml', 'training', 'mlflow'],
)

# Common environment variables
mlflow_env = {
    'MLFLOW_TRACKING_URI': 'http://mlflow.ml-platform.svc.cluster.local:5000',
    'MLFLOW_S3_ENDPOINT_URL': 'http://minio.minio.svc.cluster.local:80',
    'FEAST_FEATURE_SERVER': 'http://feast-server.ml-platform.svc.cluster.local:6566',
}

# Task: Prepare training data
prepare_data = KubernetesPodOperator(
    namespace='ml-platform',
    image='python:3.11-slim',
    name='prepare-training-data',
    task_id='prepare_training_data',
    cmds=['python', '-c'],
    arguments=["""
import os
import json

feast_server = os.environ.get('FEAST_FEATURE_SERVER')
print(f"Fetching features from Feast: {feast_server}")

# Simulate data preparation
dataset = {
    'train_samples': 8000,
    'validation_samples': 1000,
    'test_samples': 1000,
    'features': 50,
    'target': 'purchase_probability'
}
print(f"Training dataset prepared: {json.dumps(dataset)}")
"""],
    env_vars=mlflow_env,
    get_logs=True,
    dag=dag,
)

# Task: Train model (baseline)
train_baseline = KubernetesPodOperator(
    namespace='ml-platform',
    image='python:3.11-slim',
    name='train-baseline',
    task_id='train_baseline',
    cmds=['python', '-c'],
    arguments=["""
import os
import json

mlflow_uri = os.environ.get('MLFLOW_TRACKING_URI')
print(f"MLflow URI: {mlflow_uri}")

print("Training baseline model (Logistic Regression)...")
metrics = {
    'model': 'logistic_regression',
    'accuracy': 0.82,
    'precision': 0.80,
    'recall': 0.79,
    'f1': 0.795,
    'training_time_seconds': 45
}
print(f"Baseline metrics: {json.dumps(metrics)}")
print("Baseline model logged to MLflow")
"""],
    env_vars=mlflow_env,
    get_logs=True,
    dag=dag,
)

# Task: Train model (XGBoost)
train_xgboost = KubernetesPodOperator(
    namespace='ml-platform',
    image='python:3.11-slim',
    name='train-xgboost',
    task_id='train_xgboost',
    cmds=['python', '-c'],
    arguments=["""
import os
import json

mlflow_uri = os.environ.get('MLFLOW_TRACKING_URI')
print(f"MLflow URI: {mlflow_uri}")

print("Training XGBoost model...")
metrics = {
    'model': 'xgboost',
    'accuracy': 0.91,
    'precision': 0.90,
    'recall': 0.89,
    'f1': 0.895,
    'training_time_seconds': 180
}
print(f"XGBoost metrics: {json.dumps(metrics)}")
print("XGBoost model logged to MLflow")
"""],
    env_vars=mlflow_env,
    get_logs=True,
    dag=dag,
)

# Task: Train model (LightGBM)
train_lightgbm = KubernetesPodOperator(
    namespace='ml-platform',
    image='python:3.11-slim',
    name='train-lightgbm',
    task_id='train_lightgbm',
    cmds=['python', '-c'],
    arguments=["""
import os
import json

mlflow_uri = os.environ.get('MLFLOW_TRACKING_URI')
print(f"MLflow URI: {mlflow_uri}")

print("Training LightGBM model...")
metrics = {
    'model': 'lightgbm',
    'accuracy': 0.92,
    'precision': 0.91,
    'recall': 0.90,
    'f1': 0.905,
    'training_time_seconds': 120
}
print(f"LightGBM metrics: {json.dumps(metrics)}")
print("LightGBM model logged to MLflow")
"""],
    env_vars=mlflow_env,
    get_logs=True,
    dag=dag,
)

# Task: Compare models
compare_models = KubernetesPodOperator(
    namespace='ml-platform',
    image='python:3.11-slim',
    name='compare-models',
    task_id='compare_models',
    cmds=['python', '-c'],
    arguments=["""
import os
import json

mlflow_uri = os.environ.get('MLFLOW_TRACKING_URI')
print(f"Comparing models from MLflow: {mlflow_uri}")

results = {
    'best_model': 'lightgbm',
    'best_f1_score': 0.905,
    'comparison': {
        'logistic_regression': 0.795,
        'xgboost': 0.895,
        'lightgbm': 0.905
    }
}
print(f"Model comparison: {json.dumps(results)}")
print("Best model: LightGBM")
"""],
    env_vars=mlflow_env,
    get_logs=True,
    dag=dag,
)

# Task: Register best model
register_best_model = KubernetesPodOperator(
    namespace='ml-platform',
    image='python:3.11-slim',
    name='register-best-model',
    task_id='register_best_model',
    cmds=['python', '-c'],
    arguments=["""
import os
import json
from datetime import datetime

mlflow_uri = os.environ.get('MLFLOW_TRACKING_URI')
print(f"Registering best model to MLflow: {mlflow_uri}")

registration = {
    'model_name': 'purchase_prediction_model',
    'model_version': 1,
    'stage': 'Staging',
    'registered_at': datetime.utcnow().isoformat()
}
print(f"Model registered: {json.dumps(registration)}")
print("Model ready for deployment")
"""],
    env_vars=mlflow_env,
    get_logs=True,
    dag=dag,
)

# Define task dependencies
prepare_data >> [train_baseline, train_xgboost, train_lightgbm]
[train_baseline, train_xgboost, train_lightgbm] >> compare_models >> register_best_model
