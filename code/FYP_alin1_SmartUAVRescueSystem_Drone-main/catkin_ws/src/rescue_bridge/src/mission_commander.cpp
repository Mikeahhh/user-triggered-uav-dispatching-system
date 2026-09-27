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
    ros::Timer ready_timer_;
    std::string receiver_session_id_;

    sensor_msgs::NavSatFix current_drone_gps_;
    nav_msgs::Odometry current_vins_odom_;

    bool has_drone_gps_;
    bool has_vins_odom_;
    bool has_initial_heading_;


    bool has_active_goal_;
    geometry_msgs::Point goal_position_;
    std::string active_mission_id_;
    double arrival_threshold_;
    rescue_execution::TargetLifecycle lifecycle_;


    static constexpr double WGS84_A  = 6378137.0;
    static constexpr double WGS84_E2 = 0.00669437999014;


    double data_timeout_;
    ros::Time last_drone_gps_stamp_, last_vins_odom_stamp_;


    double safe_flight_alt_;


    double vins_heading_offset_;


    sensor_msgs::NavSatFix prev_gps_;
    bool has_prev_gps_;
    double prev_vins_yaw_;

    ros::Time last_drone_gps_time_;
    ros::Time last_vins_odom_time_;


    static double quaternionToYaw(const geometry_msgs::Quaternion& q) {
        return std::atan2(2.0 * (q.w * q.z + q.x * q.y),
                         1.0 - 2.0 * (q.y * q.y + q.z * q.z));
    }

