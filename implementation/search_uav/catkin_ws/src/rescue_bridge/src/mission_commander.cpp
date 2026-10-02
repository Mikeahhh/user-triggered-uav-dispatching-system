#include <ros/ros.h>
#include <sensor_msgs/NavSatFix.h>
#include <nav_msgs/Odometry.h>
#include <geometry_msgs/PoseStamped.h>
#include <cmath>
#include <stdexcept>
#include <random>
#include <chrono>
#include <rescue_bridge/WaypointCommand.h>
#include <rescue_bridge/WaypointFeedback.h>
#include <rescue_bridge/ExecutionControl.h>
#include "rescue_bridge/target_lifecycle.hpp"

class MissionCommander {
private:
    ros::NodeHandle nh_;
    ros::Subscriber incoming_gps_sub_;
    ros::Subscriber execution_control_sub_;
    ros::Subscriber drone_gps_sub_;
    ros::Subscriber vins_odom_sub_;
    ros::Publisher goal_pub_;
    ros::Publisher status_pub_;
    ros::Timer ready_timer_, tracking_timer_;
    std::uint64_t feedback_sequence_ = 0;
    double vertical_threshold_, hold_feedback_timeout_, position_feedback_hz_;
    rescue_execution::FrameAlignment alignment_;
    rescue_execution::CoordinateFrameState frame_state_;
    nav_msgs::Odometry anchor_odom_;
    std::string receiver_session_id_;

    sensor_msgs::NavSatFix current_drone_gps_;
    nav_msgs::Odometry current_vins_odom_;
    sensor_msgs::NavSatFix paired_gps_;
    nav_msgs::Odometry paired_odom_;
    ros::Time paired_gps_received_, paired_odom_received_;
    bool has_paired_position_ = false;

    bool has_drone_gps_;
    bool has_vins_odom_;


    bool has_active_goal_;
    geometry_msgs::Point goal_position_;
    std::string active_mission_id_;
    double arrival_threshold_;
    rescue_execution::TargetLifecycle lifecycle_;




    double data_timeout_;
    ros::Time last_drone_gps_stamp_, last_vins_odom_stamp_;


    double safe_flight_alt_;




    sensor_msgs::NavSatFix prev_gps_;
    bool has_prev_gps_;

    ros::Time last_drone_gps_time_;
    ros::Time last_vins_odom_time_;



public:
    MissionCommander()
        : has_drone_gps_(false), has_vins_odom_(false),
          has_active_goal_(false), has_prev_gps_(false) {

        std::random_device random;
        receiver_session_id_ = std::to_string(std::chrono::high_resolution_clock::now().time_since_epoch().count())
                             + "-" + std::to_string(random());

        ros::NodeHandle pnh("~");
        pnh.param("flight_alt", safe_flight_alt_, 1.5);
        pnh.param("arrival_threshold", arrival_threshold_, 1.0);
        pnh.param("data_timeout", data_timeout_, 5.0);
        pnh.param("vertical_threshold", vertical_threshold_, 1.0);
        pnh.param("hold_feedback_timeout", hold_feedback_timeout_, 1.0);
        pnh.param("position_feedback_hz", position_feedback_hz_, 5.0);
        if (!std::isfinite(safe_flight_alt_) || safe_flight_alt_ <= 0
            || !std::isfinite(arrival_threshold_) || arrival_threshold_ <= 0
            || !std::isfinite(data_timeout_) || data_timeout_ <= 0
            || !std::isfinite(vertical_threshold_) || vertical_threshold_ <= 0
            || !std::isfinite(hold_feedback_timeout_) || hold_feedback_timeout_ <= 0
            || !std::isfinite(position_feedback_hz_) || position_feedback_hz_ <= 0
            || 1.0 / position_feedback_hz_ >= hold_feedback_timeout_)
            throw std::runtime_error("invalid commander profile");
        ROS_INFO("MissionCommander: flight_alt = %.2f m", safe_flight_alt_);


        incoming_gps_sub_ = nh_.subscribe("/rescue/waypoint_command", 10,
            &MissionCommander::incomingTargetCallback, this);
        execution_control_sub_ = nh_.subscribe("/rescue/execution_control", 10,
            &MissionCommander::controlCallback, this);
        drone_gps_sub_ = nh_.subscribe("/mavros/global_position/global", 10,
            &MissionCommander::droneGpsCallback, this);
        vins_odom_sub_ = nh_.subscribe("/vins_fusion/odometry", 10,
            &MissionCommander::vinsOdomCallback, this);


        goal_pub_ = nh_.advertise<geometry_msgs::PoseStamped>("/move_base_simple/goal", 10);
        status_pub_ = nh_.advertise<rescue_bridge::WaypointFeedback>("/rescue/waypoint_feedback", 10, true);
        ready_timer_ = nh_.createTimer(ros::Duration(1.0), &MissionCommander::announceReady, this);
        tracking_timer_ = nh_.createTimer(ros::Duration(1.0 / position_feedback_hz_), &MissionCommander::trackPosition, this);
        publishFeedback(rescue_execution::Target{}, "READY", "RECEIVER_STARTED");

        ROS_INFO("Mission Commander initialized (WGS-84 + heading alignment)");
    }

