# Map customer Wagtail page-tree query slowdown to the Django ORM treebeard MP_Node path-encoding boundary

A customer reports that their Wagtail site's page navigation is suddenly
executing N+1 queries per request after they upgraded Django and added a
thin custom Page subclass. Wagtail's get_descendants() now produces a query
plan that scans the entire wagtailcore_page table instead of using the
MP_Node materialized path index.

Your task:
1. Find where Wagtail's Page model is defined and how it inherits from
   treebeard's MP_Node (the materialized-path tree mixin) for path-prefix
   queries.
2. Find where Django's QuerySet builds the SQL for a startswith path lookup
   and how the query planner picks between an index scan and a full table
   scan based on the Field's db_index and ordering.
3. Trace the cross-repo path from Page.get_descendants() through MP_Node's
   path filter into Django's QuerySet.filter() and the SQL builder. Cite
   each file and the function or attribute responsible.
4. Identify why the customer's custom Page subclass disables the path index
   (Meta.indexes inheritance + abstract base class semantics on Wagtail's
   AbstractPage).
