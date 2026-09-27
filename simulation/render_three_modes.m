function render_three_modes(study,out)

if nargin<1
    root=fileparts(mfilename('fullpath'));
    loaded=load(fullfile(root,'output','three_mode_study.mat'),'study');
    study=loaded.study;out=fullfile(root,'output');
end
c=palette();cfg=study.config;t=study.terrain;
set(groot,'defaultAxesFontName','Helvetica','defaultTextFontName','Helvetica');
fig=figure('Visible','off','Color','w','Position',[40 40 2100 1450], ...
    'Name','Sai Kung - three complete missions','NumberTitle','off');
annotation(fig,'textbox',[.02 .955 .94 .032],'String', ...
    'Three service modes over the same Sai Kung mountain terrain', ...
    'EdgeColor','none','FontSize',23,'FontWeight','bold','HorizontalAlignment','center');
annotation(fig,'textbox',[.03 .927 .92 .025],'String', ...
    'One synthetic mountain target  |  Same launch and terrain  |  Complete outbound, mission, return and landing', ...
    'EdgeColor','none','FontSize',13,'Color',[.27 .3 .33],'HorizontalAlignment','center');
names={'Mode 1: planned route','Mode 2: GPS history','Mode 3: SOS + square spiral'};
descriptions={'3 planned waypoints','15 samples fixed before dispatch','Direct transit, then all 19 search waypoints'};
x0=[.052 .355 .658];w=.265;
handles3=gobjects(1,3);handles2=gobjects(1,3);insets=gobjects(1,3);
for k=1:3
    m=study.missions(k);
    ax=axes(fig,'Position',[x0(k) .532 w .337]);handles3(k)=ax;
    drawTerrain3(ax,study,1);
    drawMission(ax,m,study,true,c);
    title(ax,{sprintf('(%c) %s',char('a'+k-1),names{k}),descriptions{k}}, ...
        'FontSize',14,'FontWeight','bold');
    xlabel(ax,'East from launch (km)');ylabel(ax,'North from launch (km)');
    zlabel(ax,'Elevation (m MSL)');
    configure3(ax,study);
    if k==1
        h1=plot3(ax,NaN,NaN,NaN,'-','Color',c.outbound,'LineWidth',2.5);
        h2=plot3(ax,NaN,NaN,NaN,'-','Color',c.search,'LineWidth',2.5);
        h3=plot3(ax,NaN,NaN,NaN,'--','Color',c.return,'LineWidth',2.5);
        h4=plot3(ax,NaN,NaN,NaN,'s','Color',c.launch,'MarkerFaceColor',c.launch);
        h5=plot3(ax,NaN,NaN,NaN,'p','Color',c.target,'MarkerFaceColor',c.target);
        lg=legend(ax,[h1 h2 h3 h4 h5],{'Outbound / route','SOS search','Return / landing','Launch / landing','Common target'}, ...
            'Orientation','horizontal','Box','off','FontSize',12);
        lg.Units='normalized';lg.Position=[.15 .887 .68 .025];
    end
    ax=axes(fig,'Position',[x0(k) .083 w .363]);handles2(k)=ax;
    drawTerrain2(ax,study);
    drawMission(ax,m,study,false,c);
    configure2(ax,cfg.scene_east_limits_m/1000,cfg.scene_north_limits_m/1000);
    title(ax,sprintf('(%c) Complete mission: top view',char('d'+k-1)), ...
        'FontSize',14,'FontWeight','bold');
    xlabel(ax,'East from launch (km)');ylabel(ax,'North from launch (km)');
    p=study.scenario.target_en_m/1000;r=cfg.target_zoom_half_width_m/1000;
    rectangle(ax,'Position',[p(1)-r p(2)-r 2*r 2*r], ...
        'EdgeColor',[.65 .22 .2],'LineStyle',':','LineWidth',1.4);
    text(ax,.022,.964,sprintf('Target reached: %.1f min\nLanded: %.1f min', ...
        m.target_arrival_s/60,m.duration_s/60),'Units','normalized', ...
        'FontSize',10.5,'BackgroundColor','w','Margin',4,'VerticalAlignment','top');

    iax=axes(fig,'Position',[x0(k)+w*.58 .291 w*.39 .142]);insets(k)=iax;
    drawTerrain2(iax,study);drawMission(iax,m,study,false,c);
    configure2(iax,p(1)+[-r r],p(2)+[-r r]);
    set(iax,'FontSize',8,'LineWidth',1.15,'XTick',p(1)+[-.2 0 .2], ...
        'YTick',p(2)+[-.2 0 .2]);
    xticklabels(iax,{'-200','0','200'});yticklabels(iax,{'-200','0','200'});
    title(iax,'Target detail (m)','FontSize',9,'FontWeight','bold');
