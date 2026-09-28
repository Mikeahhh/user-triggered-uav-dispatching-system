#include "rescue_bridge/target_lifecycle.hpp"
#include <cassert>
#include <iostream>

int main() {
    using namespace rescue_execution;
    Target target{"AUDIT/a", "exec-a", 0, 22.0, 114.0, 5.0};
    TargetLifecycle lifecycle;
    assert(lifecycle.offer(target, false, "GPS_NOT_READY").status == "REJECTED");
    assert(!lifecycle.active());
    auto skipped = target; skipped.waypoint_index = 2;
    assert(lifecycle.offer(skipped, true, "").reason == "WAYPOINT_SEQUENCE_MISMATCH");
    auto accepted = lifecycle.offer(target, true, "");
    assert(accepted.status == "ACCEPTED" && accepted.publish_goal);
    assert(!lifecycle.offer(target, true, "").publish_goal);
    auto conflict = target; conflict.latitude += .1;
    assert(lifecycle.offer(conflict, true, "").reason == "TARGET_CONTENT_CONFLICT");
    auto second = target; second.waypoint_index = 1;
    assert(lifecycle.offer(second, true, "").reason == "TARGET_BUSY");
    assert(!lifecycle.arrived(1.0, 1.0));
    assert(!lifecycle.arrived(-1, 1.0));
    assert(!lifecycle.arrived(0, INFINITY));
    assert(lifecycle.arrived(.999, 1.0));
    assert(!lifecycle.arrived(0, 1.0));
    assert(lifecycle.offer(target, true, "").status == "ARRIVED");
    assert(lifecycle.offer(skipped, true, "").reason == "WAYPOINT_SEQUENCE_MISMATCH");
    assert(lifecycle.offer(second, true, "").publish_goal);
    auto other = target; other.execution_id = "exec-b";
    assert(lifecycle.offer(other, true, "").reason == "EXECUTION_LOCKED");
    assert(!lifecycle.control("AUDIT/a", "old", true));
    assert(lifecycle.control("AUDIT/a", "exec-a", false));
    assert(!lifecycle.active());
    assert(lifecycle.offer(second, true, "").reason == "EXECUTION_RELEASED");
    assert(lifecycle.offer(other, true, "").reason == "EXECUTION_LOCKED");
    assert(lifecycle.control("AUDIT/a", "exec-a", true));
    assert(lifecycle.offer(target, true, "").reason == "EXECUTION_RELEASED");
    assert(lifecycle.offer(other, true, "").publish_goal);
    std::cout << "target lifecycle: readiness, identity, deduplication, arrival and release assertions passed\n";
}
