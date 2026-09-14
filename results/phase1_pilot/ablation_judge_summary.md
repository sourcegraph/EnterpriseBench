# Ablation Judge Summary

Removed-repo checkpoints should score <= 0.30 per PRD CRNT threshold.

| task_id | variant | checkpoint | n | mean_min | max_min | <=0.30? |
| --- | --- | --- | ---: | ---: | ---: | --- |
| incident-inv-docker-shutdown-004 | ablate-containerd | affected_services | 3 | 1.00 | 1.00 | no |
| incident-inv-docker-shutdown-004 | ablate-containerd | error_chain_trace | 3 | 1.00 | 1.00 | no |
| incident-inv-docker-shutdown-004 | ablate-containerd | remediation_proposal | 3 | 0.50 | 0.50 | no |
| incident-inv-docker-shutdown-004 | ablate-containerd | root_cause_identification | 3 | 1.00 | 1.00 | no |
| incident-inv-docker-shutdown-004 | ablate-moby | affected_services | 3 | 0.33 | 0.50 | no |
| incident-inv-docker-shutdown-004 | ablate-moby | error_chain_trace | 3 | 0.33 | 0.50 | no |
| incident-inv-docker-shutdown-004 | ablate-moby | remediation_proposal | 3 | 0.33 | 0.50 | no |
| incident-inv-docker-shutdown-004 | ablate-moby | root_cause_identification | 3 | 0.33 | 0.50 | no |
| support-map-grafana-alerts-004 | ablate-alertmanager | code_paths | 3 | 0.31 | 0.35 | no |
| support-map-grafana-alerts-004 | ablate-alertmanager | ownership | 3 | 0.50 | 0.50 | no |
| support-map-grafana-alerts-004 | ablate-alertmanager | related_issues | 3 | 0.28 | 0.50 | no |
| support-map-grafana-alerts-004 | ablate-alertmanager | severity | 3 | 0.83 | 1.00 | no |
| support-map-grafana-alerts-004 | ablate-grafana | code_paths | 3 | 0.50 | 0.50 | no |
| support-map-grafana-alerts-004 | ablate-grafana | ownership | 3 | 0.58 | 1.00 | no |
| support-map-grafana-alerts-004 | ablate-grafana | related_issues | 3 | 0.11 | 0.33 | no |
| support-map-grafana-alerts-004 | ablate-grafana | severity | 3 | 1.00 | 1.00 | no |
