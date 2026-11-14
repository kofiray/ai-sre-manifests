"""P9b fixture DAG: one tiny task every 2 minutes, so the scheduler, the executor and
the metadata DB are all exercised and their faults show (W10, M02, M12). Only this
folder is git-synced: the imported example DAGs under ../dags need libraries the
image doesn't have and would show as import errors from the start."""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator
import fx_pipeline_helpers  # noqa: F401

with DAG(
    dag_id="fx_heartbeat",
    start_date=datetime(2026, 11, 1),
    schedule=timedelta(minutes=2),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=10),
    default_args={"retries": 0, "pool": "default_pool"},
    tags=["p9b-fixture"],
) as dag:
    BashOperator(task_id="beat", bash_command="echo beat")
