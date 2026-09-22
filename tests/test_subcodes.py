from app import subcodes


def test_describe_falls_back_to_raw_codes():
    assert subcodes.describe("call_ended", "bot_kicked_from_call") == "Call ended: The bot was removed from the call by the host."
    assert subcodes.describe("in_call_recording") == "In the call and recording"
    assert subcodes.describe("brand_new_code", "brand_new_sub_code") == "brand_new_code: brand_new_sub_code"