    void announceReady(const ros::TimerEvent&) {
        publishFeedback(rescue_execution::Target{}, "READY", "RECEIVER_AVAILABLE");
    }

    void droneGpsCallback(const sensor_msgs::NavSatFix::ConstPtr& msg) {
        if (msg->status.status < sensor_msgs::NavSatStatus::STATUS_FIX
            || !std::isfinite(msg->latitude) || !std::isfinite(msg->longitude)
            || msg->latitude < -90 || msg->latitude > 90
            || msg->longitude < -180 || msg->longitude > 180
            || !fresh(ros::Time::now(), msg->header.stamp)) {
            has_drone_gps_ = false;
            has_paired_position_ = false;
            ROS_WARN_THROTTLE(5.0, "Drone GPS has no fix, ignoring");
            return;
        }
        current_drone_gps_ = *msg;
        has_drone_gps_ = true;
        last_drone_gps_time_ = ros::Time::now();
        last_drone_gps_stamp_ = msg->header.stamp;
        updatePairedPosition();
    }

    void vinsOdomCallback(const nav_msgs::Odometry::ConstPtr& msg) {
        if (!std::isfinite(msg->pose.pose.position.x) || !std::isfinite(msg->pose.pose.position.y)
            || !std::isfinite(msg->pose.pose.position.z)
            || !std::isfinite(msg->pose.pose.orientation.x) || !std::isfinite(msg->pose.pose.orientation.y)
            || !std::isfinite(msg->pose.pose.orientation.z) || !std::isfinite(msg->pose.pose.orientation.w)
            || !rescue_execution::validQuaternion(msg->pose.pose.orientation.x, msg->pose.pose.orientation.y,
                                                   msg->pose.pose.orientation.z, msg->pose.pose.orientation.w)
            || !fresh(ros::Time::now(), msg->header.stamp)) {
            has_vins_odom_ = false;
            has_paired_position_ = false;
            frame_state_.observe(msg->header.frame_id, false, has_active_goal_);
            return;
        }
        if (frame_state_.observe(msg->header.frame_id, true, has_active_goal_)) {
            alignment_.reset();
            has_prev_gps_ = false;
            has_paired_position_ = false;
            if (frame_state_.targetInvalidated())
                publishFeedback(lifecycle_.current(), "REJECTED", "COORDINATE_FRAME_CHANGED");
        }
        current_vins_odom_ = *msg;
        has_vins_odom_ = true;
        last_vins_odom_time_ = ros::Time::now();
        last_vins_odom_stamp_ = msg->header.stamp;
        updatePairedPosition();
    }

