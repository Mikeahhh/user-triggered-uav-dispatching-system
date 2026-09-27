import math


_M_PER_DEG_LAT = 111320.0


def _validate_center(center_lat, center_lon):
    values = (center_lat, center_lon)
    if any(isinstance(value, bool) for value in values):
        raise ValueError("center latitude and longitude must be finite numbers")
    try:
        latitude = float(center_lat)
        longitude = float(center_lon)
    except (TypeError, ValueError) as exc:
        raise ValueError("center latitude and longitude must be finite numbers") from exc
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise ValueError("center latitude and longitude must be finite numbers")
    if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        raise ValueError("center latitude or longitude is out of range")
    if abs(math.cos(math.radians(latitude))) < 1e-6:
        raise ValueError("local tangent conversion is undefined at the poles")
    return latitude, longitude


def _positive_finite(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive finite number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return number


def _m_to_deg(dx_m, dy_m, ref_lat_deg):

    dlat = dy_m / _M_PER_DEG_LAT
    dlon = dx_m / (_M_PER_DEG_LAT * math.cos(math.radians(ref_lat_deg)))
    return dlon, dlat


def generate_spiral(center_lat, center_lon, radius_m=200.0, spacing_m=30.0):


    center_lat, center_lon = _validate_center(center_lat, center_lon)
    radius_m = _positive_finite(radius_m, "radius_m")
    spacing_m = _positive_finite(spacing_m, "spacing_m")

    waypoints = [{"latitude": center_lat, "longitude": center_lon}]

    headings = [(1, 0), (0, 1), (-1, 0), (0, -1)]
    h = 0
    leg_len = 1
    x_m, y_m = 0.0, 0.0

    while True:

        for _ in range(2):
            dx_unit, dy_unit = headings[h % 4]
            x_m += dx_unit * leg_len * spacing_m
            y_m += dy_unit * leg_len * spacing_m
            dlon, dlat = _m_to_deg(x_m, y_m, center_lat)
            waypoints.append({
                "latitude": center_lat + dlat,
                "longitude": center_lon + dlon,
            })
            if math.hypot(x_m, y_m) >= radius_m:
                return waypoints
            h += 1
        leg_len += 1


def generate_grid(center_lat, center_lon, width_m=300.0, spacing_m=40.0):


    center_lat, center_lon = _validate_center(center_lat, center_lon)
    width_m = _positive_finite(width_m, "width_m")
    spacing_m = _positive_finite(spacing_m, "spacing_m")

    half = width_m / 2.0
    waypoints = []


    n_intervals = max(1, math.ceil(width_m / spacing_m))
    n_sweeps = n_intervals + 1
    effective_spacing = width_m / n_intervals
    for i in range(n_sweeps):
        x_m = -half + i * effective_spacing

        if i % 2 == 0:
            ys = (-half, half)
        else:
            ys = (half, -half)
        for y_m in ys:
            dlon, dlat = _m_to_deg(x_m, y_m, center_lat)
            waypoints.append({
                "latitude": center_lat + dlat,
                "longitude": center_lon + dlon,
            })
    return waypoints


def waypoints_to_mqtt_payload(waypoints, mission_id, altitude=5.0,
                              hover_seconds=5.0, return_to_launch=True):

    return {
        "mission_id": mission_id,
        "mission_type": "rescue",
        "waypoints": waypoints,
        "return_to_launch": return_to_launch,
        "altitude": altitude,
        "hover_seconds": hover_seconds,
    }


if __name__ == "__main__":

    import json
    SYNTHETIC_CENTER = (0.0, 0.0)
    sp = generate_spiral(*SYNTHETIC_CENTER, radius_m=300.0, spacing_m=30.0)
    gd = generate_grid(*SYNTHETIC_CENTER, width_m=300.0, spacing_m=50.0)
    print(f"spiral: {len(sp)} waypoints, last={sp[-1]}")
    print(f"grid:   {len(gd)} waypoints, last={gd[-1]}")
    payload = waypoints_to_mqtt_payload(sp, mission_id="demo/synthetic-spiral")
    print(json.dumps(payload, indent=2)[:500])
