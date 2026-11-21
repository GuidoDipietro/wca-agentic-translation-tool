"""Cross-platform exclusive lock for the server `.lock` file.

Linux uses fcntl; Windows uses msvcrt. Raises BlockingIOError when another
instance holds the lock, matching the previous fcntl-only behavior.
"""
import os

try:
    import fcntl
except ImportError:  # Windows has no fcntl.
    fcntl = None

if os.name == 'nt':
    import msvcrt


def exclusive_lock(fileobj):
    """Acquire an exclusive, non-blocking lock or raise BlockingIOError."""
    if fcntl is not None:
        fcntl.flock(fileobj, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    if os.name == 'nt':
        try:
            fileobj.seek(0)
            msvcrt.locking(fileobj.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError('Another instance is already using this data folder.') from exc
        return
    raise BlockingIOError('File locking is not supported on this platform.')


def release_lock(fileobj):
    """Release a lock acquired with exclusive_lock; best effort."""
    try:
        if fcntl is not None:
            fcntl.flock(fileobj, fcntl.LOCK_UN)
            return
        if os.name == 'nt':
            try:
                fileobj.seek(0)
                msvcrt.locking(fileobj.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
    except OSError:
        pass