end
cb=colorbar(handles3(3),'Position',[.951 .28 .009 .45]);
cb.Label.String='Terrain elevation (m MSL)';cb.FontSize=11;
annotation(fig,'textbox',[.035 .022 .89 .034],'String', ...
    'Shared 7 km x 7 km window; equal metre scaling in 3-D. Insets show the same 560 m x 560 m target area. Skadi elevation data; synthetic records; new MATLAB kinematic simulation.', ...
    'EdgeColor','none','FontSize',10.5,'Color',[.3 .32 .35],'HorizontalAlignment','center');
drawnow;
exportFigure(fig,fullfile(out,'Fig_three_modes_complete_missions'));
audit=struct('main_3d_axes',arrayfun(@axisRecord,handles3),'main_top_axes', ...
    arrayfun(@axisRecord,handles2),'target_insets',arrayfun(@axisRecord,insets));
writeJson(fullfile(out,'figure_axes_audit.json'),audit);
close(fig);
renderProfiles(study,out,c);
renderInputs(study,out,c);
end

function drawTerrain3(ax,s,skip)
t=s.terrain;ind=1:skip:size(t.height_msl_m,1);
e=t.east_grid_m(ind,ind)/1000;n=t.north_grid_m(ind,ind)/1000;z=t.height_msl_m(ind,ind);
land=z;land(z<=.5)=NaN;
water=zeros(size(z));water(z>.5)=NaN;
hold(ax,'on');
surf(ax,e,n,water,'FaceColor',[.64 .82 .87],'EdgeColor','none','FaceLighting','none');
surf(ax,e,n,land/1000,land,'FaceColor','interp','EdgeColor','none', ...
    'AmbientStrength',.7,'DiffuseStrength',.45,'SpecularStrength',.02);
colormap(ax,terrainColors(256));clim(ax,[0 700]);
contour3(ax,e,n,land/1000,0:.1:.7,'LineColor',[.35 .4 .31],'LineWidth',.35);
lighting(ax,'gouraud');camlight(ax,35,65);
set(findobj(ax,'Type','surface'),'AmbientStrength',.9,'DiffuseStrength',.25,'SpecularStrength',.02);
end

function drawTerrain2(ax,s)
t=s.terrain;z=t.height_msl_m;
map=terrainColors(256);
ix=1+round(min(1,max(0,z/700))*255);
rgb=reshape(map(ix(:),:),[size(z) 3]);
[gx,gy]=gradient(z,s.config.terrain_plot_step_m);
shade=.86+.14*(-.7*gx+.7*gy+1)./sqrt(gx.^2+gy.^2+1);
rgb=min(1,max(0,rgb.*shade));
for channel=1:3
    layer=rgb(:,:,channel);water=[.71 .86 .9];layer(z<=.5)=water(channel);rgb(:,:,channel)=layer;
end
hold(ax,'on');
image(ax,'XData',s.config.scene_east_limits_m/1000, ...
    'YData',s.config.scene_north_limits_m/1000,'CData',rgb);
land=z;land(z<=.5)=NaN;
contour(ax,t.east_grid_m/1000,t.north_grid_m/1000,land,50:100:650, ...
    'LineColor',[.43 .46 .34],'LineWidth',.35);
contour(ax,t.east_grid_m/1000,t.north_grid_m/1000,z,[.5 .5], ...
    'LineColor',[.18 .42 .49],'LineWidth',.85);
end

function drawMission(ax,m,s,is3,c)
r=m.trace;
cut=m.return_start_s;
outbound=r.time_s<=cut;
returning=r.time_s>=cut;
if m.mode==3
    transit=outbound & r.time_s<=m.target_arrival_s;
    search=outbound & r.time_s>=m.target_arrival_s;
else
    transit=outbound;search=false(height(r),1);
