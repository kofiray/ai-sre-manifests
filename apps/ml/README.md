# apps/ml — the ML estate (ESTATE=ml-platform)

The reference ML platform (MLflow, Feast, DataHub, KServe, Ray, Milvus, LiteLLM, Kafka,
Airflow, Postgres/Redis/MinIO/etcd, Prometheus/Loki/Tempo/Grafana, Kyverno, ESO,
cert-manager, Harbor, Velero, Argo Rollouts), imported as one clean tree (no history) and
adapted for this build. Booted only on `ESTATE=ml-platform`; the `minimal` estate never
sees it.

**How it is delivered.** `scripts/cluster-apps.sh` (ml-platform only) applies `ml-root` →
`apps/ml/root`, a Helm chart that owns the AppProject, every namespace, and one
ApplicationSet with **RollingSync**: one step per wave, and a wave starts only when every
app in the one before is Healthy. `ML_REVISION=<branch>` boots a branch without touching
`main`.

## What changed from the source, and why

**Not imported**
- `cilium`, `argocd` (self-management), the source's root Application: this build keeps
  its own CNI, ArgoCD and root. The source's would have re-managed them from another repo.
- `schema-registry`, `alertmanager`, `kourier`: render nothing. `evidently`: its images do
  not exist, but it still deployed a Postgres and PVCs. `security/hardening`: never
  deployed in the source either.
- Git history, and the source's own SPEC/PROGRESS/PROMPT/CLAUDE files.

**Public names: `ai-sre-live.kofiray.net`** (operator-confirmed 2026-11-12)
- Its own Cloud DNS zone `ai-sre-live-kofiray-net`, delegated from `kofiray.net` by one NS
  record. external-dns (wave 3, policy `sync`, TXT owner `ai-sre-live-ml`) and
  cert-manager (Let's Encrypt DNS-01) reach it over WIF, each with `dns.admin` on that
  zone only; neither can touch `kofiray.net`. The source's personal domain and its
  hard-coded worker IP are gone.
- The wildcard certificate uses **letsencrypt-staging** by default (Let's Encrypt allows
  5 identical certificates a week; the gate alone boots twice). `letsencrypt-prod` for
  demos.
- **Not yet:** the Gateway and HTTPRoutes are still off (`gateway-api` ships only the
  CRDs), because this build's Cilium does not run the Gateway API controller yet. That
  is the next step; until then UIs are reached with `kubectl port-forward`.

**Ordering**
- The source declared sync waves but synced `AllAtOnce`, so order came only from retries.
  RollingSync makes it real.
- `prometheus-crds` (wave 2) delivers the monitoring CRDs before anything that uses them.
  Apps in waves 3–5 ship ServiceMonitors; with waves enforced they would otherwise wait
  forever on Prometheus in wave 6. It also removes the operator start-up race (L-P2-009).
- The MinIO tenant moved to wave 5, after its operator's CRDs (wave 4).
- Namespaces belong to `root`, not to apps. In the source, mlflow created `ml-platform`,
  so pruning mlflow would delete the namespace holding feast, Ray, autogluon and DataHub
  jobs. Talos PSA labels are set there for the namespaces that need privileged pods.

**Secrets: no credential is in this tree**
- One `ClusterSecretStore` `gcp-sm`: GCP Secret Manager over **Workload Identity
  Federation** (ESO 2.13). No key exists. The federated ESO principal can read each
  `sre-agent-ml-*` secret individually, never project-wide.
- Removed: the source's two Kubernetes-provider stores (they read a `shared-secrets`
  namespace nothing created, with no RBAC), the plain Secrets in `shared-secrets`
  (Airflow fernet key, DataHub DB/ES/Kafka passwords), `admin123` (Airflow),
  `Harbor12345`, Harbor's internal DB (`changeit`) and registry defaults, predictable
  `CREATE USER … PASSWORD 'x_password'` SQL in the Postgres bootstrap, MinIO's
  `minio123` default, and two SA-key placeholder Secrets.
- Postgres users are CNPG **managed roles** whose passwords come from the same Secret
  Manager entry the consuming app reads, so they cannot drift (the source's Airflow DB
  auth would have failed). Databases are CNPG `Database` objects. Harbor uses the shared
  Postgres too.
