"""
Example ML Pipeline DAG
Demonstrates end-to-end ML workflow with platform components
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
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
    'example_ml_pipeline',
    default_args=default_args,
    description='Example ML pipeline demonstrating platform integration',
    schedule_interval='@daily',
    catchup=False,
    tags=['ml', 'example', 'platform'],
)

# Task: Data extraction
extract_data = KubernetesPodOperator(
    namespace='ml-platform',
    image='python:3.11-slim',
    name='extract-data',
    task_id='extract_data',
    cmds=['python', '-c'],
    arguments=["""
import json
import os

# Simulate data extraction
data = {
    'records': 1000,
    'features': ['feature1', 'feature2', 'feature3'],
    'timestamp': '2024-01-01T00:00:00Z'
}
print(f"Extracted data: {json.dumps(data)}")
print(f"S3 Endpoint: {os.environ.get('S3_ENDPOINT_URL', 'not set')}")
"""],
    env_vars={
        'S3_ENDPOINT_URL': 'http://minio.minio.svc.cluster.local:80',
    },
    get_logs=True,
    dag=dag,
)

# Task: Feature engineering
transform_features = KubernetesPodOperator(
    namespace='ml-platform',
    image='python:3.11-slim',
    name='transform-features',
    task_id='transform_features',
    cmds=['python', '-c'],
    arguments=["""
import os

feast_server = os.environ.get('FEAST_FEATURE_SERVER', 'not set')
print(f"Connecting to Feast: {feast_server}")
print("Transforming features...")
print("Feature transformation complete")
"""],
    env_vars={
        'FEAST_FEATURE_SERVER': 'http://feast-server.ml-platform.svc.cluster.local:6566',
    },
    get_logs=True,
    dag=dag,
)

# Task: Model training
train_model = KubernetesPodOperator(
    namespace='ml-platform',
    image='python:3.11-slim',
    name='train-model',
    task_id='train_model',
    cmds=['python', '-c'],
    arguments=["""
import os

mlflow_uri = os.environ.get('MLFLOW_TRACKING_URI', 'not set')
print(f"MLflow Tracking URI: {mlflow_uri}")
print("Starting model training...")
print("Model training complete")
print("Model logged to MLflow")
"""],
    env_vars={
        'MLFLOW_TRACKING_URI': 'http://mlflow.ml-platform.svc.cluster.local:5000',
    },
    get_logs=True,
    dag=dag,
)

# Task: Model evaluation
evaluate_model = KubernetesPodOperator(
    namespace='ml-platform',
    image='python:3.11-slim',
    name='evaluate-model',
    task_id='evaluate_model',
    cmds=['python', '-c'],
    arguments=["""
print("Evaluating model...")
metrics = {
    'accuracy': 0.95,
    'precision': 0.94,
    'recall': 0.93,
    'f1_score': 0.935
}
print(f"Evaluation metrics: {metrics}")
print("Model evaluation complete")
"""],
    get_logs=True,
    dag=dag,
)

# Task: Register model
register_model = KubernetesPodOperator(
    namespace='ml-platform',
    image='python:3.11-slim',
    name='register-model',
    task_id='register_model',
    cmds=['python', '-c'],
    arguments=["""
import os

mlflow_uri = os.environ.get('MLFLOW_TRACKING_URI', 'not set')
print(f"Registering model in MLflow: {mlflow_uri}")
print("Model registered successfully")
print("Model version: 1")
"""],
    env_vars={
        'MLFLOW_TRACKING_URI': 'http://mlflow.ml-platform.svc.cluster.local:5000',
    },
    get_logs=True,
    dag=dag,
)

# Define task dependencies
extract_data >> transform_features >> train_model >> evaluate_model >> register_model
