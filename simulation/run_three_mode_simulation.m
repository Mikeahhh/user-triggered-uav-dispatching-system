function [study,out] = run_three_mode_simulation(out)


root = fileparts(mfilename('fullpath'));
if nargin<1, out=''; end
out = prepare_simulation_output(out);
cfg = configuration();
terrain = loadTerrain(fullfile(root,'data','N22E114.hgt'),cfg);
rng(cfg.random_seed,'twister');
[scenario, sampling] = chooseScenario(terrain,cfg);
study.config = cfg;
study.scenario = scenario;
study.sampling = sampling;
study.terrain = terrain;
study.created_at = char(datetime('now','Format','yyyy-MM-dd HH:mm:ss'));
study.matlab_version = version;
study.evidence_type = 'New ideal kinematic MATLAB simulation with synthetic user records';

for mode = 1:3
    study.missions(mode) = simulateMission(mode,scenario,terrain,cfg);
end
study.verification = verifyStudy(study);
writeJson(fullfile(out,'matlab_verification.json'),study.verification);
if ~study.verification.all_passed
    disp(study.verification.checks(~[study.verification.checks.passed]));
end
assert(study.verification.all_passed,'Study verification failed.');
save(fullfile(out,'three_mode_study.mat'),'study','-v7');
writeJson(fullfile(out,'scenario_and_settings.json'), ...
    struct('config',cfg,'scenario',scenario,'sampling',sampling, ...
    'created_at',study.created_at,'matlab_version',version,'evidence_type',study.evidence_type));
writeJson(fullfile(out,'matlab_verification.json'),study.verification);

metrics = struct([]);
for mode = 1:3
    m = study.missions(mode);
    writetable(m.trace,fullfile(out,sprintf('mode%d_execution_trace.csv',mode)));
    writetable(m.events,fullfile(out,sprintf('mode%d_events.csv',mode)));
    [lat,lon] = enToLL(m.waypoints(:,1),m.waypoints(:,2),cfg);
    wp = table((1:height(m.waypoints))',lat,lon,m.waypoints(:,1), ...
        m.waypoints(:,2),groundAt(terrain,m.waypoints(:,1),m.waypoints(:,2)), ...
        'VariableNames',{'waypoint_index','latitude_deg','longitude_deg', ...
        'east_m','north_m','terrain_msl_m'});
    writetable(wp,fullfile(out,sprintf('mode%d_mission_waypoints.csv',mode)));
    metrics(mode).mode = mode;
    metrics(mode).input_waypoints = height(m.waypoints);
    metrics(mode).target_arrival_s = m.target_arrival_s;
    metrics(mode).return_start_s = m.return_start_s;
    metrics(mode).landing_complete_s = m.duration_s;
    metrics(mode).path_length_3d_m = m.path_length_3d_m;
    metrics(mode).min_cruise_agl_m = min(m.trace.agl_m(m.trace.cruise));
end
writetable(struct2table(metrics),fullfile(out,'mission_summary.csv'));
writetable(scenario.history_records,fullfile(out,'synthetic_gps_history.csv'));
fprintf('Seed %d; accepted candidate %d; target %.8f N, %.8f E, %.2f m MSL\n', ...
    cfg.random_seed,sampling.accepted_attempt,scenario.target_lat, ...
    scenario.target_lon,scenario.target_ground_msl_m);
disp(struct2table(metrics));
fprintf('All %d MATLAB assertions passed.\n',study.verification.check_count);
render_three_modes(study,out);
end