end
if is3
    plot3(ax,r.east_m(transit)/1000,r.north_m(transit)/1000,r.uav_msl_m(transit)/1000, ...
        '-','Color',c.outbound,'LineWidth',3.0);
    plot3(ax,r.east_m(search)/1000,r.north_m(search)/1000,r.uav_msl_m(search)/1000, ...
        '-','Color',c.search,'LineWidth',3.0);
    plot3(ax,r.east_m(returning)/1000,r.north_m(returning)/1000,r.uav_msl_m(returning)/1000, ...
        '--','Color',c.return,'LineWidth',2.6);
    launchZ=s.terrain.launch_ground_msl_m/1000;
    plot3(ax,0,0,launchZ,'s','MarkerFaceColor',c.launch,'MarkerEdgeColor','w','MarkerSize',8,'LineWidth',1);
    p=s.scenario.target_en_m/1000;z=s.scenario.target_ground_msl_m/1000;
    plot3(ax,p(1),p(2),z,'p','MarkerFaceColor',c.target,'MarkerEdgeColor','w','MarkerSize',13,'LineWidth',.7);
    plot3(ax,[p(1) p(1)],[p(2) p(2)],[z z+s.config.altitude_agl_m/1000],':', ...
        'Color',c.target,'LineWidth',1.1);
else
    plot(ax,r.east_m(transit)/1000,r.north_m(transit)/1000,'-','Color',c.outbound,'LineWidth',2.05);
    plot(ax,r.east_m(search)/1000,r.north_m(search)/1000,'-','Color',c.search,'LineWidth',1.9);
    plot(ax,r.east_m(returning)/1000,r.north_m(returning)/1000,'--','Color',c.return,'LineWidth',1.9);
    plot(ax,m.waypoints(:,1)/1000,m.waypoints(:,2)/1000,'o','MarkerSize',3.5, ...
        'MarkerFaceColor','w','MarkerEdgeColor',c.outbound,'LineWidth',.7);
    plot(ax,0,0,'s','MarkerFaceColor',c.launch,'MarkerEdgeColor','w','MarkerSize',8,'LineWidth',1);
    p=s.scenario.target_en_m/1000;
    plot(ax,p(1),p(2),'p','MarkerFaceColor',c.target,'MarkerEdgeColor','w','MarkerSize',12,'LineWidth',.7);
end
end

function configure3(ax,s)
xlim(ax,s.config.scene_east_limits_m/1000);ylim(ax,s.config.scene_north_limits_m/1000);
zlim(ax,[-.01 .78]);zticks(ax,[0 .6]);zticklabels(ax,{'0','600'});
set(ax,'Box','on','BoxStyle','full','LineWidth',.9,'FontSize',10.5, ...
    'GridAlpha',.16,'Projection','orthographic','Color','w');
grid(ax,'on');daspect(ax,[1 1 1]);view(ax,36,43);
end

function configure2(ax,xl,yl)
set(ax,'YDir','normal','Box','on','LineWidth',.95,'Layer','top', ...
    'FontSize',10.5,'GridAlpha',.12,'Color','w');
xlim(ax,xl);ylim(ax,yl);daspect(ax,[1 1 1]);grid(ax,'on');
end

function renderProfiles(s,out,c)
fig=figure('Visible','off','Color','w','Position',[50 50 2100 700], ...
    'Name','Complete mission altitude profiles','NumberTitle','off');
layout=tiledlayout(fig,1,3,'TileSpacing','compact','Padding','compact');
title(layout,'Terrain and aircraft height through takeoff, mission, return and landing','FontSize',19);
subtitle(layout,'Same time and height scales; 80 m AGL during transit, route following, search and return','FontSize',12);
maxTime=ceil(max([s.missions.duration_s])/60);
maxHeight=ceil(max(arrayfun(@(m)max(m.trace.uav_msl_m),s.missions))/100)*100;
for k=1:3
    ax=nexttile(layout);m=s.missions(k);r=m.trace;hold(ax,'on');
    area(ax,r.time_s/60,r.terrain_msl_m,'FaceColor',[.9 .91 .87], ...
        'EdgeColor',[.49 .51 .47],'LineWidth',1,'DisplayName','Ground MSL');
    before=r.time_s<=m.return_start_s;after=r.time_s>=m.return_start_s;
    plot(ax,r.time_s(before)/60,r.uav_msl_m(before),'-','Color',c.outbound,'LineWidth',2.2,'DisplayName','UAV: outbound / mission');
    plot(ax,r.time_s(after)/60,r.uav_msl_m(after),'--','Color',c.return,'LineWidth',2.2,'DisplayName','UAV: return / landing');
    xline(ax,m.target_arrival_s/60,':','Target','Color',c.target, ...
        'LabelVerticalAlignment','middle','LabelHorizontalAlignment','left','HandleVisibility','off');
    xline(ax,m.return_start_s/60,':','RTL','Color',c.return, ...
        'LabelVerticalAlignment','top','LabelHorizontalAlignment','right','HandleVisibility','off');
    plot(ax,m.duration_s/60,s.terrain.launch_ground_msl_m,'s','MarkerFaceColor',c.launch, ...
        'MarkerEdgeColor','w','MarkerSize',8,'HandleVisibility','off');
    title(ax,sprintf('Mode %d | landed at %.1f min',k,m.duration_s/60),'FontSize',14);
    xlabel(ax,'Time from takeoff (min)');ylabel(ax,'Elevation (m MSL)');
    xlim(ax,[0 maxTime]);ylim(ax,[0 maxHeight]);grid(ax,'on');box(ax,'on');
    set(ax,'FontSize',11,'LineWidth',1,'GridAlpha',.18);
    if k==1,legend(ax,'Location','northwest','FontSize',10,'Box','off');end
