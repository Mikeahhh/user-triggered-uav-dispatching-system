# System Implementation

This directory contains the Android mobile application, Windows ground station and onboard UAV software for the system described in Sections II and III-A of the paper. The three components connect user records, operator-confirmed dispatch and UAV mission execution.

[System overview](../README.md) · [Simulation](../simulation/README.md) · [Technical guide](../docs/Technical_Guide.md)

## Components

| Component | Role | Guide |
| --- | --- | --- |
| Android mobile application | Records planned trips, timestamped GPS history and SOS requests; synchronizes records and uploads phone records through the UAV hotspot | [Screens, architecture and setup](mobile_application/README.md) |
| Windows ground station | Presents notices for contact verification, creates confirmed search events, prepares routes and dispatches missions selected by the operator | [Interface, workflow and setup](ground_station/README.md) |
| Search UAV | Executes ordered waypoints, reports mission progress, stores and forwards phone records, and records downward-facing video | [Hardware, onboard architecture and setup](search_uav/README.md) |

## Connection to the paper

| Paper stage | Implementation entry points |
| --- | --- |
| **Stage 1: User Record Tracking and Search Event Creation** | Mobile [service pages](mobile_application/src/pages/) and [persistent tracking](mobile_application/src/services/persistentTracking.ts); ground-station [notice detection and contact verification](ground_station/src/rescue_event_manager.py) |
| **Stage 2: Search Mission Scheduling** | Ground-station [operator interface and route preparation](ground_station/src/ground_station.py), [event selection and dispatch state](ground_station/src/active_event_queue.py), and [SOS route generation](ground_station/src/sos_pattern.py) |
| **Stage 3: UAV Mission Execution and Recording** | Onboard [mission programs](search_uav/catkin_ws/src/rescue_bridge/src/), [phone-record receiver](search_uav/drone_system/receiver/phone_sos_receiver.py), and [camera recording](search_uav/drone_system/launcher/cam_recorder.py) |

The operator confirms a search before a dispatchable event is created, then selects an event, reviews its route and confirms dispatch. UAV acceptance starts execution. The [system architecture](../README.md#system-architecture) shows these stages using the original paper figure.

## Setup

Start with the component guide for the environment being installed: [Android](mobile_application/README.md#getting-started), [Windows](ground_station/README.md#getting-started), or [onboard Ubuntu and ROS](search_uav/README.md#getting-started). Supply deployment credentials and network settings locally. The onboard flight stack and build order are specified in the [flight environment guide](../docs/Flight_Environment_Setup.txt).

The separate [MATLAB simulation](../simulation/README.md) corresponds to Section III-B. Its synthetic routes, terrain-following altitude and mission timings describe that study; the component guides specify the settings used by the deployed software.