function cfg = configuration()
cfg.random_seed = 20260926;
cfg.launch_lat = 22.402;
cfg.launch_lon = 114.322;
cfg.launch_name = 'Pak Tam Chung';
cfg.metres_per_degree = 111320;
cfg.scene_east_limits_m = [-1500 5500];
cfg.scene_north_limits_m = [-1500 5500];
cfg.scene_side_m = 7000;
cfg.terrain_plot_step_m = 25;
cfg.terrain_source = 'Mapzen / Tilezen Skadi N22E114.hgt, 3601 x 3601, 1 arc-second';
cfg.terrain_source_url = 'https://elevation-tiles-prod.s3.amazonaws.com/skadi/N22/N22E114.hgt.gz';
cfg.terrain_sha256 = '433a57a6558821033957c2f2014682a259d7079e0e64843634430913a97b439a';
cfg.target_east_sampling_m = [1500 4000];
cfg.target_north_sampling_m = [500 4000];
cfg.target_distance_from_launch_m = [2000 4500];
cfg.target_elevation_msl_m = [100 500];
cfg.target_neighbourhood_min_ground_msl_m = 40;
cfg.target_neighbourhood_half_width_m = 240;
cfg.route_min_ground_msl_m = 2;
cfg.route_land_check_step_m = 5;
cfg.minimum_flight_clearance_m = 2;
cfg.clearance_check_step_m = 5;
cfg.altitude_agl_m = 80;
cfg.horizontal_speed_limit_mps = 15;
cfg.vertical_speed_limit_mps = 3;
cfg.hover_seconds = 5;
cfg.integration_step_m = 5;
% Nominal subdivision interval; terrain-limited trace segments can take longer.
cfg.max_output_time_step_s = 0.5;
cfg.spiral_spacing_m = 30;
cfg.spiral_generation_threshold_m = 200;
cfg.history_samples = 15;
cfg.synthetic_walking_speed_mps = 1;
cfg.target_zoom_half_width_m = 280;
cfg.phase_names = {'Takeoff','Transit','Route','Search','Hover','Return','Landing','Landed'};
cfg.assumptions = { ...
    'Synthetic planned route and pre-dispatch GPS history, not measured hiking data'; ...
    'Same launch, same stationary target, same terrain and speed settings'; ...
    'Ideal terrain-following position model, not PX4/ROS or aircraft dynamics'; ...
    'No acceleration, turn-rate, wind, vegetation/building obstacles or radio model'; ...
    'All supplied mission waypoints are visited, followed by direct horizontal RTL and landing'; ...
    'Mode 3 completes its whole spiral; no automatic person detection or proximity termination'; ...
    'Land/elevation constraints define this synthetic case, not official trail or terrain classification'; ...
    'Flight clearance is checked separately from ground elevation on mission and return segments'; ...
    'One illustrative scenario does not establish comparative rescue performance'};
end

function t = loadTerrain(path,cfg)
fid = fopen(path,'r','ieee-be');
assert(fid>=0,'Missing local terrain asset.');
cleanup = onCleanup(@()fclose(fid));
raw = fread(fid,[3601 3601],'int16=>double')';
assert(isequal(size(raw),[3601 3601]),'Unexpected HGT dimensions.');
assert(~any(raw==-32768,'all'),'Void samples require an explicit handling policy.');
lat = linspace(22,23,3601);
lon = linspace(114,115,3601);

t.interpolant = griddedInterpolant({lat,lon},flipud(raw),'linear','none');
t.launch_lat = cfg.launch_lat;
t.launch_lon = cfg.launch_lon;
t.metres_per_degree = cfg.metres_per_degree;
east = cfg.scene_east_limits_m(1):cfg.terrain_plot_step_m:cfg.scene_east_limits_m(2);
north = cfg.scene_north_limits_m(1):cfg.terrain_plot_step_m:cfg.scene_north_limits_m(2);
[t.east_grid_m,t.north_grid_m] = meshgrid(east,north);
t.height_msl_m = groundAt(t,t.east_grid_m,t.north_grid_m);
t.land_mask = t.height_msl_m>0.5;
t.data_extent_lat_lon = [22 23 114 115];
t.original_grid_size = [3601 3601];
t.launch_ground_msl_m = groundAt(t,0,0);
end