end
exportFigure(fig,fullfile(out,'Fig_complete_altitude_profiles'));close(fig);
end

function renderInputs(s,out,c)
fig=figure('Visible','off','Color','w','Position',[40 40 1400 1050], ...
    'Name','Common scenario and synthetic user records','NumberTitle','off');
ax=axes(fig,'Position',[.12 .1 .72 .8]);drawTerrain2(ax,s);
p1=s.scenario.mode1_planned_route_en_m/1000;
p2=s.scenario.mode2_history_route_en_m/1000;
p=s.scenario.target_en_m/1000;
h1=plot(ax,p1(:,1),p1(:,2),'-o','Color',c.outbound,'LineWidth',2,'MarkerFaceColor','w','MarkerSize',8);
h2=plot(ax,p2(:,1),p2(:,2),'-o','Color',[.05 .48 .36],'LineWidth',1.8,'MarkerFaceColor','w','MarkerSize',5);
h3=plot(ax,p(1),p(2),'p','MarkerSize',16,'MarkerFaceColor',c.target,'MarkerEdgeColor','w');
h4=plot(ax,0,0,'s','MarkerSize',10,'MarkerFaceColor',c.launch,'MarkerEdgeColor','w');
configure2(ax,s.config.scene_east_limits_m/1000,s.config.scene_north_limits_m/1000);
title(ax,{'Shared terrain, distinct input records',sprintf('First accepted mountain sample from seed %d: %.6f N, %.6f E', ...
    s.config.random_seed,s.scenario.target_lat,s.scenario.target_lon)},'FontSize',17);
xlabel(ax,'East from launch (km)');ylabel(ax,'North from launch (km)');
legend(ax,[h1 h2 h3 h4],{'Mode 1: synthetic planned route','Mode 2: synthetic GPS history', ...
    'Shared target / Mode 3 SOS position','Common UAV launch'},'Location','northwest','FontSize',11);
text(ax,.02,.025,'Input records shown here are synthetic; they are not measured hiking trails.', ...
    'Units','normalized','BackgroundColor','w','Margin',5,'FontSize',10.5);
exportFigure(fig,fullfile(out,'Fig_common_scenario_and_inputs'));close(fig);
end

function rec=axisRecord(ax)
rec=struct('xlim',ax.XLim,'ylim',ax.YLim,'zlim',ax.ZLim,'box',ax.Box, ...
    'data_aspect_ratio',ax.DataAspectRatio,'view',ax.View,'position',ax.Position);
end

function exportFigure(fig,stem)
savefig(fig,[stem '.fig']);
exportgraphics(fig,[stem '.png'],'Resolution',180,'BackgroundColor','white');

fprintf('Exported %s\n',stem);
end

function c=palette()
c.outbound=[.04 .33 .64];c.search=[.91 .49 .04];c.return=[.69 .13 .48];
c.launch=[.08 .12 .15];c.target=[.8 .13 .12];
end

function map=terrainColors(n)
stops=[0 .15 .35 .6 .8 1];
rgb=[.79 .86 .65;.64 .76 .48;.57 .66 .41;.63 .58 .40;.70 .64 .51;.83 .80 .71];
map=interp1(stops,rgb,linspace(0,1,n),'linear');
end

function writeJson(path,value)
fid=fopen(path,'w');cleanup=onCleanup(@()fclose(fid));
fwrite(fid,jsonencode(value,PrettyPrint=true),'char');
end
