# Documentation images

The component guides use existing manuscript figures and outdoor materials. The system architecture is reproduced directly from the paper. Additional component diagrams describe the current source and are editable SVGs.

## Ground station

- [Windows interface](screenshots/ground-station-windows.jpg): the original JPEG extracted from `station(alin).pdf`, the control-platform figure included by `main_MetaCom26_anonymous.tex`.
- [Mission-record detail](screenshots/ground-station-records.png) and [connection detail](screenshots/ground-station-connections.png): direct region renderings of that same PDF.
- The screenshot retains its captured version label and original server/status fields. The README explains the current operator workflow separately.

## Mobile application

- [Three service screens](screenshots/mobile-paper.png): `mapp(alin).pdf`, the mobile-interface figure included by the same manuscript. Outer blank page margins are omitted; labels and screen content are unchanged.

## Search UAV

- [Hardware figure](screenshots/uav-hardware-paper.png): `drone(alin).pdf`, the manuscript prototype figure, rendered with outer blank page margins omitted.
- [Airframe photograph](outdoor/airframe.jpg): existing outdoor test image in this repository.

## Architecture diagrams

| Diagram | Source reference |
| --- | --- |
| [System architecture](diagrams/system-architecture.png) | Figure 1 of the manuscript (`Fig1.pdf`), rendered directly with its original text and connections |
| [Android architecture](diagrams/mobile-architecture.svg) | [Persistent recording](../Android_Recording.md) |
| [Ground-station architecture](diagrams/ground-station-architecture.svg) | [Ground station](../../implementation/ground_station/src/ground_station.py), event manager, repository and dispatch journal |
| [Operator workflow](diagrams/operator-workflow.svg) | [Rescue event manager](../../implementation/ground_station/src/rescue_event_manager.py) and mission preparation |
| [UAV architecture](diagrams/uav-architecture.svg) | [Mission bridge](../../implementation/search_uav/catkin_ws/src/rescue_bridge/src/mqtt_bridge.py) and [flight setup](../Flight_Environment_Setup.txt) |
| [Mission lifecycle](diagrams/mission-lifecycle.svg) | [Execution state](../../implementation/search_uav/catkin_ws/src/rescue_bridge/src/execution_state.py) |
| [Phone-record delivery](diagrams/record-delivery.svg) | [Receiver](../../implementation/search_uav/drone_system/receiver/phone_sos_receiver.py) and delivery eligibility |
| [Simulation workflow](diagrams/simulation-workflow.svg) | [Simulation entry point](../../simulation/run_all.m) and saved scenario |

The additional SVG diagrams use white backgrounds, black text and simple connectors. The manuscript architecture figure retains its original colors on a white background. Screenshots and photographs retain their original evidence scope.
