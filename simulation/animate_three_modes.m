function animate_three_modes(previewOnly,frameIndices)

if nargin<1, previewOnly=false; end
frameWorker=nargin>=2 && ~isempty(frameIndices);
root=fileparts(mfilename('fullpath'));out=fullfile(root,'output');
loaded=load(fullfile(out,'three_mode_study.mat'),'study');s=loaded.study;
cfg=s.config;t=s.terrain;
outbound=[.04 .33 .64];search=[.91 .49 .04];returnColor=[.69 .13 .48];
targetColor=[.8 .13 .12];launchColor=[.08 .12 .15];
fig=figure('Visible','off','Color','w','Position',[40 50 1500 920], ...
    'Name','MATLAB: shared Sai Kung terrain, three complete missions','NumberTitle','off');
annotation(fig,'textbox',[.02 .955 .96 .035],'String', ...
    'One mountain target - three complete UAV missions','EdgeColor','none', ...
    'FontSize',20,'FontWeight','bold','HorizontalAlignment','center');
clockLabel=annotation(fig,'textbox',[.04 .915 .92 .027],'String','', ...
    'EdgeColor','none','FontSize',12,'HorizontalAlignment','center');
annotation(fig,'textbox',[.03 .025 .94 .025],'String', ...
    'Blue: outbound / route   |   Orange: SOS search   |   Magenta: return / landing   |   80 m AGL; 1:1 terrain geometry; synthetic records', ...
    'EdgeColor','none','FontSize',10.5,'HorizontalAlignment','center');
fps=8;speedup=48;maxTime=max([s.missions.duration_s]);
timeFrames=unique([0:speedup/fps:maxTime,maxTime]);
timeFrames=[timeFrames repmat(maxTime,1,fps*2)];
if previewOnly, timeFrames=[maxTime*.55 maxTime]; end
if ~frameWorker, frameIndices=1:numel(timeFrames); end
frameDirectory=fullfile(out,'animation_frames');
if ~previewOnly && ~isfolder(frameDirectory), mkdir(frameDirectory); end
ind=1:3:size(t.height_msl_m,1);
e=t.east_grid_m(ind,ind)/1000;n=t.north_grid_m(ind,ind)/1000;z=t.height_msl_m(ind,ind);
land=z;land(z<=.5)=NaN;water=zeros(size(z));water(z>.5)=NaN;
map=interp1([0 .15 .35 .6 .8 1],[.79 .86 .65;.64 .76 .48;.57 .66 .41; ...
    .63 .58 .40;.70 .64 .51;.83 .80 .71],linspace(0,1,256));
x0=[.052 .365 .678];w=.27;maxHeight=600;
names={'Mode 1: planned route','Mode 2: GPS history','Mode 3: SOS square spiral'};
for k=1:3
    ax=axes(fig,'Position',[x0(k) .46 w .415]);hold(ax,'on');
    surf(ax,e,n,water,'FaceColor',[.64 .82 .87],'EdgeColor','none','FaceLighting','none');
    surf(ax,e,n,land/1000,land,'FaceColor','interp','EdgeColor','none', ...
        'AmbientStrength',.7,'DiffuseStrength',.45,'SpecularStrength',.02);
    colormap(ax,map);clim(ax,[0 700]);lighting(ax,'gouraud');camlight(ax,35,65);
    set(findobj(ax,'Type','surface'),'AmbientStrength',.9,'DiffuseStrength',.25,'SpecularStrength',.02);
    p=s.scenario.target_en_m/1000;pz=s.scenario.target_ground_msl_m/1000;
    plot3(ax,p(1),p(2),pz,'p','MarkerFaceColor',targetColor,'MarkerEdgeColor','w','MarkerSize',12);
    plot3(ax,0,0,t.launch_ground_msl_m/1000,'s','MarkerFaceColor',launchColor,'MarkerEdgeColor','w','MarkerSize',8);
    h(k).outbound=plot3(ax,NaN,NaN,NaN,'-','Color',outbound,'LineWidth',2.2);
    h(k).search=plot3(ax,NaN,NaN,NaN,'-','Color',search,'LineWidth',2.2);
    h(k).return=plot3(ax,NaN,NaN,NaN,'--','Color',returnColor,'LineWidth',2.2);
    h(k).aircraft=plot3(ax,0,0,t.launch_ground_msl_m/1000,'^', ...
        'MarkerFaceColor',[.15 .16 .2],'MarkerEdgeColor','w','MarkerSize',11,'LineWidth',.9);
    h(k).stem=plot3(ax,[0 0],[0 0],[0 0],':','Color',[.15 .16 .2],'LineWidth',.9);
    title(ax,names{k},'FontSize',14,'FontWeight','bold');
    xlabel(ax,'East (km)');ylabel(ax,'North (km)');zlabel(ax,'Elevation (m MSL)');
    set(ax,'Box','on','BoxStyle','full','FontName','Helvetica','FontSize',9,'LineWidth',.9, ...
        'GridAlpha',.13,'Projection','orthographic');
    xlim(ax,cfg.scene_east_limits_m/1000);ylim(ax,cfg.scene_north_limits_m/1000);
    zlim(ax,[-.01 .78]);zticks(ax,[0 .6]);zticklabels(ax,{'0','600'});
    daspect(ax,[1 1 1]);view(ax,36,43);grid(ax,'on');
    h(k).status=annotation(fig,'textbox',[x0(k) .423 w .035],'String','', ...
        'EdgeColor','none','FontSize',11,'HorizontalAlignment','center','FontWeight','bold');
    axp=axes(fig,'Position',[x0(k) .118 w .236]);hold(axp,'on');r=s.missions(k).trace;
    area(axp,r.time_s/60,r.terrain_msl_m,'FaceColor',[.9 .91 .87], ...
        'EdgeColor',[.58 .6 .54],'LineWidth',.7);
    plot(axp,r.time_s/60,r.uav_msl_m,':','Color',[.76 .78 .8],'LineWidth',.8);
    h(k).profile=plot(axp,NaN,NaN,'-','Color',outbound,'LineWidth',1.8);
    h(k).profileReturn=plot(axp,NaN,NaN,'--','Color',returnColor,'LineWidth',1.8);
    h(k).profileMarker=plot(axp,0,t.launch_ground_msl_m,'o','MarkerSize',5, ...
        'MarkerFaceColor',launchColor,'MarkerEdgeColor','w');
    title(axp,'Ground and aircraft height','FontSize',11);
    xlabel(axp,'Time from takeoff (min)');ylabel(axp,'Elevation (m MSL)');
    xlim(axp,[0 ceil(maxTime/60)]);ylim(axp,[0 maxHeight]);box(axp,'on');grid(axp,'on');
    set(axp,'FontSize',10,'LineWidth',.9,'GridAlpha',.16);
