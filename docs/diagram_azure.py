"""Architecture diagram for the self-healing platform on AKS (phase 3).

Renders docs/architecture-azure.png with official Azure and Kubernetes icons
(mingrammer diagrams) and the official Cilium and Falco icons from the CNCF
artwork repo (docs/icons/). Run from the repo root:
python3 docs/diagram_azure.py (needs graphviz `dot` on PATH).

Same colours as the EKS diagram: blue = availability, orange = security and
network drift, red = runtime threat response, grey dashed = the runner
injecting a fault and measuring user impact from outside the app.
"""
import os

from diagrams import Cluster, Diagram, Edge
from diagrams.azure.compute import DiskSnapshots, VMScaleSet
from diagrams.azure.containers import ContainerRegistries, KubernetesServices
from diagrams.azure.identity import ManagedIdentities
from diagrams.azure.integration import LogicApps
from diagrams.azure.managementgovernance import Monitor, Policy
from diagrams.azure.networking import NetworkSecurityGroups
from diagrams.azure.security import KeyVaults, MicrosoftDefenderForCloud
from diagrams.azure.storage import BlobStorage, QueuesStorage
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
    "Self-healing platform on AKS: availability (blue), drift (orange), runtime threat (red)",
    filename="docs/architecture-azure",
    show=False,
    direction="LR",
    graph_attr=graph_attrs,
    node_attr=node_attrs,
):
    runner = User("scenario runner\n(inject, probe, judge)")

    with Cluster("lab subscription, East US 2"):
        with Cluster("AKS 1.34 (bring your own CNI, workload identity)"):
            aks = KubernetesServices("control plane")
            with Cluster("tainted workload pool, autoscaled 3 to 4\n(spread minDomains 3, Honor, 30s tolerations)"):
                web = Pod("web Rollout\nprobes, HPA,\nmemory limit")
            with Cluster("system pool"):
                cilium = Custom("Cilium + Hubble\n(cluster pool, VXLAN)", CILIUM)
                falco = Custom("Falco -> Talon\nquarantine label", FALCO)
                argo = Argocd("Argo CD self-heal\n+ Rollouts canary")
                prom = Prometheus("Prometheus\n+ Sloth SLOs")
                chaos = ChaosMesh("Chaos Mesh\npod faults")
                worker = Pod("R2 worker\ncordon, then relabel")

        vmss = VMScaleSet("stopped or\nquarantined instance")
        amw = Monitor("Azure Monitor workspace\n(managed Prometheus)")
        kv = KeyVaults("Key Vault\npartner key")
        ids = ManagedIdentities("one identity\nper caller")
        acr = ContainerRegistries("ACR\n(sha tags)")
        nsg = NetworkSecurityGroups("node subnet NSG")
        policy = Policy("deployIfNotExists\nrewrite to Deny")
        defender = MicrosoftDefenderForCloud("Defender for\nContainers")
        logic = LogicApps("Logic App\n(Consumption)")
        queue = QueuesStorage("incidents queue")
        blob = BlobStorage("evidence\n(Entra only)")
        snap = DiskSnapshots("OS-disk snapshot\n+ Run Command")

    runner >> Edge(label="k6, toggles, exec", **inject) >> web
    runner >> Edge(label="az vmss stop (by ID)", **inject) >> vmss
    runner >> Edge(label="open 0.0.0.0/0", **inject) >> nsg

    chaos >> Edge(label="PodChaos", **avail) >> web
    prom >> Edge(label="canary analysis", **avail) >> argo
    argo >> Edge(label="abort / revert", **avail) >> web
    prom >> Edge(label="remote write", **avail) >> amw
    acr >> Edge(**avail) >> web
    vmss >> Edge(label="NotReady; replica moves to an autoscaled node", **avail) >> web

    kv >> Edge(label="ESO + Reloader", **drift) >> web
    ids >> Edge(label="federated tokens", **drift) >> kv
    nsg >> Edge(label="DINE", **drift) >> policy
    policy >> Edge(label="rewrite rule", **drift) >> nsg

    falco >> Edge(label="R1 relabel", **threat) >> cilium
    cilium >> Edge(label="quarantine policy", **threat) >> web
    defender >> Edge(label="alert (workflow automation)", **threat) >> logic
    logic >> Edge(label="ARM copy checked, enqueue", **threat) >> queue
    queue >> Edge(**threat) >> worker
    worker >> Edge(label="cordon, relabel", **threat) >> aks
    worker >> Edge(label="isolated.json", **threat) >> blob
    logic >> Edge(label="snapshot + capture", **threat) >> snap
    logic >> Edge(label="manifest", **threat) >> blob
