# Map customer React Suspense boundary unmount on Relay store invalidation to the React reconciler / Relay environment boundary

A customer reports that their React 18 + Relay 16 page silently unmounts
and remounts a Suspense boundary every time a Relay store invalidation
fires from a mutation, even though no fragment subscribed to that
boundary actually reads the invalidated record. The whole subtree's
client-side state is lost.

Your task:
1. Find where React's reconciler decides whether a Suspense boundary
   re-renders or unmounts after a context update.
2. Find where Relay's RelayModernEnvironment publishes a store
   invalidation and how it notifies subscribers via
   useSubscribeToInvalidationState.
3. Trace the cross-repo path: Relay store mutation commit ->
   notify subscribers -> useFragment / useLazyLoadQuery hook re-eval
   -> React useSyncExternalStore subscription -> reconciler
   schedules update -> Suspense boundary re-render decision. Cite
   each file and the function or attribute responsible.
4. Identify why a global store-version bump can transitively cause
   any consumer of useFragment under the Suspense boundary to
   re-throw a promise, forcing the boundary to fall back.
