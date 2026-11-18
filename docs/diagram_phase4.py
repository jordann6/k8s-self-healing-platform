"""Architecture diagram for the phase 4 rows, which run the same on EKS and AKS.

Renders docs/architecture-phase4.png with official AWS, Azure and Kubernetes icons
(mingrammer diagrams) and the Cilium and KEDA icons from the CNCF artwork repo
(docs/icons/). Run from the repo root: python3 docs/diagram_phase4.py
(needs graphviz `dot` on PATH).

Blue = scaling and sizing (Q1 to Q3), red = admission and encryption (P1, N1),
orange = recovery and operations (D1, O1), green = the two rows that ran on kind, EKS and AKS
(G1 Gateway API front door, X1 Quarantine operator), grey dashed = the runner injecting a
fault and judging from outside. The two object stores are the per-cloud pair.
"""
import os

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import ECR
from diagrams.aws.network import NLB
from diagrams.aws.storage import S3
from diagrams.azure.containers import ContainerRegistries
from diagrams.azure.network import LoadBalancers
from diagrams.azure.storage import BlobStorage
from diagrams.custom import Custom
from diagrams.k8s.compute import Pod, Deployment
from diagrams.k8s.network import SVC
from diagrams.k8s.others import CRD
from diagrams.k8s.rbac import Role
from diagrams.k8s.podconfig import ConfigMap
from diagrams.onprem.client import User
from diagrams.onprem.database import PostgreSQL
from diagrams.onprem.queue import RabbitMQ

HERE = os.path.dirname(os.path.abspath(__file__))
CILIUM = os.path.join(HERE, "icons", "cilium.png")
KEDA = os.path.join(HERE, "icons", "keda.png")
ENVOY = os.path.join(HERE, "icons", "envoy.png")

graph_attrs = {"fontsize": "13", "bgcolor": "white", "pad": "0.5", "nodesep": "0.55", "ranksep": "1.0", "splines": "spline"}
node_attrs = {"fontsize": "11"}

scale = {"color": "royalblue", "fontcolor": "royalblue", "penwidth": "2"}
sec = {"color": "firebrick", "fontcolor": "firebrick", "penwidth": "2"}
ops = {"color": "darkorange", "fontcolor": "darkorange3", "penwidth": "2"}
kindonly = {"color": "seagreen4", "fontcolor": "seagreen4", "penwidth": "2"}
inject = {"color": "gray40", "fontcolor": "gray30", "style": "dashed"}

with Diagram(
    "Phase 4 rows on EKS and AKS 1.36 (blue, red, orange) and the G1 and X1 rows (green)",
    filename="docs/architecture-phase4",
    show=False,
    direction="LR",
    graph_attr=graph_attrs,
    node_attr=node_attrs,
):
    runner = User("scenario runner\n(inject, probe, judge)")

    with Cluster("EKS 1.36 or AKS 1.36, Cilium 1.20.4 (bring your own)"):
        with Cluster("scaling and sizing"):
            queue = RabbitMQ("RabbitMQ\n240 messages, manual ack")
            worker = Pod("queue worker\n0 to 5 to 0")
            keda = Custom("KEDA\nqueue depth, fallback\non metric failure", KEDA)
            vpa = Deployment("VPA recommender only\n(Off mode, never resizes)")

        with Cluster("admission and encryption"):
            kyverno = ConfigMap("Kyverno verifyImages\nfail closed, cosign key")
            cilium = Custom("Cilium WireGuard\nnode to node, policy still\nenforced", CILIUM)

        with Cluster("recovery and operations"):
            pg = PostgreSQL("Postgres\n100 known rows")
            backup = Pod("backup + restore\njobs")
            o1 = Pod("O1 namespace\nquota, limits, evidence-gated\nrelease, alert receiver")

    with Cluster("G1 and X1, run on kind, EKS and AKS (X1 on the default CNI on kind)"):
        with Cluster("G1 front door"):
            gw = Custom("Envoy Gateway\nplatform namespace owns the\nlistener, one host, labelled\nnamespaces only", ENVOY)
            route = SVC("HTTPRoute\napp namespace owns it")
            tls = ConfigMap("cert-manager lab CA\nHTTPS listener, hardened only")
        with Cluster("X1 operator"):
            crd = CRD("Quarantine CRD\nstatus: Isolated, Preserved,\nReleased, Failed")
            ctl = Pod("controller\nstdlib, resumes from status")
            rbac = Role("Role\npatch pods, no delete,\nno Secrets")

    nlb = NLB("EKS in-tree NLB\ninternal, no public IP")
    ilb = LoadBalancers("AKS internal frontend\nprivate IP, no public IP")
    ecr = ECR("ECR")
    acr = ContainerRegistries("ACR")
    s3 = S3("S3 Object Lock\nCOMPLIANCE, 1 day")
    blob = BlobStorage("Blob version-level\nWORM, locked, 1 day")

    runner >> Edge(label="publish, kill a worker,\nbreak the metric", **inject) >> queue
    runner >> Edge(label="unsigned, tag-only,\nuntrusted images", **inject) >> kyverno
    runner >> Edge(label="delete the PVC", **inject) >> pg
    runner >> Edge(label="capture on the NIC", **inject) >> cilium

    queue >> Edge(label="depth", **scale) >> keda
    keda >> Edge(label="scale", **scale) >> worker
    worker >> Edge(label="redeliver", **scale) >> queue
    vpa >> Edge(label="recommend", **scale) >> worker

    ecr >> Edge(label="digest-pinned", **sec) >> kyverno
    acr >> Edge(label="digest-pinned", **sec) >> kyverno

    pg >> Edge(label="logical dump", **ops) >> backup
    backup >> Edge(label="EKS, IRSA", **ops) >> s3
    backup >> Edge(label="AKS, workload identity", **ops) >> blob
    backup >> Edge(label="restore into fresh target", **ops) >> pg
    runner >> Edge(label="evidence, alert, release", **inject) >> o1

    runner >> Edge(label="stray host, cross-namespace\nroute, deleted backend", **inject) >> route
    route >> Edge(label="attach", **kindonly) >> gw
    runner >> Edge(label="client pod in the cluster", **inject) >> nlb
    runner >> Edge(label="client pod in the cluster", **inject) >> ilb
    nlb >> Edge(label="proxy Service", **kindonly) >> gw
    ilb >> Edge(label="proxy Service", **kindonly) >> gw
    tls >> Edge(label="g1-tls", **kindonly) >> gw
    runner >> Edge(label="create Quarantine, early\nrelease, kill controller", **inject) >> crd
    crd >> Edge(label="reconcile", **kindonly) >> ctl
    rbac >> Edge(label="bounds", **kindonly) >> ctl
