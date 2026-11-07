from ingest_blueprint.scale import px_per_m_from_door_widths


def test_door_chord_of_30px_is_one_meter_reference():
    # 30 px opening assumed to be 0.9 m.
    assert round(px_per_m_from_door_widths([30.0]), 3) == 33.333
