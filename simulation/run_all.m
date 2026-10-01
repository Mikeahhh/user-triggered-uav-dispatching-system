function run_all(task,out)


if nargin == 0, task = 'paper'; end
if nargin<2, out=''; end
task = validatestring(task, {'paper','simulate','video'});
root = fileparts(mfilename('fullpath'));
oldPath = path;
cleanup = onCleanup(@() path(oldPath));
addpath(root, '-begin');
studyFile = fullfile(root,'output','three_mode_study.mat');
if strcmp(task, 'simulate')
    assert(isfile(fullfile(root,'data','N22E114.hgt')), ...
        'Terrain file missing: keep the data folder beside run_all.m.');
    [~,out]=run_three_mode_simulation(out);
    studyFile=fullfile(out,'three_mode_study.mat');
    out=prepare_simulation_output(fullfile(out,'paper'));
else
    out=prepare_simulation_output(out);
end
assert(isfile(studyFile), ...
    'Saved study missing. Restore output/three_mode_study.mat or run run_all(''simulate'').');
if strcmp(task, 'video')
    animate_three_modes(false,[],out,studyFile);
    fprintf('Animation: %s\n',fullfile(out,'Three_modes_complete_mission_replay.mp4'));
else
    render_paper_terrain(studyFile,out);
    fix_saved_paper_figure(out);
    fprintf('Paper figure: %s\n',fullfile(out,'terrain_missions.png'));
    fprintf('Editable MATLAB figure: %s\n',fullfile(out,'terrain_missions.fig'));
end
end
