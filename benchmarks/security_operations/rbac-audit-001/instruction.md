# RBAC Authorization Audit: Calico Network Policy Tier Changes

## Context

Our platform security team received a report from a cluster administrator who
noticed that a user with permissions only on the "default" tier was able to move
a network policy into the "restricted" tier. This should not be possible — tier
permissions are supposed to gate which tiers a user can read and write policies
in.

Calico uses a dual authorization model:
- The **Kubernetes apiserver** (custom resource storage) checks RBAC on policy
  operations
- An **admission webhook** provides additional tier-based authorization checks

We suspect the two authorization paths are checking different things, creating a
gap that allows unauthorized tier changes.

## What I Need

1. **Affected policy types**: Identify all Calico network policy types whose
   tier assignment is enforced through the apiserver's custom resource storage
   layer for policies.

2. **Apiserver authorization analysis**: For each affected policy type, locate
   the code path in its storage layer that runs on a policy update and
   determine exactly which tier(s) it authorizes against before allowing the
   change.

3. **Webhook authorization analysis**: Locate the admission webhook's RBAC
   authorization logic for policies. On an update, determine exactly which
   version of the policy object it inspects when deciding which tier to
   authorize against.

4. **The bypass**: Compare the two authorization paths you traced above and
   describe the specific scenario where they disagree. Explain concretely how
   a user could exploit that disagreement to move a policy into a tier they
   don't have permission on.

5. **Remediation**: What changes to the code would close the gap? Be specific
   about which function(s) need to change and how.

## Output

Write your findings to `/workspace/security_audit.md` with clear sections for
each of the above.
