"""The fixture consumer group `fx-consumer` on topic fx-events. Scaled to 0 by M01 (lag
grows); fed a poison record by M18 (it crash-loops on a record it can't decode)."""
import json, os
from kafka import KafkaConsumer
c = KafkaConsumer("fx-events", bootstrap_servers=os.environ["FX_KAFKA"], group_id="fx-consumer",
                  enable_auto_commit=False, auto_offset_reset="earliest")
print("fx-consumer: started", flush=True)
for m in c:
    json.loads(m.value)          # a poison (non-JSON) record raises here, on purpose
    c.commit()
