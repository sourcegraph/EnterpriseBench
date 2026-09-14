# Map customer TypeScript decorator regression to the Babel plugin-proposal-decorators / SWC transform stage boundary

A customer reports that after switching their build pipeline from Babel
to SWC for faster CI, their @injectable() class decorators on Nest.js
providers stop firing. The classes still construct, but the DI metadata
table never registers them, so resolution fails at runtime with
'Cannot read properties of undefined (reading getMetadata)'.

Your task:
1. Find where Babel's plugin-proposal-decorators decides between the
   stage-2 legacy form and the stage-3 (TC39) form, and how it interacts
   with @babel/plugin-transform-typescript ordering.
2. Find where SWC's TypeScript transform handles decorators -- which
   stage it implements, and whether it applies decorators before or
   after class-property emit.
3. Trace the cross-repo path: babel preset-typescript ->    plugin-proposal-decorators (legacy=true) -> Reflect.metadata side
   effect -> Nest's IoC registration. Then trace SWC's parallel path
   and identify the divergence.
4. Identify the configuration choice (babel-plugin order, swc.jsc.
   transform.legacyDecorator + decoratorMetadata) that determines
   whether DI metadata is emitted, and why the customer's swc config
   produces no metadata at all.
