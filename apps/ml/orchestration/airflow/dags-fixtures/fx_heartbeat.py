"""P9b fixture DAG: one tiny task every 2 minutes, so the scheduler, the executor and
the metadata DB are all exercised and their faults show (W10, M02, M12). Only this
folder is git-synced: the imported example DAGs under ../dags need libraries the
image doesn't have and would show as import errors from the start."""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator
import logging

# The fixture's own pool, kept in git: each parse sets fx_pool to this many slots
# (Airflow 2.10 has no config or chart value for pools). M02 sets it to 0.
# A failure here must never become an import error (that is M12's signal).
FX_POOL_SLOTS = 1
try:
    from airflow.models.pool import Pool
    Pool.create_or_update_pool("fx_pool", slots=FX_POOL_SLOTS,
                               description="P9b fixture pool", include_deferred=False)
except Exception as e:  # noqa: BLE001
    logging.getLogger(__name__).warning("fx_pool not set: %s", e)

with DAG(
    dag_id="fx_heartbeat",
    start_date=datetime(2026, 11, 1),
    schedule=timedelta(minutes=2),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=10),
    # New DAGs start paused by default (core.dags_are_paused_at_creation), so the
    # fixture never ran: no task instances, nothing for W10/M02/M12 to stop.
    is_paused_upon_creation=False,
    default_args={"retries": 0, "pool": "fx_pool"},
    tags=["p9b-fixture"],
) as dag:
    BashOperator(task_id="beat", bash_command="echo beat")
