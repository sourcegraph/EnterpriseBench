# Map customer zod-prisma codegen drift after Prisma schema reorder to the Prisma DMMF / zod-prisma generator boundary

A customer reports that after reordering fields in their
schema.prisma file, their zod-prisma-generated zod schemas now
validate request bodies inconsistently across services. Some
services accept payloads other services reject, even though both
regenerate from the same schema.prisma file.

Your task:
1. Find where Prisma's generator API exposes the DMMF (Data Model
   Meta Format) to downstream generators like zod-prisma.
2. Find where zod-prisma walks the DMMF model fields and how field
   ordering affects the produced zod object.
3. Trace the cross-repo path: prisma generate -> generator helper
   getDMMF -> DMMF.Model.fields array -> zod-prisma model writer
   -> zod object schema. Cite each file and the function responsible.
4. Identify why DMMF's fields order is documented as schema-source
   order, and why zod's z.object().strict() can validate identical
   key sets but generate different default-discriminator behavior
   when zod-prisma emits unions with field-position-dependent keys.
