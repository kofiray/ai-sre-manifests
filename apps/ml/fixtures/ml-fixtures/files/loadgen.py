"""ml-fixtures loadgen: steady, small traffic against every ML service a P9b scenario
breaks, with one Prometheus metric family the scenario alerts are written against.

  mlfx_requests_total{target, op, outcome}      outcome = ok | error | timeout
  mlfx_request_seconds{target, op}              latency histogram
  mlfx_last_success_timestamp_seconds{target, op}
  mlfx_kafka_group_lag{group}                   end offsets - committed, from the admin API
                                                (so it still reads while the consumer is down)
  mlfx_http_responses_total{target, op, code}   HTTP ops only: the status of the op's last
                                                call (code = 200, 401, 408, 503, ... or
                                                "timeout" / "conn" when no response came).
                                                Tells a 408 from LiteLLM (C08) apart from a
                                                hang (M05) and a 401 (M15). A separate family,
                                                so no alert on mlfx_requests_total changes.

Each target runs in its own thread on its own interval. A failure is counted, never
raised: the loadgen must keep measuring through every fault it exists to observe.
No secret is printed; the LiteLLM key is read from the environment and sent only as a
request header.
"""
import hashlib, json, os, random, threading, time, traceback
import requests
from prometheus_client import Counter, Gauge, Histogram, start_http_server

REQ = Counter("mlfx_requests_total", "fixture requests", ["target", "op", "outcome"])
LAT = Histogram("mlfx_request_seconds", "fixture latency", ["target", "op"],
                buckets=(.01, .025, .05, .1, .25, .5, 1, 2, 5, 10, 30))
LAST = Gauge("mlfx_last_success_timestamp_seconds", "last ok", ["target", "op"])
LAG = Gauge("mlfx_kafka_group_lag", "consumer group lag", ["group"])
HTTP = Counter("mlfx_http_responses_total", "fixture HTTP ops by the last call's status",
               ["target", "op", "code"])
_ctx = threading.local()   # each target runs in its own thread
E = os.environ.get
TIMEOUT = float(E("FX_TIMEOUT", "10"))


def measure(target, op, fn):
    t0 = time.time()
    _ctx.code = None
    try:
        fn()
        REQ.labels(target, op, "ok").inc()
        LAST.labels(target, op).set(time.time())
    except requests.Timeout:
        REQ.labels(target, op, "timeout").inc()
        _ctx.code = "timeout"
    except Exception as e:  # counted, logged briefly, never fatal
        REQ.labels(target, op, "error").inc()
        if isinstance(e, requests.RequestException):   # no response at all
            _ctx.code = "conn"
        print(f"{target}/{op}: {type(e).__name__}: {str(e)[:160]}", flush=True)
    finally:
        LAT.labels(target, op).observe(time.time() - t0)
        if _ctx.code is not None:       # only ops that made an HTTP call
            HTTP.labels(target, op, _ctx.code).inc()


def ok(r):
    _ctx.code = str(r.status_code)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")
    return r


def every(seconds, fn):
    def loop():
        while True:
            try:
                fn()
            except Exception:
                traceback.print_exc()
            time.sleep(seconds * random.uniform(0.9, 1.1))
    threading.Thread(target=loop, daemon=True).start()


# ---- MLflow: log a run with a metric; log a small artifact through the server
MLFLOW = E("FX_MLFLOW", "http://mlflow.ml-platform.svc.cluster.local:5000")
def mlflow_run():
    def go():
        r = ok(requests.post(f"{MLFLOW}/api/2.0/mlflow/runs/create",
                             json={"experiment_id": "0", "start_time": int(time.time() * 1000)}, timeout=TIMEOUT))
        rid = r.json()["run"]["info"]["run_id"]
        ok(requests.post(f"{MLFLOW}/api/2.0/mlflow/runs/log-metric", json={
            "run_id": rid, "key": "fx", "value": random.random(), "timestamp": int(time.time() * 1000)}, timeout=TIMEOUT))
        ok(requests.post(f"{MLFLOW}/api/2.0/mlflow/runs/update", json={"run_id": rid, "status": "FINISHED"}, timeout=TIMEOUT))
    measure("mlflow", "log_run", go)
def mlflow_artifact():
    measure("mlflow", "log_artifact", lambda: ok(requests.put(
        f"{MLFLOW}/api/2.0/mlflow-artifacts/artifacts/fx/{int(time.time())}.txt",
        data=b"fixture", timeout=TIMEOUT)))