end
drawnow;
for frameIndex=frameIndices
    now=timeFrames(frameIndex);
    clockLabel.String=sprintf('Shared simulation clock %02d:%05.2f   |   48x playback   |   Fixed scene and camera for all modes', ...
        floor(now/60),mod(now,60));
    for k=1:3
        m=s.missions(k);r=m.trace;local=min(now,m.duration_s);
        past=r.time_s<=local;
        before=past & r.time_s<=m.return_start_s;
        after=past & r.time_s>=m.return_start_s;
        if k==3
            transit=before & r.time_s<=m.target_arrival_s;
            spiral=before & r.time_s>=m.target_arrival_s;
        else
            transit=before;spiral=false(height(r),1);
        end
        setLine(h(k).outbound,r,transit);setLine(h(k).search,r,spiral);setLine(h(k).return,r,after);
        position=interp1(r.time_s,[r.east_m r.north_m r.uav_msl_m r.terrain_msl_m],local,'linear');
        set(h(k).aircraft,'XData',position(1)/1000,'YData',position(2)/1000,'ZData',position(3)/1000);
        set(h(k).stem,'XData',repmat(position(1)/1000,1,2),'YData',repmat(position(2)/1000,1,2), ...
            'ZData',position([4 3])/1000);
        set(h(k).profile,'XData',r.time_s(before)/60,'YData',r.uav_msl_m(before));
        set(h(k).profileReturn,'XData',r.time_s(after)/60,'YData',r.uav_msl_m(after));
        set(h(k).profileMarker,'XData',local/60,'YData',position(3));
        last=find(past,1,'last');phase=char(r.phase(last));
        if now>=m.duration_s,phase='LANDED';end
        h(k).status.String=sprintf('%s  |  %.0f m MSL / %.0f m AGL',phase,position(3),max(0,position(3)-position(4)));
    end
    drawnow;

    captureTimer=tic;
    img=print(fig,'-RGBImage','-r100');
    if previewOnly || frameIndex<=3
        fprintf('Frame capture %.2f s\n',toc(captureTimer));
    end
    img=img(1:2*floor(size(img,1)/2),1:2*floor(size(img,2)/2),:);
    if previewOnly
        imwrite(img,fullfile(root,'verification','print_capture_preview.png'));
    else
        imwrite(img,fullfile(frameDirectory,sprintf('frame_%04d.png',frameIndex)));
    end
    if frameIndex==numel(timeFrames) && ~previewOnly
        imwrite(img,fullfile(out,'Video_final_frame.png'));
    end
    if mod(frameIndex,10)==0 || frameIndex==numel(timeFrames)
        fprintf('Video frame %d / %d\n',frameIndex,numel(timeFrames));
    end
end
if previewOnly || frameWorker, close(fig); return; end
close(fig);
assemble_animation;
fprintf('Video saved. %d frames at %d fps.\n',numel(timeFrames),fps);
end

function setLine(h,r,mask)
set(h,'XData',r.east_m(mask)/1000,'YData',r.north_m(mask)/1000,'ZData',r.uav_msl_m(mask)/1000);
end
