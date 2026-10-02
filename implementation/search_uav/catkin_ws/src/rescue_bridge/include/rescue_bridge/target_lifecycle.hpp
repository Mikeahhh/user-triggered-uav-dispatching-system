#pragma once
#include <cmath>
#include <cstdint>
#include <map>
#include <regex>
#include <set>
#include <string>
#include <tuple>
#include "rescue_bridge/navigation_geometry.hpp"

namespace rescue_execution {
struct Target {
    std::string mission_id, execution_id;
    std::uint32_t waypoint_index = 0;
    double latitude = 0, longitude = 0, altitude = 0;
};
struct Decision {
    std::string status, reason;
    bool publish_goal = false;
};

class TargetLifecycle {
    using Key = std::tuple<std::string, std::uint32_t>;
    struct Accepted { Target target; bool arrived = false; };
    std::map<Key, Accepted> history_;
    std::set<std::string> released_;
    std::string locked_execution_, locked_mission_;
    Key current_;
    bool active_ = false;
public:
    static bool valid(const Target& t) {
        static const std::regex token("[A-Za-z0-9][A-Za-z0-9._-]{0,127}");
        static const std::regex mission("[A-Za-z0-9][A-Za-z0-9._-]{0,127}(/[A-Za-z0-9][A-Za-z0-9._-]{0,127})?");
        return std::regex_match(t.execution_id, token) && std::regex_match(t.mission_id, mission)
            && t.waypoint_index <= 100000 && std::isfinite(t.latitude) && std::isfinite(t.longitude)
            && std::isfinite(t.altitude) && t.latitude >= -90 && t.latitude <= 90
            && t.longitude >= -180 && t.longitude <= 180 && t.altitude > 0;
    }
    Decision offer(const Target& target, bool ready, const std::string& reason) {
        if (!valid(target)) return {"REJECTED", "INVALID_TARGET", false};
        if (released_.count(target.execution_id)) return {"REJECTED", "EXECUTION_RELEASED", false};
        if (!locked_execution_.empty() && (locked_execution_ != target.execution_id || locked_mission_ != target.mission_id))
            return {"REJECTED", "EXECUTION_LOCKED", false};
        const Key key(target.execution_id, target.waypoint_index);
        auto existing = history_.find(key);
        if (existing != history_.end()) {
            const auto& old = existing->second.target;
            if (old.mission_id != target.mission_id || old.latitude != target.latitude
                || old.longitude != target.longitude || old.altitude != target.altitude)
                return {"REJECTED", "TARGET_CONTENT_CONFLICT", false};
            if (!ready) return {"REJECTED", reason, false};
            return {existing->second.arrived ? "ARRIVED" : "ACCEPTED", "DUPLICATE", false};
        }
        if (active_) return {"REJECTED", "TARGET_BUSY", false};
        const auto expected = locked_execution_.empty() ? 0 : std::get<1>(current_) + 1;
        if (target.waypoint_index != expected) return {"REJECTED", "WAYPOINT_SEQUENCE_MISMATCH", false};
        if (!ready) return {"REJECTED", reason, false};
        locked_execution_ = target.execution_id;
        locked_mission_ = target.mission_id;
        current_ = key;
        history_.emplace(key, Accepted{target, false});
        active_ = true;
        return {"ACCEPTED", "", true};
    }
    bool arrived(double distance_xy, double threshold) {
        if (!active_ || !std::isfinite(distance_xy) || !std::isfinite(threshold)
            || distance_xy < 0 || threshold <= 0 || distance_xy >= threshold) return false;
        history_.at(current_).arrived = true;
        active_ = false;
        return true;
    }
    Target current() const { return history_.at(current_).target; }
    bool arrived3d(double dx, double dy, double dz, double horizontal, double vertical) {
        if (!withinTarget(dx, dy, dz, horizontal, vertical)) return false;
        return arrived(std::hypot(dx, dy), horizontal);
    }
    bool active() const { return active_; }
    bool control(const std::string& mission, const std::string& execution, bool release) {
        if (execution.empty() || (!locked_execution_.empty()
            && (execution != locked_execution_ || mission != locked_mission_))) return false;
        active_ = false;
        if (!release) released_.insert(execution);
        if (release) {
            released_.insert(execution);
            locked_execution_.clear();
            locked_mission_.clear();
        }
        return true;
    }
};
}