# ---- Feast: push one row to the online store, read it back
FEAST = E("FX_FEAST", "http://feast-server.ml-platform.svc.cluster.local:6566")
def feast():
    eid = random.randint(1, 50)
    measure("feast", "push", lambda: ok(requests.post(f"{FEAST}/push", json={
        "push_source_name": "fx_push", "to": "online",
        "df": {"fx_id": [eid], "value": [random.random()],
               "event_timestamp": [time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())]}}, timeout=TIMEOUT)))
    def read():
        r = ok(requests.post(f"{FEAST}/get-online-features", json={
            "features": ["fx_features:value"], "entities": {"fx_id": [eid]}}, timeout=TIMEOUT))
        if "results" not in r.json():
            raise RuntimeError("no results")
    measure("feast", "online_read", read)


# ---- LiteLLM: a completion against the in-cluster stub (no real provider, no spend).
# It uses a VIRTUAL key, not the master key: LiteLLM accepts the master key without its
# database, so only a virtual key makes a key check reach the DB (M15). The key is
# derived from the master key, so a loadgen restart reuses it instead of piling up keys.
# It lives in memory only and is sent only as a request header.
LITELLM = E("FX_LITELLM", "http://litellm.litellm.svc.cluster.local:4000")
_llm = {}
def _vkey():
    if "k" not in _llm:
        master = E("LITELLM_MASTER_KEY", "")
        k = "sk-fx-" + hashlib.sha256(("fx-loadgen:" + master).encode()).hexdigest()[:32]
        r = requests.post(f"{LITELLM}/key/generate", headers={"Authorization": f"Bearer {master}"},
                          json={"key": k, "key_alias": "fx-loadgen", "models": ["fx-echo"]}, timeout=TIMEOUT)
        if r.status_code >= 400 and not any(w in r.text.lower() for w in ("already exists", "unique constraint", "duplicate")):
            ok(r)   # counted as this op's failure; retried next tick
        _llm["k"] = k
    return _llm["k"]
def litellm():
    measure("litellm", "completion", lambda: ok(requests.post(
        f"{LITELLM}/chat/completions", headers={"Authorization": f"Bearer {_vkey()}"},
        json={"model": "fx-echo", "messages": [{"role": "user", "content": "ping"}]}, timeout=TIMEOUT)))


# ---- KServe: predict on the iris model
KSERVE = E("FX_KSERVE", "http://fx-iris-predictor.ml-fixtures.svc.cluster.local")
def kserve():
    measure("kserve", "predict", lambda: ok(requests.post(
        f"{KSERVE}/v1/models/fx-iris:predict", json={"instances": [[6.8, 2.8, 4.8, 1.4]]}, timeout=TIMEOUT)))


# ---- DataHub GMS: health
DATAHUB = E("FX_DATAHUB", "http://datahub-datahub-gms.datahub.svc.cluster.local:8080")
def datahub():
    measure("datahub", "gms_health", lambda: ok(requests.get(f"{DATAHUB}/health", timeout=TIMEOUT)))
def datahub_search():
    # GMS search goes to Elasticsearch, so a starved ES (R10) shows as user-facing errors
    measure("datahub", "search", lambda: ok(requests.post(
        f"{DATAHUB}/entities?action=search", headers={"X-RestLi-Protocol-Version": "2.0.0"},
        json={"input": "*", "entity": "dataset", "start": 0, "count": 10}, timeout=TIMEOUT)))


# ---- Milvus: one collection, searched
MILVUS = E("FX_MILVUS", "milvus.milvus.svc.cluster.local")
_milvus = {}
def milvus():
    def go():
        from pymilvus import MilvusClient
        try:
            c = _milvus.get("c")
            if c is None:
                c = MilvusClient(uri=f"http://{MILVUS}:19530", timeout=TIMEOUT)
                if not c.has_collection("fx_vectors"):
                    c.create_collection("fx_vectors", dimension=8)
                    c.insert("fx_vectors", [{"id": i, "vector": [random.random() for _ in range(8)]} for i in range(2000)])
                c.load_collection("fx_vectors")
                _milvus["c"] = c
            c.search("fx_vectors", data=[[random.random() for _ in range(8)]], limit=3)
        except Exception:
            _milvus.pop("c", None)   # reconnect next time
            raise
    measure("milvus", "search", go)
def milvus_flush():
    # MilvusClient has no flush() in pymilvus 2.4; the ORM Collection does. Own alias,
    # so it never shares a connection with the search thread.
    def go():
        from pymilvus import Collection, connections, utility
        try:
            if "f" not in _milvus:
                connections.connect(alias="fxflush", uri=f"http://{MILVUS}:19530", timeout=TIMEOUT)
                _milvus["f"] = True
            if not utility.has_collection("fx_vectors", using="fxflush", timeout=TIMEOUT):
                raise RuntimeError("fx_vectors not created yet")
            col = Collection("fx_vectors", using="fxflush")
            col.insert([{"id": random.randint(10**6, 10**12), "vector": [random.random() for _ in range(8)]}
                        for _ in range(5)], timeout=TIMEOUT)
            col.flush(timeout=TIMEOUT)
        except Exception:
            _milvus.pop("f", None)
            try:
                connections.disconnect("fxflush")
            except Exception:
                pass
            raise
    measure("milvus", "flush", go)


