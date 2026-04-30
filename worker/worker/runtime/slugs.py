"""Re-export of shared.core.slugs so worker callers don't have to reach into shared.

The original implementation lived here. It moved to ``shared.core.slugs`` so the
API can generate slugs at deployment-create time too. Worker code that imports
``worker.runtime.slugs`` keeps working via this thin re-export.
"""
from shared.core.slugs import is_valid_slug, make_slug

__all__ = ["is_valid_slug", "make_slug"]