function [s,record] = chooseScenario(t,cfg)
rejections = struct('distance',0,'target_elevation',0,'neighbourhood',0,'route_land',0);
for attempt = 1:10000
    p = [cfg.target_east_sampling_m(1)+rand*diff(cfg.target_east_sampling_m), ...
        cfg.target_north_sampling_m(1)+rand*diff(cfg.target_north_sampling_m)];
    distance = norm(p);
    if distance<cfg.target_distance_from_launch_m(1) || distance>cfg.target_distance_from_launch_m(2)
        rejections.distance = rejections.distance+1; continue
    end
    z = groundAt(t,p(1),p(2));
    if z<cfg.target_elevation_msl_m(1) || z>cfg.target_elevation_msl_m(2)
        rejections.target_elevation = rejections.target_elevation+1; continue
    end
    n = linspace(-cfg.target_neighbourhood_half_width_m, ...
        cfg.target_neighbourhood_half_width_m,17);
    [ne,nn] = meshgrid(n+p(1),n+p(2));
    if min(groundAt(t,ne,nn),[],'all')<cfg.target_neighbourhood_min_ground_msl_m
        rejections.neighbourhood = rejections.neighbourhood+1; continue
    end
    unit = p/distance;
    perpendicular = [-unit(2),unit(1)];
    fractions = [0.27;0.63;1];
    lateral = [210;-150;0];
    planned = fractions.*p + lateral.*perpendicular;
    historyFraction = linspace(0.27,1,cfg.history_samples)';
    historyLateral = interp1(fractions,lateral,historyFraction,'linear') + ...
        210*sin(pi*(historyFraction-0.27)/0.73);
    history = historyFraction.*p + historyLateral.*perpendicular;
    history(end,:) = p;
    [lat,lon] = enToLL(p(1),p(2),cfg);
    [spiral,spiralLL] = expandingSquare(lat,lon,cfg);
    routes = {[0 0;planned;0 0],[0 0;history;0 0],[0 0;spiral;0 0]};
    valid = true;
    for k = 1:3
        pts = samplePolyline(routes{k},cfg.route_land_check_step_m);
        if min(groundAt(t,pts(:,1),pts(:,2)))<cfg.route_min_ground_msl_m
            valid = false; break
        end
    end
    if ~valid
        rejections.route_land = rejections.route_land+1; continue
    end
    s.launch_en_m = [0 0];
    s.target_en_m = p;
    s.target_lat = lat;
    s.target_lon = lon;
    s.target_ground_msl_m = z;
    s.mode1_planned_route_en_m = planned;
    s.mode2_history_route_en_m = history;
    s.mode3_spiral_en_m = spiral;
    s.mode3_spiral_lat_lon = spiralLL;
    elapsed = [0;cumsum(vecnorm(diff(history),2,2))]/cfg.synthetic_walking_speed_mps;
    [hlat,hlon] = enToLL(history(:,1),history(:,2),cfg);
    s.history_records = table((1:height(history))',elapsed,hlat,hlon, ...
        history(:,1),history(:,2),'VariableNames', ...
        {'sample_index','sample_time_s','latitude_deg','longitude_deg','east_m','north_m'});
    s.route_generation = 'Mode 1: 3 synthetic route landmarks at fractions 0.27/0.63/1; lateral offsets 210/-150/0 m. Mode 2: 15 chronological synthetic samples along an additional 210 m sinusoidal detour, with the same first and last positions. All segments checked above 2 m terrain MSL.';
    record.method = 'Uniform candidate sampling in a declared EN rectangle followed by deterministic rejection. The first accepted candidate is used; no manual selection.';
    record.accepted_attempt = attempt;
    record.rejected_counts = rejections;
    record.seed = cfg.random_seed;
    return
end
error('No acceptable mountain scenario found under the declared criteria.');
end

function [en,ll] = expandingSquare(lat,lon,cfg)

xy = [0 0];
heading = [1 0;0 1;-1 0;0 -1];
h = 1; leg = 1;
while true
    done = false;
    for j = 1:2
        xy(end+1,:) = xy(end,:)+heading(h,:)*leg*cfg.spiral_spacing_m;
        if norm(xy(end,:))>=cfg.spiral_generation_threshold_m
            done = true; break
        end
        h = mod(h,4)+1;
    end
    if done, break; end
    leg = leg+1;
end
ll = [lat+xy(:,2)/cfg.metres_per_degree, ...
    lon+xy(:,1)/(cfg.metres_per_degree*cosd(lat))];
en = [(ll(:,2)-cfg.launch_lon)*cfg.metres_per_degree*cosd(cfg.launch_lat), ...
    (ll(:,1)-cfg.launch_lat)*cfg.metres_per_degree];
end

function m = simulateMission(mode,s,t,cfg)
input = {s.mode1_planned_route_en_m,s.mode2_history_route_en_m,s.mode3_spiral_en_m};
waypoints = input{mode};

