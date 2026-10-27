from core.device_profiler import DeviceProfileFactory


def test_deterministic_device_profile():
    profile1 = DeviceProfileFactory.get_deterministic_profile("+1234567890")
    profile2 = DeviceProfileFactory.get_deterministic_profile("+1234567890")
    profile_no_plus = DeviceProfileFactory.get_deterministic_profile("1234567890")
    profile_with_ext = DeviceProfileFactory.get_deterministic_profile("+1234567890.session")
    profile_with_path = DeviceProfileFactory.get_deterministic_profile("storage/source_sessions/+1234567890.session")

    assert profile1 == profile2
    assert profile1 == profile_no_plus
    assert profile1 == profile_with_ext
    assert profile1 == profile_with_path
    assert profile1.device_model != ""
    assert profile1.system_version != ""
    assert profile1.app_version != ""


def test_profiles_diversity():
    profiles = [DeviceProfileFactory.get_deterministic_profile(f"+100000000{i}") for i in range(10)]
    unique_models = {p.device_model for p in profiles}
    assert len(unique_models) > 1
