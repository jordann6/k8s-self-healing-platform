"""Architecture diagram for the self-healing platform on EKS (phase 2).

Renders docs/architecture.png with official AWS and Kubernetes icons
(mingrammer diagrams) and the official Cilium and Falco icons from the CNCF
artwork repo (docs/icons/). Run from the repo root: python3 docs/diagram.py
(needs graphviz `dot` on PATH).

Each colour is one matrix: blue = availability, orange = security and network
drift, red = runtime threat response. Grey edges are the runner injecting a
fault and measuring user impact from outside the app.
"""
import os

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import EC2, ElasticKubernetesService
from diagrams.aws.database import RDS
from diagrams.aws.integration import Eventbridge, StepFunctions
from diagrams.aws.management import (
    AmazonManagedPrometheus,
    CloudwatchAlarm,
    Config,
    SystemsManagerAutomation,
    SystemsManagerRunCommand,
)
from diagrams.aws.security import Guardduty, SecretsManager
from diagrams.aws.storage import SimpleStorageServiceS3
from diagrams.custom import Custom
from diagrams.k8s.chaos import ChaosMesh
from diagrams.k8s.compute import Pod
from diagrams.onprem.client import User
from diagrams.onprem.gitops import Argocd
from diagrams.onprem.monitoring import Prometheus

HERE = os.path.dirname(os.path.abspath(__file__))
CILIUM = os.path.join(HERE, "icons", "cilium.png")
FALCO = os.path.join(HERE, "icons", "falco.png")

graph_attrs = {"fontsize": "13", "bgcolor": "white", "pad": "0.5", "nodesep": "0.55", "ranksep": "1.0", "splines": "spline"}
node_attrs = {"fontsize": "11"}

avail = {"color": "royalblue", "fontcolor": "royalblue", "penwidth": "2"}
drift = {"color": "darkorange", "fontcolor": "darkorange3", "penwidth": "2"}
threat = {"color": "firebrick", "fontcolor": "firebrick", "penwidth": "2"}
inject = {"color": "gray40", "fontcolor": "gray30", "style": "dashed"}

with Diagram(
    "Self-healing platform on EKS: availability (blue), drift (orange), runtime threat (red)",
    filename="docs/architecture",
    show=False,
    direction="LR",
    graph_attr=graph_attrs,
    node_attr=node_attrs,
):
    runner = User("scenario runner\n(inject, probe, judge)")

    with Cluster("lab account"):
        with Cluster("EKS 1.34"):
            eks = ElasticKubernetesService("control plane")
            with Cluster("Karpenter workload nodes (NodeRepair, drift, PDB, minDomains)"):
                web = Pod("web Rollout\nprobes, HPA,\nmemory limit")
                dbprobe = Pod("dbprobe\nboth secret\nversions")
            with Cluster("platform"):
                cilium = Custom("Cilium ENI mode\n+ Hubble", CILIUM)
                falco = Custom("Falco -> Talon\nquarantine label", FALCO)
                argo = Argocd("Argo CD self-heal\n+ Rollouts canary")
                prom = Prometheus("Prometheus\n+ Sloth SLOs")
                chaos = ChaosMesh("Chaos Mesh\npod faults")

        amp = AmazonManagedPrometheus("Managed Prometheus\n(SigV4, CMK)")
        alarm = CloudwatchAlarm("FIS stop condition\n(prober error rate)")
        node = EC2("killed or\nquarantined node")
        sm = SecretsManager("Secrets Manager\nkey + DB rotation")
        rds = RDS("RDS Postgres\nalternating users")
        cfg = Config("Config rule\nSG open to internet")
        revoke = SystemsManagerAutomation("custom SSM revoke\n(any port)")
        gd = Guardduty("GuardDuty\nRuntime Monitoring")
        bus = Eventbridge("EventBridge")
        sfn = StepFunctions("responder: cordon,\nisolate, snapshot")
        cmd = SystemsManagerRunCommand("Run Command\nforensic capture")
        s3 = SimpleStorageServiceS3("evidence\n(SSE-KMS)")

    runner >> Edge(label="k6, toggles, exec", **inject) >> web
    runner >> Edge(label="FIS terminate", **inject) >> node
    runner >> Edge(label="open 0.0.0.0/0", **inject) >> cfg

    chaos >> Edge(label="PodChaos", **avail) >> web
    prom >> Edge(label="canary analysis", **avail) >> argo
    argo >> Edge(label="abort / revert", **avail) >> web
    prom >> Edge(label="remote write", **avail) >> amp
    alarm >> Edge(label="abort", **avail) >> node

    sm >> Edge(label="ESO + Reloader", **drift) >> web
    sm >> Edge(label="rotate", **drift) >> rds
    dbprobe >> Edge(**drift) >> rds
    cfg >> Edge(label="auto-remediate", **drift) >> revoke

    falco >> Edge(label="R1 relabel", **threat) >> cilium
    cilium >> Edge(label="quarantine policy", **threat) >> web
    gd >> Edge(label="finding", **threat) >> bus
    bus >> Edge(**threat) >> sfn
    sfn >> Edge(label="R2 cordon + SG swap", **threat) >> node
    sfn >> Edge(**threat) >> cmd
    cmd >> Edge(**threat) >> s3
    sfn >> Edge(label="isolate pod", **threat) >> eks
