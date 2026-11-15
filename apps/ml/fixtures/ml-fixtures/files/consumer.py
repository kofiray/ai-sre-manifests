"""The fixture consumer group `fx-consumer` on topic fx-events. Scaled to 0 by M01 (lag
grows); fed a poison record by M18 (it crash-loops on a record it can't decode); its
max_poll_interval_ms cut below the batch work by K07 (it is sent out of the group mid
batch, every commit fails, it rejoins: a rebalance storm and growing lag).

It polls a batch, decodes every record, does the batch's work (a 2 s sleep standing in
for a downstream write) and commits. A commit refused because the member was sent out
of the group (CommitFailedError) is logged and the loop goes on: the next poll rejoins
and reads the batch again from the last committed offset.

kafka-python 2.0.6 has a client bug on that same path: when the member is sent out of
the group, its heartbeat thread can die with "RuntimeError: cannot release un-acquired
lock", and poll() re-raises it. A healthy client rejoins instead of exiting, so the
consumer is rebuilt and rejoins (seen on the P9b long batch 2026-11-14: without this,
K07 showed a crash loop, not a rebalance storm). Anything else still raises, so M18's
poison record still crash-loops it."""
import json, os, time
from kafka import KafkaConsumer
from kafka.errors import CommitFailedError
def make():
    return KafkaConsumer("fx-events", bootstrap_servers=os.environ["FX_KAFKA"], group_id="fx-consumer",
                  max_poll_interval_ms=300000,
                  enable_auto_commit=False, auto_offset_reset="earliest")
c = make()
print("fx-consumer: started", flush=True)
while True:
    try:
        batch = c.poll(timeout_ms=1000)
    except RuntimeError as e:
        if "un-acquired lock" not in str(e):
            raise
        print("fx-consumer: heartbeat thread died (%s), rebuilding the consumer" % e, flush=True)
        try:
            c.close(autocommit=False)
        except Exception:
            pass
        c = make()
        continue
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
