"""Generic dataclass <-> JSON-friendly codec (ck3_chronicler-27ov.10 / audit H1/J2).

A single encode/decode pair driven by ``dataclasses.fields()`` + typing
introspection, replacing ~700 lines of hand-mirrored serde in
:mod:`chronicler.save.baseline`. Every field the dataclass declares is
serialized and restored automatically, so the whole "added a field without
a matching to/from-dict override" bug class (kig9, zm0q, g56m, qug, audit
H2 — ten dropped fields) cannot recur: a new field rides through the codec
for free.

Type universe (the SaveSnapshot graph):

* scalars — ``int`` / ``float`` / ``str`` / ``bool`` / ``None`` (JSON-native)
* ``X | None`` optionals (PEP 604 unions and ``typing.Optional``)
* nested ``@dataclass`` instances (recursed)
* ``tuple[X, ...]`` (variadic) and ``tuple[A, B]`` (fixed) -> JSON arrays
* ``frozenset[X]`` / ``set[X]`` -> sorted JSON arrays (deterministic output)
* ``dict[K, V]`` where ``K`` is ``int`` or ``tuple[int, ...]`` — JSON has no
  non-string keys, so int keys are stringified and tuple keys are encoded
  ``"a:b"`` (matching the legacy on-disk format byte-for-byte)

The encoder is value-driven (it has concrete objects in hand); the decoder
is type-hint-driven (it must rebuild tuples/frozensets/int keys/dataclasses
from the JSON primitives the encoder flattened them into).

Decode is deliberately lenient about *absent* keys — a field missing from
the dict falls back to its dataclass default (or ``None`` for a defaulted-by-
nullability field), so a baseline written by an older build still loads its
overlapping fields. It is *strict* about malformed values: a type mismatch
raises, and the caller (``baseline.load_baseline``) discards the whole file
and rebaselines rather than silently keeping a half-decoded snapshot.
"""

from __future__ import annotations

import types
import typing
from dataclasses import MISSING, fields, is_dataclass
from functools import cache
from typing import Any, get_args, get_origin

_NONE_TYPE = type(None)


def to_jsonable(obj: Any) -> Any:
    """Encode a dataclass instance (or any value in its field graph) into a
    JSON-serializable structure. Value-driven: dispatches on runtime type."""
    if obj is None or isinstance(obj, (str, bool, int, float)):
        return obj
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (frozenset, set)):
        return _sorted([to_jsonable(v) for v in obj])
    if isinstance(obj, (tuple, list)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, dict):
        return {_encode_key(k): to_jsonable(v) for k, v in obj.items()}
    raise TypeError(f"cannot encode value of type {type(obj).__name__!r}")


def from_jsonable(cls: type, data: Any) -> Any:
    """Decode a JSON structure back into an instance of dataclass ``cls``.

    Raises ``TypeError`` / ``KeyError`` / ``ValueError`` on malformed input;
    callers that must never crash should wrap the call (as ``load_baseline``
    does) and treat a raise as "discard and rebaseline".
    """
    return _decode(data, cls)


# --- encoding helpers ---


def _sorted(items: list[Any]) -> list[Any]:
    """Sort encoded set members for deterministic output. Set element types
    here are all orderable (int / str / [int, int]); fall back to insertion
    order for anything that isn't (defence-in-depth — no such field today)."""
    try:
        return sorted(items)
    except TypeError:
        return items


def _encode_key(key: Any) -> str:
    """Encode a dict key. Tuple keys (``tuple[int, int]``) become ``"a:b"``;
    every other key is stringified (JSON keys must be strings)."""
    if isinstance(key, tuple):
        return ":".join(str(part) for part in key)
    return str(key)


# --- decoding helpers ---


@cache
def _hints(cls: type) -> dict[str, Any]:
    """Resolved type hints for ``cls`` (caches across the snapshot's many
    rows so reflection cost is paid once per dataclass, not per instance)."""
    return typing.get_type_hints(cls)


def _is_union(origin: Any) -> bool:
    return origin is typing.Union or origin is types.UnionType


def _non_none_arg(tp: Any) -> Any:
    """The sole non-``None`` arm of an ``X | None`` union."""
    args = [a for a in get_args(tp) if a is not _NONE_TYPE]
    return args[0]


def _is_optional(tp: Any) -> bool:
    return _is_union(get_origin(tp)) and _NONE_TYPE in get_args(tp)


def _decode(value: Any, tp: Any) -> Any:
    if value is None:
        return None
    origin = get_origin(tp)
    if _is_union(origin):
        return _decode(value, _non_none_arg(tp))
    if is_dataclass(tp):
        return _decode_dataclass(tp, value)
    if origin is tuple:
        args = get_args(tp)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(v, args[0]) for v in value)
        return tuple(_decode(v, a) for v, a in zip(value, args, strict=True))
    if origin in (frozenset, set):
        (elem,) = get_args(tp)
        factory = frozenset if origin is frozenset else set
        return factory(_decode(v, elem) for v in value)
    if origin is list:
        (elem,) = get_args(tp)
        return [_decode(v, elem) for v in value]
    if origin is dict:
        key_t, val_t = get_args(tp)
        return {_decode_key(k, key_t): _decode(v, val_t) for k, v in value.items()}
    # Leaf scalar. JSON round-trips int/str/bool/None as-is; coerce an int
    # back to float for float-typed fields (e.g. dynasty_renown 0 -> 0.0).
    if tp is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    return value


def _decode_dataclass(cls: type, data: Any) -> Any:
    if not isinstance(data, dict):
        raise TypeError(f"{cls.__name__} expects a dict, got {type(data).__name__}")
    hints = _hints(cls)
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name in data:
            kwargs[f.name] = _decode(data[f.name], hints[f.name])
        elif f.default is not MISSING or f.default_factory is not MISSING:
            continue  # absent + has a default: let the constructor apply it
        elif _is_optional(hints[f.name]):
            kwargs[f.name] = None  # absent + nullable: legacy lenient behaviour
        else:
            raise KeyError(f"{cls.__name__} missing required field {f.name!r}")
    return cls(**kwargs)


def _decode_key(key: str, key_t: Any) -> Any:
    """Decode a stringified dict key back to its declared type."""
    if get_origin(key_t) is tuple:
        args = get_args(key_t)
        parts = key.split(":")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode_scalar_key(p, args[0]) for p in parts)
        return tuple(_decode_scalar_key(p, a) for p, a in zip(parts, args, strict=True))
    return _decode_scalar_key(key, key_t)


def _decode_scalar_key(key: str, key_t: Any) -> Any:
    if key_t is int:
        return int(key)
    if key_t is float:
        return float(key)
    return key
