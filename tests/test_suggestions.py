from product_pulse.analytics.service import closest


def test_closest_handles_typos_case_and_partial_names() -> None:
    names = [
        "$pageview",
        "signed_up",
        "project_created",
        "invited_teammate",
        "subscription_started",
    ]
    assert closest("pageview", names)[0] == "$pageview"
    assert closest("Signed_Up", names)[0] == "signed_up"
    assert "subscription_started" in closest("subscription", names)
    assert closest("zzz", names) == []
