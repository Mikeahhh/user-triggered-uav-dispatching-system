cmake_minimum_required(VERSION 3.0.2)
project(rescue_bridge)
add_compile_options(-std=c++14)

find_package(catkin REQUIRED COMPONENTS
  roscpp
  rospy
  std_msgs
  sensor_msgs
  nav_msgs
  geometry_msgs
  message_generation
)

add_message_files(FILES WaypointCommand.msg WaypointFeedback.msg ExecutionControl.msg)
generate_messages()

catkin_package(
  INCLUDE_DIRS include
  CATKIN_DEPENDS roscpp rospy std_msgs sensor_msgs nav_msgs geometry_msgs message_runtime
)

include_directories(
  include
  ${catkin_INCLUDE_DIRS}
)


add_executable(mission_commander src/mission_commander.cpp)
target_link_libraries(mission_commander ${catkin_LIBRARIES})
add_dependencies(mission_commander ${${PROJECT_NAME}_EXPORTED_TARGETS} ${catkin_EXPORTED_TARGETS})


catkin_install_python(PROGRAMS
  src/mqtt_bridge.py
  src/sim_gps_adapter.py
  src/sim_fake_gps.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)


install(FILES
  src/mission_protocol.py
  src/execution_protocol.py
  src/mission_transfer_protocol.py
  src/mission_transfer_store.py
  src/execution_state.py
  src/collection_context.py
  src/target_lifecycle.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)


install(TARGETS mission_commander
  RUNTIME DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)


install(DIRECTORY launch/
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}/launch
)

install(DIRECTORY include/${PROJECT_NAME}/ DESTINATION ${CATKIN_PACKAGE_INCLUDE_DESTINATION})
