"""Architecture diagram for the self-healing platform on GKE (ADR-0027).

Renders docs/architecture-gcp.png with official Google Cloud and Kubernetes
icons (mingrammer diagrams) and the official Cilium, Falco and KEDA icons from
the CNCF artwork repo (docs/icons/). Run from the repo root:
python3 docs/diagram_gcp.py (needs graphviz `dot` on PATH).

Same colours as the EKS and AKS diagrams: blue = availability, orange =
security and network drift, red = runtime threat response, grey dashed = the
runner injecting a fault and measuring user impact from outside the app.

GKE runs the in-cluster rows only. The GCP-native responses (S1 rotation, S4
firewall revert, R2 Pub/Sub response) were proven in the GCP landing zone, on
a different cluster and project; they are drawn in their own dotted box so
the picture never implies they ran here.
"""
import os

from diagrams import Cluster, Diagram, Edge
from diagrams.custom import Custom
from diagrams.gcp.analytics import Pubsub
from diagrams.gcp.compute import ComputeEngine, KubernetesEngine, Run
from diagrams.gcp.devtools import ContainerRegistry
from diagrams.gcp.network import NAT, FirewallRules
from diagrams.gcp.security import CloudAssetInventory, Iam, SecretManager
from diagrams.k8s.chaos import ChaosMesh
from diagrams.k8s.compute import Pod
from diagrams.onprem.client import User
from diagrams.onprem.gitops import Argocd
from diagrams.onprem.monitoring import Prometheus

HERE = os.path.dirname(os.path.abspath(__file__))
CILIUM = os.path.join(HERE, "icons", "cilium.png")
FALCO = os.path.join(HERE, "icons", "falco.png")
KEDA = os.path.join(HERE, "icons", "keda.png")

graph_attrs = {"fontsize": "13", "bgcolor": "white", "pad": "0.5", "nodesep": "0.55", "ranksep": "1.0", "splines": "spline"}
node_attrs = {"fontsize": "11"}
elsewhere = {"style": "dotted", "color": "gray50", "fontcolor": "gray30", "bgcolor": "#f2f2f2"}

avail = {"color": "royalblue", "fontcolor": "royalblue", "penwidth": "2"}
drift = {"color": "darkorange", "fontcolor": "darkorange3", "penwidth": "2"}
threat = {"color": "firebrick", "fontcolor": "firebrick", "penwidth": "2"}
inject = {"color": "gray40", "fontcolor": "gray30", "style": "dashed"}

with Diagram(
    "Self-healing platform on GKE: availability (blue), drift (orange), runtime threat (red)",
    filename="docs/architecture-gcp",
    show=False,
    direction="LR",
    graph_attr=graph_attrs,
    node_attr=node_attrs,
):
    runner = User("scenario runner\n(inject, probe, judge)")

    with Cluster("lab project, us-central1-a"):
        with Cluster("GKE 1.36 Standard, zonal (BYO Cilium on the legacy datapath,\nprivate nodes, IAM-only DNS endpoint, Workload Identity)"):
            gke = KubernetesEngine("control plane")
            with Cluster("tainted workload pool, fixed at 4\n(spread minDomains 3, Honor, 30s tolerations)"):
                web = Pod("web Rollout\nprobes, HPA (KEDA),\nmemory limit")
            with Cluster("system pool"):
                cilium = Custom("Cilium + Hubble\n(native routing on\nthe pod range)", CILIUM)
                falco = Custom("Falco (modern eBPF)\n-> Talon quarantine label", FALCO)
                argo = Argocd("Argo CD self-heal\n+ Rollouts canary")
                prom = Prometheus("Prometheus\n+ Sloth SLOs")
                chaos = ChaosMesh("Chaos Mesh\npod faults")
                keda = Custom("KEDA (pinned chart)\nHPA on Prometheus CPU", KEDA)

        vm = ComputeEngine("stopped node VM\n(by providerID name)")
        registry = ContainerRegistry("Artifact Registry\n(immutable sha tags)")
        nodesa = Iam("node service account\n(least privilege)")
        nat = NAT("Cloud NAT\n(only egress)")

    with Cluster("proven in the GCP landing zone, not on this cluster", graph_attr=elsewhere):
        asset = CloudAssetInventory("org asset feed")
        topic = Pubsub("Pub/Sub push")
        handler = Run("private handler\n(Cloud Run)")
        fw = FirewallRules("0.0.0.0/0 rule\ndisabled (S4)")
        secret = SecretManager("Secret Manager\nSECRET_ROTATE")
        rotator = Run("private rotator\n(Cloud Run, S1)")

    runner >> Edge(label="k6, toggles, exec", **inject) >> web
    runner >> Edge(label="gcloud instances stop", **inject) >> vm

    chaos >> Edge(label="PodChaos", **avail) >> web
    prom >> Edge(label="canary analysis", **avail) >> argo
    argo >> Edge(label="abort / revert", **avail) >> web
    prom >> Edge(label="pod CPU, 10s", **avail) >> keda
    keda >> Edge(label="scale (A3)", **avail) >> web
    registry >> Edge(**avail) >> web
    nodesa >> Edge(label="pull", **avail) >> registry
    vm >> Edge(label="NotReady; replicas move to the 3 left", **avail) >> web
    nat >> Edge(label="chart images", **avail) >> gke

    argo >> Edge(label="S3 revert drift", **drift) >> web
    falco >> Edge(label="R1 relabel", **threat) >> cilium
    cilium >> Edge(label="quarantine policy", **threat) >> web

    asset >> Edge(**drift) >> topic
    secret >> Edge(label="Pub/Sub push", **drift) >> rotator
    topic >> Edge(**drift) >> handler
    handler >> Edge(label="disable", **drift) >> fw
