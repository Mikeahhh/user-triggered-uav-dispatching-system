function run_all(task)


if nargin == 0, task = 'paper'; end
task = validatestring(task, {'paper','simulate','video'});
root = fileparts(mfilename('fullpath'));
oldPath = path;
cleanup = onCleanup(@() path(oldPath));
addpath(root, '-begin');
if ~isfolder(fullfile(root,'verification'))
    mkdir(fullfile(root,'verification'));
end

if strcmp(task, 'simulate')
    assert(isfile(fullfile(root,'data','N22E114.hgt')), ...
        'Terrain file missing: keep the data folder beside run_all.m.');
    run_three_mode_simulation();
end

studyFile = fullfile(root,'output','three_mode_study.mat');
assert(isfile(studyFile), ...
    'Saved study missing. Restore output/three_mode_study.mat or run run_all(''simulate'').');
if strcmp(task, 'video')
    animate_three_modes(false);
    fprintf('Animation: %s\n',fullfile(root,'output','Three_modes_complete_mission_replay.mp4'));
else
    out = fullfile(root,'regenerated','paper');
    if ~isfolder(out), mkdir(out); end
    render_paper_terrain(studyFile,out);
    fix_saved_paper_figure(out);
    fprintf('Paper figure: %s\n',fullfile(out,'terrain_missions.png'));
    fprintf('Editable MATLAB figure: %s\n',fullfile(out,'terrain_missions.fig'));
end
end
