"""Shared API-test harness (ck3_chronicler-27ov.68 / audit M-T2).

Before this, ``test_api.py`` carried ~33 inline copies of the same 8-10
line campaign-bootstrap ritual — ``setenv CHRONICLER_DATA_DIR`` →
``make_engine_for_path`` → ``Base.metadata.create_all`` → optional seed →
``create_campaign`` → ``create_app`` → ``TestClient`` — plus a handful of
section fixtures (``cost_client``, ``closing_client`` …) each re-spelling
it. The audit measured 41× ``create_all``, 49× ``setenv``, 68× ``TestClient``.

:class:`CampaignHarness` owns that ritual once: env isolation, the
per-campaign DB bootstrap + optional seeding, registry registration
(archive / overview / last-tick / tracked-character side effects), and the
``create_app`` + ``TestClient`` lifecycle (entered eagerly, torn down at
fixture scope). The ``_seed_*`` helpers collapse to ``Callable[[Session],
None]`` seed callables that only populate a campaign-DB session.

Path layout is deliberately stable and reconstructable —
``<data_dir>/registry.db`` and ``<data_dir>/campaigns/<slug>.db`` — because
a few tests reopen a campaign DB by hand (e.g. the cost-summary tests add a
biography to ``campaigns/cost.db`` after the fixture built it).

Exposed as the ``api`` (full :class:`CampaignHarness`) and ``make_campaign``
(single-campaign one-liner) fixtures in ``tests/conftest.py``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from chronicler.api import create_app
from chronicler.db import Base, make_engine_for_path, make_session_factory
from chronicler.db.registry import (
    Campaign,
    add_tracked_character,
    archive_campaign,
    create_campaign,
    set_campaign_last_tick,
    update_campaign_overview,
)
from chronicler.narrative.provider import NarrativeProvider

SeedFn = Callable[[Session], None]


def _slug(name: str) -> str:
    """Filesystem-safe stem for a campaign DB file.

    A bare alphanumeric name (``"cost"``, ``"closing"``) maps to itself so
    tests that reopen ``campaigns/<name>.db`` by hand keep resolving.
    """
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s or "campaign"


@dataclass
class CampaignHarness:
    """Builds isolated registry + campaign DBs and entered TestClients.

    One instance per test (the ``api`` fixture). All campaigns share the
    single ``registry`` so multi-campaign list/migrate tests just call
    :meth:`campaign` more than once before :meth:`client`.
    """

    data_dir: Path
    _used_slugs: set[str] = field(default_factory=set)
    _clients: list[TestClient] = field(default_factory=list)

    @property
    def registry(self) -> Path:
        return self.data_dir / "registry.db"

    def db_path_for(self, name: str) -> Path:
        """Stable, collision-free DB path for ``name`` under the data dir."""
        stem = _slug(name)
        candidate = stem
        n = 2
        while candidate in self._used_slugs:
            candidate = f"{stem}_{n}"
            n += 1
        self._used_slugs.add(candidate)
        return self.data_dir / "campaigns" / f"{candidate}.db"

    def campaign(
        self,
        name: str = "test-campaign",
        *,
        seed: SeedFn | None = None,
        archived: bool = False,
        overview: dict[str, Any] | None = None,
        last_tick: dict[str, Any] | None = None,
        tracked: list[tuple[int, str]] | None = None,
        create_kwargs: dict[str, Any] | None = None,
        db_name: str | None = None,
    ) -> Campaign:
        """Bootstrap + register one campaign in the shared registry.

        :param seed: optional callable handed an open session over the
            fresh campaign DB; the harness commits and disposes around it.
        :param archived: seal the campaign immediately after registering.
        :param overview: kwargs forwarded to ``update_campaign_overview``.
        :param last_tick: kwargs forwarded to ``set_campaign_last_tick``.
        :param tracked: ``(ck3_id, role)`` pairs added as tracked characters.
        :param create_kwargs: extra kwargs for ``create_campaign``
            (``founding_dynasty_name``, ``ck3_version``, …).
        :param db_name: pin the DB file stem (``<data_dir>/campaigns/
            <db_name>.db``) instead of slugging ``name`` — for the few
            tests that reopen the campaign DB by a fixed path.
        :returns: the registered :class:`Campaign`.
        """
        db_path = (
            self.data_dir / "campaigns" / f"{db_name}.db"
            if db_name is not None
            else self.db_path_for(name)
        )
        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = make_engine_for_path(db_path)
        Base.metadata.create_all(engine)
        if seed is not None:
            session_factory = make_session_factory(engine)
            with session_factory() as s:
                seed(s)
                s.commit()
        engine.dispose()

        camp = create_campaign(
            name,
            db_path=str(db_path),
            registry=self.registry,
            **(create_kwargs or {}),
        )
        if archived:
            archive_campaign(camp.id, registry=self.registry)
        if overview:
            update_campaign_overview(camp.id, registry=self.registry, **overview)
        if last_tick:
            set_campaign_last_tick(camp.id, registry=self.registry, **last_tick)
        for ck3_id, role in tracked or ():
            add_tracked_character(camp.id, ck3_id, role=role, registry=self.registry)
        return camp

    def client(self, *, provider: NarrativeProvider | None = None) -> TestClient:
        """Build the app over the shared registry and enter a TestClient.

        The client is entered eagerly (runs the app lifespan) and tracked
        for teardown by the fixture.
        """
        app = create_app(registry_path=self.registry, narrative_provider=provider)
        client = TestClient(app)
        client.__enter__()
        self._clients.append(client)
        return client

    def make_campaign(
        self,
        name: str = "test-campaign",
        *,
        provider: NarrativeProvider | None = None,
        **campaign_kwargs: Any,
    ) -> TestClient:
        """Single-campaign one-liner: register one campaign, return a client.

        The 90% case — equivalent to :meth:`campaign` followed by
        :meth:`client`. Multi-campaign tests call :meth:`campaign` directly.
        """
        self.campaign(name, **campaign_kwargs)
        return self.client(provider=provider)

    def close(self) -> None:
        for client in reversed(self._clients):
            client.__exit__(None, None, None)
        self._clients.clear()
