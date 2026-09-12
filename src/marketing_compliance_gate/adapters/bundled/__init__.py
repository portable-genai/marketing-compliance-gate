"""Adapters over data bundled IN the repository, shared by every profile that wants them.

The profile families (``gcp``, ``local``, ``onprem``, ``platform``) bind a port to a backend.
The one adapter here binds a port to a versioned artefact shipped inside the package, so it
belongs to no backend: the ``gcp`` profile serves rules from it, and the ``local`` profile
indexes the same artefact on disk. Nothing in this package imports a cloud SDK.
"""