    void updatePairedPosition() {
        if (!has_drone_gps_ || !has_vins_odom_
            || !rescue_execution::matchedObservations(ros::Time::now().toSec(),
                last_drone_gps_time_.toSec(), last_drone_gps_stamp_.toSec(),
                last_vins_odom_time_.toSec(), last_vins_odom_stamp_.toSec(), data_timeout_)) return;
        paired_gps_ = current_drone_gps_;
        paired_odom_ = current_vins_odom_;
        paired_gps_received_ = last_drone_gps_time_;
        paired_odom_received_ = last_vins_odom_time_;
        has_paired_position_ = true;
        if (!alignment_.ready()) {
            if (!has_prev_gps_) {
                prev_gps_ = paired_gps_;
                anchor_odom_ = paired_odom_;
                has_prev_gps_ = true;
            } else {
                auto en = rescue_execution::gpsToEastNorth(prev_gps_.latitude, prev_gps_.longitude,
                                                           paired_gps_.latitude, paired_gps_.longitude);
                auto local = rescue_execution::PlanarVector{
                    paired_odom_.pose.pose.position.x - anchor_odom_.pose.pose.position.x,
                    paired_odom_.pose.pose.position.y - anchor_odom_.pose.pose.position.y};
                alignment_.calibrate(en, local);
            }
        }
    }

    void trackPosition(const ros::TimerEvent&) {
        if (!has_active_goal_) return;
        const auto target = lifecycle_.current();
        if (frame_state_.targetInvalidated()) {
            publishFeedback(target, "REJECTED", "COORDINATE_FRAME_CHANGED");
            return;
        }
        const double now = ros::Time::now().toSec();
        if (!has_vins_odom_ || !has_drone_gps_ || !alignment_.ready()
            || !fresh(last_drone_gps_time_, last_drone_gps_stamp_)
            || !rescue_execution::freshObservation(now, last_vins_odom_time_.toSec(), last_vins_odom_stamp_.toSec(), hold_feedback_timeout_)) {
            publishFeedback(target, "POSITION_STALE", "POSITION_UNCONFIRMED");
            return;
        }
        const double dx = current_vins_odom_.pose.pose.position.x - goal_position_.x;
        const double dy = current_vins_odom_.pose.pose.position.y - goal_position_.y;
        const double dz = current_vins_odom_.pose.pose.position.z - goal_position_.z;
        const bool inside = rescue_execution::withinTarget(dx, dy, dz, arrival_threshold_, vertical_threshold_);
        const bool first = lifecycle_.arrived3d(dx, dy, dz, arrival_threshold_, vertical_threshold_);
        publishFeedback(target, first ? "ARRIVED" : inside ? "HOLDING" : "OUTSIDE",
                        inside ? "THREE_DIMENSIONAL_TOLERANCE" : "OUTSIDE_TARGET_TOLERANCE", inside,
                        last_vins_odom_stamp_.toSec());
    }

    bool fresh(const ros::Time& received, const ros::Time& stamp) const {
        const ros::Time now = ros::Time::now();
        return rescue_execution::freshObservation(now.toSec(), received.toSec(), stamp.toSec(), data_timeout_);
    }

    void controlCallback(const rescue_bridge::ExecutionControl::ConstPtr& msg) {
        if (msg->receiver_session_id != receiver_session_id_) return;
        if (msg->action != "CANCEL" && msg->action != "RELEASE") return;
        if (lifecycle_.control(msg->mission_id, msg->execution_id, msg->action == "RELEASE")) {
            has_active_goal_ = false;
            if (msg->action == "RELEASE") frame_state_.releaseTarget();
            rescue_execution::Target identity;
            identity.mission_id = msg->mission_id;
            identity.execution_id = msg->execution_id;
            publishFeedback(identity, msg->action == "RELEASE" ? "RELEASED" : "CANCELLED", "TARGET_TRACKER_ONLY");
        }
    }

