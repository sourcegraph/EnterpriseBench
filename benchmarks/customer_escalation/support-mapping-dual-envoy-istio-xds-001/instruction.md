# Map customer Istio listener drain timing 503 spikes during xDS push to the Envoy ListenerManager / Istio Pilot ADS push-order boundary

A customer reports that during routine Istio Pilot config pushes their
service mesh sees brief 503 spikes for ~5 seconds on existing
connections, even for unchanged listeners. The spikes correlate with
ADS push events; the listener_manager logs show drain_in_progress
but no listener config actually changed.

Your task:
1. Find where Envoy's ListenerManager processes an LDS update and
   decides whether to drain existing listeners.
2. Find where Istio Pilot generates the ADS resource set and the
   ordering between LDS, RDS, CDS, and EDS resources for a config
   change.
3. Trace the cross-repo path: Pilot ConfigGenerator -> ADS Push ->
   Envoy XdsManager -> ListenerManager addOrUpdateListener ->
   ConnectionHandler drain. Cite each file and the function or
   attribute responsible.
4. Identify why Pilot's full-snapshot ADS push (vs delta-xDS) can
   send LDS resources with the same hash but a new version, causing
   Envoy to re-process them and trigger a drain even when nothing
   changed.
