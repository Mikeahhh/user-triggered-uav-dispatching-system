function assemble_animation()

root=fileparts(mfilename('fullpath'));out=fullfile(root,'output');
frames=fullfile(out,'animation_frames');
record=load(fullfile(out,'three_mode_study.mat'),'study');
maxTime=max([record.study.missions.duration_s]);fps=8;speedup=48;
times=unique([0:speedup/fps:maxTime,maxTime]);
count=numel(times)+fps*2;
for k=1:count
    assert(isfile(fullfile(frames,sprintf('frame_%04d.png',k))),'Missing frame %d',k);
end
temporary=fullfile(out,'Three_modes_complete_mission_replay.inprogress.mp4');


encoder='ffmpeg';
if isfile('/opt/homebrew/bin/ffmpeg'), encoder='/opt/homebrew/bin/ffmpeg'; end
command=sprintf('%s -v error -y -framerate %d -start_number 1 -i %s -frames:v %d -c:v libx264 -preset medium -crf 16 -pix_fmt yuv420p -movflags +faststart %s', ...
    quoteShell(encoder),fps,quoteShell(fullfile(frames,'frame_%04d.png')),count,quoteShell(temporary));
[status,message]=system(command);
assert(status==0,'Video encoding failed: %s',message);
movefile(temporary,fullfile(out,'Three_modes_complete_mission_replay.mp4'),'f');
copyfile(fullfile(frames,sprintf('frame_%04d.png',count)),fullfile(out,'Video_final_frame.png'));
fprintf('Assembled %d native MATLAB frames, %d fps, %.3f seconds.\n',count,fps,count/fps);
end

function q=quoteShell(value)
q=[char(39) strrep(value,char(39),[char(39) char(34) char(39) char(34) char(39)]) char(39)];
end
