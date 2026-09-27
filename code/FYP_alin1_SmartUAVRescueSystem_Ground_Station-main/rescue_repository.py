import copy

from rescue_event_manager import scan_user_records


class StateConflict(ValueError):
    pass


def user_transaction(database_root, user_id, transform):
    reference = database_root.child("users").child(user_id)
    transaction = getattr(reference, "transaction", None)
    if not callable(transaction):
        raise StateConflict("conditional database transactions are unavailable")

    def update(current):
        if not isinstance(current, dict):
            raise StateConflict("user record is unavailable")
        result = transform(copy.deepcopy(current))
        if not isinstance(result, dict):
            raise StateConflict("transaction produced an invalid user record")
        return result

    return transaction(update)


def apply_user_reconciliation(user, user_id, now_ms, config):


    if not isinstance(user, dict):
        raise StateConflict("user record is unavailable")
    user = copy.deepcopy(user)
    result = scan_user_records(
        user_id, user, now_ms,
        legacy_timezone=config.get("event_timezone"),
        quick_start_timeout_ms=config.get("quick_start_timeout_ms"),
    )
    for group, key in (("rescue_alerts", "alerts"), ("rescue_events", "events")):
        target = user.setdefault(group, {})
        if not isinstance(target, dict):
            raise StateConflict(f"{group} must be an object")
        for identity, value in result[key].items():
            target.setdefault(identity, value)
    from quick_start_freshness import ALERT_DETECTION_FIELDS, MONITORING_GROUP
    if result["monitoring"]:
        monitors = user.setdefault(MONITORING_GROUP, {})
        if not isinstance(monitors, dict):
            raise StateConflict("quick_start_monitoring must be an object")
        for identity, value in result["monitoring"].items():


            existing = monitors.setdefault(identity, {})
            if not isinstance(existing, dict):
                raise StateConflict("session monitoring must be an object")
            monitors[identity] = value
    for identity, patch in result["alert_updates"].items():
        if not set(patch).issubset(ALERT_DETECTION_FIELDS):
            raise StateConflict("detector attempted to change human verification state")
        alert = user.get("rescue_alerts", {}).get(identity)
        if not isinstance(alert, dict) or alert.get("status") != "PENDING":
            raise StateConflict("alert changed while updating detection fields")
        alert.update(patch)
    return user, result


def reconcile_user(database_root, user_id, now_ms, config, *, clock_ms=None):


    if clock_ms is not None and not callable(clock_ms):
        raise TypeError("clock_ms must be callable")
    observed = {}

    def update(user):
        evaluated_at_ms = now_ms if clock_ms is None else clock_ms()
        updated, result = apply_user_reconciliation(user, user_id, evaluated_at_ms, config)
        observed.clear()
        observed.update(result)
        return updated

    committed = user_transaction(database_root, user_id, update)
    return committed, observed


def transition_alert(database_root, user_id, alert_id, transform):
    def update(user):
        alerts = user.get("rescue_alerts", {})
        alert = alerts.get(alert_id) if isinstance(alerts, dict) else None
        if not isinstance(alert, dict):
            raise StateConflict("alert is no longer available")
        if alert.get("user_id") != user_id or alert.get("alert_id") != alert_id:
            raise StateConflict("alert identity does not match its Firebase path")
        user["rescue_alerts"][alert_id] = transform(alert, user)
        return user

    return user_transaction(database_root, user_id, update)
