# Detect config drift between ArgoCD application controller defaults and argo-helm chart values.yaml overrides

Your platform team deploys ArgoCD using the official argo-helm Helm chart. After
upgrading to ArgoCD v2.9.0 with argo-helm chart version argo-cd-5.51.0, several
application syncs started failing with timeout errors.

Investigation suggests configuration drift between the ArgoCD application controller's
built-in defaults and the Helm chart's values.yaml overrides. The Helm chart may be
setting values that conflict with or override controller defaults in unexpected ways.

Your task:
1. Examine the ArgoCD application controller's default configuration values in the
   Go source code (look for default constants, config maps, and flag defaults in
   the controller package).
2. Examine the argo-helm chart's values.yaml for the argo-cd chart, focusing on
   controller-related configuration sections.
3. Identify all configuration drift points where the Helm chart values diverge from
   the ArgoCD controller's built-in defaults — particularly around sync timeouts,
   retry settings, resource tracking methods, and health check configurations.
4. For each drift point, document: the ArgoCD default (with source file), the Helm
   override (with values.yaml path), and the potential impact.

Write your analysis to /workspace/charts/DRIFT_REPORT.json with:
{
  "drift_points": [
    {
      "config_key": "<configuration parameter>",
      "argocd_default": "<default value from Go source>",
      "argocd_source_file": "<path in argo-cd repo>",
      "helm_override": "<value in Helm chart>",
      "helm_source_path": "<values.yaml key path>",
      "impact": "<potential impact of this drift>",
      "override_chain": ["source -> intermediate -> final"]
    }
  ]
}
