#!/usr/bin/env python3
"""Structural checks on the rendered manifests.

`kustomize build` proves the YAML parses and the overlays apply. This proves the
things that actually break a deployment and that no schema validator would catch:
selectors that match nothing, references to Secrets or ConfigMaps that are never
created, containers without probes or limits, and PodDisruptionBudgets that would
deadlock a node drain.

    python3 deploy/validate.py                # both overlays
    python3 deploy/validate.py prod
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("pyyaml is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent
WORKLOADS = {"Deployment", "StatefulSet", "Job", "CronJob"}


def render(overlay: str) -> list[dict]:
    result = subprocess.run(
        ["kustomize", "build", str(ROOT / "overlays" / overlay)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.exit(f"kustomize build failed for {overlay}:\n{result.stderr}")
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def pod_spec(doc: dict) -> dict | None:
    kind = doc.get("kind")
    if kind in {"Deployment", "StatefulSet", "Job"}:
        return doc["spec"]["template"]["spec"]
    if kind == "CronJob":
        return doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return None


def check(overlay: str) -> list[str]:
    docs = render(overlay)
    problems: list[str] = []

    def fail(msg: str) -> None:
        problems.append(f"[{overlay}] {msg}")

    by_kind: dict[str, list[dict]] = {}
    for doc in docs:
        by_kind.setdefault(doc["kind"], []).append(doc)

    # --- every Service selector matches at least one workload's labels ------
    workload_labels = [
        doc["spec"]["template"]["metadata"].get("labels", {})
        for kind in ("Deployment", "StatefulSet")
        for doc in by_kind.get(kind, [])
    ]
    for service in by_kind.get("Service", []):
        selector = service["spec"].get("selector") or {}
        if not selector:
            fail(f"Service {service['metadata']['name']} has no selector")
            continue
        if not any(selector.items() <= labels.items() for labels in workload_labels):
            fail(
                f"Service {service['metadata']['name']} selector {selector} "
                "matches no pod template"
            )

    # --- referenced ConfigMaps and Secrets ---------------------------------
    defined_configmaps = {d["metadata"]["name"] for d in by_kind.get("ConfigMap", [])}
    # Secrets are created out of band on purpose; these are the expected names.
    external_secrets = {"iptt-db", "iptt-app"}

    for doc in docs:
        spec = pod_spec(doc)
        if not spec:
            continue
        name = f"{doc['kind']} {doc['metadata']['name']}"
        for container in spec.get("containers", []):
            for source in container.get("envFrom", []):
                if "configMapRef" in source:
                    ref = source["configMapRef"]["name"]
                    if ref not in defined_configmaps:
                        fail(f"{name} references undefined ConfigMap '{ref}'")
                if "secretRef" in source:
                    ref = source["secretRef"]["name"]
                    if ref not in external_secrets:
                        fail(f"{name} references unexpected Secret '{ref}'")
            for env in container.get("env", []):
                value_from = env.get("valueFrom", {})
                if "configMapKeyRef" in value_from:
                    ref = value_from["configMapKeyRef"]
                    if ref["name"] not in defined_configmaps:
                        fail(f"{name} env {env['name']} -> missing ConfigMap {ref['name']}")
                    else:
                        data = next(
                            d["data"]
                            for d in by_kind["ConfigMap"]
                            if d["metadata"]["name"] == ref["name"]
                        )
                        if ref["key"] not in data:
                            fail(
                                f"{name} env {env['name']} -> ConfigMap "
                                f"{ref['name']} has no key '{ref['key']}'"
                            )
                if "secretKeyRef" in value_from:
                    ref = value_from["secretKeyRef"]
                    if ref["name"] not in external_secrets:
                        fail(f"{name} env {env['name']} -> unexpected Secret {ref['name']}")

    # --- security and resource hygiene -------------------------------------
    for doc in docs:
        spec = pod_spec(doc)
        if not spec:
            continue
        name = f"{doc['kind']} {doc['metadata']['name']}"
        pod_security = spec.get("securityContext", {})
        if not pod_security.get("runAsNonRoot"):
            fail(f"{name} does not set runAsNonRoot")
        # OpenShift assigns an arbitrary UID under restricted-v2; pinning one
        # makes the pod fail to admit.
        if "runAsUser" in pod_security:
            fail(f"{name} pins runAsUser, which conflicts with the restricted-v2 SCC")

        for container in spec.get("containers", []):
            label = f"{name} container {container['name']}"
            security = container.get("securityContext", {})
            if security.get("allowPrivilegeEscalation") is not False:
                fail(f"{label} does not disable privilege escalation")
            if security.get("capabilities", {}).get("drop") != ["ALL"]:
                fail(f"{label} does not drop all capabilities")
            resources = container.get("resources", {})
            for section in ("requests", "limits"):
                for key in ("cpu", "memory"):
                    if key not in resources.get(section, {}):
                        fail(f"{label} has no {section}.{key}")

            # Long-running services must be probed; Jobs must not be.
            if doc["kind"] in {"Deployment", "StatefulSet"}:
                for probe in ("livenessProbe", "readinessProbe"):
                    if probe not in container:
                        fail(f"{label} has no {probe}")

    # --- PodDisruptionBudget sanity ----------------------------------------
    replicas = {
        doc["metadata"]["name"]: doc["spec"].get("replicas", 1)
        for doc in by_kind.get("Deployment", [])
    }
    for budget in by_kind.get("PodDisruptionBudget", []):
        target = budget["metadata"]["name"]
        minimum = budget["spec"].get("minAvailable")
        count = replicas.get(target)
        if count is None:
            fail(f"PodDisruptionBudget {target} does not name a Deployment")
        elif isinstance(minimum, int) and minimum >= count:
            fail(
                f"PodDisruptionBudget {target} requires {minimum} of {count} "
                "replicas and would block every node drain"
            )

    # --- the API must not be exposed directly ------------------------------
    for route in by_kind.get("Route", []):
        target = route["spec"]["to"]["name"]
        if target != "iptt-web":
            fail(f"Route {route['metadata']['name']} exposes '{target}', not the web service")
        if route["spec"].get("tls", {}).get("termination") != "edge":
            fail(f"Route {route['metadata']['name']} does not terminate TLS")

    # --- the database must be reachable from the API only ------------------
    db_policies = [
        p
        for p in by_kind.get("NetworkPolicy", [])
        if p["spec"]["podSelector"].get("matchLabels", {}).get(
            "app.kubernetes.io/component"
        )
        == "database"
    ]
    if not db_policies:
        fail("no NetworkPolicy restricts ingress to the database")

    if not any(
        p["metadata"]["name"].endswith("default-deny-ingress")
        for p in by_kind.get("NetworkPolicy", [])
    ):
        fail("no default-deny ingress NetworkPolicy")

    # IP families. A Service with no ipFamilyPolicy defaults to SingleStack in
    # the cluster's primary family, which on a dual-stack cluster leaves the
    # other family with no ClusterIP and no DNS record - and the failure is
    # silent until something tries to connect over it.
    for svc in by_kind.get("Service", []):
        policy = svc["spec"].get("ipFamilyPolicy")
        if policy is None:
            fail(
                f"Service {svc['metadata']['name']} does not declare "
                "ipFamilyPolicy; it will be SingleStack in the cluster's "
                "primary family only"
            )
        elif policy not in {"PreferDualStack", "RequireDualStack", "SingleStack"}:
            fail(
                f"Service {svc['metadata']['name']} has an invalid "
                f"ipFamilyPolicy: {policy!r}"
            )

    return problems


def main() -> int:
    overlays = sys.argv[1:] or ["dev", "prod"]
    all_problems: list[str] = []
    for overlay in overlays:
        found = check(overlay)
        all_problems += found
        status = "FAIL" if found else " ok "
        print(f"[{status}] {overlay}: {len(found)} problem(s)")

    if all_problems:
        print()
        for problem in all_problems:
            print(f"  - {problem}")
        return 1

    print("\nAll structural checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
