#!/usr/bin/env bash
# Checkpoint 3: Validate corrected configuration if provided
set -euo pipefail

export WORKSPACE="${WORKSPACE:-/workspace}"
export CHART_DIR="$WORKSPACE/charts/bitnami/spring-cloud-dataflow"
export REPORT="$WORKSPACE/charts/DRIFT_REPORT.json"

if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DRIFT_REPORT.json not found"}\n'
  exit 0
fi

# Check if any corrected config files exist
export HAS_CORRECTED=false
for f in "$CHART_DIR/values.yaml" "$CHART_DIR/templates/_helpers.tpl" "$CHART_DIR/templates/externalrabbitmq-secrets.yaml"; do
  if [[ -f "$f" ]]; then
    HAS_CORRECTED=true
    break
  fi
done

if [[ "$HAS_CORRECTED" = false ]]; then
  # No corrected chart files provided. This checkpoint is only truly
  # "optional" when the DRIFT_REPORT.json itself already spells out the
  # concrete, repo-specific fix: the new values.yaml key needed to select a
  # custom secret key, PLUS the rabbitmq.enabled conditional gating that
  # _helpers.tpl needs for the password/erlangCookie validation, PLUS the
  # override chain through _helpers.tpl and externalrabbitmq-secrets.yaml.
  # None of these tokens appear in the task prompt, so a report that merely
  # restates the prompt (or is empty/garbage) cannot satisfy this.
  if python3 -c "
import json, os
report = json.load(open(os.environ['REPORT']))
points = report.get('drift_points', [])
text = json.dumps(points).lower()
has_key = ('existingpasswordsecretpasswordkey' in text
           or 'existingsecretpasswordkey' in text
           or 'existingpasswordsecretkey' in text)
has_gate = 'rabbitmq.enabled' in text and ('erlang' in text or 'password' in text)
has_chain = '_helpers.tpl' in text and 'externalrabbitmq-secrets.yaml' in text
exit(0 if (has_key and has_gate and has_chain) else 1)
" 2>/dev/null; then
    printf '{"score": 1.0, "passed": true, "reason": "DRIFT_REPORT specifies the concrete fix: new secret-key field, rabbitmq.enabled gating, and override chain"}\n'
  else
    printf '{"score": 0.25, "passed": false, "reason": "No corrected chart files provided and DRIFT_REPORT.json lacks the concrete fix (existingPasswordSecretPasswordKey-style field, rabbitmq.enabled gating, and _helpers.tpl/externalrabbitmq-secrets.yaml chain)"}\n'
  fi
  exit 0
fi

# If helm is available, try to template
if command -v helm &>/dev/null; then
  if helm dependency build "$CHART_DIR" 2>/dev/null && helm template test-scdf "$CHART_DIR" --dry-run 2>/dev/null | grep -q 'kind:'; then
    printf '{"score": 1.0, "passed": true, "reason": "Corrected chart templates successfully"}\n'
  else
    printf '{"score": 0.25, "passed": false, "reason": "Corrected chart fails helm template"}\n'
  fi
else
  # No helm — check that values.yaml has the expected new parameters
  if python3 -c "
import os
values_file = os.path.join(os.environ['CHART_DIR'], 'values.yaml')
if not os.path.exists(values_file):
    exit(1)
content = open(values_file).read()
# Check for existence of secret key configuration parameter
if 'existingSecretPasswordKey' in content or 'existingPasswordSecretKey' in content or 'secretKey' in content:
    exit(0)
exit(1)
" 2>/dev/null; then
    printf '{"score": 1.0, "passed": true, "reason": "Corrected values.yaml includes secret key parameter"}\n'
  else
    printf '{"score": 0.25, "passed": false, "reason": "Corrected config missing expected parameters"}\n'
  fi
fi
