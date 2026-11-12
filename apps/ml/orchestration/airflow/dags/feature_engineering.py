"""
Feature Engineering DAG
Demonstrates Feast feature store integration
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
    'feature_engineering',
    default_args=default_args,
    description='Feature engineering pipeline with Feast integration',
    schedule_interval='@hourly',
    catchup=False,
    tags=['ml', 'features', 'feast'],
)

# Common environment variables
feast_env = {
    'FEAST_FEATURE_SERVER': 'http://feast-server.ml-platform.svc.cluster.local:6566',
    'MINIO_ENDPOINT': 'http://minio.minio.svc.cluster.local:80',
    'REDIS_HOST': 'redis.redis.svc.cluster.local',
}

# Task: Extract raw data
extract_raw_data = KubernetesPodOperator(
    namespace='ml-platform',
    image='python:3.11-slim',
    name='extract-raw-data',
    task_id='extract_raw_data',
    cmds=['python', '-c'],
    arguments=["""
import json
from datetime import datetime

# Simulate raw data extraction
print("Extracting raw data from source systems...")
data = {
    'user_transactions': 5000,
    'product_views': 15000,
    'user_profiles': 2000,
    'extracted_at': datetime.utcnow().isoformat()
}
print(f"Raw data extracted: {json.dumps(data)}")
"""],
    get_logs=True,
    dag=dag,
)

# Task: Compute user features
compute_user_features = KubernetesPodOperator(
    namespace='ml-platform',
    image='python:3.11-slim',
    name='compute-user-features',
    task_id='compute_user_features',
    cmds=['python', '-c'],
    arguments=["""
import os

feast_server = os.environ.get('FEAST_FEATURE_SERVER')
print(f"Feast server: {feast_server}")

print("Computing user features...")
features = [
    'user_total_purchases_7d',
    'user_avg_order_value_30d',
    'user_purchase_frequency',
    'user_last_active_days'
]
print(f"Computed features: {features}")
print("User features computed successfully")
"""],
    env_vars=feast_env,
    get_logs=True,
    dag=dag,
)

# Task: Compute product features
compute_product_features = KubernetesPodOperator(
    namespace='ml-platform',
    image='python:3.11-slim',
    name='compute-product-features',
    task_id='compute_product_features',
    cmds=['python', '-c'],
    arguments=["""
import os

feast_server = os.environ.get('FEAST_FEATURE_SERVER')
print(f"Feast server: {feast_server}")

print("Computing product features...")
features = [
    'product_view_count_7d',
    'product_purchase_count_30d',
    'product_avg_rating',
    'product_stock_level'
]
print(f"Computed features: {features}")
print("Product features computed successfully")
"""],
    env_vars=feast_env,
    get_logs=True,
    dag=dag,
)

# Task: Materialize features to online store
materialize_online = KubernetesPodOperator(
    namespace='ml-platform',
    image='python:3.11-slim',
    name='materialize-online',
    task_id='materialize_online',
    cmds=['python', '-c'],
    arguments=["""
import os
from datetime import datetime

redis_host = os.environ.get('REDIS_HOST')
print(f"Redis host: {redis_host}")

print("Materializing features to online store (Redis)...")
print(f"Materialization timestamp: {datetime.utcnow().isoformat()}")
print("Online store updated successfully")
"""],
    env_vars=feast_env,
    get_logs=True,
    dag=dag,
)

# Task: Validate feature freshness
validate_freshness = KubernetesPodOperator(
    namespace='ml-platform',
    image='python:3.11-slim',
    name='validate-freshness',
    task_id='validate_freshness',
    cmds=['python', '-c'],
    arguments=["""
import os
from datetime import datetime, timedelta

print("Validating feature freshness...")
max_staleness = timedelta(hours=1)
last_updated = datetime.utcnow()
print(f"Last update: {last_updated}")
print(f"Max staleness: {max_staleness}")
print("Feature freshness validated")
"""],
    env_vars=feast_env,
    get_logs=True,
    dag=dag,
)

# Define task dependencies
extract_raw_data >> [compute_user_features, compute_product_features]
[compute_user_features, compute_product_features] >> materialize_online >> validate_freshness
