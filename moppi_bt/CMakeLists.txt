cmake_minimum_required(VERSION 3.8)
project(moppi_bt)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(behaviortree_cpp REQUIRED)
find_package(behaviortree_ros2 REQUIRED)
find_package(moppi_interfaces REQUIRED)
find_package(ament_index_cpp REQUIRED)

# main.cpp를 컴파일해서 'bt_main'이라는 실행 파일을 만듦
add_executable(bt_main src/main.cpp)
ament_target_dependencies(bt_main
  rclcpp
  behaviortree_cpp
  behaviortree_ros2
  moppi_interfaces
  ament_index_cpp
)

# 만든 실행 파일을 install 폴더로 복사 (ros2 run으로 실행할 수 있게 함)
install(TARGETS bt_main
  DESTINATION lib/${PROJECT_NAME}
)

install(DIRECTORY behavior_trees
  DESTINATION share/${PROJECT_NAME}
)

ament_package()

