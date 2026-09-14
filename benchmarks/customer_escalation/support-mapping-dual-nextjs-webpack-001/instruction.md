# Map Next.js production build failure to webpack module federation resolve conflict in shared dependencies

A customer reports that their Next.js application builds successfully in
development but fails in production builds with:

    ModuleBuildError: Module build failed (from ./node_modules/next/dist/build/webpack/loaders/next-swc-loader.js):
    Error: Failed to load SWC binary for linux/x64

After working around the SWC issue, they get a different error:

    Error: Conflicting exports for 'default' in ./node_modules/react-dom/index.js
    Module not found: Can't resolve 'react-dom/client' in '/app/pages/_app.js'

The issue appears when using Next.js with a custom webpack configuration that
adds Module Federation plugin. The module resolution conflict occurs between
Next.js's internal webpack configuration and the customer's custom config.

Your task:
1. Find where Next.js configures webpack internally — trace from the build
   entry point through the webpack config generation, identifying how Next.js
   sets resolve.alias and module rules for React/React-DOM.
2. Find where webpack resolves conflicting module exports — trace through
   webpack's module factory and resolution algorithm to identify how multiple
   export definitions for the same module are handled.
3. Map the conflict path: Next.js sets react-dom alias -> customer adds Module
   Federation -> webpack resolver encounters two definitions -> error. Document
   each file and function in the chain.
4. Identify the resolution order and why Next.js's internal aliases conflict
   with Module Federation's shared scope.

- Full module resolution chain from Next.js config through webpack resolver
- Why Module Federation conflicts with Next.js react-dom alias
- Correct configuration approach for combining Next.js with Module Federation
