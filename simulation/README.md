# Terrain Simulation

This directory reproduces the search-mission simulation in Section III-B of the paper. It compares Event Booking, Quick Start and SOS missions near Pak Tam Chung, Sai Kung. Each trajectory includes takeoff, search-route execution, return and landing over a common terrain model.

[System overview](../README.md) · [Mobile application](../implementation/mobile_application/README.md) · [Ground station](../implementation/ground_station/README.md) · [Search UAV](../implementation/search_uav/README.md)

**Environment:** The saved study records MATLAB R2025b Update 4 (`25.2.0.3150157`). Keep the supplied elevation tile and saved workspace in their original locations. Replay rendering also requires `ffmpeg`.

## Mission view

![Saved terrain figure showing the three complete mission trajectories](paper_current/terrain_missions.png)

[Full-resolution figure](paper_current/terrain_missions.png) · [Editable MATLAB figure](paper_current/terrain_missions.fig) · [Saved replay](output/Three_modes_complete_mission_replay.mp4)

The terrain figure uses saved trajectories. The route and GPS inputs are synthetic; the elevation tile supplies the terrain. Archived figures retain their original labels, while the current renderers use Event Booking, Quick Start and SOS.

## Simulation architecture

![Simulation workflow showing common inputs, mission computation, saved outputs and three reproduction modes](../docs/assets/diagrams/simulation-workflow.svg)

| Mode | Input to the simulated mission | Waypoints |
| --- | --- | --- |
| **Event Booking** | Ordered planned route | 3 |
| **Quick Start** | Timestamp-ordered GPS history available before dispatch | 15 |
| **SOS** | Centre plus expanding-square endpoints | 19 |

The three modes share the launch point and target. SOS completes its full search route before returning, even after the target is reached. The simulated clock begins at takeoff.

## Scope of the simulation

The inputs represent records already available for mission preparation. The model computes the resulting flight path and elapsed flight time. Event Booking overdue decisions, Quick Start update timeouts, SOS reception, operator contact, route approval and communication delays belong to the [system implementation](../implementation/README.md); they are outside this simulation's clock.

The model uses ideal terrain-following positions and speed limits. It does not model aircraft dynamics, acceleration, turn-rate limits, wind, vegetation or buildings, radio coverage, or automatic person detection. The common target is stationary. The study illustrates the three route behaviors in one selected scenario.

## Scenario and parameters

| Parameter | Saved value |
| --- | --- |
| Random generator and seed | MATLAB Twister, `20260926` |
| Launch location | 22.402° N, 114.322° E |
| Target location | 22.40782761° N, 114.35687745° E |
| Target ground elevation | Approximately 177.44 m |
| Terrain-following cruise | 80 m above ground |
| Horizontal / vertical speed limits | 15 m/s / 3 m/s |
| Waypoint hover | 5 s |
| SOS square spiral | Start at 30 m; increase by 30 m every two segments; stop at the first endpoint at least 200 m from the centre |

The source is the N22E114 Skadi tile: a 3601 × 3601 grid at 1 arc-second spacing with EGM96 elevations. Bilinear interpolation produces the 25 m plotting grid without increasing source resolution. Synthetic Quick Start sample times use a 1 m/s walking history; mobile booking estimates separately use 4 km/h.

### Target selection

[`chooseScenario` in run_three_mode_simulation.m](run_three_mode_simulation.m) draws candidate east and north offsets independently and uniformly, using the fixed generator above. Coordinates are relative to the launch point.

| Selection step | Rule |
| --- | --- |
| Candidate rectangle | 1,500–4,000 m east and 500–4,000 m north |
| Distance from launch | 2,000–4,500 m |
| Target terrain elevation | 100–500 m above mean sea level |
| Surrounding terrain | At least 40 m elevation at every point of a 17 × 17 grid extending 240 m in each direction from the target |
| Route terrain | At least 2 m elevation along all three mission routes and direct return segments, sampled at intervals no greater than 5 m |
| Selection order | Use the first candidate that passes every rule, with at most 10,000 attempts |

The saved case accepts candidate **6**. Earlier candidates were rejected twice for distance, twice for target elevation and once for the surrounding terrain. These constraints define the synthetic scenario; they are not a trail classification. After trajectory generation, flight clearance is checked separately against the terrain, with a minimum of 2 m and sampling intervals no greater than 5 m.

### Route inputs

After selecting the target, the route construction is deterministic:

