"""The parse pool must use a spawn context, and the reason is a port, not a preference.

Linux's default start method is fork, and a forked worker inherits every open fd
including uvicorn's listening socket on :8000. Kill the parent ungracefully and the
workers are reparented to init still holding that socket: the port stays bound by
processes that will never answer a request, so the next launch cannot bind, Vite
comes up alone, and every ``/api`` call returns a proxy Bad Gateway until someone
hunts the strays down by hand. One hard kill poisoned every subsequent launch (#60).

The assertion is on the CONSTRUCTION rather than on a live socket because the real
reproduction needs a bound uvicorn, a real save and a SIGKILL, which is an
integration concern. What can regress silently is someone dropping the
``mp_context`` argument, and that is what this pins.
"""

from __future__ import annotations

import multiprocessing
import pathlib
from concurrent.futures import ProcessPoolExecutor

from chronicler.save import ingest


def test_the_parse_pool_is_built_with_an_explicit_spawn_context():
    """Pinned against the call site's source.

    Reaching the real construction means standing up the whole ingest loop, which
    is an integration concern. What can regress silently is someone deleting the
    ``mp_context`` argument as noise, and a source assertion catches exactly that.
    The behaviour it buys is proved separately, below.
    """
    source = pathlib.Path(ingest.__file__).read_text(encoding="utf-8")
    assert 'mp_context=multiprocessing.get_context("spawn")' in source, (
        "the parse pool lost its explicit spawn context; a forked worker inherits "
        "uvicorn's listening socket and orphans keep :8000 bound (#60)"
    )


def test_spawn_context_workers_do_not_inherit_a_listening_socket():
    """The property that actually matters, demonstrated on a real socket.

    A spawn-context child re-execs the interpreter and receives only the fds
    explicitly passed to it, so a socket open in the parent is not in the child's
    table. Under fork it would be, which is the whole bug.
    """
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as pool:
            inherited = pool.submit(_fd_is_a_listening_socket, sock.fileno()).result(timeout=60)
        assert inherited is False
    finally:
        sock.close()


def _fd_is_a_listening_socket(fd: int) -> bool:
    """Run in the worker: is `fd` a listening socket in THIS process?

    Module-level so it pickles for a spawn worker, which is the same constraint
    the real parse worker lives under.
    """
    import socket

    try:
        s = socket.socket(fileno=fd)
    except OSError:
        return False
    try:
        return s.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
    except OSError:
        return False
    finally:
        s.detach()