- MinIO had six different key names for one credential; all map to one secret now.
- Grafana and Harbor admin secrets are now actually wired into their charts.

**Storage**
- The source shipped StorageClasses but no driver. `gcp-pd-csi` now includes the upstream
  GCE PD CSI driver (v1.27.5 stable). Its key-file mount is replaced by a projected
  ServiceAccount token + a WIF credential config. Its cloud identity is a custom role with
  only the 20 calls the driver makes, limited by IAM condition to this estate's instances
  and `pvc-*` disks (upstream's setup grants `roles/editor`).
- AutoGluon's two PVCs are off (nothing mounts them; Pending PVCs read as Progressing and
  would stall wave 9). CNPG backups are off for now (MinIO comes up a wave later; the
  bucket did not exist; the credential keys did not match).

**Observability**
- This estate's kube-prometheus-stack **replaces** the build's own (SPEC-AMENDMENT-1 §C2).
  It takes the same names and namespace (`kps-*` in `monitoring`), so the agent, the
  incident bridge and the gates work unchanged. Alertmanager uses the build's routing:
  failure-library alerts (`scenario` label) → the bridge's pinned ClusterIP; the rest →
  `null`. The source's three receivers delivered nowhere.
- The admission webhook is off (L-P1-012). etcd scraping is off (Talos keeps etcd
  internal). Prometheus remote-write stays closed, so no pod can push metrics into the
  evidence the agent reads.

**Found on the first boot (2026-11-12/13)**
- The source's ~50 `ignoreDifferences` rules for StatefulSet/Deployment defaults broke
  ArgoCD server-side diff (ignoring `ports[].protocol` strips a list key). Removed; the
  ML apps use server-side diff like the rest of the build (L-P9-005).
- Ignoring all CRD annotations stripped `api-approved.kubernetes.io` from the Gateway
  API CRDs; removed.
- PD CSI on Talos: `/etc/udev` cannot be a hostPath (read-only `/etc`); disks are found
  by their `scsi-0Google_PersistentDisk_*` links (Talos has no GCE `google-*` rules).
- Bitnami withdrew `bitnami/kubectl` from Docker Hub: Kyverno and Velero hook images →
  `bitnamilegacy/kubectl:1.32.0`. Kyverno's upgrade-only hooks are off (ArgoCD hangs on
  their non-Job hook resources).
- external-dns: `fsGroup` was in the container security context.

**Bugs fixed (from the import inventory)**
- MinIO addressed as `:9000` on the service or in the wrong namespace (mlflow, kserve,
  autogluon, airflow, velero, harbor, four DAGs); five referenced buckets never created.
- Feast's Redis host; MLflow's Tempo host; Airflow's Feast/Milvus hosts; Argo Rollouts'
  Prometheus URL; Grafana's Prometheus datasource.
- DataHub's and KubeRay's ServiceMonitors selected the wrong namespace; DataHub's
  `datahub-actions` / `datahub-upgrade` keys did not match the chart.
- KServe's `kserve-sa` referenced an S3 secret that existed only in another namespace.
- Harbor's `imageChartStorage` sat one level too high (S3 silently ignored).
- LiteLLM's `local-llama` pointed at a vLLM service that is never deployed.
- Kyverno's privileged-container policy had no `=()` anchors, so it would deny almost
  every pod; it is fixed, and all Kyverno policies run in Audit for P9a.
- cert-manager's retired `ExperimentalGatewayAPISupport` gate → `config.enableGatewayAPI`.
- Velero: AWS plugin v1.11 → v1.13.2 (matches Velero 1.17), CSI feature off (no snapshot
  CRDs), malformed `restoreResourcesVersionPriority` removed.
- Redis PDB `minAvailable: 1` on one replica blocked every drain; off.

**Known, not yet changed (watch on first boot)**
- Docker Hub anonymous pull limits behind one NAT; several multi-GB images (Ray,
  AutoGluon, TF Serving, DataHub GMS).
- Milvus S3 credentials (chart 4.2.0 has no `existingSecret`); Milvus `indexCoordinator`
  on 2.5.x; KServe `kserve/mlserver` image; MLflow `pip install` at start (needs PyPI).
- Elasticsearch keeps the chart's dummy `ELASTIC_PASSWORD` (security is off; the probe
  script needs the variable). Kafka and Elasticsearch have no auth, as in the source.