rows = [0 0 0 t.launch_ground_msl_m t.launch_ground_msl_m 1 0 0];
eventRows = {0,'takeoff_start',0};
appendVertical(cfg.altitude_agl_m,1);
eventRows(end+1,:) = {rows(end,1),'takeoff_complete',0};
targetArrival = NaN;
for w = 1:height(waypoints)
    if w==1, phase=2; elseif mode==3, phase=4; else, phase=3; end
    appendHorizontal(waypoints(w,:),phase,w);
    eventRows(end+1,:) = {rows(end,1),'waypoint_arrival',w};
    if norm(waypoints(w,:)-s.target_en_m)<1e-5 && isnan(targetArrival)
        targetArrival = rows(end,1);
        eventRows(end+1,:) = {rows(end,1),'common_target_arrival',w};
    end
    appendHover(w);
end
returnStart = rows(end,1);
eventRows(end+1,:) = {returnStart,'return_start',height(waypoints)};
appendHorizontal([0 0],6,height(waypoints)+1);
eventRows(end+1,:) = {rows(end,1),'home_arrival',height(waypoints)+1};
appendHover(height(waypoints)+1);
landingStart = rows(end,1);
eventRows(end+1,:) = {landingStart,'landing_start',height(waypoints)+1};
appendVertical(0,7);
eventRows(end+1,:) = {rows(end,1),'landing_complete',height(waypoints)+1};
trace = array2table(rows,'VariableNames',{'time_s','east_m','north_m', ...
    'terrain_msl_m','uav_msl_m','phase_code','target_index','cruise'});
trace.cruise = logical(trace.cruise);
trace.agl_m = trace.uav_msl_m-trace.terrain_msl_m;
trace.phase = string(cfg.phase_names(trace.phase_code))';
if isrow(trace.phase), trace.phase=trace.phase'; end
m.mode = mode;
m.waypoints = waypoints;
m.trace = trace;
m.events = cell2table(eventRows,'VariableNames',{'time_s','event','waypoint_index'});
m.target_arrival_s = targetArrival;
m.return_start_s = returnStart;
m.landing_start_s = landingStart;
m.duration_s = rows(end,1);
m.path_length_3d_m = sum(vecnorm(diff(rows(:,[2 3 5])),2,2));

    function appendVertical(finalAGL,phase)
        start = rows(end,:);
        finalZ = start(4)+finalAGL;
        duration = abs(finalZ-start(5))/cfg.vertical_speed_limit_mps;
        count = max(1,ceil(duration/cfg.max_output_time_step_s));
        for j=1:count
            a=j/count;
            rows(end+1,:)=[start(1)+a*duration,start(2:4), ...
                start(5)+a*(finalZ-start(5)),phase,start(7),0];
        end
    end

    function appendHorizontal(destination,phase,index)
        source = rows(end,2:3);
        distance = norm(destination-source);
        n = max(1,ceil(distance/cfg.integration_step_m));
        for q=1:n
            next = source+(destination-source)*q/n;
            previous = rows(end,:);
            zg = groundAt(t,next(1),next(2)); zu = zg+cfg.altitude_agl_m;
            dt = max(norm(next-previous(2:3))/cfg.horizontal_speed_limit_mps, ...
                abs(zu-previous(5))/cfg.vertical_speed_limit_mps);
            if dt<=eps, continue; end
            nt = max(1,ceil(dt/cfg.max_output_time_step_s));
            for j=1:nt
                a=j/nt;
                p=previous(2:3)+a*(next-previous(2:3));
                ground=groundAt(t,p(1),p(2));
                current=rows(end,:);
                localDt=max(norm(p-current(2:3))/cfg.horizontal_speed_limit_mps, ...
                    abs(ground+cfg.altitude_agl_m-current(5))/cfg.vertical_speed_limit_mps);
                rows(end+1,:)=[current(1)+localDt,p,ground, ...
                    ground+cfg.altitude_agl_m,phase,index,1];
            end
        end
    end

    function appendHover(index)
        previous=rows(end,:);
        n=max(1,ceil(cfg.hover_seconds/cfg.max_output_time_step_s));
        for j=1:n
            rows(end+1,:)=[previous(1)+j/n*cfg.hover_seconds, ...
                previous(2:5),5,index,1];
        end
    end
end