public:
    MissionCommander()
        : has_drone_gps_(false), has_vins_odom_(false),
          has_initial_heading_(false), has_active_goal_(false),
          vins_heading_offset_(0.0), has_prev_gps_(false), prev_vins_yaw_(0.0) {

        std::random_device random;
        receiver_session_id_ = std::to_string(std::chrono::high_resolution_clock::now().time_since_epoch().count())
                             + "-" + std::to_string(random());

        ros::NodeHandle pnh("~");
        pnh.param("flight_alt", safe_flight_alt_, 1.5);
        pnh.param("arrival_threshold", arrival_threshold_, 1.0);
        pnh.param("data_timeout", data_timeout_, 5.0);
        if (!std::isfinite(safe_flight_alt_) || safe_flight_alt_ <= 0
            || !std::isfinite(arrival_threshold_) || arrival_threshold_ <= 0
            || !std::isfinite(data_timeout_) || data_timeout_ <= 0)
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
            ROS_WARN_THROTTLE(5.0, "Drone GPS has no fix, ignoring");
            return;
        }


        if (has_prev_gps_ && has_vins_odom_ && !has_initial_heading_
            && fresh(last_vins_odom_time_, last_vins_odom_stamp_)) {
            double dn, de;
            gpsToENU(prev_gps_.latitude, prev_gps_.longitude,
                     msg->latitude, msg->longitude, dn, de);
            double dist = std::sqrt(dn * dn + de * de);


            if (dist > 2.0) {
                double gps_heading = std::atan2(de, dn);
                double vins_yaw = quaternionToYaw(current_vins_odom_.pose.pose.orientation);
                vins_heading_offset_ = gps_heading - vins_yaw;
                has_initial_heading_ = true;
                ROS_INFO("Heading calibrated: GPS=%.1f deg, VINS=%.1f deg, offset=%.1f deg",
                         gps_heading * 180.0 / M_PI,
                         vins_yaw * 180.0 / M_PI,
                         vins_heading_offset_ * 180.0 / M_PI);
            }
        }

        prev_gps_ = *msg;
        has_prev_gps_ = true;

        current_drone_gps_ = *msg;
        has_drone_gps_ = true;
        last_drone_gps_time_ = ros::Time::now();
        last_drone_gps_stamp_ = msg->header.stamp;
    }

    void vinsOdomCallback(const nav_msgs::Odometry::ConstPtr& msg) {
        if (!std::isfinite(msg->pose.pose.position.x) || !std::isfinite(msg->pose.pose.position.y)
            || !std::isfinite(msg->pose.pose.position.z)
            || !std::isfinite(msg->pose.pose.orientation.x) || !std::isfinite(msg->pose.pose.orientation.y)
            || !std::isfinite(msg->pose.pose.orientation.z) || !std::isfinite(msg->pose.pose.orientation.w)
            || !fresh(ros::Time::now(), msg->header.stamp)) {
            has_vins_odom_ = false;
            return;
        }
        current_vins_odom_ = *msg;
        has_vins_odom_ = true;
        last_vins_odom_time_ = ros::Time::now();
        last_vins_odom_stamp_ = msg->header.stamp;


        if (lifecycle_.active()) {
            double dx = msg->pose.pose.position.x - goal_position_.x;
            double dy = msg->pose.pose.position.y - goal_position_.y;
            double dist = std::sqrt(dx * dx + dy * dy);

            if (lifecycle_.arrived(dist, arrival_threshold_)) {
                ROS_INFO("ARRIVED at goal (dist=%.2f m)", dist);
                publishFeedback(lifecycle_.current(), "ARRIVED", "HORIZONTAL_THRESHOLD_REACHED");
                has_active_goal_ = false;
            }
        }
    }

    bool fresh(const ros::Time& received, const ros::Time& stamp) const {
        const ros::Time now = ros::Time::now();
        const double age = (now - received).toSec();
        const double source_age = (now - stamp).toSec();
        return age >= 0 && age <= data_timeout_ && source_age >= 0 && source_age <= data_timeout_;
    }

    void controlCallback(const rescue_bridge::ExecutionControl::ConstPtr& msg) {
        if (msg->receiver_session_id != receiver_session_id_) return;
        if (msg->action != "CANCEL" && msg->action != "RELEASE") return;
        if (lifecycle_.control(msg->mission_id, msg->execution_id, msg->action == "RELEASE")) {
            has_active_goal_ = false;
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
        std::string not_ready;
        if (!has_drone_gps_) not_ready = "GPS_NOT_READY";
        else if (!has_vins_odom_) not_ready = "ODOMETRY_NOT_READY";
        else if (!fresh(last_drone_gps_time_, last_drone_gps_stamp_)) not_ready = "GPS_STALE";
        else if (!fresh(last_vins_odom_time_, last_vins_odom_stamp_)) not_ready = "ODOMETRY_STALE";
        if (msg->altitude_specified && (!std::isfinite(msg->altitude) || msg->altitude < .5 || msg->altitude > 120))
            not_ready = "INVALID_TASK_ALTITUDE";
        const auto decision = lifecycle_.offer(target, not_ready.empty(), not_ready);
        if (!decision.publish_goal) {
            if (decision.status == "ARRIVED") publishFeedback(target, "ACCEPTED", "DUPLICATE");
            publishFeedback(target, decision.status, decision.reason);
            return;
        }
        if (!has_initial_heading_)
            ROS_WARN_THROTTLE(3.0, "Heading not calibrated: existing zero-offset assumption remains active");

        ROS_INFO("Target GPS: lat=%.6f, lon=%.6f", msg->latitude, msg->longitude);


        double north_offset, east_offset;
        gpsToENU(current_drone_gps_.latitude, current_drone_gps_.longitude,
                 msg->latitude, msg->longitude,
                 north_offset, east_offset);

        ROS_INFO("ENU offset: N=%.2f m, E=%.2f m", north_offset, east_offset);


        double theta = vins_heading_offset_;
        double vins_dx =  std::cos(theta) * north_offset + std::sin(theta) * east_offset;
        double vins_dy = -std::sin(theta) * north_offset + std::cos(theta) * east_offset;


        geometry_msgs::PoseStamped goal;
        goal.header.stamp = ros::Time::now();
        goal.header.frame_id = current_vins_odom_.header.frame_id;

        goal.pose.position.x = current_vins_odom_.pose.pose.position.x + vins_dx;
        goal.pose.position.y = current_vins_odom_.pose.pose.position.y + vins_dy;
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
                         const std::string& reason) {
        rescue_bridge::WaypointFeedback msg;
        msg.mission_id = target.mission_id;
        msg.execution_id = target.execution_id;
        msg.waypoint_index = target.waypoint_index;
        msg.receiver_session_id = receiver_session_id_;
        msg.effective_altitude = target.altitude;
        msg.arrival_threshold = arrival_threshold_;
        msg.data_timeout = data_timeout_;
        msg.status = status;
        msg.reason = reason;
        status_pub_.publish(msg);
    }

private:


    void gpsToENU(double lat1_deg, double lon1_deg,
                  double lat2_deg, double lon2_deg,
                  double& north_m, double& east_m) {
        double lat1 = lat1_deg * M_PI / 180.0;
        double lat2 = lat2_deg * M_PI / 180.0;
        double dlat = lat2 - lat1;
        double dlon = (lon2_deg - lon1_deg) * M_PI / 180.0;


        double sin_lat = std::sin((lat1 + lat2) / 2.0);
        double W = std::sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat);
        double M = WGS84_A * (1.0 - WGS84_E2) / (W * W * W);
        double N = WGS84_A / W;

        north_m = dlat * M;
        east_m  = dlon * N * std::cos((lat1 + lat2) / 2.0);
    }
};

int main(int argc, char** argv) {
    ros::init(argc, argv, "mission_commander");
    MissionCommander commander;
    ros::spin();
    return 0;
}
