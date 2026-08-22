"""Offline helpers for ylx.imu-physical-acceptance.v1 evidence.

The functions in this module intentionally operate only on caller-supplied JSON
objects. They never open devices, start captures, mutate network state, or infer
physical PASS from synthetic fixtures.
"""

from __future__ import annotations

import math
import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


PASS_RESULT = "PASS"
PHYSICAL_VERIFIED = "VERIFIED"
CAP_PHY_03 = "CAP-PHY-03"
CAP_PHY_04 = "CAP-PHY-04"
STABILITY_01 = "STABILITY-01"
REQUIRED_VERDICTS = (CAP_PHY_03, CAP_PHY_04, STABILITY_01)
PHYSICAL_ORACLE_SOURCES = {
    "physical_oracle",
    "six_face_physical_oracle",
    "known_rate_physical_oracle",
    "tick_rollover_capture",
    "external_event_physical_oracle",
}
ZERO_UUID = "00000000-0000-0000-0000-000000000000"
REQUIRED_PACKET_LAYOUT = [
    (0, 3, False, "big", "packet", "timestamp24"),
    (3, 2, True, "big", "sample0", "accel_x"),
    (5, 2, True, "big", "sample0", "accel_y"),
    (7, 2, True, "big", "sample0", "accel_z"),
    (9, 2, True, "big", "sample0", "gyro_x"),
    (11, 2, True, "big", "sample0", "gyro_y"),
    (13, 2, True, "big", "sample0", "gyro_z"),
    (15, 2, True, "big", "sample1", "accel_x"),
    (17, 2, True, "big", "sample1", "accel_y"),
    (19, 2, True, "big", "sample1", "accel_z"),
    (21, 2, True, "big", "sample1", "gyro_x"),
    (23, 2, True, "big", "sample1", "gyro_y"),
    (25, 2, True, "big", "sample1", "gyro_z"),
]
SIX_FACE_SET = {"+X", "-X", "+Y", "-Y", "+Z", "-Z"}
KNOWN_RATE_SET = {
    (axis, direction)
    for axis in ("x", "y", "z")
    for direction in ("positive", "negative")
}
YLX_USB_VID_PID = "1bcf:0b15"
YLX_UVC_PROFILES = {
    ("0100", "11111111-2222-3333-8444-555555555555", 3, 1, 27),
}
SIX_FACE_EXPECTED_AXES = {
    "+X": ("x", "positive"),
    "-X": ("x", "negative"),
    "+Y": ("y", "positive"),
    "-Y": ("y", "negative"),
    "+Z": ("z", "positive"),
    "-Z": ("z", "negative"),
}
SAFE_RELATIVE_POSIX_LOCATOR_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/-"
)
SAFE_VENDOR_URI_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~/-"
)


class ImuPhysicalAcceptanceError(ValueError):
    """Raised when an evidence document attempts an unsafe physical verdict."""


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON value {value!r}")


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        _reject_nonfinite_json(value)
    return parsed


def _reject_json_surrogates(candidate: Any) -> None:
    if isinstance(candidate, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in candidate):
            raise ValueError("isolated Unicode surrogate")
    elif isinstance(candidate, list):
        for item in candidate:
            _reject_json_surrogates(item)
    elif isinstance(candidate, dict):
        for key, item in candidate.items():
            _reject_json_surrogates(key)
            _reject_json_surrogates(item)


def load_strict_json_bytes(raw: bytes, *, location: str = "JSON input") -> Any:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ImuPhysicalAcceptanceError(f"{location}: invalid UTF-8: {error}") from error
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=_reject_nonfinite_json,
            parse_float=_parse_finite_json_float,
        )
        _reject_json_surrogates(value)
    except (json.JSONDecodeError, ValueError) as error:
        raise ImuPhysicalAcceptanceError(f"{location}: invalid strict JSON: {error}") from error
    return value