function v = verifyStudy(s)
checks=struct('name',{},'passed',{});
check('All three modes use one common launch and target', ...
    norm(s.scenario.mode1_planned_route_en_m(end,:)-s.scenario.target_en_m)<1e-8 && ...
    norm(s.scenario.mode2_history_route_en_m(end,:)-s.scenario.target_en_m)<1e-8 && ...
    norm(s.scenario.mode3_spiral_en_m(1,:)-s.scenario.target_en_m)<1e-5);
check('Target is inside the declared mountain elevation range', ...
    s.scenario.target_ground_msl_m>=s.config.target_elevation_msl_m(1) && ...
    s.scenario.target_ground_msl_m<=s.config.target_elevation_msl_m(2));
check('Mode 2 historical samples have strictly increasing timestamps', ...
    all(diff(s.scenario.history_records.sample_time_s)>0));
for k=1:3
    m=s.missions(k);r=m.trace;dt=diff(r.time_s);
    check(sprintf('Mode %d time strictly increases',k),all(dt>0));
    check(sprintf('Mode %d begins and ends on ground at launch',k), ...
        max(abs([r.east_m([1 end]);r.north_m([1 end]);r.agl_m([1 end])]))<1e-8);
    check(sprintf('Mode %d maintains 80 m AGL at all cruise samples',k), ...
        max(abs(r.agl_m(r.cruise)-s.config.altitude_agl_m))<1e-8);
    check(sprintf('Mode %d stays within horizontal speed bound',k), ...
        max(hypot(diff(r.east_m),diff(r.north_m))./dt)<=s.config.horizontal_speed_limit_mps+1e-7);
    check(sprintf('Mode %d stays within vertical speed bound',k), ...
        max(abs(diff(r.uav_msl_m))./dt)<=s.config.vertical_speed_limit_mps+1e-7);
    arrivals=m.events(strcmp(m.events.event,'waypoint_arrival'),:);
    check(sprintf('Mode %d visits all mission waypoints in order',k), ...
        isequal(arrivals.waypoint_index,(1:height(m.waypoints))'));
    check(sprintf('Mode %d returns only after all waypoints',k), ...
        m.return_start_s>=arrivals.time_s(end)+s.config.hover_seconds-1e-8);
    check(sprintf('Mode %d route stays on selected land',k), ...
        min(r.terrain_msl_m)>=s.config.route_min_ground_msl_m);
    flight=(find(r.cruise,1)-1):find(r.cruise,1,'last');
    clearance=flight_clearance(r.east_m(flight),r.north_m(flight),r.uav_msl_m(flight), ...
        @(east,north)groundAt(s.terrain,east,north),s.config.minimum_flight_clearance_m, ...
        s.config.clearance_check_step_m);
    check(sprintf('Mode %d has at least 2 m flight clearance on mission and return',k),clearance.passed);
    check(sprintf('Mode %d flight clearance samples are at most 5 m apart',k), ...
        clearance.maximum_sample_spacing_m<=s.config.clearance_check_step_m+1e-9);
    check(sprintf('Mode %d reaches the common target',k),isfinite(m.target_arrival_s));
end
v.checks=checks;v.check_count=numel(checks);v.all_passed=all([checks.passed]);
v.scope='Numerical model checks, not physical flight or rescue validation';
    function check(name,passed)
        checks(end+1)=struct('name',name,'passed',logical(passed));
    end
end

function p = samplePolyline(vertices,step)
p=vertices(1,:);
for k=2:height(vertices)
    n=max(1,ceil(norm(vertices(k,:)-vertices(k-1,:))/step));
    a=(1:n)'/n;
    p=[p;vertices(k-1,:)+a.*(vertices(k,:)-vertices(k-1,:))];
end
end

function z = groundAt(t,east,north)
lat=t.launch_lat+north/t.metres_per_degree;
lon=t.launch_lon+east/(t.metres_per_degree*cosd(t.launch_lat));
z=t.interpolant(lat,lon);
end

function [lat,lon] = enToLL(east,north,cfg)
lat=cfg.launch_lat+north/cfg.metres_per_degree;
lon=cfg.launch_lon+east/(cfg.metres_per_degree*cosd(cfg.launch_lat));
end

function writeJson(path,value)
fid=fopen(path,'w');assert(fid>=0);
cleanup=onCleanup(@()fclose(fid));
fwrite(fid,jsonencode(value,PrettyPrint=true),'char');
end
