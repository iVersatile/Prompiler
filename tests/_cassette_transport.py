"""Bridge a CassettePlayer to an httpx.MockTransport.

The handler pops one interaction per request in order. Method and URL are
asserted to match the recording so silent drift between adapter code and
cassette fixture surfaces loudly. Response headers/body come straight from
the cassette; request body is not re-verified (kept loose so cassettes
stay readable when adapters add non-load-bearing fields).
"""

from __future__ import annotations

import httpx

from _cassette import CassettePlayer


def make_cassette_transport(player: CassettePlayer) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        interaction = player.next()
        assert request.method == interaction.method, (
            f"cassette method mismatch: request={request.method!r} recorded={interaction.method!r}"
        )
        assert str(request.url) == interaction.url, (
            f"cassette url mismatch: request={str(request.url)!r} recorded={interaction.url!r}"
        )
        return httpx.Response(
            status_code=interaction.status,
            headers=interaction.response_headers,
            content=interaction.response_body,
        )

    return httpx.MockTransport(handler)
