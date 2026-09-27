from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Tuple


HIGH_PRIORITY = "HIGH"
NORMAL_PRIORITY = "NORMAL"
PENDING_STATUS = "PENDING"
SOS_TRIGGER = "SOS"


class SchedulerValidationError(ValueError):
    pass


def _epoch_ms(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise SchedulerValidationError(f"{field_name} must be epoch milliseconds")
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise SchedulerValidationError(
            f"{field_name} must be epoch milliseconds"
        ) from None
    if not math.isfinite(numeric) or numeric < 0 or not numeric.is_integer():
        raise SchedulerValidationError(f"{field_name} must be a non-negative integer")
    return int(numeric)


def _wait_threshold_ms(value: Any) -> int:
    threshold = _epoch_ms(value, "wait_threshold_ms")
    if threshold <= 0:
        raise SchedulerValidationError("wait_threshold_ms must be greater than zero")
    return threshold


def _event_id(event: Mapping[str, Any]) -> str:
    value = event.get("event_id")
    if not isinstance(value, str) or not value.strip():
        raise SchedulerValidationError("event_id must be a non-empty string")
    return value


def _user_id(event: Mapping[str, Any]) -> str:
    value = event.get("user_id")
    if not isinstance(value, str) or not value.strip():
        raise SchedulerValidationError("user_id must be a non-empty string")
    return value


def queue_waiting_time_ms(event: Mapping[str, Any], now_ms: Any) -> int:
    now = _epoch_ms(now_ms, "now_ms")
    created_at = _epoch_ms(event.get("created_at_ms"), "created_at_ms")
    if created_at > now:
        raise SchedulerValidationError("created_at_ms cannot be later than now_ms")
    return now - created_at


def abnormal_duration_ms(event: Mapping[str, Any], now_ms: Any) -> int:


    now = _epoch_ms(now_ms, "now_ms")
    created_at = _epoch_ms(event.get("created_at_ms"), "created_at_ms")
    abnormal_since = _epoch_ms(
        event.get("abnormal_since_ms", created_at) if event.get("abnormal_since_ms") is not None else created_at, "abnormal_since_ms"
    )
    if created_at > now:
        raise SchedulerValidationError("created_at_ms cannot be later than now_ms")
    if abnormal_since > created_at:
        raise SchedulerValidationError(
            "abnormal_since_ms cannot be later than created_at_ms"
        )
    return now - abnormal_since


def is_high_priority(
    event: Mapping[str, Any], now_ms: Any, wait_threshold_ms: Any
) -> bool:


    _event_id(event)
    _user_id(event)
    threshold = _wait_threshold_ms(wait_threshold_ms)
    trigger_type = event.get("trigger_type")
    if trigger_type not in {SOS_TRIGGER, "EVENT_BOOKING_TIMEOUT", "QUICK_START_INACTIVITY", "QUICK_START_LOCATION_TIMEOUT"}:
        raise SchedulerValidationError(f"unsupported trigger_type: {trigger_type!r}")

    abnormal_duration_ms(event, now_ms)
    return queue_waiting_time_ms(event, now_ms) >= threshold


def effective_priority(
    event: Mapping[str, Any], now_ms: Any, wait_threshold_ms: Any
) -> str:
    return (
        HIGH_PRIORITY
        if is_high_priority(event, now_ms, wait_threshold_ms)
        else NORMAL_PRIORITY
    )


def schedule_metrics(
    event: Mapping[str, Any], now_ms: Any, wait_threshold_ms: Any
) -> Dict[str, Any]:


    high = is_high_priority(event, now_ms, wait_threshold_ms)
    duration = None
    if event.get("trigger_type") != SOS_TRIGGER:
        duration = abnormal_duration_ms(event, now_ms)
    return {
        "effective_priority": HIGH_PRIORITY if high else NORMAL_PRIORITY,
        "queue_waiting_time_ms": queue_waiting_time_ms(event, now_ms),
        "abnormal_duration_ms": duration,
    }


def _sort_key(
    event: Mapping[str, Any], now_ms: int, wait_threshold_ms: int
) -> Tuple[Any, ...]:
    event_id = _event_id(event)
    user_id = _user_id(event)
    created_at = _epoch_ms(event.get("created_at_ms"), "created_at_ms")
    high = is_high_priority(event, now_ms, wait_threshold_ms)
    if high:


        return (0, created_at, event_id, user_id)
    return (
        1,
        -abnormal_duration_ms(event, now_ms),
        created_at,
        event_id,
        user_id,
    )


def order_pending_events(
    events: Iterable[Mapping[str, Any]], now_ms: Any, wait_threshold_ms: Any
) -> List[Mapping[str, Any]]:


    now = _epoch_ms(now_ms, "now_ms")
    threshold = _wait_threshold_ms(wait_threshold_ms)
    pending = []
    for event in events:
        if not isinstance(event, Mapping):
            raise SchedulerValidationError("each event must be a mapping")
        if str(event.get("status", "")).upper() != PENDING_STATUS:
            continue

        _sort_key(event, now, threshold)
        pending.append(event)
    return sorted(pending, key=lambda item: _sort_key(item, now, threshold))
