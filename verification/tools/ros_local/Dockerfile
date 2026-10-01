ARG ROS_IMAGE=ros@sha256:72b8bc59035dc0a5b8e07aae28c16caa84192971d72d207c72ed734fb1d5e97d
FROM ${ROS_IMAGE}
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential cmake libeigen3-dev python3-paho-mqtt python3-yaml python3-numpy \
    mosquitto iproute2 ros-noetic-cmake-modules ros-noetic-sensor-msgs ros-noetic-nav-msgs \
    ros-noetic-geometry-msgs ros-noetic-message-generation ros-noetic-message-runtime \
    && rm -rf /var/lib/apt/lists/*
ENV ROS_MASTER_URI=http://127.0.0.1:11311 ROS_IP=127.0.0.1 ROS_HOME=/results/ros-home
WORKDIR /results/workspace
ENTRYPOINT ["/bin/bash"]
