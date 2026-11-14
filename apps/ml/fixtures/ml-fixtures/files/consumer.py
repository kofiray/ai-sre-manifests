"""The fixture consumer group `fx-consumer` on topic fx-events. Scaled to 0 by M01 (lag
grows); fed a poison record by M18 (it crash-loops on a record it can't decode); its
max_poll_interval_ms cut below the batch work by K07 (it is sent out of the group mid
batch, every commit fails, it rejoins: a rebalance storm and growing lag).

It polls a batch, decodes every record, does the batch's work (a 2 s sleep standing in
for a downstream write) and commits. A commit refused because the member was sent out
of the group (CommitFailedError) is logged and the loop goes on: the next poll rejoins
and reads the batch again from the last committed offset. Anything else still raises."""
import json, os, time
from kafka import KafkaConsumer
from kafka.errors import CommitFailedError
c = KafkaConsumer("fx-events", bootstrap_servers=os.environ["FX_KAFKA"], group_id="fx-consumer",
                  max_poll_interval_ms=1000,
                  enable_auto_commit=False, auto_offset_reset="earliest")
print("fx-consumer: started", flush=True)
while True:
    batch = c.poll(timeout_ms=1000)
    if not batch:
        continue
    for records in batch.values():
        for m in records:
            json.loads(m.value)  # a poison (non-JSON) record raises here, on purpose
    time.sleep(2)
    try:
        c.commit()
    except CommitFailedError as e:
        print("fx-consumer: commit failed (%s), rejoining on the next poll" % e, flush=True)
