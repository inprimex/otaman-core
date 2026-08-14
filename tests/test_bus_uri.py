"""Tests for otaman_core.bus.uri — the bus-uri-addressing capability.

Scenarios mirror the formal spec
(otaman-specs .../single-bus-per-program/specs/bus-uri-addressing/spec.md).
"""

from __future__ import annotations

import pytest

from otaman_core.bus.uri import (
    SCHEME,
    BusUri,
    BusUriError,
    canonicalize,
    parse,
    to_org,
)

LOCAL = {"local_org": "otaman-dev", "local_program": "otaman-dev"}


class TestAcceptedForms:
    def test_bare_name_is_local(self):
        # spec: "bare name is local"
        uri = parse("cli-agent", **LOCAL)
        assert uri == BusUri("otaman-dev", "otaman-dev", "cli-agent")
        assert str(uri) == "otaman://otaman-dev/otaman-dev/cli-agent"

    def test_shorthand_crosses_programs_same_org(self):
        # spec: "shorthand crosses programs"
        uri = parse("pm-agent@poc-openwerables", **LOCAL)
        assert uri == BusUri("otaman-dev", "poc-openwerables", "pm-agent")
        assert str(uri) == "otaman://otaman-dev/poc-openwerables/pm-agent"

    def test_full_uri_passthrough(self):
        raw = "otaman://greenbin/greenbin/backend-agent"
        uri = parse(raw, **LOCAL)
        assert uri == BusUri("greenbin", "greenbin", "backend-agent")
        # canonical round-trip is stable
        assert str(uri) == raw

    def test_shorthand_uses_local_org_not_target_program_as_org(self):
        # org always comes from local context for shorthand
        uri = parse("qa-agent@greenbin", local_org="acme", local_program="site")
        assert uri.org == "acme"
        assert uri.program == "greenbin"


class TestSpecialRecipients:
    def test_all_shorthand_is_program_scoped(self):
        # spec: "special recipients are program-scoped"
        uri = parse("all@greenbin", **LOCAL)
        assert uri == BusUri("otaman-dev", "greenbin", "all")

    def test_human_bare_is_local(self):
        uri = parse("human", **LOCAL)
        assert uri == BusUri("otaman-dev", "otaman-dev", "human")

    def test_human_full_uri(self):
        uri = parse("otaman://greenbin/greenbin/human", **LOCAL)
        assert uri.agent == "human"


class TestProjectionsAndPredicates:
    def test_to_org_projection(self):
        assert to_org("backend-agent@greenbin", **LOCAL) == "otaman-dev"
        assert to_org("otaman://contoso/site/ops", **LOCAL) == "contoso"

    def test_is_cross_org(self):
        assert parse("qa-agent@greenbin", **LOCAL).is_cross_org("otaman-dev") is False
        assert parse("otaman://contoso/site/ops-agent", **LOCAL).is_cross_org("otaman-dev") is True

    def test_is_cross_program(self):
        local = parse("cli-agent", **LOCAL)
        assert local.is_cross_program("otaman-dev", "otaman-dev") is False
        other = parse("pm-agent@poc-openwerables", **LOCAL)
        assert other.is_cross_program("otaman-dev", "otaman-dev") is True
        # same program, different org is still "cross program" for delivery
        assert (
            parse("otaman://acme/otaman-dev/ops-agent", **LOCAL).is_cross_program(
                "otaman-dev", "otaman-dev"
            )
            is True
        )

    def test_canonicalize_returns_string(self):
        assert canonicalize("cli-agent", **LOCAL) == "otaman://otaman-dev/otaman-dev/cli-agent"


class TestRoundTrip:
    @pytest.mark.parametrize(
        "raw",
        [
            "cli-agent",
            "pm-agent@poc-openwerables",
            "otaman://greenbin/greenbin/backend-agent",
            "all@greenbin",
        ],
    )
    def test_reparse_of_canonical_is_idempotent(self, raw):
        once = parse(raw, **LOCAL)
        twice = parse(str(once), **LOCAL)
        assert once == twice
        assert str(once) == str(twice)


class TestErrors:
    @pytest.mark.parametrize("raw", ["", "   ", "\n"])
    def test_empty_input_rejected(self, raw):
        with pytest.raises(BusUriError, match="empty"):
            parse(raw, **LOCAL)

    def test_wrong_scheme_treated_as_name_then_rejected(self):
        # "http://x/y/z" has no @ and is not otaman://, so it's a bare name
        # that fails segment validation (slashes are invalid).
        with pytest.raises(BusUriError, match="invalid agent"):
            parse("http://x/y/z", **LOCAL)

    @pytest.mark.parametrize(
        "raw",
        [
            "otaman://org/program",  # too few segments
            "otaman://org/program/agent/extra",  # too many
            "otaman://org//agent",  # empty middle
        ],
    )
    def test_malformed_full_uri_rejected(self, raw):
        with pytest.raises(BusUriError):
            parse(raw, **LOCAL)

    def test_multiple_at_signs_rejected(self):
        with pytest.raises(BusUriError, match="shorthand"):
            parse("a@b@c", **LOCAL)

    @pytest.mark.parametrize(
        "raw",
        [
            "Bad-Caps@program",  # uppercase agent
            "agent@Bad-Caps",  # uppercase program
            "1agent@program",  # leading digit
            "a b@program",  # space
            "agent@",  # empty program
            "@program",  # empty agent
        ],
    )
    def test_invalid_segments_rejected(self, raw):
        with pytest.raises(BusUriError):
            parse(raw, **LOCAL)

    def test_invalid_local_context_rejected(self):
        # local context lands in the result, so it is validated too
        with pytest.raises(BusUriError, match="org"):
            parse("cli-agent", local_org="Bad_Org", local_program="otaman-dev")

    def test_non_string_rejected(self):
        with pytest.raises(BusUriError):
            parse(None, **LOCAL)  # type: ignore[arg-type]


class TestScheme:
    def test_scheme_constant(self):
        assert SCHEME == "otaman://"