    void incomingTargetCallback(const rescue_bridge::WaypointCommand::ConstPtr& msg) {
        rescue_execution::Target target{msg->mission_id, msg->execution_id, msg->waypoint_index,
                                       msg->latitude, msg->longitude,
                                       msg->altitude_specified ? msg->altitude : safe_flight_alt_};
        if (msg->receiver_session_id != receiver_session_id_) {
            publishFeedback(target, "REJECTED", "RECEIVER_SESSION_MISMATCH");
            return;
        }
        if (frame_state_.targetInvalidated()) {
            publishFeedback(target, "REJECTED", "COORDINATE_FRAME_CHANGED");
            return;
        }
        std::string not_ready;
        if (!has_drone_gps_) not_ready = "GPS_NOT_READY";
        else if (!has_vins_odom_) not_ready = "ODOMETRY_NOT_READY";
        else if (!alignment_.ready()) not_ready = "FRAME_ALIGNMENT_UNAVAILABLE";
        else if (!fresh(last_drone_gps_time_, last_drone_gps_stamp_)) not_ready = "GPS_STALE";
        else if (!fresh(last_vins_odom_time_, last_vins_odom_stamp_)) not_ready = "ODOMETRY_STALE";
        else if (!has_paired_position_ || !rescue_execution::matchedObservations(ros::Time::now().toSec(),
                    paired_gps_received_.toSec(), paired_gps_.header.stamp.toSec(),
                    paired_odom_received_.toSec(), paired_odom_.header.stamp.toSec(), data_timeout_))
            not_ready = "SYNCHRONIZED_POSITION_UNAVAILABLE";
        if (msg->altitude_specified && (!std::isfinite(msg->altitude) || msg->altitude < .5 || msg->altitude > 120))
            not_ready = "INVALID_TASK_ALTITUDE";
        const auto decision = lifecycle_.offer(target, not_ready.empty(), not_ready);
        if (!decision.publish_goal) {
            if (decision.status == "ARRIVED") publishFeedback(target, "ACCEPTED", "DUPLICATE");
            publishFeedback(target, decision.status, decision.reason);
            return;
        }
        const auto en = rescue_execution::gpsToEastNorth(paired_gps_.latitude, paired_gps_.longitude,
                                                         msg->latitude, msg->longitude);
        const auto local = alignment_.transform(en);
        const double vins_dx = local.x, vins_dy = local.y;

        geometry_msgs::PoseStamped goal;
        goal.header.stamp = ros::Time::now();
        goal.header.frame_id = paired_odom_.header.frame_id;

        goal.pose.position.x = paired_odom_.pose.pose.position.x + vins_dx;
        goal.pose.position.y = paired_odom_.pose.pose.position.y + vins_dy;
        goal.pose.position.z = target.altitude;


        double target_yaw = std::atan2(vins_dy, vins_dx);
        goal.pose.orientation.x = 0.0;
        goal.pose.orientation.y = 0.0;
        goal.pose.orientation.z = std::sin(target_yaw / 2.0);
        goal.pose.orientation.w = std::cos(target_yaw / 2.0);

        ROS_INFO("Goal in VINS frame: x=%.2f, y=%.2f, z=%.2f, yaw=%.1f deg",
                 goal.pose.position.x, goal.pose.position.y, goal.pose.position.z,
                 target_yaw * 180.0 / M_PI);

        try {
            goal_pub_.publish(goal);
        } catch (const std::exception&) {
            lifecycle_.control(target.mission_id, target.execution_id, false);
            publishFeedback(target, "REJECTED", "LOCAL_GOAL_PUBLICATION_FAILED");
            return;
        }


        goal_position_ = goal.pose.position;
        active_mission_id_ = msg->mission_id;
        has_active_goal_ = true;
        publishFeedback(target, "ACCEPTED", "LOCAL_GOAL_PUBLISHED");
    }

    void publishFeedback(const rescue_execution::Target& target, const std::string& status,
                         const std::string& reason, bool within = false, double position_stamp = 0) {
        rescue_bridge::WaypointFeedback msg;
        msg.mission_id = target.mission_id;
        msg.execution_id = target.execution_id;
        msg.waypoint_index = target.waypoint_index;
        msg.receiver_session_id = receiver_session_id_;
        msg.effective_altitude = target.altitude;
        msg.arrival_threshold = arrival_threshold_;
        msg.data_timeout = data_timeout_;
        msg.vertical_threshold = vertical_threshold_;
        msg.within_tolerance = within;
        msg.position_stamp = position_stamp;
        msg.feedback_seq = ++feedback_sequence_;
        msg.status = status;
        msg.reason = reason;
        status_pub_.publish(msg);
    }

};

int main(int argc, char** argv) {
    ros::init(argc, argv, "mission_commander");
    MissionCommander commander;
    ros::spin();
    return 0;
}