1. **Event Booking:** place three waypoints at 27%, 63% and 100% of the launch-to-target baseline, with lateral offsets of 210 m left, 150 m right and 0 m. The last waypoint is the common target.
2. **Quick Start:** take 15 equally spaced fractions of that baseline from 27% to 100%. Interpolate the Event Booking lateral offsets and add a half-sine detour with amplitude 210 m. The first and last positions coincide with the first and last Event Booking waypoints. Sample timestamps equal cumulative distance along this synthetic history divided by 1 m/s; all samples exist before dispatch.
3. **SOS:** start at the common target, then generate counterclockwise square-spiral endpoints in east, north, west and south order. The first leg is 30 m, and its length grows by 30 m every two legs. Stop generating at the first endpoint at least 200 m from the centre. The resulting input contains the centre and 18 endpoints, all visited before return.

### Flight sequence and timing

Each mission climbs vertically to 80 m above the launch terrain, follows its ordered waypoints at 80 m above local terrain, hovers for 5 s at each waypoint, returns directly to launch, hovers for another 5 s and lands vertically. Horizontal route steps are no longer than 5 m. Each step takes the greater of its horizontal distance divided by 15 m/s and its absolute altitude change divided by 3 m/s. The implementation subdivides steps when needed and recalculates the local terrain height and elapsed time.

`target_arrival_s` records the first arrival at the common target; `landing_complete_s` includes every remaining waypoint, hover, return and landing. SOS therefore reaches the target before starting its square spiral and continues for the full route.

## Saved results

| Mode | Target arrival | Landing complete |
| --- | ---: | ---: |
| Event Booking | 360.66 s | 715.65 s |
| Quick Start | 409.08 s | 764.07 s |
| SOS | 344.99 s | 1008.53 s |

These timings describe this fixed scenario. Different route geometry, record history or search extent changes mission duration. The table is a comparison of synthetic trajectories, not measured outdoor mission times.

<details>
<summary><strong>View altitude profiles and complete mission paths</strong></summary>

![Saved altitude profiles](output/Fig_complete_altitude_profiles.png)

![Saved complete three-mode mission paths](output/Fig_three_modes_complete_missions.png)

</details>

## Reproduce the study

Set the MATLAB current folder to this `simulation` directory. Start with `paper` to redraw the saved study; use `simulate` to recompute the scenario and trajectories.

```matlab
% Redraw the paper figure from the saved study.
run_all('paper')

% Recompute trajectories into a new result directory.
run_all('simulate', fullfile(pwd, '..', 'local-results', 'simulation-01'))

% Render a replay of the saved trajectories; requires ffmpeg.
run_all('video', fullfile(pwd, '..', 'local-results', 'video-01'))
```

The default paper rendering writes to a new directory under `regenerated/`. Explicit result directories must be new. The existing `output/` folder remains the saved input/result archive.

| Command | Inputs read | Expected output |
| --- | --- | --- |
| `run_all('paper')` | `output/three_mode_study.mat` | `terrain_missions.png` and editable `terrain_missions.fig` in the new result directory |
| `run_all('simulate', result_dir)` | `data/N22E114.hgt` and settings in `run_three_mode_simulation.m` | Scenario settings, GPS history, mission CSV tables, `three_mode_study.mat`, mission/altitude figures, and the paper figure under `result_dir/paper/` |
| `run_all('video', result_dir)` | Saved trajectories in `output/three_mode_study.mat` | `Three_modes_complete_mission_replay.mp4` and rendered frames in the new result directory |

The recomputation prints the seed, accepted candidate, target coordinates and a mission summary. With the unchanged source and terrain tile, compare the waypoint counts and timings with the saved-results table above. The paper command redraws existing trajectories; it does not recompute the simulation. The video command likewise uses the saved study, even if a separate recomputation has been run.

## Files and results

| Material | Location |
| --- | --- |
| MATLAB entry point | [run_all.m](run_all.m) |
| Scenario generation and execution | [run_three_mode_simulation.m](run_three_mode_simulation.m) |
| Paper and mission renderers | [render_paper_terrain.m](render_paper_terrain.m) · [render_three_modes.m](render_three_modes.m) |
| Terrain source | [data/N22E114.hgt](data/N22E114.hgt) |
| Settings and mission timings | [scenario_and_settings.json](output/scenario_and_settings.json) · [mission_summary.csv](output/mission_summary.csv) |
| Saved workspace and GPS history | [three_mode_study.mat](output/three_mode_study.mat) · [synthetic_gps_history.csv](output/synthetic_gps_history.csv) |
| Per-mode input/output tables | `output/mode*_mission_waypoints.csv` · `output/mode*_execution_trace.csv` · `output/mode*_events.csv` |

Waypoint tables contain the ordered mission inputs. Execution traces contain elapsed time, local position, terrain and UAV altitude, flight phase and waypoint index. Event tables record arrivals, return and landing transitions. `mission_summary.csv` reports target arrival, return start, landing completion, path length and minimum cruise height. `scenario_and_settings.json` contains the inputs needed to interpret the saved study.

The 80 m terrain-following setting belongs to this study. Onboard ROS defaults and physical flight configuration are documented in the [UAV guide](../implementation/search_uav/README.md).
