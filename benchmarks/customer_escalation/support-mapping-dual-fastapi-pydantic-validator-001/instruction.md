# Map customer FastAPI request-body field-drop to the Pydantic v2 model_validator chain integration boundary

A customer reports that after upgrading from Pydantic v1 to Pydantic v2
their FastAPI POST endpoint silently drops a discriminated-union field on
the request body. The endpoint returns 200 OK, the response body shows the
field as None, but the client clearly sent it. No 422 error is raised.

Your task:
1. Find where FastAPI builds a request-body parameter from a Pydantic
   model and validates it before invoking the route handler.
2. Find where Pydantic v2 evaluates a discriminated union and how
   model_validator(mode='before') interacts with discriminator field
   resolution.
3. Trace the cross-repo flow: FastAPI dependency injection -> body
   parameter solving -> Pydantic model_validate_json -> discriminator
   resolution -> field assignment. Cite each file and function.
4. Identify why a model_validator(mode='before') that pre-processes
   incoming JSON can silently strip the discriminator key, leading to
   the union resolving to its default variant with the field set to None.