def load_strict_json_file(path: Path) -> Any:
    return load_strict_json_bytes(path.read_bytes(), location=str(path))


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sequence(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _percentile(values: list[float], percentile: float) -> float | None:
    finite = sorted(value for value in values if math.isfinite(value))
    if not finite:
        return None
    rank = math.ceil((percentile / 100.0) * len(finite))
    index = min(max(rank - 1, 0), len(finite) - 1)
    return finite[index]


def _median(values: list[float]) -> float | None:
    finite = sorted(value for value in values if math.isfinite(value))
    if not finite:
        return None
    middle = len(finite) // 2
    if len(finite) % 2:
        return finite[middle]
    return (finite[middle - 1] + finite[middle]) / 2.0


def _verdict_result(evidence: dict[str, Any], acceptance_id: str) -> str | None:
    return _mapping(_mapping(evidence.get("verdicts")).get(acceptance_id)).get("result")


def _pass_verdicts(evidence: dict[str, Any]) -> list[str]:
    return [
        acceptance_id
        for acceptance_id in REQUIRED_VERDICTS
        if _verdict_result(evidence, acceptance_id) == PASS_RESULT
    ]


def _is_verified(section: Any) -> bool:
    return _mapping(section).get("status") == PHYSICAL_VERIFIED


def _source(section: Any) -> str | None:
    source = _mapping(section).get("source")
    return source if isinstance(source, str) else None


def _has_primary_vendor_doc(evidence: dict[str, Any]) -> bool:
    for doc in _sequence(evidence.get("vendor_docs")):
        doc = _mapping(doc)
        if doc.get("kind") == "vendor_primary" and doc.get("status") == "PRESENT":
            return True
    return False


def _traceable_primary_vendor_doc_refs(
    evidence: dict[str, Any],
    required_proves: set[str],
) -> set[str]:
    refs: set[str] = set()
    for doc in _sequence(evidence.get("vendor_docs")):
        doc = _mapping(doc)
        if doc.get("kind") != "vendor_primary" or doc.get("status") != "PRESENT":
            continue
        has_hash = _valid_sha256(doc.get("sha256"))
        has_version = isinstance(doc.get("version"), str) and bool(doc["version"])
        proves = {item for item in _sequence(doc.get("proves")) if isinstance(item, str)}
        if not (has_hash and has_version and required_proves <= proves):
            continue
        uri = doc.get("uri")
        if _is_allowed_vendor_uri(uri):
            refs.add(uri)
        local_ref = doc.get("local_ref")
        if _is_safe_relative_posix_locator(local_ref):
            refs.add(local_ref)
    return refs


def _has_traceable_primary_vendor_doc(evidence: dict[str, Any], required_proves: set[str]) -> bool:
    return bool(_traceable_primary_vendor_doc_refs(evidence, required_proves))


def _has_physical_oracle_source(evidence: dict[str, Any], section_names: list[str]) -> bool:
    for name in section_names:
        if _source(evidence.get(name)) in PHYSICAL_ORACLE_SOURCES:
            return True
    return False


def _issue_154_only(source_issues: list[Any]) -> bool:
    issue_set = {item for item in source_issues if isinstance(item, str)}
    return bool(issue_set) and issue_set <= {"pi-dev#154", "#154"}


def _sync_event_stats(sync: dict[str, Any]) -> dict[str, Any]:
    offsets: list[float] = []
    absolute_offsets: list[float] = []
    fit_residuals: list[float] = []
    read_uncertainties: list[float] = []
    frame_intervals: list[float] = []
    event_ids: list[str] = []
    video_times: list[float] = []
    imu_times: list[float] = []
    for event in _sequence(sync.get("events")):
        event = _mapping(event)
        event_id = event.get("event_id")
        if isinstance(event_id, str):
            event_ids.append(event_id)
        offset = _finite_number(event.get("offset_s"))
        if offset is None:
            video_time = _finite_number(event.get("video_time_s"))
            imu_time = _finite_number(event.get("imu_time_s"))
            if video_time is not None and imu_time is not None:
                offset = imu_time - video_time
        video_time = _finite_number(event.get("video_time_s"))
        if video_time is not None:
            video_times.append(video_time)
        imu_time = _finite_number(event.get("imu_time_s"))
        if imu_time is not None:
            imu_times.append(imu_time)
        if offset is not None:
            offsets.append(offset)
            absolute_offsets.append(abs(offset))
        residual = _finite_number(event.get("fit_residual_s"))
        if residual is not None:
            fit_residuals.append(abs(residual))
        uncertainty = _finite_number(event.get("read_uncertainty_s"))
        if uncertainty is not None:
            read_uncertainties.append(abs(uncertainty))
        frame_interval = _finite_number(event.get("frame_interval_s"))
        if frame_interval is not None:
            frame_intervals.append(frame_interval)
    return {
        "event_count": len(_sequence(sync.get("events"))),
        "distinct_event_id_count": len(set(event_ids)),
        "event_ids_distinct": len(event_ids) == len(_sequence(sync.get("events"))) == len(set(event_ids)),
        "video_times_strictly_increasing": all(
            b > a for a, b in zip(video_times, video_times[1:], strict=False)
        )
        and len(video_times) == len(_sequence(sync.get("events"))),
        "imu_times_strictly_increasing": all(
            b > a for a, b in zip(imu_times, imu_times[1:], strict=False)
        )
        and len(imu_times) == len(_sequence(sync.get("events"))),
        "frame_intervals_all_positive": len(frame_intervals) == len(_sequence(sync.get("events")))
        and all(value > 0 for value in frame_intervals),
        "offset_median_s": _median(offsets),
        "offset_abs_median_s": _median(absolute_offsets),
        "offset_abs_p95_s": _percentile(absolute_offsets, 95),
        "fit_residual_p95_s": _percentile(fit_residuals, 95),
        "read_uncertainty_p95_s": _percentile(read_uncertainties, 95),
        "frame_interval_median_s": _median(frame_intervals),
        "frame_interval_min_s": min(frame_intervals) if frame_intervals else None,
        "frame_interval_max_s": max(frame_intervals) if frame_intervals else None,
    }


def _linear_fit_host_time(samples: list[tuple[float, float]]) -> dict[str, Any]:
    if len(samples) < 2:
        return {
            "estimated_tick_hz": None,
            "fit_residual_p50_s": None,
            "fit_residual_p95_s": None,
            "fit_residual_max_s": None,
        }
    ticks = [sample[0] for sample in samples]
    times = [sample[1] for sample in samples]
    mean_tick = sum(ticks) / len(ticks)
    mean_time = sum(times) / len(times)
    variance = sum((tick - mean_tick) ** 2 for tick in ticks)
    if variance <= 0:
        return {
            "estimated_tick_hz": None,
            "fit_residual_p50_s": None,
            "fit_residual_p95_s": None,
            "fit_residual_max_s": None,
        }
    slope = sum(
        (tick - mean_tick) * (time - mean_time)
        for tick, time in zip(ticks, times, strict=True)
    ) / variance
    intercept = mean_time - slope * mean_tick
    residuals = [abs(time - (slope * tick + intercept)) for tick, time in samples]
    tick_hz = (1.0 / slope) if slope > 0 else None
    return {
        "estimated_tick_hz": tick_hz,
        "fit_residual_p50_s": _percentile(residuals, 50),
        "fit_residual_p95_s": _percentile(residuals, 95),
        "fit_residual_max_s": max(residuals) if residuals else None,
    }


def _tick_rollover_stats(tick_rollover: dict[str, Any]) -> dict[str, Any]:
    modulo = 1 << 24
    half_modulo = modulo // 2
    rollovers = 0
    regressions = 0
    duplicates = 0
    unwrapped: list[int] = []
    host_fit_samples: list[tuple[float, float]] = []
    read_durations: list[float] = []
    previous_raw: int | None = None
    previous_unwrapped: int | None = None

    for sample in _sequence(tick_rollover.get("raw_samples")):
        sample = _mapping(sample)
        raw_tick = sample.get("raw_tick")
        if not isinstance(raw_tick, int) or isinstance(raw_tick, bool):
            continue
        if raw_tick < 0 or raw_tick >= modulo:
            continue
        if previous_raw is not None and raw_tick < previous_raw:
            if previous_raw - raw_tick > half_modulo:
                rollovers += 1
            else:
                regressions += 1
        unwrapped_tick = raw_tick + rollovers * modulo
        if previous_unwrapped is not None:
            if unwrapped_tick == previous_unwrapped:
                duplicates += 1
            elif unwrapped_tick < previous_unwrapped:
                regressions += 1
        unwrapped.append(unwrapped_tick)
        host_time = _finite_number(sample.get("host_time_s"))
        if host_time is not None:
            host_fit_samples.append((float(unwrapped_tick), host_time))
        read_duration = _finite_number(sample.get("read_duration_s"))
        if read_duration is not None:
            read_durations.append(abs(read_duration))
        previous_raw = raw_tick
        previous_unwrapped = unwrapped_tick

    fit = _linear_fit_host_time(host_fit_samples)
    strictly_increasing = all(b > a for a, b in zip(unwrapped, unwrapped[1:], strict=False))
    return {
        "sample_count": len(unwrapped),
        "rollover_count": rollovers,
        "duplicate_count": duplicates,
        "regression_count": regressions,
        "strictly_increasing": strictly_increasing,
        "read_duration_p95_s": _percentile(read_durations, 95),
        **fit,
    }


def _close_enough(actual: Any, expected: Any, *, absolute: float = 1e-9) -> bool:
    actual_number = _finite_number(actual)
    expected_number = _finite_number(expected)
    if actual_number is None or expected_number is None:
        return actual is None and expected is None
    return abs(actual_number - expected_number) <= absolute


def _collect_summary_mismatch_errors(
    evidence: dict[str, Any],
    computed: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    sync = _mapping(evidence.get("video_imu_sync_oracle"))
    sync_stats = _mapping(computed.get("video_imu_sync_oracle"))
    sync_pairs = {
        "offset_p95_s": "offset_abs_p95_s",
        "fit_residual_p95_s": "fit_residual_p95_s",
        "read_uncertainty_p95_s": "read_uncertainty_p95_s",
    }
    for reported, computed_key in sync_pairs.items():
        if reported in sync and not _close_enough(sync.get(reported), sync_stats.get(computed_key), absolute=1e-6):
            errors.append(
                f"self-reported video_imu_sync_oracle.{reported} differs from computed {computed_key}"
            )

    tick = _mapping(evidence.get("tick_rollover"))
    tick_stats = _mapping(computed.get("tick_rollover"))
    tick_pairs = {
        "rollover_count": "rollover_count",
        "duplicate_count": "duplicate_count",
        "regression_count": "regression_count",
        "fit_residual_p95_s": "fit_residual_p95_s",
        "read_duration_p95_s": "read_duration_p95_s",
    }
    for reported, computed_key in tick_pairs.items():
        if reported in tick and not _close_enough(tick.get(reported), tick_stats.get(computed_key), absolute=1e-6):
            errors.append(
                f"self-reported tick_rollover.{reported} differs from computed {computed_key}"
            )
    tick_clock = _mapping(evidence.get("tick_clock"))
    if "device_tick_hz" in tick_clock and tick_clock.get("device_tick_hz") is not None:
        if not _close_enough(
            tick_clock.get("device_tick_hz"),
            tick_stats.get("estimated_tick_hz"),
            absolute=0.5,
        ):
            errors.append("self-reported tick_clock.device_tick_hz differs from computed estimated_tick_hz")
    return errors


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _valid_commit(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(
        character in "0123456789abcdef" for character in value
    )


def _real_identity_errors(evidence: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    device = _mapping(evidence.get("device_identity"))
    build = _mapping(evidence.get("build_identity"))
    session = _mapping(evidence.get("session_identity"))
    uvc = _mapping(device.get("uvc_extension"))
    raw_artifacts = _sequence(evidence.get("raw_artifacts"))
    if not (
        isinstance(device.get("video_node"), str)
        and device["video_node"].startswith("/dev/video")
        and isinstance(device.get("bcd_device"), str)
        and isinstance(uvc.get("guid"), str)
        and uvc.get("guid") != ZERO_UUID
        and isinstance(uvc.get("unit"), int)
        and uvc.get("unit") > 0
        and uvc.get("selector") == 1
        and uvc.get("get_len_bytes") == 27
    ):
        errors.append("real device/build/session identity requires known video node and UVC identity")
    if not (
        _valid_commit(build.get("pi_dev_commit"))
        and _valid_commit(build.get("rp_ylx_commit"))
        and isinstance(build.get("wheel"), str)
        and build["wheel"].lower() != "unknown"
        and isinstance(build.get("kernel"), str)
        and build["kernel"].lower() != "unknown"
    ):
        errors.append("real device/build/session identity requires 40hex commits and non-unknown package/kernel")
    if not (
        isinstance(session.get("session_id"), str)
        and isinstance(session.get("capture_started_at"), str)
        and isinstance(session.get("capture_ended_at"), str)
        and _valid_sha256(session.get("source_manifest_sha256"))
    ):
        errors.append("real device/build/session identity requires session id, timestamps, and manifest sha")
    if not raw_artifacts:
        errors.append("real device/build/session identity requires nonempty raw_artifacts")
    for artifact in raw_artifacts:
        artifact = _mapping(artifact)
        if not (
            isinstance(artifact.get("path"), str)
            and _valid_sha256(artifact.get("sha256"))
            and isinstance(artifact.get("bytes"), int)
            and artifact["bytes"] > 0
        ):
            errors.append("real device/build/session identity requires raw_artifacts with path, sha256, and positive bytes")
            break
    return errors


def _hardware_profile_errors(evidence: dict[str, Any]) -> list[str]:
    device = _mapping(evidence.get("device_identity"))
    uvc = _mapping(device.get("uvc_extension"))
    observed_profile = (
        device.get("bcd_device"),
        uvc.get("guid"),
        uvc.get("unit"),
        uvc.get("selector"),
        uvc.get("get_len_bytes"),
    )
    if device.get("usb_vid_pid") != YLX_USB_VID_PID or observed_profile not in YLX_UVC_PROFILES:
        return [
            "PASS requires exact YLX hardware profile usb_vid_pid=1bcf:0b15 with matching bcd/UVC identity"
        ]
    return []


def _packet_layout_errors(evidence: dict[str, Any]) -> list[str]:
    fields = _sequence(_mapping(evidence.get("packet_facts")).get("fields"))
    observed = []
    for field in fields:
        field = _mapping(field)
        observed.append(
            (
                field.get("offset"),
                field.get("width_bytes"),
                field.get("signed"),
                field.get("endian"),
                field.get("group"),
                field.get("semantic"),
            )
        )
    if observed != REQUIRED_PACKET_LAYOUT:
        return ["packet_facts.fields must match the closed 27-byte offset/width/signed/endian/group/semantic layout"]
    return []


def _finite_positive_fields(section: dict[str, Any], fields: list[str]) -> list[str]:
    missing = []
    for field in fields:
        value = _finite_number(section.get(field))
        if value is None or value <= 0:
            missing.append(field)
    return missing


def _six_face_errors(evidence: dict[str, Any]) -> list[str]:
    six_face = _mapping(evidence.get("six_face"))
    faces = [_mapping(face) for face in _sequence(six_face.get("faces"))]
    errors: list[str] = []
    face_names = [face.get("face") for face in faces]
    if len(faces) != 6 or set(face_names) != SIX_FACE_SET or len(set(face_names)) != 6:
        errors.append("six_face must contain exactly +X,-X,+Y,-Y,+Z,-Z once")
    for face in faces:
        face_name = face.get("face")
        expected_axis, expected_sign = SIX_FACE_EXPECTED_AXES.get(face_name, (None, None))
        if (
            face.get("dominant_axis") != expected_axis
            or face.get("dominant_axis_sign") != expected_sign
        ):
            errors.append("six_face signed dominant axis must match each named face")
            break
        coverage = _finite_number(face.get("axis_coverage"))
        if coverage is None or coverage < 0.55:
            errors.append("six_face axis coverage must be >=0.55 for every face")
            break
        if not isinstance(face.get("sample_count"), int) or face["sample_count"] < 100:
            errors.append("six_face sample_count must be >=100 for every face")
            break
        residual = _finite_number(face.get("gravity_norm_residual_p95_m_s2"))
        if residual is None or residual > 0.15:
            errors.append("six_face gravity residual p95 must be <=0.15m/s2 for every face")
            break
        leakage = _finite_number(face.get("leakage_ratio"))
        if leakage is None or leakage > 0.10:
            errors.append("six_face leakage must be <=10% for every face")
            break
    summary_leakage = _finite_number(six_face.get("cross_axis_leakage_max"))
    if summary_leakage is None or summary_leakage > 0.10:
        errors.append("six_face cross_axis_leakage_max must be <=10%")
    drift = _finite_number(six_face.get("scale_drift_max"))
    if drift is None or drift > 0.03:
        errors.append("six_face scale_drift_max must be <=3%")
    rank = six_face.get("fit_rank")
    if not isinstance(rank, int) or isinstance(rank, bool) or rank != 3:
        errors.append("six_face fit rank must be 3")
    condition = _finite_number(six_face.get("condition_number"))
    if condition is None or condition > 1.25:
        errors.append("six_face fit condition_number must be <=1.25")
    return errors


def _known_rate_errors(evidence: dict[str, Any]) -> list[str]:
    known_rate = _mapping(evidence.get("known_rate"))
    trials = [_mapping(trial) for trial in _sequence(known_rate.get("trials"))]
    errors: list[str] = []
    coverage = {(trial.get("axis"), trial.get("direction")) for trial in trials}
    exception = _mapping(known_rate.get("coverage_exception"))
    exception_ok = (
        exception.get("status") == "JUSTIFIED"
        and isinstance(exception.get("reason"), str)
        and bool(exception["reason"])
        and bool(_sequence(exception.get("evidence_refs")))
    )
    if coverage != KNOWN_RATE_SET and not exception_ok:
        errors.append("known_rate must cover x/y/z positive and negative or carry a strict auditable exception")
    for trial in trials:
        if not isinstance(trial.get("inliers"), int) or trial["inliers"] < 80:
            errors.append("known_rate inliers must be >=80 for every trial")
            break
        scale_error = _finite_number(trial.get("scale_error"))
        if scale_error is None or scale_error > 0.05:
            errors.append("known_rate scale_error must be <=5% for every trial")
            break
        leakage = _finite_number(trial.get("cross_axis_leakage"))
        if leakage is None or leakage > 0.10:
            errors.append("known_rate cross_axis_leakage must be <=10% for every trial")
            break
    scale_error_max = _finite_number(known_rate.get("scale_error_max"))
    if scale_error_max is None or scale_error_max > 0.05:
        errors.append("known_rate scale_error_max must be <=5%")
    leakage_max = _finite_number(known_rate.get("cross_axis_leakage_max"))
    if leakage_max is None or leakage_max > 0.10:
        errors.append("known_rate cross_axis_leakage_max must be <=10%")
    condition = _finite_number(known_rate.get("condition_number"))
    if condition is None or condition > 1.25:
        errors.append("known_rate condition_number must be <=1.25")
    return errors


def _known_ref_sets(evidence: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    artifact_refs = {
        artifact.get("path")
        for artifact in (_mapping(item) for item in _sequence(evidence.get("raw_artifacts")))
        if isinstance(artifact.get("path"), str)
    }
    vendor_refs = set()
    for doc in (_mapping(item) for item in _sequence(evidence.get("vendor_docs"))):
        for field in ("uri", "local_ref"):
            value = doc.get(field)
            if isinstance(value, str) and value:
                vendor_refs.add(value)
    return artifact_refs, vendor_refs, artifact_refs | vendor_refs


def _is_safe_relative_posix_locator(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if not value.isascii():
        return False
    if value.startswith("/") or "\\" in value or "?" in value or "#" in value:
        return False
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        return False
    if not all(character in SAFE_RELATIVE_POSIX_LOCATOR_CHARS for character in value):
        return False
    parts = value.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _is_allowed_vendor_uri(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if not value.isascii():
        return False
    if not (value.startswith("vendor://") or value.startswith("https://")):
        return False
    if any(character in value for character in ("\\", "?", "#", "%", ";", "@")):
        return False
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        return False
    parts = urlsplit(value)
    if parts.scheme not in {"vendor", "https"}:
        return False
    if parts.scheme != parts.scheme.lower():
        return False
    if parts.query or parts.fragment or parts.username or parts.password:
        return False
    if not parts.netloc:
        return False
    uri_body = parts.netloc + parts.path
    if not all(character in SAFE_VENDOR_URI_CHARS for character in uri_body):
        return False
    if parts.netloc in {"", ".", ".."}:
        return False
    path_segments = parts.path.split("/")[1:] if parts.path else []
    if any(segment in {"", ".", ".."} for segment in path_segments):
        return False
    return True


def _locator_safety_errors(evidence: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for artifact in (_mapping(item) for item in _sequence(evidence.get("raw_artifacts"))):
        if not _is_safe_relative_posix_locator(artifact.get("path")):
            errors.append("raw_artifact.path must be a safe relative POSIX locator")
    for doc in (_mapping(item) for item in _sequence(evidence.get("vendor_docs"))):
        uri = doc.get("uri")
        if uri is not None and not _is_allowed_vendor_uri(uri):
            errors.append(
                "vendor URI must use lowercase vendor/https with ASCII path segments and no userinfo/query/fragment/percent/semicolon/dot segments"
            )
        local_ref = doc.get("local_ref")
        if local_ref is not None and not _is_safe_relative_posix_locator(local_ref):
            errors.append("vendor local_ref must be a safe relative POSIX locator")
    return errors


def _artifact_index_errors(evidence: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    artifacts_by_path: dict[str, tuple[Any, Any, Any]] = {}
    for artifact in (_mapping(item) for item in _sequence(evidence.get("raw_artifacts"))):
        path = artifact.get("path")
        if not isinstance(path, str):
            continue
        identity = (artifact.get("kind"), artifact.get("sha256"), artifact.get("bytes"))
        if path in artifacts_by_path:
            errors.append(f"duplicate raw_artifacts.path {path}")
            if artifacts_by_path[path] != identity:
                errors.append(f"conflicting artifact identity for raw_artifacts.path {path}")
        else:
            artifacts_by_path[path] = identity

    vendor_locators: dict[str, tuple[Any, Any, Any]] = {}
    for doc in (_mapping(item) for item in _sequence(evidence.get("vendor_docs"))):
        identity = (doc.get("kind"), doc.get("sha256"), doc.get("version"))
        for field in ("uri", "local_ref"):
            locator = doc.get(field)
            if not isinstance(locator, str) or not locator:
                continue
            if locator in vendor_locators:
                errors.append(f"duplicate vendor locator {locator}")
                if vendor_locators[locator] != identity:
                    errors.append(f"conflicting vendor locator identity for {locator}")
            else:
                vendor_locators[locator] = identity
    return errors


def _artifact_index(evidence: dict[str, Any]) -> dict[str, str]:
    index: dict[str, str] = {}
    for artifact in (_mapping(item) for item in _sequence(evidence.get("raw_artifacts"))):
        path = artifact.get("path")
        kind = artifact.get("kind")
        if isinstance(path, str) and isinstance(kind, str):
            index[path] = kind
    return index


def _section_ref_kinds(evidence: dict[str, Any], section_name: str) -> list[str]:
    index = _artifact_index(evidence)
    section = _mapping(evidence.get(section_name))
    return [
        index[ref]
        for ref in _sequence(section.get("evidence_refs"))
        if isinstance(ref, str) and ref in index
    ]


def _has_kind(kinds: list[str], required: str) -> bool:
    return required in kinds


def _has_any_kind(kinds: list[str], required: set[str]) -> bool:
    return any(kind in required for kind in kinds)


def _operator_log_only(kinds: list[str]) -> bool:
    return bool(kinds) and set(kinds) <= {"operator_log"}


def _cap_phy_04_typed_artifact_errors(evidence: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for section_name in ("accelerometer_range", "gyroscope_range"):
        if _source(evidence.get(section_name)) != "physical_oracle":
            continue
        kinds = _section_ref_kinds(evidence, section_name)
        if not _has_kind(kinds, "raw_capture") or not _has_kind(kinds, "analysis_report"):
            errors.append(
                f"CAP-PHY-04 {section_name} physical oracle requires section-specific raw_capture and analysis_report artifacts"
            )
        if _operator_log_only(kinds):
            errors.append("operator_log cannot satisfy CAP-PHY-04 physical oracle evidence")

    cap4_sections = [
        ("six_face", "six_face_physical_oracle"),
        ("known_rate", "known_rate_physical_oracle"),
    ]
    for section_name, required_source in cap4_sections:
        if _source(evidence.get(section_name)) != required_source:
            continue
        kinds = _section_ref_kinds(evidence, section_name)
        if (
            not _has_kind(kinds, "raw_capture")
            or not _has_any_kind(kinds, {"photo", "video"})
            or not _has_kind(kinds, "analysis_report")
        ):
            errors.append(
                f"CAP-PHY-04 {section_name} physical oracle requires section-specific raw_capture plus photo/video/analysis artifacts"
            )
        if _operator_log_only(kinds):
            errors.append("operator_log cannot satisfy CAP-PHY-04 physical oracle evidence")
    return errors


def _cap_phy_03_typed_artifact_errors(evidence: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for section_name in ("tick_clock", "sample_phase", "tick_rollover"):
        kinds = _section_ref_kinds(evidence, section_name)
        if not _has_kind(kinds, "raw_capture"):
            errors.append(f"CAP-PHY-03 {section_name} tick evidence requires raw_capture")
        if _operator_log_only(kinds):
            errors.append("operator_log cannot satisfy CAP-PHY-03 tick evidence")

    sync_kinds = _section_ref_kinds(evidence, "video_imu_sync_oracle")
    if (
        not _has_kind(sync_kinds, "raw_capture")
        or not _has_any_kind(sync_kinds, {"photo", "video"})
        or not _has_kind(sync_kinds, "analysis_report")
    ):
        errors.append(
            "CAP-PHY-03 external sync oracle requires video/photo/analysis artifacts plus raw_capture"
        )
    if _operator_log_only(sync_kinds):
        errors.append("operator_log cannot satisfy CAP-PHY-03 external sync evidence")
    return errors


def _pass_evidence_ref_errors(evidence: dict[str, Any], pass_verdicts: list[str]) -> list[str]:
    errors: list[str] = []
    _, _, known_refs = _known_ref_sets(evidence)
    for acceptance_id in pass_verdicts:
        verdict = _mapping(_mapping(evidence.get("verdicts")).get(acceptance_id))
        evidence_refs = [
            ref for ref in _sequence(verdict.get("evidence_refs")) if isinstance(ref, str) and ref
        ]
        if not evidence_refs:
            errors.append(f"{acceptance_id} PASS requires nonempty evidence_refs")
        elif known_refs and any(ref not in known_refs for ref in evidence_refs):
            errors.append(f"{acceptance_id} PASS evidence_refs must bind to vendor_docs or raw_artifacts")
    return errors


def _nested_evidence_ref_errors(evidence: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    _, _, known_refs = _known_ref_sets(evidence)

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                child_path = f"{path}.{key}" if path else key
                if key == "evidence_refs" and not path.startswith("verdicts"):
                    refs = [ref for ref in _sequence(item) if isinstance(ref, str) and ref]
                    if not refs or any(ref not in known_refs for ref in refs):
                        errors.append(f"nested evidence_refs at {child_path} must bind to vendor_docs or raw_artifacts")
                else:
                    walk(item, child_path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(evidence, "")
    return errors


def _range_authority_errors(evidence: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    artifact_refs, vendor_refs, _ = _known_ref_sets(evidence)
    section_specs = [
        ("accelerometer_range", "full_scale_g"),
        ("gyroscope_range", "full_scale_dps"),
    ]
    for section_name, full_scale_field in section_specs:
        section = _mapping(evidence.get(section_name))
        source = _source(section)
        refs = {ref for ref in _sequence(section.get("evidence_refs")) if isinstance(ref, str)}
        if source == "operator_record":
            errors.append(f"CAP-PHY-04 PASS rejects operator_record for {section_name}.source")
            continue
        if source not in {"vendor_primary", "physical_oracle"}:
            errors.append(f"CAP-PHY-04 PASS requires local source authority for {section_name}")
            continue
        if source == "vendor_primary":
            proving_refs = _traceable_primary_vendor_doc_refs(evidence, {section_name})
            if not refs or not refs <= vendor_refs:
                errors.append(f"CAP-PHY-04 PASS requires {section_name}.evidence_refs to bind vendor primary evidence")
            if not refs & proving_refs:
                errors.append(f"CAP-PHY-04 PASS requires traceable vendor primary proving {section_name}")
        if source == "physical_oracle":
            if not refs or not refs <= artifact_refs:
                errors.append(f"CAP-PHY-04 PASS requires {section_name}.evidence_refs to bind raw physical artifacts")
            if section.get("full_scale_source") != "physical_oracle" and not _has_traceable_primary_vendor_doc(
                evidence,
                {section_name},
            ):
                errors.append(f"CAP-PHY-04 PASS requires vendor primary for {section_name} full-scale when physical oracle does not prove {full_scale_field}")
    return errors


def _axis_mapping_authority_errors(evidence: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    artifact_refs, vendor_refs, _ = _known_ref_sets(evidence)
    axis_mapping = _mapping(evidence.get("axis_mapping"))
    source = _source(axis_mapping)
    refs = {ref for ref in _sequence(axis_mapping.get("evidence_refs")) if isinstance(ref, str)}
    if source in {"none", "operator_record", "offline_analyzer", "synthetic_fixture", "test_fixture"}:
        errors.append(
            f"CAP-PHY-04 PASS rejects axis_mapping.source={source}; VERIFIED axis_mapping requires vendor_primary or physical_oracle authority"
        )
        return errors
    if source == "vendor_primary":
        proving_refs = _traceable_primary_vendor_doc_refs(evidence, {"axis_mapping"})
        if not refs or not refs <= vendor_refs:
            errors.append("CAP-PHY-04 PASS requires axis_mapping.evidence_refs to bind vendor primary evidence")
        if not refs & proving_refs:
            errors.append("CAP-PHY-04 PASS requires traceable vendor primary proving axis_mapping")
        return errors
    if source == "physical_oracle":
        if not refs or not refs <= artifact_refs:
            errors.append("CAP-PHY-04 PASS requires axis_mapping.evidence_refs to bind raw physical artifacts")
        kinds = _section_ref_kinds(evidence, "axis_mapping")
        if (
            not _has_kind(kinds, "raw_capture")
            or not _has_any_kind(kinds, {"photo", "video"})
            or not _has_kind(kinds, "analysis_report")
        ):
            errors.append(
                "CAP-PHY-04 axis_mapping physical oracle requires section-specific raw_capture plus photo/video/analysis artifacts"
            )
        if _operator_log_only(kinds):
            errors.append("operator_log cannot satisfy CAP-PHY-04 axis_mapping physical oracle evidence")
        return errors
    errors.append(
        f"CAP-PHY-04 PASS rejects axis_mapping.source={source}; VERIFIED axis_mapping requires vendor_primary or physical_oracle authority"
    )
    return errors


def _safety_pass_errors(evidence: dict[str, Any]) -> list[str]:
    safety = _mapping(evidence.get("safety"))
    errors: list[str] = []
    if _sequence(safety.get("forbidden_actions_observed")):
        errors.append("PASS verdicts are forbidden when safety.forbidden_actions_observed is nonempty")
    rollback_status = safety.get("rollback_status")
    if rollback_status not in {"completed", "not_applicable"}:
        errors.append("PASS verdicts require safety.rollback_status completed or not_applicable")
    if rollback_status == "not_applicable":
        reason = safety.get("rollback_reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append("PASS verdicts require safety.rollback_reason when rollback_status=not_applicable")
    return errors


def _cap_phy_03_stats_errors(evidence: dict[str, Any], computed: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    sync_stats = _mapping(computed.get("video_imu_sync_oracle"))
    tick_stats = _mapping(computed.get("tick_rollover"))
    tick_rollover = _mapping(evidence.get("tick_rollover"))
    if sync_stats.get("event_count", 0) < 20:
        errors.append("CAP-PHY-03 PASS requires at least 20 video_imu_sync_oracle events")
    if sync_stats.get("distinct_event_id_count", 0) < 20 or not sync_stats.get("event_ids_distinct"):
        errors.append("CAP-PHY-03 PASS requires >=20 distinct event IDs")
    if not sync_stats.get("video_times_strictly_increasing") or not sync_stats.get("imu_times_strictly_increasing"):
        errors.append("CAP-PHY-03 PASS requires monotonic video and IMU event times")
    if not sync_stats.get("frame_intervals_all_positive"):
        errors.append("CAP-PHY-03 PASS requires finite positive frame intervals")
    offset_abs_median = sync_stats.get("offset_abs_median_s")
    if offset_abs_median is None or offset_abs_median > 0.010:
        errors.append("CAP-PHY-03 PASS requires offset median <= 0.010s")
    offset_abs_p95 = sync_stats.get("offset_abs_p95_s")
    frame_interval = sync_stats.get("frame_interval_median_s")
    if (
        offset_abs_p95 is None
        or frame_interval is None
        or offset_abs_p95 > frame_interval
    ):
        errors.append("CAP-PHY-03 PASS requires offset p95 <= one frame interval")
    read_uncertainty = sync_stats.get("read_uncertainty_p95_s")
    frame_interval_min = sync_stats.get("frame_interval_min_s")
    if (
        read_uncertainty is None
        or frame_interval_min is None
        or read_uncertainty > min(0.005, 0.5 * frame_interval_min)
    ):
        errors.append("CAP-PHY-03 PASS requires read uncertainty <= min(5ms, half frame interval)")
    fit_residual = sync_stats.get("fit_residual_p95_s")
    if fit_residual is None or fit_residual > 0.005:
        errors.append("CAP-PHY-03 PASS requires host/device fit residual p95 <= 0.005s")

    if tick_stats.get("sample_count", 0) < 4:
        errors.append("CAP-PHY-03 PASS requires sufficient tick sample protocol data")
    if tick_stats.get("strictly_increasing") is not True:
        errors.append("CAP-PHY-03 PASS requires strict increasing tick samples")
    if tick_stats.get("duplicate_count") != 0 or tick_stats.get("regression_count") != 0:
        errors.append("CAP-PHY-03 PASS requires zero duplicate/regression tick samples")
    tick_hz = _finite_number(tick_stats.get("estimated_tick_hz"))
    if tick_hz is None or tick_hz <= 0:
        errors.append("CAP-PHY-03 PASS requires finite positive computed tick Hz")
    tick_fit = tick_stats.get("fit_residual_p95_s")
    if tick_fit is None or tick_fit > 0.005:
        errors.append("CAP-PHY-03 PASS requires tick fit residual p95 <= 5ms")
    read_duration = tick_stats.get("read_duration_p95_s")
    if read_duration is None or read_duration > 0.010:
        errors.append("CAP-PHY-03 PASS requires tick read duration p95 <= 10ms")
    if tick_stats.get("rollover_count", 0) < 1:
        errors.append("CAP-PHY-03 PASS requires real rollover_count>=1 from raw tick samples")
    if tick_rollover.get("status") == PHYSICAL_VERIFIED and tick_stats.get("sample_count", 0) < 4:
        errors.append("CAP-PHY-03 PASS requires raw tick sample evidence, not summary-only tick rollover")
    return errors


def _cap_phy_04_stats_errors(evidence: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    accel_missing = _finite_positive_fields(
        _mapping(evidence.get("accelerometer_range")),
        ["lsb_per_g", "full_scale_g"],
    )
    gyro_missing = _finite_positive_fields(
        _mapping(evidence.get("gyroscope_range")),
        ["lsb_per_dps", "full_scale_dps"],
    )
    if accel_missing:
        errors.append("CAP-PHY-04 PASS requires finite positive accelerometer_range " + ", ".join(accel_missing))
    if gyro_missing:
        errors.append("CAP-PHY-04 PASS requires finite positive gyroscope_range " + ", ".join(gyro_missing))
    if not _has_traceable_primary_vendor_doc(
        evidence,
        {"chip_identity", "accelerometer_range", "gyroscope_range", "sample_phase"},
    ) and not _has_physical_oracle_source(
        evidence,
        ["accelerometer_range", "gyroscope_range", "axis_mapping", "six_face", "known_rate"],
    ):
        errors.append(
            "CAP-PHY-04 PASS requires traceable vendor primary documentation or physical oracle evidence"
        )
    axis_mapping = _mapping(evidence.get("axis_mapping"))
    if axis_mapping.get("coordinate_frame") == "opencv_optical":
        errors.append(
            "CAP-PHY-04 PASS must not reinterpret legacy opencv_optical axes without a new discriminator, exact 3-axis unique sign mapping, and physical fit"
        )
    elif axis_mapping.get("coordinate_frame") != "raw_device_axes":
        errors.append("CAP-PHY-04 PASS defaults to raw_device_axes unless a future discriminator defines remapping")
    errors.extend(_six_face_errors(evidence))
    errors.extend(_known_rate_errors(evidence))
    return errors


def analyze_imu_physical_acceptance(evidence: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic offline statistics and procedural gate diagnostics."""

    candidate = deepcopy(evidence)
    computed = {
        "video_imu_sync_oracle": _sync_event_stats(
            _mapping(candidate.get("video_imu_sync_oracle"))
        ),
        "tick_rollover": _tick_rollover_stats(_mapping(candidate.get("tick_rollover"))),
    }
    errors = _collect_gate_errors(candidate, computed=computed)
    return {
        "schema": "ylx.imu-physical-acceptance.analysis.v1",
        "input_schema": candidate.get("schema"),
        "dry_run": True,
        "computed": computed,
        "gate_errors": errors,
        "gate_status": "VALID" if not errors else "INVALID",
    }


def _collect_gate_errors(
    evidence: dict[str, Any],
    *,
    computed: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    computed = computed or {
        "video_imu_sync_oracle": _sync_event_stats(
            _mapping(evidence.get("video_imu_sync_oracle"))
        ),
        "tick_rollover": _tick_rollover_stats(_mapping(evidence.get("tick_rollover"))),
    }
    pass_verdicts = _pass_verdicts(evidence)
    errors.extend(_collect_summary_mismatch_errors(evidence, computed))

    if evidence.get("test_only") is True:
        if evidence.get("physical_hardware_claim") is not False:
            errors.append(
                "test_only evidence must set physical_hardware_claim=false"
            )
        if pass_verdicts:
            errors.append(
                "synthetic/test_only evidence cannot carry PASS verdicts: "
                + ", ".join(pass_verdicts)
            )

    if evidence.get("physical_hardware_claim") is not True and pass_verdicts:
        errors.append(
            "physical_hardware_claim=true is required before any physical PASS verdict"
        )

    if pass_verdicts:
        errors.extend(_real_identity_errors(evidence))
        errors.extend(_hardware_profile_errors(evidence))
        errors.extend(_locator_safety_errors(evidence))
        errors.extend(_artifact_index_errors(evidence))
        errors.extend(_packet_layout_errors(evidence))
        errors.extend(_safety_pass_errors(evidence))
        errors.extend(_pass_evidence_ref_errors(evidence, pass_verdicts))
        errors.extend(_nested_evidence_ref_errors(evidence))

    if _mapping(evidence.get("stability_01")).get("status") == PASS_RESULT:
        errors.append("stability_01.status=PASS is rejected; this IMU harness cannot certify STABILITY-01")

    if _verdict_result(evidence, CAP_PHY_03) == PASS_RESULT:
        sync = _mapping(evidence.get("video_imu_sync_oracle"))
        tick_rollover = _mapping(evidence.get("tick_rollover"))
        if not _is_verified(sync):
            errors.append(
                "CAP-PHY-03 PASS requires video_imu_sync_oracle.status=VERIFIED"
            )
        if _source(sync) != "external_event_physical_oracle":
            errors.append(
                "CAP-PHY-03 PASS requires video_imu_sync_oracle.source=external_event_physical_oracle"
            )
        if not _is_verified(evidence.get("tick_clock")):
            errors.append("CAP-PHY-03 PASS requires verified tick_clock")
        if not _is_verified(evidence.get("sample_phase")):
            errors.append("CAP-PHY-03 PASS requires verified sample_phase")
        if not _is_verified(tick_rollover):
            errors.append("CAP-PHY-03 PASS requires verified tick_rollover")
        if _source(evidence.get("tick_clock")) != "tick_rollover_capture":
            errors.append("CAP-PHY-03 PASS requires tick_clock.source=tick_rollover_capture")
        if _source(evidence.get("sample_phase")) != "tick_rollover_capture":
            errors.append("CAP-PHY-03 PASS requires sample_phase.source=tick_rollover_capture")
        if _source(tick_rollover) != "tick_rollover_capture":
            errors.append("CAP-PHY-03 PASS requires tick_rollover.source=tick_rollover_capture")
        errors.extend(_cap_phy_03_typed_artifact_errors(evidence))
        errors.extend(_cap_phy_03_stats_errors(evidence, computed))

    if _verdict_result(evidence, CAP_PHY_04) == PASS_RESULT:
        required_verified = [
            "accelerometer_range",
            "gyroscope_range",
            "axis_mapping",
            "six_face",
            "known_rate",
        ]
        missing = [name for name in required_verified if not _is_verified(evidence.get(name))]
        if missing:
            errors.append(
                "CAP-PHY-04 PASS requires verified sections: " + ", ".join(missing)
            )
        if not (
            _has_traceable_primary_vendor_doc(
                evidence,
                {"chip_identity", "accelerometer_range", "gyroscope_range", "sample_phase"},
            )
            or _has_physical_oracle_source(evidence, required_verified)
        ):
            errors.append(
                "CAP-PHY-04 PASS requires vendor primary documentation or physical oracle evidence"
            )
        if _source(evidence.get("six_face")) != "six_face_physical_oracle":
            errors.append("CAP-PHY-04 PASS requires six_face physical oracle")
        if _source(evidence.get("known_rate")) != "known_rate_physical_oracle":
            errors.append("CAP-PHY-04 PASS requires known_rate physical oracle")
        if "coverage_exception" in _mapping(evidence.get("known_rate")):
            errors.append("CAP-PHY-04 PASS forbids known_rate.coverage_exception; missing axes must be PARTIAL/BLOCKED")
        errors.extend(_range_authority_errors(evidence))
        errors.extend(_axis_mapping_authority_errors(evidence))
        errors.extend(_cap_phy_04_typed_artifact_errors(evidence))
        errors.extend(_cap_phy_04_stats_errors(evidence))

    if _verdict_result(evidence, STABILITY_01) == PASS_RESULT:
        stability = _mapping(evidence.get("stability_01"))
        source_issues = _sequence(stability.get("source_issues"))
        errors.append(
            "this IMU physical harness does not own STABILITY-01 PASS; use independent composite stability evidence"
        )
        if _issue_154_only(source_issues):
            errors.append(
                "STABILITY-01 PASS cannot be derived from pi-dev#154 evidence alone"
            )

    return errors


def validate_imu_physical_acceptance(
    evidence: dict[str, Any],
    *,
    location: str = "imu physical acceptance evidence",
) -> None:
    """Raise if procedural gates reject a v1 IMU physical evidence document."""

    errors = _collect_gate_errors(evidence)
    if errors:
        raise ImuPhysicalAcceptanceError(f"{location}: " + "; ".join(errors))
