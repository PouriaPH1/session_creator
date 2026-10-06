from core.device_profiler import DeviceProfileFactory


def test_deterministic_device_profile():
    profile1 = DeviceProfileFactory.get_deterministic_profile("+1234567890")
    profile2 = DeviceProfileFactory.get_deterministic_profile("+1234567890")
    assert profile1 == profile2
    assert profile1.device_model != ""
    assert profile1.system_version != ""
    assert profile1.app_version != ""


def test_profiles_diversity():
    profiles = [DeviceProfileFactory.get_deterministic_profile(f"+100000000{i}") for i in range(10)]
    unique_models = {p.device_model for p in profiles}
    assert len(unique_models) > 1