# ---- Kafka: produce; measure the fixture consumer group's lag from the admin API
KAFKA = E("FX_KAFKA", "ml-platform-kafka-bootstrap.kafka.svc.cluster.local:9092")
_kafka = {}
def kafka_produce():
    def go():
        from kafka import KafkaProducer
        p = _kafka.get("p") or KafkaProducer(bootstrap_servers=KAFKA, acks="all", request_timeout_ms=int(TIMEOUT * 1000),
                                             max_block_ms=int(TIMEOUT * 1000))
        _kafka["p"] = p
        try:
            p.send("fx-events", json.dumps({"t": time.time()}).encode()).get(timeout=TIMEOUT)
        except Exception:
            _kafka.pop("p", None)
            raise
    measure("kafka", "produce", go)
def kafka_lag():
    def go():
        from kafka import KafkaAdminClient, KafkaConsumer
        try:
            a = _kafka.get("a") or KafkaAdminClient(bootstrap_servers=KAFKA, request_timeout_ms=int(TIMEOUT * 1000))
            _kafka["a"] = a
            off = a.list_consumer_group_offsets("fx-consumer")
            c = _kafka.get("c") or KafkaConsumer(bootstrap_servers=KAFKA)
            _kafka["c"] = c
            end = c.end_offsets(list(off.keys())) if off else {}
            LAG.labels("fx-consumer").set(sum(max(end[tp] - m.offset, 0) for tp, m in off.items()))
        except Exception:
            _kafka.pop("a", None); _kafka.pop("c", None)
            raise
    measure("kafka", "lag_read", go)

# ---- Ray: keep one small job in flight on the cluster, through the head's Job API.
# The task writes to every page of ~1.5 GB (a zeroed buffer alone wouldn't count against
# the worker's memory) for ~60 s. Counted when a job ENDS: ok on SUCCEEDED, error on
# FAILED/STOPPED, on a job that vanished (the head restarted) or a failed submission.
RAY = E("FX_RAY", "http://ml-platform-cluster-head-svc.ml-platform.svc.cluster.local:8265")
RAY_JOB = ("python -c \"import ray, time\n"
           "ray.init()\n"
           "@ray.remote(num_cpus=1)\n"
           "def hold(mb):\n"
           "    b = bytearray(mb * 1024 * 1024)\n"
           "    b[::4096] = b'\\x01' * (len(b) // 4096)\n"
           "    time.sleep(60)\n"
           "    return len(b)\n"
           "print(ray.get(hold.remote(1536)))\"")
_ray = {}
def ray_job():
    sid = _ray.get("sid")
    if sid:
        r = requests.get(f"{RAY}/api/jobs/{sid}", timeout=TIMEOUT)
        if r.status_code == 404:
            _ray.pop("sid", None)
            measure("ray", "job", lambda: (_ for _ in ()).throw(RuntimeError("job vanished (head restarted?)")))
        else:
            st = ok(r).json().get("status")
            if st in ("PENDING", "RUNNING"):
                return
            _ray.pop("sid", None)
            def done():
                if st != "SUCCEEDED":
                    raise RuntimeError(f"job {st}")
            measure("ray", "job", done)
    def submit():
        r = ok(requests.post(f"{RAY}/api/jobs/", json={"entrypoint": RAY_JOB}, timeout=TIMEOUT))
        _ray["sid"] = r.json()["submission_id"]
    try:
        submit()
    except Exception as e:
        measure("ray", "job", lambda: (_ for _ in ()).throw(e))

if __name__ == "__main__":
    start_http_server(9100)
    enabled = set((E("FX_TARGETS") or "mlflow,feast,litellm,kserve,datahub,milvus,kafka,ray").split(","))
    if "mlflow" in enabled:  every(15, mlflow_run); every(30, mlflow_artifact)
    if "feast" in enabled:   every(5, feast)
    if "litellm" in enabled: every(10, litellm)
    if "kserve" in enabled:  every(5, kserve)
    if "datahub" in enabled: every(15, datahub); every(15, datahub_search)
    if "milvus" in enabled:  every(10, milvus); every(30, milvus_flush)
    if "kafka" in enabled:   every(2, kafka_produce); every(15, kafka_lag)
    if "ray" in enabled:     every(20, ray_job)
    print(f"ml-fixtures loadgen: targets {sorted(enabled)}; metrics on :9100", flush=True)
    while True:
        time.sleep(3600)
