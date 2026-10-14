from src.util.fhir_bundle import get_bundle_next_link, get_bundle_resources


def test_bundle_helpers_tolerate_malformed_entries_and_filter_resources():
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            None,
            {"resource": "not-a-resource"},
            {"resource": {"resourceType": "Patient", "id": "patient-1"}},
            {"resource": {"resourceType": "Observation", "id": "observation-1"}},
        ],
        "link": [None, {"relation": "self", "url": "https://fhir.example/Patient"}, {"relation": "next", "url": "https://fhir.example/Patient?page=2"}],
    }

    assert get_bundle_resources(bundle) == [
        {"resourceType": "Patient", "id": "patient-1"},
        {"resourceType": "Observation", "id": "observation-1"},
    ]
    assert get_bundle_resources(bundle, "Patient") == [{"resourceType": "Patient", "id": "patient-1"}]
    assert get_bundle_next_link(bundle) == "https://fhir.example/Patient?page=2"
