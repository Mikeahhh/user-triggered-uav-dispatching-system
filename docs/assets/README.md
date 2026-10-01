# Documentation images

The component guides use existing manuscript figures and outdoor materials. Diagram files describe the current source and are editable SVGs.

## Ground station

- [Windows interface](screenshots/ground-station-windows.jpg): the original JPEG extracted from `station(alin).pdf`, the control-platform figure included by `main_MetaCom26_anonymous.tex`.
- [Mission-record detail](screenshots/ground-station-records.png) and [connection detail](screenshots/ground-station-connections.png): direct region renderings of that same PDF.
- The screenshot retains its captured version label and original server/status fields. The README explains the current operator workflow separately.

## Mobile application

- [Three service screens](screenshots/mobile-paper.png): `mapp(alin).pdf`, the mobile-interface figure included by the same manuscript. Outer blank page margins are omitted; labels and screen content are unchanged.

## Search UAV

- [Hardware figure](screenshots/uav-hardware-paper.png): `drone(alin).pdf`, the manuscript prototype figure, rendered with outer blank page margins omitted.
- [Airframe photograph](outdoor/airframe.jpg): existing outdoor test image in this repository.

## Source figure checksums

| Original figure | SHA-256 |
| --- | --- |
| `station(alin).pdf` | `51f968b0850252f74ce87d3857c4caefc7bb8f42d868550c6db076d82f02fbda` |
| `mapp(alin).pdf` | `a0b018ff63bc79d48d37bbfb8c450a2309456b0c2a4ea7df1ae2e22397fd463f` |
| `drone(alin).pdf` | `551ec57432ca77cc02b5da8dca7d9b285178e7dfc6ded002ec3817a9155c5a0a` |

## Architecture diagrams

| Diagram | Source reference |
| --- | --- |
| [System topology](diagrams/system-topology.svg) | [Technical guide](../Technical_Guide.md) and the three component sources |
| [Android architecture](diagrams/mobile-architecture.svg) | [Persistent recording](../Android_Recording.md) |
| [Ground-station architecture](diagrams/ground-station-architecture.svg) | [Ground station](../../code/ground_station/src/ground_station.py), event manager, repository and dispatch journal |
| [Operator workflow](diagrams/operator-workflow.svg) | [Rescue event manager](../../code/ground_station/src/rescue_event_manager.py) and mission preparation |
| [UAV architecture](diagrams/uav-architecture.svg) | [Mission bridge](../../code/search_uav/catkin_ws/src/rescue_bridge/src/mqtt_bridge.py) and [flight setup](../Flight_Environment_Setup.txt) |
| [Mission lifecycle](diagrams/mission-lifecycle.svg) | [Execution state](../../code/search_uav/catkin_ws/src/rescue_bridge/src/execution_state.py) |
| [Phone-record delivery](diagrams/record-delivery.svg) | [Receiver](../../code/search_uav/drone_system/receiver/phone_sos_receiver.py) and delivery eligibility |
| [Simulation workflow](diagrams/simulation-workflow.svg) | [Simulation entry point](../../simulation/run_all.m) and saved scenario |

The diagrams use white backgrounds, black text and simple connectors. Screenshots and photographs retain their original evidence scope.
