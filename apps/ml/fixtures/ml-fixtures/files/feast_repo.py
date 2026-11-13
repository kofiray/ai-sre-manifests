# The fixture feature view: one entity, one float feature, written via a push source.
from datetime import timedelta
from feast import Entity, FeatureView, Field, PushSource
from feast.infra.offline_stores.contrib.postgres_offline_store.postgres_source import PostgreSQLSource
from feast.types import Float32

fx = Entity(name="fx", join_keys=["fx_id"])
batch = PostgreSQLSource(name="fx_batch", query="SELECT 1 AS fx_id, 0.0::real AS value, now() AS event_timestamp",
                         timestamp_field="event_timestamp")
push = PushSource(name="fx_push", batch_source=batch)
fx_features = FeatureView(name="fx_features", entities=[fx], ttl=timedelta(days=1),
                          schema=[Field(name="value", dtype=Float32)], online=True, source=push)
