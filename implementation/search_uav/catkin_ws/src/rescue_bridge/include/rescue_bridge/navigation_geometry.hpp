#pragma once
#include <cmath>
#include <stdexcept>
#include <string>

namespace rescue_execution {
struct PlanarVector { double x, y; };

inline PlanarVector gpsToEastNorth(double lat1, double lon1, double lat2, double lon2) {
    if (!std::isfinite(lat1) || !std::isfinite(lon1) || !std::isfinite(lat2) || !std::isfinite(lon2)
        || std::abs(lat1) > 90 || std::abs(lat2) > 90 || std::abs(lon1) > 180 || std::abs(lon2) > 180)
        throw std::invalid_argument("invalid GPS coordinate");
    const double radians = std::acos(-1.0) / 180.0;
    const double mean = (lat1 + lat2) * radians / 2.0;
    const double e2 = 0.00669437999014;
    const double w = std::sqrt(1.0 - e2 * std::pow(std::sin(mean), 2));
    const double meridian = 6378137.0 * (1.0 - e2) / (w * w * w);
    const double normal = 6378137.0 / w;
    return {std::remainder(lon2 - lon1, 360.0) * radians * normal * std::cos(mean),
            (lat2 - lat1) * radians * meridian};
}

inline bool validQuaternion(double x, double y, double z, double w) {
    const double norm = x*x + y*y + z*z + w*w;
    return std::isfinite(norm) && std::abs(norm - 1.0) <= .01;
}

inline bool freshObservation(double now, double received, double source, double timeout) {
    return std::isfinite(now) && std::isfinite(received) && std::isfinite(source)
        && std::isfinite(timeout) && timeout > 0 && source > 0
        && now >= received && now - received <= timeout && now >= source && now - source <= timeout;
}

inline bool matchedObservations(double now, double gps_received, double gps_source,
                                double odom_received, double odom_source, double timeout,
                                double maximum_skew = .25) {
    return std::isfinite(maximum_skew) && maximum_skew >= 0
        && freshObservation(now, gps_received, gps_source, timeout)
        && freshObservation(now, odom_received, odom_source, timeout)
        && std::abs(gps_source - odom_source) <= maximum_skew;
}

class CoordinateFrameState {
    std::string frame_;
    bool observed_ = false;
    bool target_invalidated_ = false;
public:
    bool observe(const std::string& frame, bool observation_valid, bool has_target) {
        if (!observation_valid) return false;
        const bool changed = observed_ && frame != frame_;
        target_invalidated_ = target_invalidated_ || (changed && has_target);
        frame_ = frame;
        observed_ = true;
        return changed;
    }
    bool targetInvalidated() const { return target_invalidated_; }
    void releaseTarget() { target_invalidated_ = false; }
};

inline bool withinTarget(double dx, double dy, double dz, double horizontal, double vertical) {
    return std::isfinite(dx) && std::isfinite(dy) && std::isfinite(dz)
        && std::isfinite(horizontal) && std::isfinite(vertical) && horizontal > 0 && vertical > 0
        && std::hypot(dx, dy) < horizontal && std::abs(dz) < vertical;
}

class FrameAlignment {
    bool ready_ = false;
    double angle_ = 0;
public:
    bool calibrate(PlanarVector east_north, PlanarVector local_displacement) {
        if (!std::isfinite(east_north.x) || !std::isfinite(east_north.y)
            || !std::isfinite(local_displacement.x) || !std::isfinite(local_displacement.y)
            || std::hypot(east_north.x, east_north.y) < 2.0
            || std::hypot(local_displacement.x, local_displacement.y) < 2.0) return false;
        angle_ = std::atan2(local_displacement.y, local_displacement.x) - std::atan2(east_north.y, east_north.x);
        ready_ = true;
        return true;
    }
    bool ready() const { return ready_; }
    void reset() { ready_ = false; }
    PlanarVector transform(PlanarVector en) const {
        if (!ready_ || !std::isfinite(en.x) || !std::isfinite(en.y)) throw std::logic_error("alignment unavailable");
        return {std::cos(angle_)*en.x - std::sin(angle_)*en.y,
                std::sin(angle_)*en.x + std::cos(angle_)*en.y};
    }
};
}
