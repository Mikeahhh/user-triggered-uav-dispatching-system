function result = flight_clearance(east,north,altitude,groundAt,minimum,step)
east=east(:);north=north(:);altitude=altitude(:);
assert(numel(east)==numel(north) && numel(east)==numel(altitude) && ~isempty(east), ...
    'simulation:InvalidClearanceInput','Flight coordinates must have equal nonzero length.');
assert(all(isfinite([east;north;altitude])) && isscalar(minimum) && isfinite(minimum) ...
    && minimum>0 && isscalar(step) && isfinite(step) && step>0, ...
    'simulation:InvalidClearanceInput','Clearance coordinates and limits must be finite and valid.');
positions=[east(1),north(1),altitude(1)];
for k=2:numel(east)
    count=max(1,ceil(hypot(east(k)-east(k-1),north(k)-north(k-1))/step));
    fraction=(1:count)'/count;
    start=[east(k-1),north(k-1),altitude(k-1)];
    finish=[east(k),north(k),altitude(k)];
    positions=[positions;start+fraction.*(finish-start)];
end
ground=groundAt(positions(:,1),positions(:,2));
ground=ground(:);
assert(numel(ground)==height(positions),'simulation:InvalidTerrainResult', ...
    'Terrain samples must match flight sample count.');
clearance=positions(:,3)-ground;
spacing=vecnorm(diff(positions(:,1:2)),2,2);
result=struct('passed',all(isfinite(ground)) && all(clearance>=minimum-1e-9), ...
    'minimum_clearance_m',min(clearance),'required_clearance_m',minimum, ...
    'sample_count',height(positions),'maximum_sample_spacing_m',max([0;spacing]));
end
