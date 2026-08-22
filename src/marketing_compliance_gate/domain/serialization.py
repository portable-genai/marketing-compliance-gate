"""JSON-safe serialization for domain objects.

``to_jsonable(obj)`` converts dataclasses, enums, datetimes and nested containers into
plain JSON-serializable Python. Used by the platform HTTP clients and the audit sink.

**Sourced from the shared ``hex-service-kit`` commons.** The walker used
to live here as a copy; it is now re-exported from :mod:`hex_service_kit.serialization`
(same rules: enum ``.value``, ISO datetimes, dataclass field dicts, tuples to lists,
stringified keys, never raises). Pure standard library.
"""

from __future__ import annotations

from hex_service_kit.serialization import to_jsonable

__all__ = ["to_jsonable"]
