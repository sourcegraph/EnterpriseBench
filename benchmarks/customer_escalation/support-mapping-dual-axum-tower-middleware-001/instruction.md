# Map customer axum auth middleware bypassed for tracing-instrumented routes to the Router / tower ServiceBuilder layer-order boundary

A customer reports that their axum service has an auth middleware
that runs on most routes but is silently bypassed for routes nested
under a Router that wraps a tracing layer. Requests to those routes
succeed without the Authorization header; the auth layer never
produces a span.

Your task:
1. Find where axum's Router applies layers and how nested Routers
   compose with tower::ServiceBuilder layers.
2. Find where tower::ServiceBuilder enforces layer order and how
   .layer() differs from .route_layer() in axum.
3. Trace the cross-repo path: axum::Router::route_layer vs
   axum::Router::layer -> Router::merge -> tower::ServiceBuilder ->
   tower::Layer::layer composition. Cite each file and the function
   responsible.
4. Identify why route_layer() applied to a parent Router does NOT
   wrap routes added via Router::merge from a sub-router, leaving
   the auth layer skipped for nested routes.
