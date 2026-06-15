# Identify dead code in client-go from removed or deprecated Kubernetes API groups in v1.28

Kubernetes v1.28.0 removed several deprecated API groups and versions as part of the
ongoing API lifecycle management. However, client-go v0.28.0 may still contain client
code, informers, listers, and typed clients for these removed API groups.

Your task:
1. Identify API groups/versions that were removed or fully deprecated in Kubernetes
   v1.28.0 — check the API server registration code, generated API types, and
   deprecation annotations in staging/src/k8s.io/api/.
2. Search client-go for code that references these removed API groups — look for
   typed clients, informers, listers, and fake clients in the corresponding
   directories.
3. Determine which client-go code paths are now dead because the API server no
   longer serves those endpoints — distinguish between:
   - Fully dead (API group removed entirely)
   - Partially dead (version removed but group still exists under a new version)
   - Compatibility stubs (intentionally kept for backward compatibility)
4. For each dead code path, document the removed API, the client-go files affected,
   and whether removal would be safe.

Write your findings to /workspace/dead_code_report.json as a JSON array where each
entry uses `file` (the dead client-go file path) and `symbol` (the dead typed-client,
informer, lister, or fake-client symbol) as the primary keys:
[
  {
    "file": "path/in/client-go",
    "symbol": "DeadSymbolName",
    "api_group": "apigroup/version",
    "status": "removed|deprecated",
    "k8s_evidence_file": "path/in/kubernetes/repo",
    "kind": "typed_client|informer|lister|fake_client|expansion",
    "confidence": "high|medium|low",
    "evidence": "explanation of why this is dead code",
    "safe_to_remove": true|false
  }
]
