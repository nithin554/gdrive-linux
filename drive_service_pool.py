"""Thread-local pool of Google Drive API service instances.

``googleapiclient`` and its underlying ``httplib2``/``urllib3`` HTTP transport
are **not thread-safe** — concurrent ``execute()`` or ``next_chunk()`` calls
on the same service object cause SSL corruption, connection errors, and
segfaults.

The solution: give each thread its **own** ``googleapiclient.discovery.build()``
service instance, each with an independent HTTP connection pool. Threads that
do only reads (FUSE ``read()`` calls) can then proceed truly in parallel
without lock contention.

Usage:
    pool = DriveServicePool(get_credentials_func)
    svc = pool.get()          # Returns THIS thread's service (creates if needed)
    pool.dispose_all()        # Clean up on shutdown

Write safety:
    Mutations (create, update, delete) still need the ``_drive_api_lock``
    from SyncManager because they modify shared mapping state. This pool
    only eliminates the lock for **reads**.
"""

import logging
import threading
import weakref
from collections.abc import Callable

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

log = logging.getLogger(__name__)


class DriveServicePool:
    """Manages per-thread Google Drive API service instances.

    Each thread that calls ``get()`` receives its own ``drive`` service
    object, created via ``googleapiclient.discovery.build("drive", "v3", ...)``
    with an independent HTTP connection pool.

    Thread services are lazily created and cached in a ``threading.local()``
    so there's zero overhead for lookup — each thread simply retrieves its
    own instance.

    When a thread dies, its service object is automatically released:
    the ``threading.local()`` is GC'd, the service's refcount drops to zero,
    and it falls out of the internal ``WeakSet`` — no memory leak.

    Args:
        get_credentials:
            A zero-argument callable that returns a valid
            ``google.oauth2.credentials.Credentials`` object. Called once
            per new thread the first time ``get()`` is invoked on that thread.
            Typically ``lambda: authenticate_google_drive()`` or a wrapper
            that reuses a cached credentials object.
    """

    def __init__(self, get_credentials: Callable[[], Credentials | None]):
        self._get_credentials = get_credentials
        # WeakSet — when a thread dies, its service object loses the last
        # strong reference (the threading.local is GC'd) and is automatically
        # removed from this set. No memory leak from dead threads.
        self._all_services: weakref.WeakSet = weakref.WeakSet()
        self._lock = threading.Lock()
        self._local = threading.local()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self):
        """Return the Drive service instance for the **current thread**.

        Creates one if this thread hasn't called ``get()`` before.
        The service is cached in ``threading.local()`` so subsequent calls
        on the same thread are O(1) with zero allocation.

        Returns:
            A ``googleapiclient.discovery.Resource`` for the Drive API v3,
            or ``None`` if credentials could not be obtained.
        """
        try:
            svc = self._local.service
            if svc is not None:
                return svc
        except AttributeError:
            pass

        # First time on this thread — build a new service
        creds = self._get_credentials()
        if creds is None:
            log.error(
                "DriveServicePool: No credentials available for thread %s.",
                threading.current_thread().name,
            )
            self._local.service = None
            return None

        try:
            svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        except Exception as exc:
            log.error(
                "DriveServicePool: Failed to build Drive service for thread %s: %s",
                threading.current_thread().name,
                exc,
            )
            self._local.service = None
            return None

        self._local.service = svc
        with self._lock:
            self._all_services.add(svc)
        log.debug(
            "DriveServicePool: Created service for thread %s (total alive: ~%d).",
            threading.current_thread().name,
            self.active_service_count,
        )
        return svc

    def dispose_all(self):
        """Close all HTTP connections and clear the pool.

        Call this on application shutdown to release socket resources.
        After calling this, the pool is empty and each thread will create
        a fresh service on its next ``get()`` call.
        """
        with self._lock:
            for svc in list(self._all_services):
                try:
                    if hasattr(svc, "_http") and svc._http:
                        svc._http.close()
                except Exception as exc:
                    log.debug("DriveServicePool: Error closing HTTP connection: %s", exc)
            self._all_services.clear()
        log.info("DriveServicePool: Disposed all service instances.")

    @property
    def active_service_count(self) -> int:
        """Number of service instances currently alive (across all threads).

        This is an approximation — ``weakref.WeakSet`` sizes are only
        updated after a garbage collection cycle.
        """
        with self._lock:
            return len(self._all_services)
